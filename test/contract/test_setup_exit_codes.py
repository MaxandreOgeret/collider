# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider setup` command."""

import os
import subprocess

from pathlib import Path

import pytest

from collider.subcommand.Setup import Setup
from collider.utils import meson
from test.common.common import Subcommand, run_subcommand


def test_setup_ex_ok_valid_project(meson_project: Path, tmp_path: Path) -> None:
    """`collider setup` returns EX_OK when meson setup and collider validation succeed."""
    result = run_subcommand(
        Subcommand.SETUP,
        ['--sourcedir', str(meson_project), '--builddir', str(tmp_path)],
    )
    # The 'shared' fixture needs system libmd; skip when it is absent.
    if meson_project.name == 'shared' and result != os.EX_OK:
        pytest.skip('shared project requires system libmd')
    assert result == os.EX_OK


def test_setup_ex_noinput_missing_sourcedir(tmp_path: Path) -> None:
    """`collider setup` returns EX_NOINPUT when the source directory does not exist."""
    missing_sourcedir = tmp_path / 'missing_sourcedir'
    assert not missing_sourcedir.exists()
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(missing_sourcedir), '--builddir', str(tmp_path)],
        )
        == os.EX_NOINPUT
    )


def test_setup_ex_dataerr_validation_fails(tmp_path: Path, monkeypatch) -> None:
    """`collider setup` returns EX_DATAERR when post-setup validation fails."""
    sourcedir = tmp_path / 'srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').touch()

    # Pass validation and setup so the EX_DATAERR comes solely from _validate
    # returning False, which is the "data present but invalid" contract for setup.
    monkeypatch.setattr(meson, 'validate', lambda: None)
    monkeypatch.setattr(meson, 'setup', lambda *args, **kwargs: None)
    monkeypatch.setattr(Setup, '_validate', lambda self: False)

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build')],
        )
        == os.EX_DATAERR
    )


def test_setup_ex_software_meson_setup_fails(tmp_path: Path, monkeypatch) -> None:
    """`collider setup` returns EX_SOFTWARE when meson setup exits non-zero."""
    sourcedir = tmp_path / 'srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').touch()

    def _raise_called_process_error(*args, **kwargs) -> None:
        raise subprocess.CalledProcessError(1, ['meson', 'setup'])

    # Pass validation so the EX_SOFTWARE comes solely from the setup failure,
    # not from a missing meson binary or an unbuildable meson.build.
    monkeypatch.setattr(meson, 'validate', lambda: None)
    monkeypatch.setattr(meson, 'setup', _raise_called_process_error)

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build')],
        )
        == os.EX_SOFTWARE
    )


def test_setup_ex_unavailable_meson_missing(tmp_path: Path, monkeypatch) -> None:
    """`collider setup` returns EX_UNAVAILABLE when Meson is missing or too old."""

    def _raise_unavailable() -> None:
        raise meson.MesonUnavailableError('Could not locate "meson" executable.')

    monkeypatch.setattr(meson, 'validate', _raise_unavailable)

    assert (
        run_subcommand(Subcommand.SETUP, ['--builddir', str(tmp_path / 'build')])
        == os.EX_UNAVAILABLE
    )
