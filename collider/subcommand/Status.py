# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Report which wraps are tracked by collider in the current project."""

from __future__ import annotations

import argparse
import os

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile, compute_wrap_hash
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.resolver import (
    RootSpec,
    build_dep_name_index,
    resolve_all_dependencies,
)


class Status(SubcommandInterface):
    """Report which wraps are tracked by collider in the current project."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Show dependency and wrap status.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider status --help`."""
        del cls
        return (
            'Show collider-tracked, system, and untracked wraps in the current project.\n'
            'If collider.lock exists, also report whether local wraps match locked hashes.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        # Status has no arguments; keeping a stub keeps CLI discovery uniform.
        del parser

    @override
    def execute(self) -> int:
        """Run the status command.
        :return: Exit code.
        """
        if not self._validate_cwd():
            return os.EX_NOINPUT

        colliderfile_path = Path.cwd() / Colliderfile.get_filename()
        colliderfile = Colliderfile.from_path(colliderfile_path)

        tracked = [
            dep for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        ]
        system_deps = [
            dep for dep in colliderfile.dependencies if dep.source == DependencySource.SYSTEM
        ]

        subprojects_dir = Path.cwd() / SUBPROJECTS_DIR
        wrap_names = self._scan_wraps(subprojects_dir)
        tracked_names = {dep.name for dep in tracked}

        lockfile_path = Path.cwd() / Lockfile.get_filename()
        lockfile = Lockfile.from_path(lockfile_path) if lockfile_path.exists() else None

        resolved_versions = self._resolve_versions(tracked, lockfile)
        transitive_names = sorted(
            name for name in wrap_names if name not in tracked_names and name in resolved_versions
        )
        untracked = sorted(
            name
            for name in wrap_names
            if name not in tracked_names and name not in resolved_versions
        )

        logger.info('‣ tracked')
        if tracked:
            for dep in sorted(tracked, key=lambda item: item.name):
                constraint = dep.version or 'any'
                wrap_path = subprojects_dir / f'{dep.name}.wrap'
                status = 'installed' if wrap_path.exists() else 'missing'
                installed_ver = resolved_versions.get(dep.name, '')
                if installed_ver:
                    version_label = f'{constraint} -> {installed_ver}'
                else:
                    version_label = constraint
                logger.info(f'  ‣ {dep.name} ({version_label}) [{status}]')
        else:
            logger.info('  ‣ none')

        if transitive_names:
            logger.info('')
            logger.info('‣ transitive')
            for name in transitive_names:
                wrap_path = subprojects_dir / f'{name}.wrap'
                status = 'installed' if wrap_path.exists() else 'missing'
                version = resolved_versions.get(name, '')
                version_label = f' ({version})' if version else ''
                logger.info(f'  ‣ {name}{version_label} [{status}]')

        if system_deps:
            logger.info('')
            logger.info('‣ system')
            for dep in sorted(system_deps, key=lambda item: item.name):
                logger.info(f'  ‣ {dep.name}')

        if untracked:
            logger.info('')
            logger.info('‣ untracked')
            for name in untracked:
                logger.info(f'  ‣ {name}')

        if lockfile is not None:
            self._report_lock_drift(lockfile, subprojects_dir)

        return os.EX_OK

    def _resolve_versions(
        self,
        tracked: list[Dependency],
        lockfile: Optional[Lockfile],
    ) -> dict[str, str]:
        """
        Resolve installed versions for all known packages (direct + transitive).
        Uses the lockfile when available, otherwise re-resolves from collider.json.
        :param tracked: Direct collider-managed dependencies.
        :param lockfile: Loaded lockfile, or None.
        :return: Mapping of package names to their resolved versions.
        """
        if lockfile is not None:
            return {name: pkg.version for name, pkg in lockfile.all_packages.items()}

        if not tracked:
            return {}

        try:
            repos = dict(self.context.config.repositories.items())
            dep_name_index = build_dep_name_index(repos)
            if not dep_name_index:
                return {}

            root_specs = [
                RootSpec(
                    name=dep.name,
                    version_spec=dep.version,
                    include_names=set(dep.include) if dep.include else None,
                    exclude_names=set(dep.exclude) if dep.exclude else None,
                )
                for dep in tracked
            ]

            resolution = resolve_all_dependencies(
                roots=root_specs,
                repos=repos,
                offline=bool(self.context.offline),
                wrap_cache=self.context.cache,
                include_conditional=any(dep.include_conditional for dep in tracked),
                exclude_optional=any(dep.exclude_optional for dep in tracked),
            )
            return {name: candidate.version for name, candidate in resolution.mapping.items()}
        except Exception:
            return {}

    @staticmethod
    def _scan_wraps(subprojects_dir: Path) -> list[str]:
        if not subprojects_dir.exists():
            return []
        return [path.stem for path in subprojects_dir.glob('*.wrap') if path.is_file()]

    @staticmethod
    def _report_lock_drift(lockfile: Lockfile, subprojects_dir: Path) -> None:
        """
        Compare installed wraps against locked hashes.
        :param lockfile: Loaded lockfile.
        :param subprojects_dir: Path to the subprojects directory.
        """
        logger.info('')
        logger.info('‣ lockfile')
        all_pkgs = lockfile.all_packages
        for name in sorted(all_pkgs):
            locked = all_pkgs[name]
            wrap_path = subprojects_dir / f'{name}.wrap'
            if not wrap_path.exists():
                logger.info(f'  ‣ {name} ({locked.version}) [missing]')
                continue
            installed_text = wrap_path.read_text(encoding='utf-8')
            if compute_wrap_hash(installed_text) == locked.wrap_hash:
                logger.info(f'  ‣ {name} ({locked.version}) [ok]')
            else:
                logger.info(f'  ‣ {name} ({locked.version}) [modified]')

    @staticmethod
    def _validate_cwd() -> bool:
        if not Path.cwd().joinpath('meson.build').exists():
            logger.critical('No meson.build file found in current directory.')
            return False

        if not Path.cwd().joinpath(Colliderfile.get_filename()).exists():
            logger.critical('No colliderfile found in current directory.')
            return False

        return True
