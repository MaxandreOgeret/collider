# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider pkg remove` command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.subcommand.pkg.Remove import Remove
from collider.utils.packaging.Dependency import Dependency, DependencySource


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    """Write a minimal meson.build and collider.json into the project directory."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repos: dict | None = None) -> Context:
    """Build a Context with no repositories so transitive resolution stays trivial."""
    config = MagicMock()
    config.repositories = repos or {}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_pkg_remove_ex_noinput_no_meson_project(tmp_path: Path) -> None:
    """pkg remove returns EX_NOINPUT when cwd is not a valid Collider Meson project."""
    cmd = Remove(argparse.Namespace(package='x', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_pkg_remove_ex_ok_removes_only_declared_dependency(tmp_path: Path) -> None:
    """pkg remove returns EX_OK when the sole declared Collider dependency is removed."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    cmd = Remove(argparse.Namespace(package='shared', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies == []
