# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Create a patch archive from Git changes for use with Meson wrap patch_url."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import tarfile

from pathlib import Path
from typing import Optional

from collider.Context import Context
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson.info import load_project_metadata
from collider.utils.meson.meson import DEFAULT_BUILD_DIR
from collider.utils.meson.project import validate_meson_project_cwd


class Patch(SubcommandInterface):
    """Create a patch archive (tar.xz) from Git changes for Meson wrap patch_url."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Create a patch archive from Git changes.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider patch --help`."""
        del cls
        return (
            'Build a tar.xz patch archive for Meson wrap patch_url workflows.\n'
            'By default it includes committed and uncommitted changes; use --list to preview.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        return """
Examples:
  ‣ collider patch
  ‣ collider patch --list
  ‣ collider patch --base v1.0 --no-include-uncommitted
  ‣ collider patch --output my_patch.tar.xz
"""

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        # pylint: disable=duplicate-code  # CLI wiring must stay in subcommand.
        parser.add_argument(
            '--builddir',
            type=Path,
            default=DEFAULT_BUILD_DIR,
            help='Meson build directory used to read project metadata.',
        )
        parser.add_argument(
            '--base',
            type=str,
            default='HEAD',
            help='Git revision to diff against (base of the patch).',
        )
        parser.add_argument(
            '--include-uncommitted',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Include uncommitted (staged and unstaged) and untracked files.',
        )
        parser.add_argument(
            '--output',
            type=Path,
            default=None,
            help='Output path (default: dist/<name>_<version>_patch.tar.xz).',
        )
        parser.add_argument(
            '--list',
            dest='list_only',
            action='store_true',
            help='Print files that would be included without writing the archive.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store patch arguments and context.
        :param args: Parsed CLI arguments (builddir, base, output, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.builddir: Path = args.builddir
        self.base_rev: str = args.base
        self.include_uncommitted: bool = args.include_uncommitted
        self.output_path: Optional[Path] = args.output
        self.list_only: bool = args.list_only

    @override
    def execute(self) -> int:
        """
        Create a patch archive (tar.xz) from Git changes, or list changed paths.
        :return: Exit code (EX_OK, EX_NOINPUT, EX_DATAERR, or EX_IOERR).
        """
        logger.debug('Running patch subcommand.')
        if not validate_meson_project_cwd():
            return os.EX_NOINPUT

        package_name, version, source_dir = load_project_metadata(self.builddir)
        if package_name is None or version is None or source_dir is None:
            return os.EX_DATAERR

        changed_paths = self._collect_changed_paths(source_dir)
        if changed_paths is None:
            return os.EX_DATAERR

        if self.list_only:
            for p in sorted(changed_paths):
                print(p)
            return os.EX_OK

        if not changed_paths:
            logger.warning('No changed files to include; output archive would be empty.')
            logger.info(
                'Skipping archive creation. Use --list to see which files would be included.'
            )
            return os.EX_OK

        out_path = self.output_path or Path('dist') / f'{package_name}_{version}_patch.tar.xz'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        archive_root = f'{package_name}-{version}'

        try:
            self._write_patch_archive(
                source_dir=source_dir,
                paths=changed_paths,
                archive_path=out_path,
                archive_root=archive_root,
            )
        except OSError as e:
            logger.critical(f'Failed to write patch archive: {e}')
            return os.EX_IOERR

        logger.info(f'Patch archive written to "{out_path.as_posix()}".')
        return os.EX_OK

    def _run_git(
        self, args: list[str], cwd: Path, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """
        Run git in the given directory.
        :param args: Git arguments (e.g. ['rev-parse', '--show-toplevel']).
        :param cwd: Working directory for the Git process.
        :param capture: Whether to capture stdout/stderr.
        :return: CompletedProcess; check returncode for success.
        """
        cmd = ['git', *args]
        logger.debug(f'Running: {" ".join(cmd)} (cwd={cwd})')
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=False,
        )

    def _collect_changed_paths(self, source_dir: Path) -> list[str] | None:
        """
        Collect paths of changed files via Git; error on deletions.
        :param source_dir: Meson source directory (Git repo root).
        :return: List of relative paths to include, or None on error (no Git, deleted file).
        """
        try:
            rev_parse = self._run_git(['rev-parse', '--show-toplevel'], cwd=source_dir)
        except FileNotFoundError:
            logger.critical(
                'Not a Git repository or Git not available. '
                'collider patch requires Git to collect changed files.'
            )
            return None
        if rev_parse.returncode != 0:
            logger.critical(
                'Not a Git repository or Git not available. '
                'collider patch requires Git to collect changed files.'
            )
            return None

        if self.include_uncommitted:
            # HEAD vs working tree and index; plus untracked.
            status = self._run_git(['diff', '--name-status', 'HEAD'], cwd=source_dir)
            if status.returncode != 0:
                logger.critical(f'Git diff failed: {status.stderr or status.stdout}')
                return None
            paths_from_diff = []
            for line in (status.stdout or '').strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split('\t', 1)
                if len(parts) != 2:
                    continue
                status_code, path = parts[0], parts[1]
                if status_code == 'D':
                    logger.critical(
                        f'Deleted file "{path}" cannot be included in a Meson wrap patch; '
                        'wrap patches cannot remove files cleanly. Commit the deletion or '
                        'exclude it from the patch.'
                    )
                    return None
                paths_from_diff.append(path)

            untracked = self._run_git(
                ['ls-files', '--others', '--exclude-standard'], cwd=source_dir
            )
            untracked_paths = (
                (untracked.stdout or '').strip().splitlines() if untracked.returncode == 0 else []
            )
            seen = set(paths_from_diff)
            for p in untracked_paths:
                p = p.strip()
                if p and p not in seen:
                    paths_from_diff.append(p)
            return paths_from_diff

        # Committed changes only: base..HEAD.
        status = self._run_git(['diff', '--name-status', f'{self.base_rev}..HEAD'], cwd=source_dir)
        if status.returncode != 0:
            logger.critical(f'Git diff failed: {status.stderr or status.stdout}')
            return None
        paths = []
        for line in (status.stdout or '').strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t', 1)
            if len(parts) != 2:
                continue
            status_code, path = parts[0], parts[1]
            if status_code == 'D':
                logger.critical(
                    f'Deleted file "{path}" cannot be included in a Meson wrap patch; '
                    'wrap patches cannot remove files cleanly.'
                )
                return None
            paths.append(path)
        return paths

    def _write_patch_archive(
        self,
        source_dir: Path,
        paths: list[str],
        archive_path: Path,
        archive_root: str,
    ) -> None:
        """Write a tar.xz with root archive_root/ containing the given paths. Meson extracts via shutil.unpack_archive()."""
        with tarfile.open(archive_path, 'w:xz') as tf:
            for rel_path in paths:
                if self.include_uncommitted:
                    full = source_dir / rel_path
                    if not full.is_file():
                        logger.debug(f'Skipping non-file: {rel_path}')
                        continue
                    data = full.read_bytes()
                else:
                    show = self._run_git(['show', f'HEAD:{rel_path}'], cwd=source_dir)
                    if show.returncode != 0:
                        logger.warning(f'Skipping file not at HEAD: {rel_path}')
                        continue
                    data = (show.stdout or '').encode('utf-8')
                arcname = f'{archive_root}/{rel_path}'
                ti = tarfile.TarInfo(name=arcname)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
