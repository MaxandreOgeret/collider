# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Bootstrap collider metadata in an existing Meson project."""

from __future__ import annotations

import argparse
import os

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override


class Init(SubcommandInterface):
    """Bootstrap collider metadata in an existing Meson project."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Initialize collider metadata for this project.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider init --help`."""
        del cls
        return (
            'Create `collider.json` in the current Meson project.\n'
            'Run this once to start tracking dependencies with collider.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        # Init has no arguments; keep a stub for CLI discovery.
        del parser

    @override
    def execute(self) -> int:
        """Run the init command.
        :return: Exit code.
        """
        project_root = Path.cwd()
        meson_path = project_root / 'meson.build'
        if not meson_path.exists():
            logger.critical('No meson.build file found in current directory.')
            return os.EX_DATAERR

        colliderfile_path = project_root / Colliderfile.get_filename()
        if colliderfile_path.exists():
            if colliderfile_path.is_dir():
                logger.critical('collider.json exists but is a directory.')
                return os.EX_DATAERR
            logger.info('collider.json already exists.')
        else:
            # collider.json is the single source of truth for managed dependencies.
            Colliderfile().save(colliderfile_path)
            logger.info('Created collider.json.')

        return os.EX_OK
