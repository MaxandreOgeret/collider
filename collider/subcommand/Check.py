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
from collider.Package import get_provide_names
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson.scan import filter_dependencies, scan_dependencies
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.resolver import build_dep_name_index


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
            'Resolves dependency() names through installed wrap provides and repository\n'
            'metadata when available.\n'
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

        dep_name_index = self._build_dependency_name_index()

        # Stale check uses the raw scan so conditional deps are not falsely flagged.
        scanned_all_names = {dep.name for dep in scanned}
        scanned_included_names = {dep.name for dep in filtered.included}
        collider_names = {dep.name for dep in colliderfile.dependencies}
        managed_names = {
            dep.name for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        }

        resolved_scanned_names = {dep_name_index.get(name, name) for name in scanned_all_names}
        untracked = sorted(
            name
            for name in scanned_included_names
            if dep_name_index.get(name, name) not in collider_names
        )
        stale = sorted(managed_names - resolved_scanned_names)

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

    def _build_dependency_name_index(self) -> dict[str, str]:
        """Resolve Meson dependency names to collider package names when possible."""
        dep_name_index: dict[str, str] = {}

        subprojects_dir = self.sourcedir / 'subprojects'
        if subprojects_dir.exists():
            for wrap_path in subprojects_dir.glob('*.wrap'):
                package_name = wrap_path.stem
                dep_name_index.setdefault(package_name, package_name)
                try:
                    provide_names = get_provide_names(wrap_path.read_text(encoding='utf-8'))
                except Exception:
                    continue
                for provide_name in provide_names:
                    dep_name_index.setdefault(provide_name, package_name)

        repos = dict(getattr(self.context.config, 'repositories', {}).items())
        dep_name_index.update(
            {
                name: package_name
                for name, package_name in build_dep_name_index(repos).items()
                if name not in dep_name_index
            }
        )
        return dep_name_index
