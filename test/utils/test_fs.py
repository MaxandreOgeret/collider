# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import errno
import os
import stat

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.utils.fs import atomic_write_text


def test_atomic_write_text_creates_file(tmp_path: Path):
    target = tmp_path / 'sub' / 'out.txt'
    atomic_write_text(target, 'hello')
    assert target.read_text(encoding='UTF-8') == 'hello'


def test_atomic_write_text_overwrites_existing(tmp_path: Path):
    target = tmp_path / 'out.txt'
    target.write_text('old', encoding='UTF-8')
    atomic_write_text(target, 'new')
    assert target.read_text(encoding='UTF-8') == 'new'


def test_atomic_write_text_failure_preserves_existing(tmp_path: Path):
    target = tmp_path / 'out.txt'
    target.write_text('original', encoding='UTF-8')

    with patch('collider.utils.fs.tempfile.NamedTemporaryFile', side_effect=IOError('boom')):
        with pytest.raises(IOError):
            atomic_write_text(target, 'new')

    assert target.read_text(encoding='UTF-8') == 'original'


def test_atomic_write_text_no_temp_leftovers(tmp_path: Path):
    target = tmp_path / 'out.txt'
    atomic_write_text(target, 'data')
    assert [p.name for p in tmp_path.iterdir()] == ['out.txt']


def test_atomic_write_text_write_error_cleans_temp_and_preserves_existing(tmp_path: Path):
    target = tmp_path / 'out.txt'
    target.write_text('original', encoding='UTF-8')
    leftover = tmp_path / 'leftover.tmp'
    leftover.write_text('', encoding='UTF-8')

    fake_file = MagicMock()
    fake_file.__enter__.return_value.name = str(leftover)
    fake_file.__enter__.return_value.write.side_effect = IOError('write failed')

    with patch('collider.utils.fs.tempfile.NamedTemporaryFile', return_value=fake_file):
        with pytest.raises(IOError):
            atomic_write_text(target, 'new')

    assert not leftover.exists()
    assert target.read_text(encoding='UTF-8') == 'original'


def test_atomic_write_text_preserves_existing_mode(tmp_path: Path):
    target = tmp_path / 'out.txt'
    target.write_text('old', encoding='utf-8')
    os.chmod(target, 0o640)

    atomic_write_text(target, 'new')

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_write_text_new_file_uses_conventional_mode(tmp_path: Path):
    target = tmp_path / 'out.txt'

    atomic_write_text(target, 'data')

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_atomic_write_text_falls_back_to_move_across_filesystems(tmp_path: Path):
    target = tmp_path / 'out.txt'

    with patch.object(Path, 'replace', side_effect=OSError(errno.EXDEV, 'cross-device')):
        with patch('collider.utils.fs.shutil.move') as mock_move:
            atomic_write_text(target, 'data')

    mock_move.assert_called_once()


def test_atomic_write_text_reraises_non_exdev_replace_error(tmp_path: Path):
    target = tmp_path / 'out.txt'

    with patch.object(Path, 'replace', side_effect=OSError(errno.EACCES, 'denied')):
        with pytest.raises(OSError):
            atomic_write_text(target, 'data')
