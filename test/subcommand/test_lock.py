# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the explicit lockfile command."""

import argparse
import hashlib
import logging
import os
import urllib.error
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


ORIGIN = 'https://wrapdb.example.com/v2'


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


def _pre_cache_archive(cache: WrapCache, content: bytes, filename: str) -> None:
    """Store an archive in the cache so verify_archives finds it."""
    content_hash = hashlib.sha256(content).hexdigest()
    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (cache.archives_dir / f'{content_hash}-{filename}').write_bytes(content)


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
    repo.origin_url = ORIGIN

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
    assert lockfile.dependencies['shared'].origin == ORIGIN
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
                origin=ORIGIN,
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
    repo.origin_url = ORIGIN

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
    repo.origin_url = ORIGIN

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
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)
    context.cache.store_wrap(package)
    _pre_cache_archive(context.cache, content, 'pkg.tar.xz')

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
    assert lockfile.dependencies['shared'].origin == ORIGIN


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
    repo.origin_url = ORIGIN

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


# -- Archive verification happy paths -----------------------------------------


def test_lock_verifies_source_archive(tmp_path: Path, monkeypatch) -> None:
    """Lock succeeds when the source archive hash matches wrap metadata."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'source archive payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

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
    assert lockfile.dependencies['shared'].origin == ORIGIN


def test_lock_verifies_cached_archive(tmp_path: Path, monkeypatch) -> None:
    """Lock succeeds when archives are already cached with correct hashes."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'cached payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)
    _pre_cache_archive(context.cache, content, 'pkg.tar.xz')

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    def urlopen_should_not_download(url, **_kwargs):
        if 'pkg.tar.xz' in str(url):
            raise AssertionError('Should not download pre-cached archive')
        return _DummyResponse(content)

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen_should_not_download)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)


# -- Archive verification non-happy paths ------------------------------------


def test_lock_fails_on_source_archive_hash_mismatch(tmp_path: Path, monkeypatch, caplog) -> None:
    """Lock fails with EX_DATAERR when source archive hash does not match."""
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
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    tampered = b'tampered content'
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(tampered))

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.CRITICAL),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert any('archive verification failed' in m.lower() for m in caplog.messages)
    assert any(content_hash in m for m in caplog.messages)
    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_lock_fails_on_patch_archive_hash_mismatch(tmp_path: Path, monkeypatch, caplog) -> None:
    """Lock fails when the patch archive hash does not match wrap metadata."""
    patch_hash = hashlib.sha256(b'patch').hexdigest()
    source_content = b'source'
    source_hash = hashlib.sha256(source_content).hexdigest()

    wrap_text = (
        '[wrap-file]\n'
        f'source_url=https://example.com/pkg.tar.xz\n'
        f'source_filename=pkg.tar.xz\n'
        f'source_hash={source_hash}\n'
        f'patch_url=https://example.com/pkg-patch.tar.xz\n'
        f'patch_filename=pkg-patch.tar.xz\n'
        f'patch_hash={patch_hash}\n'
    )

    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    package = WrapPackage.from_wrap_text('shared', '1.0.0', wrap_text)

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    tampered_patch = b'bad patch'

    def mock_urlopen(url, **_kwargs):
        if 'patch' in str(url):
            return _DummyResponse(tampered_patch)
        return _DummyResponse(source_content)

    monkeypatch.setattr(urllib.request, 'urlopen', mock_urlopen)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.CRITICAL),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert any('archive verification failed' in m.lower() for m in caplog.messages)
    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_lock_fails_offline_when_archive_not_cached(tmp_path: Path, caplog) -> None:
    """Lock fails when offline and archive is not in the local cache."""
    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.side_effect = AssertionError('offline should use cache')
    repo.requires_network.return_value = True
    repo.origin_url = ORIGIN

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
        with (
            caplog.at_level(logging.CRITICAL),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert any('archive verification failed' in m.lower() for m in caplog.messages)


def test_lock_fails_on_corrupt_cached_archive(tmp_path: Path, monkeypatch, caplog) -> None:
    """Lock fails when the cached archive has a wrong hash (corruption)."""
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
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    # Write corrupt archive into cache.
    context.cache.archives_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = context.cache.archives_dir / f'{content_hash}-pkg.tar.xz'
    corrupt_path.write_bytes(b'corrupt data')

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    # Re-download after corrupt cache eviction also returns bad data.
    monkeypatch.setattr(
        urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(b'still wrong')
    )

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.WARNING),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR


# -- Corrupt cache recovery --------------------------------------------------


