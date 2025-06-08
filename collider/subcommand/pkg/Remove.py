# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Remove a project dependency and its installed wrap state."""

from __future__ import annotations

import argparse
import os

from pathlib import Path
from typing import Optional

import resolvelib

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile
from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.meson.project import validate_meson_project_cwd
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.resolver import (
    RootSpec,
    build_dep_name_index,
    resolve_all_dependencies,
)
from collider.utils.project_state import (
    load_colliderfile,
    remove_collider_dependency,
    remove_installed_artifacts,
    warn_if_lockfile_needs_refresh,
)


class Remove(SubcommandInterface):
    """Remove a project dependency and its installed wrap state."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Remove one package from the project.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg remove --help`."""
        del cls
        return (
            'Remove a Collider-managed dependency from collider.json and delete its '
            'installed wrap state from subprojects/.\n'
            'Use `collider lock` to refresh collider.lock explicitly afterwards.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return '  ‣ collider pkg remove fmt\n  ‣ collider pkg rm fmt\n'

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('package', type=str, help='Name of the package to remove.')

    def __init__(self, args: argparse.Namespace, context: Context):
        """Store remove arguments and context."""
        super().__init__(args, context)
        self.package_name: str = args.package

    @override
    def execute(self) -> int:
        """Run the remove command."""
        if not validate_meson_project_cwd():
            return os.EX_NOINPUT

        colliderfile = load_colliderfile()
        removed_dependency = remove_collider_dependency(colliderfile, self.package_name)
        removed_artifacts = remove_installed_artifacts(self.package_name)

        if not removed_dependency and not removed_artifacts:
            logger.critical(
                f'Package "{self.package_name}" is not a Collider-managed dependency in this project.'
            )
            return os.EX_NOINPUT

        if removed_dependency:
            logger.info(f'Removed "{self.package_name}" from collider.json.')
        if removed_artifacts:
            logger.info(f'Removed installed wrap state for "{self.package_name}".')

        self._cleanup_orphaned_transitive(colliderfile)
        warn_if_lockfile_needs_refresh(self.package_name)
        return os.EX_OK

    def _cleanup_orphaned_transitive(self, colliderfile: Colliderfile) -> None:
        """
        Remove transitive wraps that are no longer needed by any remaining dependency.

        Only wraps known to Collider (listed in the lockfile) are considered
        candidates for cleanup. Manually placed wraps are never touched.

        :param colliderfile: Colliderfile after the direct dependency has been removed.
        """
        subprojects_dir = Path.cwd() / SUBPROJECTS_DIR
        remaining_wraps = self._scan_wraps(subprojects_dir)
        if not remaining_wraps:
            return

        collider_managed = self._load_managed_names()
        if not collider_managed:
            logger.warning(
                'No lockfile found; cannot determine orphaned transitive dependencies. '
                'Remove unused wraps manually or run "collider lock" first.'
            )
            return

        remaining_deps = [
            dep for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        ]
        direct_names = {dep.name for dep in remaining_deps}

        needed: set[str] = set(direct_names)
        if remaining_deps:
            repos = dict(self.context.config.repositories.items())
            dep_name_index = build_dep_name_index(repos)
            if not dep_name_index:
                logger.warning(
                    'Could not determine orphaned transitive dependencies. '
                    'Remove unused wraps manually.'
                )
                return

            root_specs = [
                RootSpec(
                    name=dep.name,
                    version_spec=dep.version,
                    include_names=set(dep.include) if dep.include else None,
                    exclude_names=set(dep.exclude) if dep.exclude else None,
                )
                for dep in remaining_deps
            ]

            try:
                resolution = resolve_all_dependencies(
                    roots=root_specs,
                    repos=repos,
                    offline=bool(self.context.offline),
                    wrap_cache=self.context.cache,
                    include_conditional=any(dep.include_conditional for dep in remaining_deps),
                    exclude_optional=any(dep.exclude_optional for dep in remaining_deps),
                )
                needed.update(resolution.mapping.keys())
            except (
                resolvelib.RequirementsConflicted,
                resolvelib.ResolutionImpossible,
                resolvelib.ResolutionTooDeep,
                Exception,
            ):
                logger.warning(
                    'Could not determine orphaned transitive dependencies. '
                    'Remove unused wraps manually.'
                )
                return

        orphaned = sorted(
            name for name in remaining_wraps if name not in needed and name in collider_managed
        )
        if not orphaned:
            return

        logger.info('')
        logger.info('Removing unused transitive dependencies:')
        logger.info(f'  {" ".join(orphaned)}')

        for name in orphaned:
            remove_installed_artifacts(name)

    @staticmethod
    def _load_managed_names() -> set[str]:
        """Return the set of package names Collider manages, from the lockfile."""
        lockfile_path = Path.cwd() / Lockfile.get_filename()
        if not lockfile_path.exists():
            return set()
        try:
            lockfile = Lockfile.from_path(lockfile_path)
            return set(lockfile.all_packages.keys())
        except Exception:
            return set()

    @staticmethod
    def _scan_wraps(subprojects_dir: Path) -> list[str]:
        """Return stem names of all .wrap files in subprojects/."""
        if not subprojects_dir.exists():
            return []
        return [p.stem for p in subprojects_dir.glob('*.wrap') if p.is_file()]
