# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the install subcommand."""

import argparse
import hashlib
import os
import urllib.request

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packaging.specifiers import SpecifierSet

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile, compute_wrap_hash
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Add import Add
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


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


def _wrap_text(source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/shared.tar.xz\n'
        'source_filename=shared.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def _make_context(tmp_path: Path, repo: RepositoryInterface) -> Context:
    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_install_wrap_success(tmp_path: Path, monkeypatch) -> None:
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    wrap_path = tmp_path / 'subprojects' / 'shared.wrap'
    assert wrap_path.exists()
    cached_archive = tmp_path / 'subprojects' / 'packagecache' / 'shared.tar.xz'
    assert cached_archive.exists()


def test_install_offline_uses_cache(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = True
    repo.get_package.side_effect = AssertionError('Should not hit network in offline mode')

    context = _make_context(tmp_path, repo)
    context.cache.store_wrap(package)

    cache_archive = context.cache.archives_dir / f'{content_hash}-shared.tar.xz'
    cache_archive.parent.mkdir(parents=True, exist_ok=True)
    cache_archive.write_bytes(content)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    args = argparse.Namespace(package='shared', offline=True)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Using cached wrap (offline).' in caplog.text

    wrap_path = tmp_path / 'subprojects' / 'shared.wrap'
    assert wrap_path.exists()


def test_install_offline_remote_missing_cache(tmp_path: Path) -> None:
    _init_project(tmp_path)

    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = True
    repo.get_package.side_effect = AssertionError('Should not hit network in offline mode')

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    args = argparse.Namespace(package='shared', offline=True)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_install_offline_local_missing_archive(tmp_path: Path) -> None:
    _init_project(tmp_path)

    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text('deadbeef'))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    args = argparse.Namespace(package='shared', offline=True)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_install_preserves_declared_version(tmp_path: Path, monkeypatch) -> None:
    """Existing collider.json version stays untouched."""
    existing_dep = Dependency('shared', DependencySource.COLLIDER, '>=1.0')
    _init_project(tmp_path, dependencies=[existing_dep])

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '2.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert len(colliderfile.dependencies) == 1
    assert colliderfile.dependencies[0].version == '>=1.0'


def test_install_honors_declared_version_constraint(tmp_path: Path, monkeypatch) -> None:
    """Existing collider.json constraint filters candidate versions."""
    existing_dep = Dependency('shared', DependencySource.COLLIDER, '<2.0.0')
    _init_project(tmp_path, dependencies=[existing_dep])

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.5.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.5.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.5.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Add.search_packages', return_value=all_matches
        ) as search:
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert search.call_args.args[2].contains('1.5.0')
    assert not search.call_args.args[2].contains('2.0.0')

    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_install_with_version_flag_persists_constraint(tmp_path: Path, monkeypatch) -> None:
    """pkg add --version records the requested constraint in collider.json."""
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.5.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.5.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.5.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', version=SpecifierSet('>=1.0,<2.0'), offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Add.search_packages', return_value=all_matches
        ) as search:
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert search.call_args.args[2].contains('1.5.0')
    assert not search.call_args.args[2].contains('2.0.0')

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies[0].version == '<2.0,>=1.0'


def test_install_fails_on_invalid_declared_version_constraint(tmp_path: Path) -> None:
    """Invalid collider.json specifiers fail before resolution."""
    existing_dep = Dependency('shared', DependencySource.COLLIDER, 'not-a-specifier')
    _init_project(tmp_path, dependencies=[existing_dep])

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_install_fails_on_existing_different_wrap(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(parents=True, exist_ok=True)
    (subprojects / 'shared.wrap').write_text('different', encoding='utf-8')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert 'already installed' in caplog.text


def test_install_does_not_create_lockfile(tmp_path: Path, monkeypatch) -> None:
    """pkg add leaves lockfile creation to `collider lock`."""
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_install_does_not_update_existing_lockfile(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """pkg add warns when an existing lockfile becomes stale."""
    _init_project(tmp_path)

    # Pre-populate a lockfile with an existing entry.
    from collider.file_model.lockfile import LockedPackage

    existing_hash = compute_wrap_hash('existing wrap')
    lockfile = Lockfile(
        dependencies={
            'other': LockedPackage(version='1.0', wrap_hash=existing_hash),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert len(lockfile.dependencies) == 1
    assert 'other' in lockfile.dependencies
    assert 'shared' not in lockfile.all_packages
    assert 'run "collider lock" to refresh it' in caplog.text


def test_install_does_not_warn_when_lockfile_already_matches(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No stale warning is emitted when collider.lock already matches the install."""
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    from collider.file_model.lockfile import LockedPackage

    lockfile = Lockfile(
        dependencies={
            'shared': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'run "collider lock" to refresh it' not in caplog.text


def test_install_adds_dependency_without_version(tmp_path: Path, monkeypatch) -> None:
    """New dependency in collider.json has no version pinned."""
    _init_project(tmp_path)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(package='shared', offline=False)
    cmd = Add(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Add.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert len(colliderfile.dependencies) == 1
    assert colliderfile.dependencies[0].name == 'shared'
    assert colliderfile.dependencies[0].version is None
