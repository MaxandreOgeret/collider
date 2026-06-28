# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Configure Meson builds and validate collider-managed dependencies."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess

from pathlib import Path
from typing import Optional

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils import meson
from collider.utils.compat import override
from collider.utils.meson.info import InfoFile, get_project_info, validate_projectinfo
from collider.utils.meson.infoTypes import ProjectInfo
from collider.utils.packaging import validate_dependencies
from collider.utils.project_state import (
    collect_force_fallback_names,
    managed_package_names,
    scan_wraps,
)


class Setup(SubcommandInterface):
    """Configure Meson builds and validate collider-managed dependencies."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Run meson setup with collider validation.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider setup --help`."""
        del cls
        return (
            'Run `meson setup`, then validate collider metadata and dependencies.\n'
            'Pass Meson flags after `--`, for example: collider setup -- --buildtype=debug.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        return """
Examples:
  ‣ collider setup
  ‣ collider setup --builddir my_builddir
  ‣ collider setup -- --buildtype=debug
  ‣ collider setup -- --reconfigure
"""

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            '--sourcedir', type=Path, help='Path to the source directory.', default=Path('.')
        )
        parser.add_argument(
            '--builddir',
            type=Path,
            help='Path to the build directory.',
            default=meson.DEFAULT_BUILD_DIR,
        )

        parser.add_argument(
            'meson_setup_args',
            nargs=argparse.REMAINDER,
            help='Arguments passed directly to "meson setup". Use "--" to separate from collider arguments.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store setup arguments and context.
        :param args: Parsed CLI arguments (sourcedir, builddir, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.sourcedir: Path = args.sourcedir
        self.builddir: Path = args.builddir
        self.meson_setup_args = args.meson_setup_args

        if self.meson_setup_args:
            if self.meson_setup_args[0] == '--':
                self.meson_setup_args = self.meson_setup_args[1:]
            else:
                logger.critical(msg := 'Expected "--" separator before meson arguments.')
                logger.critical(f'E.g. "collider setup -- {" ".join(self.meson_setup_args)}"')
                raise ValueError(msg)

    @override
    def execute(self) -> int:
        """Run the setup command.
        :return: Exit code.
        """
        meson.validate()
        builddir_preexisted = self.builddir.exists()

        if not self.sourcedir.exists():
            logger.critical(f'Source directory "{self.sourcedir.absolute()}" does not exist')
            return os.EX_NOINPUT

        if not (self.sourcedir / 'meson.build').exists():
            logger.critical(
                f'No "meson.build" file found in "{self.sourcedir.absolute()}" '
                '- not a valid Meson project'
            )
            return os.EX_NOINPUT

        if not (self.sourcedir / Colliderfile.get_filename()).exists():
            logger.critical(
                f'No "{Colliderfile.get_filename()}" file found in "{self.sourcedir.absolute()}" '
                '- not a valid Collider project.'
            )
            return os.EX_NOINPUT

        try:
            fallback_args = self._force_fallback_args()
        except ValueError as exc:
            logger.critical(str(exc))
            return os.EX_DATAERR

        try:
            meson.setup(
                sourcedir=self.sourcedir,
                builddir=self.builddir,
                args=fallback_args + self.meson_setup_args,
            )
        except subprocess.CalledProcessError as e:
            logger.critical('meson setup failed: %s', e)
            self._cleanup_builddir(builddir_preexisted)
            return os.EX_SOFTWARE

        if not self._validate():
            logger.critical('Could not validate project.')
            self._cleanup_builddir(builddir_preexisted)
            return os.EX_DATAERR

        return os.EX_OK

    @staticmethod
    def _user_forces_fallback(meson_args: list[str]) -> bool:
        """
        Detect a user-supplied force-fallback option in either Meson spelling.
        Meson rejects setting `force_fallback_for` via both `--force-fallback-for` and
        `-Dforce_fallback_for`, so collider must spot either form and defer rather than
        inject a second one that aborts the build.
        :param meson_args: Meson arguments passed after `--`.
        """
        names = ('--force-fallback-for', '-Dforce_fallback_for')
        return any(
            arg == name or arg.startswith(name + '=') for arg in meson_args for name in names
        )

    def _force_fallback_args(self) -> list[str]:
        """
        Force Meson to use collider's wraps so the build matches locked versions.
        Scope comes from collider.lock when present (direct + transitive, authoritative);
        without a lock, every present wrap is forced as a best effort and the user is warned
        that transitive deps may not be scoped correctly. Meson keeps only the last
        `--force-fallback-for`, so a user-supplied one is deferred to rather than overridden
        with a flag Meson would discard alongside a misleading message.
        :return: A single `--force-fallback-for` argument, or an empty list.
        :raises ValueError: When collider.lock exists but is malformed.
        """
        managed = managed_package_names(self.sourcedir)

        if self._user_forces_fallback(self.meson_setup_args):
            logger.warning(
                'A "--force-fallback-for" argument was supplied; collider will not force '
                'its managed wraps, so locked versions may be shadowed by system copies.'
            )
            return []

        subprojects_dir = self.sourcedir / meson.SUBPROJECTS_DIR
        forced = collect_force_fallback_names(subprojects_dir, managed)

        # Surface wraps a present lock does not cover, so an empty/stale lock under-scoping
        # the build is visible rather than a silent reproducibility hole.
        if managed is not None:
            excluded = sorted(set(scan_wraps(subprojects_dir)) - managed)
            if excluded:
                logger.info(
                    f'{len(excluded)} wrap(s) in "subprojects/" are not in collider.lock and are '
                    f"left to Meson's default resolution: {', '.join(excluded)}. "
                    'Run "collider lock" if collider should manage them.'
                )

        if not forced:
            return []
        if managed is None:
            logger.warning(
                'No collider.lock found: forcing all wraps in "subprojects/" as a best effort. '
                'Transitive dependencies may not be scoped correctly; '
                'run "collider lock" for authoritative, reproducible resolution.'
            )
        logger.info(
            f'Forcing wrap fallback for {len(forced)} managed dependency name(s) '
            'so locked wraps are used instead of system copies.'
        )
        return [f'--force-fallback-for={",".join(forced)}']

    def _cleanup_builddir(self, builddir_preexisted: bool) -> None:
        """Remove only build directories created by this run."""
        if builddir_preexisted:
            logger.warning(
                f'Not removing pre-existing build directory "{self.builddir.as_posix()}" '
                'after setup failure.'
            )
            return

        if not self.builddir.exists():
            return

        try:
            shutil.rmtree(self.builddir)
        except OSError as exc:
            logger.warning(f'Failed to remove build directory "{self.builddir.as_posix()}": {exc}')

    def _validate(self) -> bool:
        """Enforce metadata and dependency sanity before accepting a build."""
        project_info = get_project_info(InfoFile.projectinfo, self.builddir)
        try:
            validate_projectinfo(project_info)
        except ValueError:
            return False

        subproject_names = self._subproject_names_from_info(project_info)
        return validate_dependencies(
            Colliderfile.from_path(self.sourcedir / Colliderfile.get_filename()),
            get_project_info(InfoFile.dependencies, self.builddir),
            subproject_names=subproject_names,
        )

    @staticmethod
    def _subproject_names_from_info(project_info: ProjectInfo) -> set[str]:
        """Extract subproject names from intro-projectinfo.json; supports list of str or list of dict with 'name'."""
        entries = project_info.get('subprojects') or []
        names: set[str] = set()
        for entry in entries:
            if isinstance(entry, str):
                name = entry
            else:
                name = entry.get('name') if isinstance(entry, dict) else None
            if name:
                names.add(name)
        return names
