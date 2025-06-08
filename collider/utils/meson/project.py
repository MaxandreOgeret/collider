# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Validation for Meson project and colliderfile in the current directory."""

from __future__ import annotations

from pathlib import Path

from collider.file_model.colliderfile import Colliderfile
from collider.log import logger


def validate_meson_project_cwd() -> bool:
    """
    Refuse to run when CWD is not a Meson project with a colliderfile.
    :return: True if meson.build and colliderfile exist in CWD, False otherwise.
    """
    if not Path.cwd().joinpath('meson.build').exists():
        logger.critical('No meson.build file found in current directory.')
        return False
    colliderfile_path = Path.cwd().joinpath(Colliderfile.get_filename())
    if not colliderfile_path.exists():
        logger.critical('No colliderfile found in current directory.')
        return False
    return True
