# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Run external commands and capture output."""

from __future__ import annotations

import subprocess

from enum import Enum
from pathlib import Path
from typing import Optional

from collider.log import logger


class StdStream(Enum):
    """Capture options for subprocess output."""

    NONE = 0
    STDOUT = 1
    STDERR = 2


def run(
    args: list[str], *, capture=StdStream.NONE, check=True, strip=True, cwd: Optional[Path] = None
) -> Optional[str]:
    """
    Wrapper around subprocess with logging and optional capture.
    :param args: Command and arguments to run (list of strings).
    :param capture: Which stream to capture (NONE, STDOUT, or STDERR).
    :param check: If True, raise CalledProcessError on non-zero exit.
    :param strip: If True, strip whitespace from captured output.
    :param cwd: Working directory; default is current directory.
    :return: Captured output string, or None if capture is NONE.
    """
    logger.debug(f'Running command: {args}')
    resolved_cwd = Path.cwd() if cwd is None else cwd

    process = subprocess.run(
        args,
        shell=False,
        universal_newlines=True,
        check=check,
        capture_output=capture != StdStream.NONE,
        cwd=resolved_cwd,
    )

    logger.debug(f'Return code: {process.returncode}')

    if capture == StdStream.STDOUT:
        out = process.stdout
        return out.strip() if out and strip else out

    if capture == StdStream.STDERR:
        out = process.stderr
        return out.strip() if out and strip else out

    return None
