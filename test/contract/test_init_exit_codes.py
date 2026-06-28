# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider init` command."""

import os

from pathlib import Path
from unittest.mock import patch

from collider.file_model.colliderfile import Colliderfile
from test.common.common import Subcommand, run_subcommand


def _mock_project_info(info: dict | None):
    """Patch scan_project_info in the Init module to avoid invoking real meson."""
    return patch('collider.subcommand.Init.scan_project_info', return_value=info)


def test_init_ex_ok_meson_build_present(tmp_path: Path) -> None:
    """`collider init` returns EX_OK when meson.build exists and collider.json is ensured."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / Colliderfile.get_filename()).exists()


def test_init_ex_noinput_missing_meson_build(tmp_path: Path) -> None:
    """`collider init` returns EX_NOINPUT when no meson.build exists in cwd."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.INIT, []) == os.EX_NOINPUT
    finally:
        os.chdir(cwd)

    assert not (tmp_path / Colliderfile.get_filename()).exists()