def test_lock_corrupt_cache_redownloads_and_succeeds(tmp_path: Path, monkeypatch, caplog) -> None:
    """Corrupt cached archive is evicted, re-downloaded with correct hash, lock succeeds."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'correct payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    context.cache.archives_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = context.cache.archives_dir / f'{content_hash}-pkg.tar.xz'
    corrupt_path.write_bytes(b'corrupt data')

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.WARNING),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_OK
    assert any('corrupt' in m.lower() for m in caplog.messages)
    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies['shared'].origin == ORIGIN


def test_lock_corrupt_cache_network_unavailable_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    """Corrupt cache + network failure: lock must fail, no lockfile written."""
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
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    context.cache.archives_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = context.cache.archives_dir / f'{content_hash}-pkg.tar.xz'
    corrupt_path.write_bytes(b'corrupt data')

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    def mock_urlopen(url, **_kwargs):
        raise urllib.error.URLError('simulated network failure')

    monkeypatch.setattr(urllib.request, 'urlopen', mock_urlopen)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.WARNING),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_lock_archive_unreachable_not_cached_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    """Archive host unreachable, nothing in cache: lock must fail."""
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
    repo.origin_url = ORIGIN

    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    def mock_urlopen(url, **_kwargs):
        raise urllib.error.URLError('simulated unreachable')

    monkeypatch.setattr(urllib.request, 'urlopen', mock_urlopen)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.CRITICAL),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert not (tmp_path / Lockfile.get_filename()).exists()


# -- Atomicity ---------------------------------------------------------------


def _wrap_text_named(source_hash: str, filename: str) -> str:
    return (
        '[wrap-file]\n'
        f'source_url=https://example.com/{filename}\n'
        f'source_filename={filename}\n'
        f'source_hash={source_hash}\n'
    )


def test_lock_atomicity_second_package_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    """First package passes archive verification, second fails: no lockfile written."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('pkg_a', DependencySource.COLLIDER, None),
            Dependency('pkg_b', DependencySource.COLLIDER, None),
        ],
    )

    content_a = b'content a'
    hash_a = hashlib.sha256(content_a).hexdigest()
    package_a = WrapPackage.from_wrap_text('pkg_a', '1.0.0', _wrap_text_named(hash_a, 'a.tar.xz'))

    content_b = b'content b'
    hash_b = hashlib.sha256(content_b).hexdigest()
    package_b = WrapPackage.from_wrap_text('pkg_b', '1.0.0', _wrap_text_named(hash_b, 'b.tar.xz'))

    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

    def get_package(repo_key):
        if 'pkg_a' in repo_key:
            return package_a
        if 'pkg_b' in repo_key:
            return package_b
        return None

    repo.get_package.side_effect = get_package

    key_a = make_repo_key('pkg_a', '1.0.0', PackageType.WRAP)
    entry_a = RepoPackageEntry('pkg_a', '1.0.0', PackageType.WRAP)
    key_b = make_repo_key('pkg_b', '1.0.0', PackageType.WRAP)
    entry_b = RepoPackageEntry('pkg_b', '1.0.0', PackageType.WRAP)

    def mock_search(repos, pattern, version_spec=None):
        if pattern.match('pkg_a'):
            return {'repo1': {key_a: entry_a}}
        if pattern.match('pkg_b'):
            return {'repo1': {key_b: entry_b}}
        return {}

    def mock_urlopen(url, **_kwargs):
        if 'a.tar.xz' in str(url):
            return _DummyResponse(content_a)
        return _DummyResponse(b'tampered')

    monkeypatch.setattr(urllib.request, 'urlopen', mock_urlopen)

    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            caplog.at_level(logging.CRITICAL),
            patch('collider.subcommand.Install.search_packages', side_effect=mock_search),
        ):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_DATAERR
    assert not (tmp_path / Lockfile.get_filename()).exists()


# -- Multi-repo origin -------------------------------------------------------


