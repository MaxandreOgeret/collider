# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider `status` command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.subcommand.Status import Status
from collider.utils.packaging.Dependency import Dependency, DependencySource


ORIGIN = 'https://wrapdb.example.com/v2/'


def test_status_ex_ok_normal_completion(tmp_path: Path) -> None:
    """`status` returns EX_OK on a normal run over a valid project with a lockfile."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    Colliderfile(
        dependencies=[Dependency('alpha', DependencySource.COLLIDER, '1.0.0')],
    ).save(tmp_path / Colliderfile.get_filename())

    # A lockfile makes resolution unnecessary, so no network/meson is needed.
    Lockfile(
        dependencies={
            'alpha': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN)
        },
    ).save(tmp_path / Lockfile.get_filename())

    cmd = Status(argparse.Namespace(), MagicMock(spec=Context))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)


def test_status_ex_noinput_missing_meson_build(tmp_path: Path, caplog) -> None:
    """`status` returns EX_NOINPUT when no meson.build exists in the current directory."""
    # Empty cwd: the first _validate_cwd() guard (missing meson.build) fires.
    cmd = Status(argparse.Namespace(), MagicMock(spec=Context))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)

    assert 'No meson.build file found in current directory.' in caplog.text
