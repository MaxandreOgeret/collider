# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Integration tests for transitive dependency resolution in pkg add."""

import argparse
import hashlib
import os
import urllib.request

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry, add_wrap_entry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Add import Add
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from collider.utils.packaging.resolver import (
    Candidate,
    Requirement,
    ResolutionResult,
    ResolutionSummary,
)


class _DummyResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _wrap_text(name: str, source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        f'source_url=https://example.com/{name}.tar.xz\n'
        f'source_filename={name}.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def _make_package(name: str, version: str) -> tuple[WrapPackage, bytes]:
    content = f'payload-{name}-{version}'.encode()
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text(name, version, _wrap_text(name, content_hash))
    return package, content


def _make_context(tmp_path: Path, repos: dict[str, RepositoryInterface]) -> Context:
    config = MagicMock()
    config.repositories = repos
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def _make_resolution_result(mapping: dict) -> ResolutionResult:
    return ResolutionResult(
        mapping=mapping,
        summary=ResolutionSummary(
            skipped_conditional=set(),
            skipped_optional=set(),
            included_optional=set(),
            unmapped_system=set(),
            skipped_conditional_by_pkg={},
            skipped_optional_by_pkg={},
        ),
    )


def test_transitive_add_resolves_transitive_deps(tmp_path: Path, monkeypatch) -> None:
    """pkg add installs the direct package plus its transitive deps."""
    _init_project(tmp_path)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    abseil_pkg, abseil_content = _make_package('abseil-cpp', '20240722.0')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])
    add_wrap_entry(packages, 'abseil-cpp', '20240722.0', ['absl_base', 'absl_strings'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False

    def get_package(repo_key):
        if 'grpc' in repo_key:
            return grpc_pkg
        if 'abseil-cpp' in repo_key:
            return abseil_pkg
        return None

    repo.get_package.side_effect = get_package

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20240722.0', 'repo1'),
    }

    def fake_urlopen(url, **kwargs):
        if 'grpc' in url:
            return _DummyResponse(grpc_content)
        if 'abseil' in url:
            return _DummyResponse(abseil_content)
        return _DummyResponse(b'')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'absl_base': 'abseil-cpp'},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'grpc.wrap').exists()
    assert (tmp_path / 'subprojects' / 'abseil-cpp.wrap').exists()

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep_names = [d.name for d in colliderfile.dependencies]
    assert 'grpc' in dep_names
    assert 'abseil-cpp' not in dep_names


