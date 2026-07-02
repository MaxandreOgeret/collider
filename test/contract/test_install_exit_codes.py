# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider install` command."""

import argparse
import hashlib
import os

from pathlib import Path
from unittest.mock import MagicMock

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.Package import WrapPackage
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.Install import Install
from collider.utils.packaging.Dependency import Dependency, DependencySource


ORIGIN = 'https://wrapdb.example.com/v2'


def _wrap_text(source_hash: str) -> str:
    """Build a minimal wrap-file body with the given source hash."""
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/pkg.tar.xz\n'
        'source_filename=pkg.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def _make_package(name: str, version: str, content: bytes) -> WrapPackage:
    """Build a WrapPackage whose source hash matches the given content."""
    content_hash = hashlib.sha256(content).hexdigest()
    return WrapPackage.from_wrap_text(name, version, _wrap_text(content_hash))


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    """Write a meson.build and a collider.json so the cwd is a valid Meson project."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    Colliderfile(dependencies=dependencies or []).save(tmp_path / Colliderfile.get_filename())


def _make_context(
    tmp_path: Path, repo: RepositoryInterface, cache: WrapCache | None = None
) -> Context:
    """Build a Context whose single configured repository is the given mock."""
    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    return Context(config=config, cache=cache or WrapCache(tmp_path / 'cache'), offline=False)


def _run(cmd: Install, tmp_path: Path) -> int:
    """Execute the command from within the project directory and restore cwd."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        return cmd.execute()
    finally:
        os.chdir(cwd)


def test_install_ex_noinput_missing_colliderfile(tmp_path: Path) -> None:
    """`collider install` returns EX_NOINPUT when collider.json is absent from the project."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')  # No collider.json.

    context = _make_context(tmp_path, MagicMock(spec=RepositoryInterface))
    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_NOINPUT


def test_install_ex_dataerr_invalid_locked_constraint(tmp_path: Path) -> None:
    """`collider install` returns EX_DATAERR when a locked dep has an unparseable constraint."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('foo', DependencySource.COLLIDER, 'not-a-specifier')],
    )
    Lockfile(
        dependencies={
            'foo': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(_wrap_text('deadbeef' * 8)),
                origin=ORIGIN,
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path, MagicMock(spec=RepositoryInterface))
    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_DATAERR


def test_install_ex_config_origin_repo_not_configured(tmp_path: Path) -> None:
    """`collider install` returns EX_CONFIG when no configured repo matches the locked origin."""
    package = _make_package('pkg', '1.0.0', b'payload')
    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )
    Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin='https://missing.example.com/',
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN  # Does not match the locked origin.
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, repo)
    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_CONFIG


def test_install_ex_unavailable_origin_missing_package(tmp_path: Path) -> None:
    """`collider install` returns EX_UNAVAILABLE when the origin repo cannot provide the package."""
    package = _make_package('pkg', '1.0.0', b'payload')
    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )
    Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = False
    repo.get_package.return_value = None  # Origin has no such package.
    context = _make_context(tmp_path, repo)
    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_UNAVAILABLE


def test_install_ex_ioerr_subproject_dir_exists(tmp_path: Path) -> None:
    """`collider install` returns EX_IOERR when the target subproject directory already exists."""
    package = _make_package('pkg', '1.0.0', b'payload')
    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )
    Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    context = _make_context(tmp_path, repo)

    # Pre-create the subproject directory so _do_install hits the "already exists" guard.
    (tmp_path / 'subprojects' / 'pkg').mkdir(parents=True)

    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_IOERR


def test_install_ex_ok_no_collider_dependencies(tmp_path: Path) -> None:
    """`collider install` returns EX_OK when collider.json declares no collider dependencies."""
    _init_project(tmp_path, dependencies=[])  # No lockfile, no collider-source deps.

    context = _make_context(tmp_path, MagicMock(spec=RepositoryInterface))
    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_OK


def _init_locked_pkg_project(tmp_path: Path, package: WrapPackage) -> None:
    """Write a meson.build, collider.json, and a lockfile pinning the given package."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('pkg', DependencySource.COLLIDER, None)],
    )
    Lockfile(
        dependencies={
            'pkg': LockedPackage(
                version='1.0.0',
                wrap_hash=compute_wrap_hash(package.wrap_text),
                origin=ORIGIN,
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())


def _origin_repo(package: WrapPackage) -> RepositoryInterface:
    """Build a mock origin repository that serves the given package."""
    repo = MagicMock(spec=RepositoryInterface)
    repo.origin_url = ORIGIN
    repo.requires_network.return_value = False
    repo.get_package.return_value = package
    return repo


def test_install_ex_ioerr_cache_prepare_permission(tmp_path: Path) -> None:
    """`collider install` returns EX_IOERR when preparing the package cache is denied."""
    package = _make_package('pkg', '1.0.0', b'payload')
    _init_locked_pkg_project(tmp_path, package)

    cache = MagicMock(spec=WrapCache)
    cache.prepare_packagecache.side_effect = PermissionError('denied')
    context = _make_context(tmp_path, _origin_repo(package), cache=cache)

    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_IOERR


def test_install_ex_ioerr_wrap_write_permission(tmp_path: Path, monkeypatch) -> None:
    """`collider install` returns EX_IOERR when writing the wrap file fails."""
    package = _make_package('pkg', '1.0.0', b'payload')
    _init_locked_pkg_project(tmp_path, package)

    # Cache is a no-op mock so prepare_packagecache cannot mask the wrap-write failure.
    cache = MagicMock(spec=WrapCache)
    context = _make_context(tmp_path, _origin_repo(package), cache=cache)

    def _deny_wrap_write(self: WrapPackage, path: Path) -> None:
        raise PermissionError('denied')

    monkeypatch.setattr(WrapPackage, 'install_to_subproject', _deny_wrap_write)

    cmd = Install(argparse.Namespace(offline=False, frozen=False), context)

    assert _run(cmd, tmp_path) == os.EX_IOERR
