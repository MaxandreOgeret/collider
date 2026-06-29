# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÃœ

"""Test project-state helpers."""

from pathlib import Path

import pytest

from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.utils.project_state import read_lockfile


def test_read_lockfile_loads_valid_file(tmp_path: Path) -> None:
    """A valid collider.lock is parsed and returned."""
    lockfile = Lockfile(
        dependencies={
            'shared': LockedPackage(
                version='1.0.0',
                wrap_hash='sha256:' + 'a' * 64,
                origin='https://wrapdb.example.com/v2/',
            ),
        }
    )
    lockfile_path = tmp_path / Lockfile.get_filename()
    lockfile.save(lockfile_path)

    loaded = read_lockfile(lockfile_path)

    assert loaded is not None
    assert 'shared' in loaded.all_packages


def test_read_lockfile_warns_on_corrupt_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed JSON is reported with a lockfile-specific warning."""
    lockfile_path = tmp_path / Lockfile.get_filename()
    lockfile_path.write_text('not valid json{{{{', encoding='utf-8')

    loaded = read_lockfile(lockfile_path)

    assert loaded is None
    assert 'collider.lock could not be read' in caplog.text


def test_read_lockfile_warns_on_oserror(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """OS-level read failures are reported with a distinct warning."""
    lockfile_path = tmp_path / Lockfile.get_filename()
    lockfile_path.mkdir()

    loaded = read_lockfile(lockfile_path)

    assert loaded is None
    assert 'Could not read collider.lock' in caplog.text