def test_lock_origin_per_package_matches_source_repo(tmp_path: Path, monkeypatch, caplog) -> None:
    """Different packages from different repos: each origin matches its source repo."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('pkg_a', DependencySource.COLLIDER, None),
            Dependency('pkg_b', DependencySource.COLLIDER, None),
        ],
    )

    content_a = b'content a'
    hash_a = hashlib.sha256(content_a).hexdigest()
    package_a = WrapPackage.from_wrap_text('pkg_a', '1.0.0', _wrap_text_named(hash_a, 'a.tar.xz'))

    content_b = b'content b'
    hash_b = hashlib.sha256(content_b).hexdigest()
    package_b = WrapPackage.from_wrap_text('pkg_b', '1.0.0', _wrap_text_named(hash_b, 'b.tar.xz'))

    origin_a = 'https://a.example.com/v2'
    origin_b = 'https://b.example.com/v2'

    repo_a = MagicMock(spec=RepositoryInterface)
    repo_a.requires_network.return_value = False
    repo_a.origin_url = origin_a
    repo_a.get_package.return_value = package_a

    repo_b = MagicMock(spec=RepositoryInterface)
    repo_b.requires_network.return_value = False
    repo_b.origin_url = origin_b
    repo_b.get_package.return_value = package_b

    key_a = make_repo_key('pkg_a', '1.0.0', PackageType.WRAP)
    entry_a = RepoPackageEntry('pkg_a', '1.0.0', PackageType.WRAP)
    key_b = make_repo_key('pkg_b', '1.0.0', PackageType.WRAP)
    entry_b = RepoPackageEntry('pkg_b', '1.0.0', PackageType.WRAP)

    def mock_search(repos, pattern, version_spec=None):
        if pattern.match('pkg_a'):
            return {'repo_a': {key_a: entry_a}}
        if pattern.match('pkg_b'):
            return {'repo_b': {key_b: entry_b}}
        return {}

    def mock_urlopen(url, **_kwargs):
        if 'a.tar.xz' in str(url):
            return _DummyResponse(content_a)
        return _DummyResponse(content_b)

    monkeypatch.setattr(urllib.request, 'urlopen', mock_urlopen)

    config = MagicMock()
    config.repositories = {'repo_a': repo_a, 'repo_b': repo_b}
    config.offline = False
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)

    args = argparse.Namespace(offline=False)
    cmd = Lock(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', side_effect=mock_search):
            result = cmd.execute()
    finally:
        os.chdir(cwd)

    assert result == os.EX_OK
    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies['pkg_a'].origin == origin_a
    assert lockfile.dependencies['pkg_b'].origin == origin_b


# -- Package migration -------------------------------------------------------


def test_lock_package_migration_changes_origin(tmp_path: Path, monkeypatch) -> None:
    """Package migrates between repos across two lock runs: origin changes, wrap_hash unchanged."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)

    origin_old = 'https://old.example.com/v2'
    origin_new = 'https://new.example.com/v2'

    # First lock: repo_old provides the package.
    repo_old = MagicMock(spec=RepositoryInterface)
    repo_old.get_package.return_value = package
    repo_old.requires_network.return_value = False
    repo_old.origin_url = origin_old

    config_old = MagicMock()
    config_old.repositories = {'repo_old': repo_old}
    config_old.offline = False
    context_old = Context(config=config_old, cache=WrapCache(tmp_path / 'cache'), offline=False)

    all_matches_old = {'repo_old': {repo_key: entry}}

    args = argparse.Namespace(offline=False)
    cmd_old = Lock(args, context_old)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches_old):
            assert cmd_old.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile_1 = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile_1.dependencies['shared'].origin == origin_old
    wrap_hash_1 = lockfile_1.dependencies['shared'].wrap_hash

    # Second lock: repo_new provides the same package with same content.
    repo_new = MagicMock(spec=RepositoryInterface)
    repo_new.get_package.return_value = package
    repo_new.requires_network.return_value = False
    repo_new.origin_url = origin_new

    config_new = MagicMock()
    config_new.repositories = {'repo_new': repo_new}
    config_new.offline = False
    context_new = Context(config=config_new, cache=WrapCache(tmp_path / 'cache'), offline=False)

    all_matches_new = {'repo_new': {repo_key: entry}}

    cmd_new = Lock(args, context_new)

    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches_new):
            assert cmd_new.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile_2 = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile_2.dependencies['shared'].origin == origin_new
    wrap_hash_2 = lockfile_2.dependencies['shared'].wrap_hash

    assert wrap_hash_1 == wrap_hash_2


# -- Priority winner ----------------------------------------------------------


def test_lock_two_repos_same_package_origin_reflects_winner(tmp_path: Path, monkeypatch) -> None:
    """Two repos have the same package; higher version wins, origin matches that repo."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package_high = WrapPackage.from_wrap_text('shared', '1.0.1', _wrap_text(content_hash))
    package_low = WrapPackage.from_wrap_text('shared', '1.0.0', _wrap_text(content_hash))

    origin_a = 'https://a.example.com/v2'
    origin_b = 'https://b.example.com/v2'

    repo_a = MagicMock(spec=RepositoryInterface)
    repo_a.requires_network.return_value = False
    repo_a.origin_url = origin_a
    repo_a.get_package.return_value = package_high

    repo_b = MagicMock(spec=RepositoryInterface)
    repo_b.requires_network.return_value = False
    repo_b.origin_url = origin_b
    repo_b.get_package.return_value = package_low

    key_high = make_repo_key('shared', '1.0.1', PackageType.WRAP)
    entry_high = RepoPackageEntry('shared', '1.0.1', PackageType.WRAP)
    key_low = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry_low = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {
        'repo_a': {key_high: entry_high},
        'repo_b': {key_low: entry_low},
    }

    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    config = MagicMock()
    config.repositories = {'repo_a': repo_a, 'repo_b': repo_b}
    config.offline = False
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)

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
    assert lockfile.dependencies['shared'].version == '1.0.1'
    assert lockfile.dependencies['shared'].origin == origin_a


# -- Wrap hash chain integrity ------------------------------------------------


def test_lock_wrap_hash_integrity_preserved_with_origin(tmp_path: Path, monkeypatch) -> None:
    """wrap_hash is computed from wrap content, not influenced by origin."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    original_wrap = _wrap_text(content_hash)
    package = WrapPackage.from_wrap_text('shared', '1.0.0', original_wrap)

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

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
    wrap_hash_1 = lockfile.dependencies['shared'].wrap_hash
    assert wrap_hash_1 == compute_wrap_hash(original_wrap)

    mutated_wrap = original_wrap.replace(
        'source_url=https://example.com/pkg.tar.xz',
        'source_url=https://evil.example.com/pkg.tar.xz',
    )
    wrap_hash_2 = compute_wrap_hash(mutated_wrap)
    assert wrap_hash_1 != wrap_hash_2
