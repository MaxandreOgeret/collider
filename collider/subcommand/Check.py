# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Detect drift between meson.build dependency() calls and collider.json."""

from __future__ import annotations

import argparse
import os

from pathlib import Path

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson.scan import filter_dependencies, scan_dependencies
from collider.utils.packaging.Dependency import DependencySource


_DEFAULT_SOURCE_DIR = Path('.')


class Check(SubcommandInterface):
    """Detect drift between meson.build dependency() calls and collider.json."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Check for drift between meson.build and collider.json.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider check --help`."""
        del cls
        return (
            'Scan meson.build for dependency() calls and compare against collider.json.\n'
            'Reports untracked dependencies (in meson.build but not in collider.json) and\n'
            'stale entries (in collider.json but absent from meson.build).\n\n'
            'Assumes the dependency() name in meson.build matches the collider package name.\n'
            'Exits with EX_DATAERR when drift is found, EX_OK when clean.'
        )

    @staticmethod
    def epilog() -> str | None:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            '--sourcedir',
            type=Path,
            default=_DEFAULT_SOURCE_DIR,
            help='Meson source directory (default: current directory).',
        )
        parser.add_argument(
            '--include-conditional',
            action='store_true',
            default=False,
            help='Also check dependencies inside Meson if-blocks.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store check arguments and context.
        :param args: Parsed CLI arguments.
        :param context: Application context.
        """
        super().__init__(args, context)
        self.sourcedir: Path = args.sourcedir
        self.include_conditional: bool = args.include_conditional

    @override
    def execute(self) -> int:
        """Run the check command.
        :return: Exit code.
        """
        meson_build = self.sourcedir / 'meson.build'
        colliderfile_path = self.sourcedir / Colliderfile.get_filename()

        if not meson_build.exists():
            logger.critical(f'No meson.build found in "{self.sourcedir}".')
            return os.EX_NOINPUT

        if not colliderfile_path.exists():
            logger.critical(f'No collider.json found in "{self.sourcedir}".')
            return os.EX_NOINPUT

        colliderfile = Colliderfile.from_path(colliderfile_path)
        scanned = scan_dependencies(meson_build)
        filtered = filter_dependencies(scanned, include_conditional=self.include_conditional)

        # Stale check uses the raw scan so conditional deps are not falsely flagged.
        scanned_all_names = {dep.name for dep in scanned}
        scanned_included_names = {dep.name for dep in filtered.included}
        collider_names = {dep.name for dep in colliderfile.dependencies}
        managed_names = {
            dep.name for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        }

        untracked = sorted(scanned_included_names - collider_names)
        stale = sorted(managed_names - scanned_all_names)

        for name in untracked:
            logger.error(
                f'"{name}" is used in meson.build but not tracked in collider.json. '
                f'Run: collider pkg add {name}'
            )
        for name in stale:
            logger.error(f'"{name}" is in collider.json but not found in meson.build.')

        if untracked or stale:
            return os.EX_DATAERR

        logger.info('No drift detected.')
        return os.EX_OK
