# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider lock` command."""

import os

from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from test.common.common import Subcommand, run_subcommand


ORIGIN = 'https://wrapdb.example.com/v2'


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    """Lay down a minimal meson.build plus collider.json in tmp_path."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def test_lock_ex_ok_no_dependencies(tmp_path: Path) -> None:
    """`collider lock` returns EX_OK and writes an empty lockfile when no deps are declared."""
    _init_project(tmp_path, dependencies=[])

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.LOCK, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    lockfile = Lockfile.from_path(tmp_path / Lockfile.get_filename())
    assert lockfile.dependencies == {}
    assert lockfile.packages == {}


def test_lock_ex_noinput_missing_meson_build(tmp_path: Path) -> None:
    """`collider lock` returns EX_NOINPUT when no meson.build exists in cwd."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.LOCK, []) == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_lock_ex_dataerr_invalid_version_spec(tmp_path: Path) -> None:
    """`collider lock` returns EX_DATAERR when a declared dependency has an invalid version spec."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, 'not-a-specifier')],
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.LOCK, []) == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_lock_ex_unavailable_no_matching_package(tmp_path: Path) -> None:
    """`collider lock` returns EX_UNAVAILABLE when a declared dependency matches no package."""
    # No repositories configured: build_dep_name_index is empty, so the non-transitive
    # branch runs and _resolve_newest finds no match for the declared dependency.
    _init_project(
        tmp_path,
        dependencies=[Dependency('doesnotexist', DependencySource.COLLIDER, None)],
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.LOCK, []) == os.EX_UNAVAILABLE
    finally:
        os.chdir(cwd)


def test_lock_ex_ioerr_fetch_returns_none(tmp_path: Path) -> None:
    """`collider lock` returns EX_IOERR when a matched package fails to fetch."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    # Repo whose metadata lists the package (search succeeds) but get_package yields None,
    # so _fetch_package returns None and the non-transitive branch returns EX_IOERR.
    repo = MagicMock(spec=RepositoryInterface)
    repo.get_package.return_value = None
    repo.requires_network.return_value = False
    repo.origin_url = ORIGIN

    config = MagicMock()
    config.repositories = {'repo1': repo}
    config.offline = False
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)

    repo_key = make_repo_key('shared', '1.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '1.0.0', PackageType.WRAP)
    all_matches = {'repo1': {repo_key: entry}}

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch('collider.config.load', return_value=context),
            patch('collider.subcommand.Install.search_packages', return_value=all_matches),
        ):
            assert run_subcommand(Subcommand.LOCK, []) == os.EX_IOERR
    finally:
        os.chdir(cwd)
