# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the setup subcommand."""

import os
import re

from pathlib import Path

import pytest

from test.common.common import Subcommand, run_subcommand


def test_execute_success(meson_project: Path, tmp_path: Path) -> None:
    """Execute the subcommand."""
    # Skip 'shared' when system lacks libmd (optional system dependency).
    if meson_project.name == 'shared':
        result = run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path)],
        )
        if result != os.EX_OK:
            pytest.skip('shared project requires system libmd')
        # Fall through to assertions below.
    else:
        assert (
            run_subcommand(
                Subcommand.SETUP,
                ['--sourcedir', str(meson_project), '--builddir', str(tmp_path)],
            )
            == os.EX_OK
        )

    for required_file in [
        'build.ninja',
        'compile_commands.json',
        'meson-info',
        'meson-logs',
        'meson-private',
    ]:
        assert (tmp_path / required_file).exists()


def test_execute_missing_sourcedir(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Execute the subcommand with a missing source directory."""

    missing_sourcedir = tmp_path / 'missing_sourcedir'
    assert not missing_sourcedir.exists()
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(missing_sourcedir), '--builddir', str(tmp_path)],
        )
        == os.EX_DATAERR
    )

    stdout, stderr = capfd.readouterr()
    assert re.search(r'Source directory .* does not exist', stderr)


def test_execute_missing_mesonbuild(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Execute the subcommand with a missing source directory."""

    empty_sourcedir = tmp_path / 'empty_sourcedir'
    empty_sourcedir.mkdir()
    assert empty_sourcedir.exists()
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(empty_sourcedir), '--builddir', str(tmp_path)],
        )
        == os.EX_DATAERR
    )

    stdout, stderr = capfd.readouterr()
    assert 'No "meson.build" file found' in stderr


def test_execute_invalid_mesonbuild(tmp_path: Path, capfd: pytest.CaptureFixture):
    sourcedir = tmp_path / 'empty_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')

    mesonbuild = sourcedir / 'meson.build'
    mesonbuild.touch(exist_ok=True)
    assert mesonbuild.exists()

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path)]
        )
        == os.EX_SOFTWARE
    )

    stdout, stderr = capfd.readouterr()
    assert 'meson setup failed' in stderr


def test_pass_args_to_meson(tmp_path: Path, meson_project: Path, capfd: pytest.CaptureFixture):
    with pytest.raises(ValueError):
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path), '--', '--help'],
            verbose=True,
        )

    stdout, stderr = capfd.readouterr()
    assert re.search(r'Running command: \[\'meson\', \'setup\'.*\'--help\']', stderr)
    assert 'usage: meson setup' in stdout


def test_missing_separator(tmp_path: Path, meson_project: Path, capfd: pytest.CaptureFixture):
    """Test that missing -- separator for extra arguments causes failure."""
    with pytest.raises(ValueError):
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path), 'reconfigure'],
        )
    stdout, stderr = capfd.readouterr()
    assert 'Expected "--" separator' in stderr


def test_builddir_cleanup_on_failure(tmp_path: Path):
    """Test that build directory is removed if meson setup fails."""
    sourcedir = tmp_path / 'empty_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    mesonbuild = sourcedir / 'meson.build'
    mesonbuild.write_text('invalid content')
    builddir = tmp_path / 'failed_build'

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_SOFTWARE
    )
    assert not builddir.exists()


def test_preexisting_builddir_not_removed_on_failure(tmp_path: Path):
    """Do not remove a build directory that existed before setup started."""
    sourcedir = tmp_path / 'broken_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text('invalid content')

    builddir = tmp_path / 'existing_builddir'
    builddir.mkdir()
    sentinel = builddir / 'keep-me.txt'
    sentinel.write_text('preserve')

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_SOFTWARE
    )
    assert builddir.exists()
    assert sentinel.exists()


def test_validate_failure_invalid_version(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Test _validate failure when project has an invalid version."""
    sourcedir = tmp_path / 'invalid_version_project'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text("project('invalid', 'cpp', version: 'not-a-version')")
    builddir = tmp_path / 'invalid_build'

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_DATAERR
    )
    assert not builddir.exists()
    stdout, stderr = capfd.readouterr()
    assert 'Project has invalid version' in stderr
    assert 'Could not validate project.' in stderr
