# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Filesystem helpers for safe, atomic writes."""

import errno
import os
import shutil
import stat
import tempfile

from pathlib import Path


def atomic_write_text(path: Path, data: str, *, encoding: str = 'utf-8') -> None:
    """
    Write text to a path atomically so a failed write never corrupts the target.
    :param path: Destination file path.
    :param data: Text content to write.
    :param encoding: Text encoding used for the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Tempfiles default to 0o600, so capture the prior mode to keep an overwrite's permissions.
    target_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    # Stage in the destination directory so replace() stays on one filesystem and is atomic.
    with tempfile.NamedTemporaryFile(
        'w', encoding=encoding, dir=path.parent, delete=False
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        try:
            tmp_file.write(data)
        except Exception:
            # The destination is untouched, so discard the partial temp file and re-raise.
            tmp_path.unlink(missing_ok=True)
            raise

    os.chmod(tmp_path, target_mode)
    try:
        tmp_path.replace(path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            tmp_path.unlink(missing_ok=True)
            raise
        # Source and destination may live on different filesystems, so fall back to a move.
        try:
            shutil.move(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
