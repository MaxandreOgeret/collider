# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg upgrade command."""

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
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Upgrade import Upgrade
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


ORIGIN = 'https://wrapdb.example.com/v2/'


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


def _make_package(name: str, version: str, content: bytes) -> WrapPackage:
    content_hash = hashlib.sha256(content).hexdigest()
    return WrapPackage.from_wrap_text(name, version, _wrap_text(content_hash))


def _init_project(tmp_path: Path, dependencies: list[Dependency]) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    colliderfile = Colliderfile(dependencies=dependencies)
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repo: RepositoryInterface, repo_name: str = 'repo1') -> Context:
    config = MagicMock()
    config.repositories = {repo_name: repo}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_upgrade_one_package_replaces_existing_wrap(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Upgrading one package replaces the installed wrap but preserves its constraint."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, '<3.0.0')],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'shared.wrap').write_text('old wrap', encoding='utf-8')

    content = b'payload'
    package = _make_package('shared', '2.0.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    lockfile = Lockfile(
        dependencies={
            'shared': LockedPackage(
                version='1.0.0',
                wrap_hash='sha256:' + '0' * 64,
                origin=ORIGIN,
            )
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Upgrade.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    wrap_path = tmp_path / 'subprojects' / 'shared.wrap'
    assert wrap_path.read_text(encoding='utf-8') == package.wrap_text
    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies[0].version == '<3.0.0'
    assert 'run "collider lock" to refresh it' in caplog.text


def test_upgrade_all_packages_uses_declared_constraints(tmp_path: Path, monkeypatch) -> None:
    """Upgrading without a package name upgrades all Collider-managed dependencies."""
    _init_project(
        tmp_path,
        [
            Dependency('alpha', DependencySource.COLLIDER, None),
            Dependency('beta', DependencySource.COLLIDER, '<3.0.0'),
        ],
    )

    alpha_content = b'alpha'
    beta_content = b'beta'
    alpha_package = _make_package('alpha', '2.0.0', alpha_content)
    beta_package = _make_package('beta', '2.5.0', beta_content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False

    packages = {
        make_repo_key('alpha', '2.0.0', PackageType.WRAP): alpha_package,
        make_repo_key('beta', '2.5.0', PackageType.WRAP): beta_package,
    }

    def get_package(repo_key: str):
        return packages[repo_key]

    repo.get_package.side_effect = get_package
    context = _make_context(tmp_path, repo)

    def search_side_effect(_repos, pattern, version_spec):
        if pattern.pattern == '^alpha$':
            return {
                'repo1': {
                    make_repo_key('alpha', '2.0.0', PackageType.WRAP): RepoPackageEntry(
                        'alpha', '2.0.0', PackageType.WRAP
                    )
                }
            }
        assert version_spec is not None and version_spec.contains('2.5.0')
        return {
            'repo1': {
                make_repo_key('beta', '2.5.0', PackageType.WRAP): RepoPackageEntry(
                    'beta', '2.5.0', PackageType.WRAP
                )
            }
        }

    payloads = iter([alpha_content, beta_content])
    monkeypatch.setattr(
        urllib.request,
        'urlopen',
        lambda url, **_kwargs: _DummyResponse(next(payloads)),
    )

    cmd = Upgrade(argparse.Namespace(package=None, version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages', side_effect=search_side_effect
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'alpha.wrap').exists()
    assert (tmp_path / 'subprojects' / 'beta.wrap').exists()


def test_upgrade_with_version_updates_constraint(tmp_path: Path, monkeypatch) -> None:
    """A package-specific upgrade may replace the declared version constraint."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    package = _make_package('shared', '1.5.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '1.5.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.5.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    cmd = Upgrade(
        argparse.Namespace(package='shared', version=SpecifierSet('<2.0.0'), offline=False),
        context,
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages', return_value=all_matches
        ) as search:
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies[0].version == '<2.0.0'
    assert search.call_args.args[2].contains('1.5.0')


def test_upgrade_requires_declared_dependency(tmp_path: Path) -> None:
    """Upgrading an unmanaged package should fail cleanly."""
    _init_project(tmp_path, [])
    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)
    cmd = Upgrade(argparse.Namespace(package='missing', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_upgrade_rejects_global_version_override(tmp_path: Path) -> None:
    """A version override must target one package, not all dependencies."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    cmd = Upgrade(
        argparse.Namespace(package=None, version=SpecifierSet('<2.0.0'), offline=False),
        context,
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_USAGE
    finally:
        os.chdir(cwd)


def test_upgrade_does_not_warn_when_lockfile_already_matches(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Upgrading an already-current package should keep a matching lockfile quiet."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, '<3.0.0')],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()

    content = b'payload'
    package = _make_package('shared', '2.0.0', content)
    (subprojects / 'shared.wrap').write_text(package.wrap_text, encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    lockfile = Lockfile(
        dependencies={
            'shared': LockedPackage.from_wrap_text(
                version='2.0.0',
                wrap_text=package.wrap_text,
                origin=ORIGIN,
            )
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={'repo1': {repo_key: entry}},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'run "collider lock" to refresh it' not in caplog.text


def test_upgrade_offline_uses_cached_wrap(tmp_path: Path, monkeypatch) -> None:
    """Offline upgrade succeeds when the target wrap is already cached."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    content = b'cached-payload'
    package = _make_package('shared', '2.0.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = True
    context = _make_context(tmp_path, repo)
    context.cache.store_wrap(package)
    context.cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (context.cache.archives_dir / f'{package.source_hash}-pkg.tar.xz').write_bytes(content)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))

    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={'repo1': {repo_key: entry}},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'shared.wrap').exists()


def test_upgrade_offline_missing_cache_fails(tmp_path: Path) -> None:
    """Offline upgrade fails when the requested wrap is not cached."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = True
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={'repo1': {repo_key: entry}},
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_upgrade_skips_invalid_versions(tmp_path: Path, monkeypatch) -> None:
    """Upgrade ignores invalid candidate versions and uses the newest valid one."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    content = b'payload'
    package = _make_package('shared', '2.0.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, repo)

    bad_key = make_repo_key('shared', 'bad-version', PackageType.WRAP)
    good_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))
    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={
                'repo1': {
                    bad_key: RepoPackageEntry('shared', 'bad-version', PackageType.WRAP),
                    good_key: RepoPackageEntry('shared', '2.0.0', PackageType.WRAP),
                }
            },
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'shared.wrap').read_text(
        encoding='utf-8'
    ) == package.wrap_text


def test_upgrade_fetch_failure_returns_ioerr(tmp_path: Path) -> None:
    """Upgrade fails cleanly when fetching the selected package fails."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = None
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={'repo1': {repo_key: entry}},
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_upgrade_fails_when_installing_downloaded_package_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Upgrade surfaces package installation failures as IO errors."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )

    content = b'payload'
    package = _make_package('shared', '2.0.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))
    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Upgrade.search_packages',
                return_value={'repo1': {repo_key: entry}},
            ),
            patch.object(
                WrapPackage,
                'install_to_subproject',
                side_effect=FileExistsError('already exists'),
            ),
        ):
            assert cmd.execute() == os.EX_IOERR
    finally:
        os.chdir(cwd)


def test_upgrade_all_stops_after_first_failure(tmp_path: Path) -> None:
    """Upgrade-all stops at the first failing package and leaves later packages untouched."""
    _init_project(
        tmp_path,
        [
            Dependency('alpha', DependencySource.COLLIDER, None),
            Dependency('beta', DependencySource.COLLIDER, None),
        ],
    )
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = None
    context = _make_context(tmp_path, repo)

    def search_side_effect(_repos, pattern, version_spec):
        del version_spec
        name = pattern.pattern.strip('^$')
        return {
            'repo1': {
                make_repo_key(name, '1.0.0', PackageType.WRAP): RepoPackageEntry(
                    name, '1.0.0', PackageType.WRAP
                )
            }
        }

    cmd = Upgrade(argparse.Namespace(package=None, version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            side_effect=search_side_effect,
        ) as search:
            assert cmd.execute() == os.EX_IOERR
            assert search.call_count == 1
    finally:
        os.chdir(cwd)

    assert not (tmp_path / 'subprojects' / 'beta.wrap').exists()


def test_upgrade_ignores_corrupt_lockfile_warning_check(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Upgrade still succeeds when collider.lock exists but is unreadable."""
    _init_project(
        tmp_path,
        [Dependency('shared', DependencySource.COLLIDER, None)],
    )
    (tmp_path / Lockfile.get_filename()).write_text('not-json{{', encoding='utf-8')

    content = b'payload'
    package = _make_package('shared', '2.0.0', content)
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, repo)

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **_kwargs: _DummyResponse(content))
    cmd = Upgrade(argparse.Namespace(package='shared', version=None, offline=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Upgrade.search_packages',
            return_value={'repo1': {repo_key: entry}},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'shared.wrap').exists()
    assert 'Invalid JSON' in caplog.text
