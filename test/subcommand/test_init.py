# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the init subcommand."""

import os

from pathlib import Path

from collider.file_model.colliderfile import Colliderfile
from collider.utils.packaging.Dependency import Dependency, DependencySource
from test.common.common import Subcommand, run_subcommand


def test_init_creates_colliderfile(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile_path = tmp_path / Colliderfile.get_filename()
    assert colliderfile_path.exists()
    colliderfile = Colliderfile.from_path(colliderfile_path)
    assert colliderfile.dependencies == []

    assert not (tmp_path / 'subprojects').exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    colliderfile_path = tmp_path / Colliderfile.get_filename()
    Colliderfile(
        description='Demo project',
        dependencies=[Dependency('sys', DependencySource.SYSTEM, None)],
    ).save(colliderfile_path)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    reloaded = Colliderfile.from_path(colliderfile_path)
    assert reloaded.description == 'Demo project'
    assert reloaded.dependencies == [Dependency('sys', DependencySource.SYSTEM, None)]


def test_init_requires_meson_build(tmp_path: Path) -> None:
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.INIT, []) == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert not (tmp_path / Colliderfile.get_filename()).exists()
