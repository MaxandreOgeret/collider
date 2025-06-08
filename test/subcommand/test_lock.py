# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the explicit lockfile command."""

import argparse
import hashlib
import os
import urllib.request

from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry, add_wrap_entry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.Lock import Lock
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


def _wrap_text(source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/pkg.tar.xz\n'
        'source_filename=pkg.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repo: RepositoryInterface) -> Context:
    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_lock_creates_lockfile(tmp_path: Path, monkeypatch) -> None:
    """collider lock resolves dependencies and writes collider.lock."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

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

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies['shared'].version == '1.0.0'
    assert lockfile.dependencies['shared'].wrap_hash == compute_wrap_hash(package.wrap_text)
    assert not (tmp_path / 'subprojects' / 'shared.wrap').exists()


def test_lock_rewrites_existing_lockfile(tmp_path: Path, monkeypatch) -> None:
    """collider lock rewrites collider.lock from collider.json intent."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    existing = Lockfile(
        dependencies={
            'other': LockedPackage(
                version='0.1.0',
                wrap_hash='sha256:' + '0' * 64,
            )
        }
    )
    existing.save(tmp_path / Lockfile.get_filename())

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

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert set(lockfile.dependencies) == {'shared'}


def test_lock_honors_declared_version_constraint(tmp_path: Path, monkeypatch) -> None:
    """collider lock enforces version constraints from collider.json."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, '<2.0.0')],
    )

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

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.Install.search_packages', return_value=all_matches
        ) as search:
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert search.call_args.args[2].contains('1.5.0')
    assert not search.call_args.args[2].contains('2.0.0')


def test_lock_invalid_declared_version_constraint_fails(tmp_path: Path) -> None:
    """Invalid collider.json specifiers are rejected by collider lock."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, 'not-a-specifier')],
    )

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_lock_writes_empty_lockfile_when_no_dependencies(tmp_path: Path) -> None:
    """collider lock should still write an empty lockfile for projects with no deps."""
    _init_project(tmp_path, dependencies=[])

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies == {}
    assert lockfile.packages == {}


def test_lock_offline_uses_cached_wraps(tmp_path: Path) -> None:
    """Offline lock generation should resolve metadata via cache without network fetches."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.side_effect = AssertionError('offline lock should use cache')
    repo.requires_network.return_value = True

    context = _make_context(tmp_path, repo)
    context.cache.store_wrap(package)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    args = argparse.Namespace(offline=True)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies['shared'].wrap_hash == compute_wrap_hash(package.wrap_text)


# -- Cross-root conflict detection -------------------------------------------


def test_lock_cross_root_conflict_detected(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    """Lock detects incompatible transitive deps across roots and fails."""
    from collider.utils.packaging.resolver import Requirement

    _init_project(
        tmp_path,
        dependencies=[
            Dependency('libfoo', DependencySource.COLLIDER, None),
            Dependency('libbar', DependencySource.COLLIDER, None),
        ],
    )

    packages: dict = {}
    add_wrap_entry(packages, 'libfoo', '1.0.0', ['libfoo'])
    add_wrap_entry(packages, 'libbar', '1.0.0', ['libbar'])
    add_wrap_entry(packages, 'zlib', '1.2.0', ['zlib'])
    add_wrap_entry(packages, 'zlib', '1.3.1', ['zlib'])

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()

    def get_package(repo_key):
        for name in ('libfoo', 'libbar', 'zlib'):
            if name in repo_key:
                return WrapPackage.from_wrap_text(name, '1.0.0', _wrap_text(content_hash))
        return None

    repo.get_package.side_effect = get_package

    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    def mock_get_deps(self_prov, candidate):
        if candidate.name == 'libfoo':
            return [Requirement('zlib', '>=1.3')]
        if candidate.name == 'libbar':
            return [Requirement('zlib', '<1.3')]
        return []

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    import logging

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.CRITICAL),
            patch(
                'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
                mock_get_deps,
            ),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_UNAVAILABLE
    assert any('resolution failed' in m.lower() for m in caplog.messages)
