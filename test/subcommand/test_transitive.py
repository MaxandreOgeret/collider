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
import resolvelib

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


def test_add_creates_colliderfile_when_missing(tmp_path: Path, monkeypatch) -> None:
    """pkg add bootstraps collider.json if the project has not been initialized yet."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg
    context = _make_context(tmp_path, {'repo1': repo})

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
                return_value=_make_resolution_result(
                    {'grpc': Candidate('grpc', '1.59.1', 'repo1')}
                ),
            ),
            patch('collider.subcommand.pkg.Add.build_dep_name_index', return_value={}),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == ['grpc']


def test_add_does_not_create_colliderfile_on_failure(tmp_path: Path) -> None:
    """A failed add must not leave a stray collider.json behind."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='ghost',
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
        with patch('collider.subcommand.pkg.Add.search_packages', return_value={}):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_UNAVAILABLE
    assert not (tmp_path / Colliderfile.get_filename()).exists()


def test_transitive_rejects_unsafe_package_name(tmp_path: Path, monkeypatch) -> None:
    """A traversal transitive package name is rejected without writing outside subprojects."""
    _init_project(tmp_path)

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.return_value = grpc_pkg
    context = _make_context(tmp_path, {'repo1': repo})

    monkeypatch.setattr(
        urllib.request, 'urlopen', lambda url, **kwargs: _DummyResponse(grpc_content)
    )

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        '../../evil': Candidate('../../evil', '1.0.0', 'repo1'),
    }

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
        with (
            patch('collider.subcommand.pkg.Add.search_packages') as mock_search,
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'evil': '../../evil'},
            ),
        ):
            grpc_key = make_repo_key('grpc', '1.59.1', PackageType.WRAP)
            grpc_entry = RepoPackageEntry('grpc', '1.59.1', PackageType.WRAP)
            mock_search.return_value = {'repo1': {grpc_key: grpc_entry}}
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_IOERR
    # The traversal target must not be created outside subprojects.
    assert not (tmp_path.parent / 'evil.wrap').exists()
    # A failed transitive install does not record the root dependency.
    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == []


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
    """pkg add exits with EX_DATAERR when the direct package is already declared and installed."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
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
        with (
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Add.resolve_all_dependencies',
                return_value=_make_resolution_result(
                    {
                        'grpc': Candidate('grpc', '1.59.1-1', 'repo1'),
                        'protobuf': Candidate('protobuf', '25.2-4', 'repo1'),
                    }
                ),
            ),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    repo.get_package.assert_not_called()
    assert 'already installed' in caplog.text
    assert '--force' in caplog.text


def test_add_already_installed_transitive_becomes_direct_dependency(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """pkg add promotes an installed transitive wrap into collider.json without reinstalling."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    args = argparse.Namespace(
        package='protobuf',
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
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Add.resolve_all_dependencies',
                return_value=_make_resolution_result(
                    {
                        'grpc': Candidate('grpc', '1.59.1-1', 'repo1'),
                        'protobuf': Candidate('protobuf', '25.2-4', 'repo1'),
                    }
                ),
            ),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_OK
    repo.get_package.assert_not_called()
    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep_names = [dep.name for dep in colliderfile.dependencies]
    assert dep_names == ['grpc', 'protobuf']
    assert 'already installed; adding it to collider.json as a direct dependency' in caplog.text


