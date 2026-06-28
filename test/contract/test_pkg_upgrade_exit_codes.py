# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider ``pkg upgrade`` command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Upgrade import Upgrade
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    """Write a minimal meson.build and collider.json into the project directory."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    Colliderfile(dependencies=dependencies or []).save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repo: RepositoryInterface | None = None) -> Context:
    """Build a Context whose only repository is the supplied mock, if any."""
    config = MagicMock()
    config.repositories = {'repo1': repo} if repo is not None else {}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_pkg_upgrade_ex_ok_no_declared_dependencies(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_OK when no Collider-managed dependencies are declared."""
    _init_project(tmp_path, [])
    cmd = Upgrade(
        argparse.Namespace(package=None, version=None, offline=False), _make_context(tmp_path)
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)


def test_pkg_upgrade_ex_usage_version_without_package(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_USAGE when --version is given without a package name."""
    _init_project(tmp_path, [Dependency('shared', DependencySource.COLLIDER, None)])
    cmd = Upgrade(
        argparse.Namespace(package=None, version='<2.0.0', offline=False),
        _make_context(tmp_path),
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_USAGE
    finally:
        os.chdir(cwd)


def test_pkg_upgrade_ex_dataerr_invalid_declared_constraint(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_DATAERR when the declared version constraint is unparseable."""
    _init_project(tmp_path, [Dependency('shared', DependencySource.COLLIDER, '@@bad@@')])
    cmd = Upgrade(
        argparse.Namespace(package='shared', version=None, offline=False),
        _make_context(tmp_path),
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_DATAERR
    finally:
        os.chdir(cwd)


def test_pkg_upgrade_ex_noinput_no_meson_project(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_NOINPUT when cwd is not a valid Collider Meson project."""
    cmd = Upgrade(
        argparse.Namespace(package=None, version=None, offline=False), _make_context(tmp_path)
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_pkg_upgrade_ex_unavailable_no_package_match(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_UNAVAILABLE when the search finds no matching package."""
    _init_project(tmp_path, [Dependency('shared', DependencySource.COLLIDER, None)])
    repo = MagicMock(spec=RepositoryInterface)
    cmd = Upgrade(
        argparse.Namespace(package='shared', version=None, offline=False),
        _make_context(tmp_path, repo),
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Upgrade.search_packages', return_value={}):
            assert cmd.execute() == os.EX_UNAVAILABLE
    finally:
        os.chdir(cwd)


def test_pkg_upgrade_ex_ioerr_offline_missing_cache(tmp_path: Path) -> None:
    """``pkg upgrade`` returns EX_IOERR when an offline upgrade's wrap is absent from the cache."""
    _init_project(tmp_path, [Dependency('shared', DependencySource.COLLIDER, None)])
    repo = MagicMock(spec=RepositoryInterface)
    repo.requires_network.return_value = True
    cmd = Upgrade(
        argparse.Namespace(package='shared', version=None, offline=True),
        _make_context(tmp_path, repo),
    )

    repo_key = make_repo_key('shared', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry('shared', '2.0.0', PackageType.WRAP)

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
