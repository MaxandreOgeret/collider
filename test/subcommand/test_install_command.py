# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the collider install subcommand."""

import argparse
import hashlib
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry, add_wrap_entry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.Install import Install
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


def _init_project(
    tmp_path: Path,
    dependencies: list[Dependency] | None = None,
) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repo: RepositoryInterface, repo_name: str = 'repo1') -> Context:
    config = MagicMock()
    config.repositories = {repo_name: repo}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def _make_package(name: str, version: str, content: bytes) -> WrapPackage:
    content_hash = hashlib.sha256(content).hexdigest()
    return WrapPackage.from_wrap_text(name, version, _wrap_text(content_hash))


# -- Restore from lockfile ---------------------------------------------------


def test_install_from_lockfile(tmp_path: Path, monkeypatch) -> None:
    """Restore packages from an existing lockfile."""
    content = b'payload'
    package = _make_package('foo', '1.0.0', content)
    wrap_hash = compute_wrap_hash(package.wrap_text)

    _init_project(
        tmp_path,
        dependencies=[Dependency('foo', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={'foo': LockedPackage(version='1.0.0', wrap_hash=wrap_hash, origin=ORIGIN)},
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN
    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    repo.search.return_value = {repo_key: RepoPackageEntry('foo', '1.0.0', PackageType.WRAP)}

    context = _make_context(tmp_path, repo)
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'foo.wrap').exists()


def test_install_skips_already_installed(tmp_path: Path) -> None:
    """Skip packages whose wrap file matches the locked hash."""
    wrap_text = _wrap_text('deadbeef' * 8)
    wrap_hash = compute_wrap_hash(wrap_text)

    _init_project(
        tmp_path,
        dependencies=[Dependency('foo', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={'foo': LockedPackage(version='1.0.0', wrap_hash=wrap_hash, origin=ORIGIN)},
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(parents=True)
    (subprojects / 'foo.wrap').write_text(wrap_text, encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.side_effect = AssertionError('Should not fetch already installed package')

    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)


# -- Fallback to collider.json -----------------------------------------------


def test_install_fallback_to_colliderfile(tmp_path: Path, monkeypatch) -> None:
    """Without a lockfile, install from collider.json without writing one."""
    content = b'payload'
    package = _make_package('bar', '2.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('bar', DependencySource.COLLIDER, None)],
    )

    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    repo_key = make_repo_key('bar', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('bar', '2.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    context = _make_context(tmp_path, repo)
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.Install.search_packages', return_value=all_matches):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'bar.wrap').exists()
    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_install_honors_declared_version_constraint(tmp_path: Path, monkeypatch) -> None:
    """collider install filters candidates using collider.json constraints."""
    content = b'payload'
    package = _make_package('bar', '1.5.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('bar', DependencySource.COLLIDER, '<2.0.0')],
    )

    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    repo_key = make_repo_key('bar', '1.5.0', PackageType.WRAP)
    entry = RepoPackageEntry('bar', '1.5.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    context = _make_context(tmp_path, repo)
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

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

    assert not (tmp_path / Lockfile.get_filename()).exists()


def test_install_invalid_declared_version_constraint_fails(tmp_path: Path) -> None:
    """Invalid collider.json specifiers are treated as data errors."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('bar', DependencySource.COLLIDER, 'not-a-specifier')],
    )

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


# -- Incompatibility warnings ------------------------------------------------


def test_install_warns_locked_not_declared(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn when a locked package is not declared in collider.json."""
    wrap_text = _wrap_text('deadbeef' * 8)
    wrap_hash = compute_wrap_hash(wrap_text)

    _init_project(tmp_path, dependencies=[])

    lockfile = Lockfile(
        dependencies={
            'orphan': LockedPackage(version='1.0', wrap_hash=wrap_hash, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(parents=True)
    (subprojects / 'orphan.wrap').write_text(wrap_text, encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cmd.execute()
    finally:
        os.chdir(cwd)

    assert 'not declared' in caplog.text


def test_install_warns_declared_not_locked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn when a declared dependency has no lock entry."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('missing', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile()
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cmd.execute()
    finally:
        os.chdir(cwd)

    assert 'no lock entry' in caplog.text


def test_install_warns_when_locked_version_violates_declared_constraint(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-frozen installs warn on intent/lock drift but still honor the lockfile."""
    content = b'payload'
    package = _make_package('foo', '2.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('foo', DependencySource.COLLIDER, '<2.0.0')],
    )

    lockfile = Lockfile(
        dependencies={
            'foo': LockedPackage(
                version='2.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN
    context = _make_context(tmp_path, repo)
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'does not satisfy declared constraint' in caplog.text
    assert (tmp_path / 'subprojects' / 'foo.wrap').exists()


# -- Frozen mode --------------------------------------------------------------


def test_install_frozen_fails_when_lockfile_has_orphan_package(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Frozen mode rejects lockfiles with packages absent from collider.json."""
    wrap_text = _wrap_text('deadbeef' * 8)
    wrap_hash = compute_wrap_hash(wrap_text)

    _init_project(tmp_path, dependencies=[])

    lockfile = Lockfile(
        dependencies={
            'orphan': LockedPackage(version='1.0', wrap_hash=wrap_hash, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=True)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert 'Frozen mode requires collider.lock to match collider.json.' in caplog.text


def test_install_frozen_fails_when_declared_dependency_is_missing_from_lockfile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Frozen mode rejects lockfiles missing declared dependencies."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('missing', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile()
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=True)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert 'Frozen mode requires collider.lock to match collider.json.' in caplog.text


def test_install_frozen_fails_when_lockfile_violates_declared_constraint(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Frozen mode rejects lockfiles that do not satisfy declared constraints."""
    wrap_text = _wrap_text('deadbeef' * 8)
    wrap_hash = compute_wrap_hash(wrap_text)

    _init_project(
        tmp_path,
        dependencies=[Dependency('foo', DependencySource.COLLIDER, '<2.0.0')],
    )

    lockfile = Lockfile(
        dependencies={'foo': LockedPackage(version='2.0.0', wrap_hash=wrap_hash, origin=ORIGIN)},
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=True)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert 'does not satisfy declared constraint' in caplog.text
    assert 'Frozen mode requires collider.lock to match collider.json.' in caplog.text


def test_install_frozen_fails_without_lockfile(tmp_path: Path) -> None:
    """Frozen mode fails when no lockfile exists."""
    _init_project(tmp_path)

    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=True)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() != os.EX_OK
    finally:
        os.chdir(cwd)


def test_install_always_fails_on_hash_mismatch(tmp_path: Path) -> None:
    """Install always fails when fetched wrap hash differs from lockfile."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash='sha256:' + 'f' * 64,
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN
    repo_key = make_repo_key('pkg', '1.0.0', PackageType.WRAP)
    repo.search.return_value = {repo_key: RepoPackageEntry('pkg', '1.0.0', PackageType.WRAP)}

    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_install_frozen_fails_when_no_repo_provides_package(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Frozen mode fails when no configured repository provides the locked package."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = None
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN
    context = _make_context(tmp_path, repo, repo_name='repo1')

    args = argparse.Namespace(offline=False, frozen=True)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_UNAVAILABLE
    finally:
        os.chdir(cwd)

    assert 'not found in origin repository' in caplog.text


def test_install_fetches_from_origin_repo(tmp_path: Path, monkeypatch) -> None:
    """Install fetches from the origin repository recorded in the lockfile."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN
    context = _make_context(tmp_path, repo, repo_name='repo1')
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'pkg.wrap').exists()


# -- No meson project --------------------------------------------------------


def test_install_fails_without_meson_project(tmp_path: Path) -> None:
    """Fail when no meson.build is present."""
    repo = MagicMock(spec=RepositoryInterface)
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


# -- Cross-root conflict detection -------------------------------------------


def _make_repo_with_dep_names(
    specs: list[tuple[str, str, list[str]]],
) -> MagicMock:
    """Build a repo mock with dependency_names so transitive resolution activates."""
    packages: dict = {}
    for name, version, dep_names in specs:
        add_wrap_entry(packages, name, version, dep_names)

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = False
    return repo


def test_install_cross_root_conflict_detected(
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Install detects incompatible transitive deps across roots and fails."""
    from collider.utils.packaging.resolver import Candidate, Requirement

    _init_project(
        tmp_path,
        dependencies=[
            Dependency('libfoo', DependencySource.COLLIDER, None),
            Dependency('libbar', DependencySource.COLLIDER, None),
        ],
    )

    repo = _make_repo_with_dep_names(
        [
            ('libfoo', '1.0.0', ['libfoo']),
            ('libbar', '1.0.0', ['libbar']),
            ('zlib', '1.2.0', ['zlib']),
            ('zlib', '1.3.1', ['zlib']),
        ]
    )

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

    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    def mock_get_deps(self_prov, candidate):
        if candidate.name == 'libfoo':
            return [Requirement('zlib', '>=1.3')]
        if candidate.name == 'libbar':
            return [Requirement('zlib', '<1.3')]
        return []

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

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


# -- Origin constraint -------------------------------------------------------


def test_install_fails_when_origin_repo_not_configured(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Install fails with EX_CONFIG when no configured repo matches the locked origin."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin='https://missing.example.com/',
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_CONFIG
    finally:
        os.chdir(cwd)

    assert 'no configured repository matches that origin' in caplog.text


def test_install_origin_url_normalization(tmp_path: Path, monkeypatch) -> None:
    """URL normalization matches origins despite case and trailing slash differences."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin='https://WrapDB.Example.COM/v2/',
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = package
    repo.requires_network.return_value = False
    repo.origin_url = 'https://wrapdb.example.com/v2'
    context = _make_context(tmp_path, repo)
    monkeypatch.setattr(
        'collider.utils.network.safe_urlopen', lambda url, **_kwargs: _DummyResponse(content)
    )

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / 'subprojects' / 'pkg.wrap').exists()


def test_install_origin_url_normalization_rejects_different_host(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Different hostnames do not match even after URL normalization."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin='https://a.example.com/v2/',
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = 'https://b.example.com/v2/'
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=False, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_CONFIG
    finally:
        os.chdir(cwd)

    assert 'no configured repository matches that origin' in caplog.text


def test_install_offline_uses_cache_with_provenance_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Offline install falls back to cache and emits a provenance warning."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = True
    repo.get_package.side_effect = AssertionError('Should not fetch in offline mode')

    context = _make_context(tmp_path, repo)
    context.cache.store_wrap(package)

    content_hash = hashlib.sha256(content).hexdigest()
    context.cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (context.cache.archives_dir / f'{content_hash}-pkg.tar.xz').write_bytes(content)

    args = argparse.Namespace(offline=True, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Origin provenance cannot be verified' in caplog.text
    assert (tmp_path / 'subprojects' / 'pkg.wrap').exists()


def test_install_offline_origin_repo_not_cached_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Offline install fails when origin repo requires network and nothing is cached."""
    content = b'payload'
    package = _make_package('pkg', '1.0.0', content)

    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )

    lockfile = Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = True

    context = _make_context(tmp_path, repo)

    args = argparse.Namespace(offline=True, frozen=False)
    cmd = Install(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_UNAVAILABLE
    finally:
        os.chdir(cwd)

    assert 'not found in cache' in caplog.text