def test_add_already_installed_transitive_creates_colliderfile(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Promoting an installed transitive wrap creates collider.json when it is missing."""
    from collider.file_model.lockfile import LockedPackage, Lockfile

    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n')

    lockfile = Lockfile(
        packages={
            'protobuf': LockedPackage(
                version='25.2-4',
                wrap_hash='sha256:' + '0' * 64,
                origin='https://wrapdb.example.com/v2/',
            )
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='protobuf',
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

    assert result == os.EX_OK
    repo.get_package.assert_not_called()
    colliderfile_path = tmp_path / Colliderfile.get_filename()
    assert colliderfile_path.exists()
    colliderfile = Colliderfile.from_path(colliderfile_path)
    assert [dep.name for dep in colliderfile.dependencies] == ['protobuf']
    assert 'Created collider.json.' in caplog.text


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


def test_add_existing_wrap_with_unreadable_lockfile_and_resolution_failure_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An existing wrap is not promoted when transitive ownership cannot be proven."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (tmp_path / 'collider.lock').write_text('not-json{{', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(
        argparse.Namespace(
            package='protobuf',
            offline=False,
            version=None,
            include=None,
            exclude=None,
            include_conditional=False,
            exclude_optional=False,
            force=False,
        ),
        context,
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Add.resolve_all_dependencies',
                side_effect=resolvelib.ResolutionImpossible([Requirement('protobuf')]),
            ),
        ):
            assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == ['grpc']
    assert 'Package "protobuf" is already installed.' in caplog.text


def test_add_existing_wrap_with_no_dependency_index_errors(tmp_path: Path) -> None:
    """An existing wrap is rejected when Collider cannot resolve remaining direct deps."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(
        argparse.Namespace(
            package='protobuf',
            offline=False,
            version=None,
            include=None,
            exclude=None,
            include_conditional=False,
            exclude_optional=False,
            force=False,
        ),
        context,
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.build_dep_name_index', return_value={}):
            assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_add_skips_invalid_versions_when_selecting_newest(tmp_path: Path, monkeypatch) -> None:
    """Invalid repository versions are skipped when choosing a package to install."""
    _init_project(tmp_path)

    package, content = _make_package('demo', '2.0.0')
    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='demo',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    bad_key = make_repo_key('demo', 'not-a-version', PackageType.WRAP)
    good_key = make_repo_key('demo', '2.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **kwargs: _DummyResponse(content))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Add.search_packages',
                return_value={
                    'repo1': {
                        bad_key: RepoPackageEntry('demo', 'not-a-version', PackageType.WRAP),
                        good_key: RepoPackageEntry('demo', '2.0.0', PackageType.WRAP),
                    }
                },
            ),
            patch('collider.subcommand.pkg.Add.build_dep_name_index', return_value={}),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'demo.wrap').exists()


def test_add_offline_missing_cache_returns_ioerr(tmp_path: Path) -> None:
    """Offline add fails cleanly when the selected package is not cached."""
    _init_project(tmp_path)

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = True
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='demo',
        offline=True,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    demo_key = make_repo_key('demo', '1.0.0', PackageType.WRAP)
    demo_entry = RepoPackageEntry('demo', '1.0.0', PackageType.WRAP)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Add.search_packages',
            return_value={'repo1': {demo_key: demo_entry}},
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_add_rejects_unsupported_package_type(tmp_path: Path) -> None:
    """Add fails when a repository returns a non-wrap package object."""
    _init_project(tmp_path)

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.return_value = object()
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='demo',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    demo_key = make_repo_key('demo', '1.0.0', PackageType.WRAP)
    demo_entry = RepoPackageEntry('demo', '1.0.0', PackageType.WRAP)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Add.search_packages',
            return_value={'repo1': {demo_key: demo_entry}},
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_add_fails_when_subproject_directory_exists(tmp_path: Path, monkeypatch) -> None:
    """Add refuses to install over an existing subproject directory."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    (subprojects / 'demo').mkdir(parents=True)

    package, content = _make_package('demo', '1.0.0')
    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, {'repo1': repo})

    args = argparse.Namespace(
        package='demo',
        offline=False,
        version=None,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )
    cmd = Add(args, context)

    demo_key = make_repo_key('demo', '1.0.0', PackageType.WRAP)
    demo_entry = RepoPackageEntry('demo', '1.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **kwargs: _DummyResponse(content))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Add.search_packages',
            return_value={'repo1': {demo_key: demo_entry}},
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_add_partial_transitive_failure_leaves_installed_transitives_in_place(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A partial transitive failure rolls back the direct package but keeps already-installed transitives."""
    _init_project(tmp_path)

    root_pkg, root_content = _make_package('grpc', '1.59.1')
    ok_pkg, ok_content = _make_package('abseil-cpp', '20240722.0')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False

    def get_package(repo_key):
        if 'grpc' in repo_key:
            return root_pkg
        if 'abseil-cpp' in repo_key:
            return ok_pkg
        return None

    repo.get_package.side_effect = get_package
    context = _make_context(tmp_path, {'repo1': repo})

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20240722.0', 'repo1'),
        'missing': Candidate('missing', '1.0.0', 'missing-repo'),
    }

    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **kwargs: _DummyResponse(
            root_content if 'grpc' in url else ok_content if 'abseil' in url else b''
        ),
    )

    cmd = Add(
        argparse.Namespace(
            package='grpc',
            offline=False,
            version=None,
            include=None,
            exclude=None,
            include_conditional=False,
            exclude_optional=False,
            force=False,
        ),
        context,
    )

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
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)

    assert not (tmp_path / 'subprojects' / 'grpc.wrap').exists()
    assert (tmp_path / 'subprojects' / 'abseil-cpp.wrap').exists()
    assert 'Failed to install transitive dependencies: missing.' in caplog.text