def test_transitive_add_skips_system_deps(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When resolve returns only root, system deps are not installed."""
    _init_project(tmp_path)

    zlib_pkg, zlib_content = _make_package('zlib', '1.3.1')

    packages: dict = {}
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = zlib_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'zlib': Candidate('zlib', '1.3.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(zlib_content),
    )

    args = argparse.Namespace(
        package='zlib',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'zlib': 'zlib'},
            ),
        ):
            zlib_key = make_repo_key('zlib', '1.3.1', PackageType.WRAP)
            zlib_entry = RepoPackageEntry('zlib', '1.3.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {zlib_key: zlib_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'zlib.wrap').exists()


def test_transitive_add_with_exclude_flag(tmp_path: Path, monkeypatch) -> None:
    """--exclude prevents a transitive dep from being installed."""
    _init_project(tmp_path)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc'])
    add_wrap_entry(packages, 'abseil-cpp', '20240722.0', ['absl_base'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=['absl_base'],
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'absl_base': 'abseil-cpp'},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'grpc.wrap').exists()
    assert not (tmp_path / 'subprojects' / 'abseil-cpp.wrap').exists()


def test_transitive_add_persists_include_exclude_in_colliderfile(
    tmp_path: Path, monkeypatch
) -> None:
    """--include and --exclude overrides are saved in collider.json."""
    _init_project(tmp_path)

    zlib_pkg, zlib_content = _make_package('zlib', '1.3.1')

    packages: dict = {}
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = zlib_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'zlib': Candidate('zlib', '1.3.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(zlib_content),
    )

    args = argparse.Namespace(
        package='zlib',
        offline=False,
        version=None,
        include=['libbaz'],
        exclude=['protobuf'],
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'zlib': 'zlib'},
            ),
        ):
            zlib_key = make_repo_key('zlib', '1.3.1', PackageType.WRAP)
            zlib_entry = RepoPackageEntry('zlib', '1.3.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {zlib_key: zlib_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep = next(d for d in colliderfile.dependencies if d.name == 'zlib')
    assert dep.include == ['libbaz']
    assert dep.exclude == ['protobuf']


def test_transitive_add_fails_on_transitive_install_failure(tmp_path: Path, monkeypatch) -> None:
    """EX_IOERR is returned when a required transitive dep fails to install."""
    _init_project(tmp_path)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])
    add_wrap_entry(packages, 'abseil-cpp', '20240722.0', ['absl_base', 'absl_strings'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20240722.0', 'nonexistent_repo'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'absl_base': 'abseil-cpp'},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_transitive_add_forwards_version_spec(tmp_path: Path, monkeypatch) -> None:
    """resolve_dependencies receives the version constraint from the CLI."""
    _init_project(tmp_path)

    zlib_pkg, zlib_content = _make_package('zlib', '1.3.1')

    packages: dict = {}
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = zlib_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'zlib': Candidate('zlib', '1.3.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(zlib_content),
    )

    from packaging.specifiers import SpecifierSet

    args = argparse.Namespace(
        package='zlib',
        offline=False,
        version=SpecifierSet('>=1.3'),
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ) as mock_resolve,
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'zlib': 'zlib'},
            ),
        ):
            zlib_key = make_repo_key('zlib', '1.3.1', PackageType.WRAP)
            zlib_entry = RepoPackageEntry('zlib', '1.3.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {zlib_key: zlib_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
        _, kwargs = mock_resolve.call_args
        assert kwargs['root_version_spec'] == '>=1.3'
    finally:
        os.chdir(cwd)


def test_transitive_add_rolls_back_on_transitive_failure(tmp_path: Path, monkeypatch) -> None:
    """Direct package wrap and cache are removed when transitive resolution fails."""
    _init_project(tmp_path)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])
    add_wrap_entry(packages, 'abseil-cpp', '20240722.0', ['absl_base'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20240722.0', 'nonexistent_repo'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'absl_base': 'abseil-cpp'},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_IOERR
        assert not (tmp_path / 'subprojects' / 'grpc.wrap').exists()
        assert not (tmp_path / 'subprojects' / 'packagecache' / 'grpc.tar.xz').exists()
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert len(colliderfile.dependencies) == 0


def test_add_already_installed_errors(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """pkg add exits with EX_DATAERR when the package wrap already exists."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    repo.get_package.assert_not_called()
    assert 'already installed' in caplog.text
    assert '--force' in caplog.text


def test_add_force_reinstalls(tmp_path: Path, monkeypatch) -> None:
    """pkg add --force removes old artifacts and reinstalls the package."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\nold=yes\n')

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=True,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    wrap_content = (subprojects / 'grpc.wrap').read_text()
    assert 'old=yes' not in wrap_content


def test_add_not_installed_proceeds_normally(tmp_path: Path, monkeypatch) -> None:
    """pkg add proceeds without error when the package is not yet installed."""
    _init_project(tmp_path)

    zlib_pkg, zlib_content = _make_package('zlib', '1.3.1')

    packages: dict = {}
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = zlib_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'zlib': Candidate('zlib', '1.3.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(zlib_content),
    )

    args = argparse.Namespace(
        package='zlib',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            zlib_key = make_repo_key('zlib', '1.3.1', PackageType.WRAP)
            zlib_entry = RepoPackageEntry('zlib', '1.3.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {zlib_key: zlib_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'zlib.wrap').exists()


def test_add_force_removes_subproject_directory(tmp_path: Path, monkeypatch) -> None:
    """--force cleans up both wrap file and subproject directory before reinstall."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\nold=yes\n')
    subproject_dir = subprojects / 'grpc'
    subproject_dir.mkdir()
    (subproject_dir / 'meson.build').write_text('project("grpc")\n')

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=True,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    wrap_content = (subprojects / 'grpc.wrap').read_text()
    assert 'old=yes' not in wrap_content


def test_add_force_when_not_installed(tmp_path: Path, monkeypatch) -> None:
    """--force on a fresh project is a no-op cleanup and installs normally."""
    _init_project(tmp_path)

    zlib_pkg, zlib_content = _make_package('zlib', '1.3.1')

    packages: dict = {}
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = zlib_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'zlib': Candidate('zlib', '1.3.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(zlib_content),
    )

    args = argparse.Namespace(
        package='zlib',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=True,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            zlib_key = make_repo_key('zlib', '1.3.1', PackageType.WRAP)
            zlib_entry = RepoPackageEntry('zlib', '1.3.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {zlib_key: zlib_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'zlib.wrap').exists()

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep_names = [d.name for d in colliderfile.dependencies]
    assert 'zlib' in dep_names


def test_add_force_updates_colliderfile(tmp_path: Path, monkeypatch) -> None:
    """--force reinstall updates the version constraint in collider.json."""
    existing_dep = Dependency('grpc', DependencySource.COLLIDER, version='>=1.50')
    _init_project(tmp_path, dependencies=[existing_dep])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\nold=yes\n')

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')

    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc', 'grpc++'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    from packaging.specifiers import SpecifierSet

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=SpecifierSet('>=1.59'),
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=True,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}

            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep = next(d for d in colliderfile.dependencies if d.name == 'grpc')
    assert dep.version == '>=1.59'


def test_add_persists_include_conditional_flag(tmp_path: Path, monkeypatch) -> None:
    """pkg add --include-conditional persists the flag in collider.json."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {'grpc': Candidate('grpc', '1.59.1', 'repo1')}

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=True,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}
            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep = next(d for d in colliderfile.dependencies if d.name == 'grpc')
    assert dep.include_conditional is True
    assert dep.exclude_optional is None


def test_add_persists_exclude_optional_flag(tmp_path: Path, monkeypatch) -> None:
    """pkg add --exclude-optional persists the flag in collider.json."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    packages: dict = {}
    add_wrap_entry(packages, 'grpc', '1.59.1', ['grpc'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg

    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {'grpc': Candidate('grpc', '1.59.1', 'repo1')}

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(grpc_content),
    )

    args = argparse.Namespace(
        package='grpc',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=True,
        force=False,
    )
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}
            result = cmd.execute()

        assert result == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep = next(d for d in colliderfile.dependencies if d.name == 'grpc')
    assert dep.exclude_optional is True
    assert dep.include_conditional is None
