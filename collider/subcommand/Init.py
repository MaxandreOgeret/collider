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
from collider.utils.meson.scan import scan_project_info


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

    @staticmethod
    def _hint_missing_license(meson_path: Path) -> None:
        """
        Emit a hint when meson.build has no license field.
        Missing license causes a warning on every `collider setup`; nudging at init
        time avoids the user discovering the gap only later.
        :param meson_path: Path to the meson.build file.
        """
        project_info = scan_project_info(meson_path)
        if project_info is None:
            return
        licenses = project_info.get('license', [])
        if not licenses or licenses == ['unknown']:
            name = project_info.get('descriptive_name', 'mylib')
            version = project_info.get('version', '1.0.0')
            if version == 'undefined':
                version = '1.0.0'
            logger.warning(
                'meson.build has no license field. '
                'Add one to avoid warnings at setup time. '
                f"Example: project('{name}', 'cpp', version: '{version}', license: 'MIT')"
            )

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

        self._hint_missing_license(meson_path)

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
