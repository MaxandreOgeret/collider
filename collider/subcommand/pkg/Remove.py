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
from collider.subcommand.pkg.Prune import run_prune
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
    find_collider_dependency,
    load_colliderfile,
    read_lockfile,
    remove_collider_dependency,
    remove_installed_artifacts,
    scan_wraps,
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
            'Use --prune to also remove orphaned transitive dependencies.\n'
            'Use `collider lock` to refresh collider.lock explicitly afterwards.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return (
            '  \u2023 collider pkg remove fmt\n'
            '  \u2023 collider pkg rm fmt\n'
            '  \u2023 collider pkg rm --prune fmt\n'
        )

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('package', type=str, help='Name of the package to remove.')
        parser.add_argument(
            '--prune',
            action='store_true',
            help='Also remove orphaned transitive dependencies.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """Store remove arguments and context."""
        super().__init__(args, context)
        self.package_name: str = args.package
        self.prune: bool = args.prune

    @override
    def execute(self) -> int:
        """Run the remove command."""
        if not validate_meson_project_cwd():
            return os.EX_NOINPUT

        lockfile_path = Path.cwd() / Lockfile.get_filename()
        prune_was_skipped = self.prune and not lockfile_path.exists()

        colliderfile = load_colliderfile()
        if find_collider_dependency(colliderfile, self.package_name) is None:
            logger.critical(
                f'Package "{self.package_name}" is not a Collider-managed dependency in this project.'
            )
            return os.EX_NOINPUT

        needed_packages = self._resolve_remaining_needed_packages(colliderfile)
        still_needed = self._package_still_needed(colliderfile, needed_packages)
        removed_dependency = remove_collider_dependency(colliderfile, self.package_name)
        removed_artifacts = False
        if still_needed is True:
            logger.info(
                f'Kept installed wrap state for "{self.package_name}" because '
                'other dependencies still require it.'
            )
        elif still_needed is None:
            logger.warning(
                f'Could not determine whether "{self.package_name}" is still needed by '
                'other dependencies; leaving installed wrap state in place.'
            )
        else:
            removed_artifacts = remove_installed_artifacts(self.package_name)

        if not removed_dependency:
            logger.critical(
                f'Package "{self.package_name}" is not a Collider-managed dependency in this project.'
            )
            return os.EX_NOINPUT

        if removed_dependency:
            logger.info(f'Removed "{self.package_name}" from collider.json.')
        if removed_artifacts:
            logger.info(f'Removed installed wrap state for "{self.package_name}".')

        if self.prune:
            run_prune(self.context)
        elif still_needed is not True:
            self._inform_about_remaining_wraps(
                colliderfile,
                known_needed_names=needed_packages,
                preserved_needed_name=self.package_name if still_needed is True else None,
            )

        warn_if_lockfile_needs_refresh(self.package_name)
        if prune_was_skipped:
            logger.info(
                'prune skipped: no lockfile; run "collider lock" to create ownership metadata.'
            )
        return os.EX_OK

    def _resolve_remaining_needed_packages(self, colliderfile: Colliderfile) -> Optional[set[str]]:
        """
        Resolve the package set needed by the remaining direct dependencies.

        :return: Needed package names when resolution succeeds, or None if not safely known.
        """
        remaining_deps = [
            dep
            for dep in colliderfile.dependencies
            if dep.source == DependencySource.COLLIDER and dep.name != self.package_name
        ]
        if not remaining_deps:
            return set()

        repos = dict(self.context.config.repositories.items())
        dep_name_index = build_dep_name_index(repos)
        if not dep_name_index:
            return None

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
                include_conditional=True,
                exclude_optional=False,
            )
        except (
            resolvelib.RequirementsConflicted,
            resolvelib.ResolutionImpossible,
            resolvelib.ResolutionTooDeep,
            Exception,
        ):
            return None

        return set(resolution.mapping.keys())

    def _package_still_needed(
        self, colliderfile: Colliderfile, needed_packages: Optional[set[str]] = None
    ) -> bool | None:
        """
        Determine whether the removed package is still required transitively.

        :return: True if still needed, False if not, None if it cannot be determined safely.
        """
        if needed_packages is None:
            needed_packages = self._resolve_remaining_needed_packages(colliderfile)
        if needed_packages is not None:
            return self.package_name in needed_packages

        return None

    @staticmethod
    def _inform_about_remaining_wraps(
        colliderfile: Colliderfile,
        known_needed_names: Optional[set[str]] = None,
        preserved_needed_name: Optional[str] = None,
    ) -> None:
        """Surface a follow-up hint when wraps still remain after direct removal."""
        remaining_wraps = set(scan_wraps(Path.cwd() / SUBPROJECTS_DIR))
        if not remaining_wraps:
            return

        direct_names = {
            dep.name for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        }
        extra_wraps = remaining_wraps - direct_names
        if known_needed_names is not None:
            extra_wraps -= known_needed_names - direct_names
        if preserved_needed_name is not None:
            extra_wraps.discard(preserved_needed_name)
        if not extra_wraps:
            return

        lockfile_path = Path.cwd() / Lockfile.get_filename()
        if not lockfile_path.exists():
            logger.info(
                'Additional wraps remain in subprojects/. Collider cannot safely '
                'determine which are orphaned without existing ownership metadata. '
                'Remove them manually.'
            )
            return

        lockfile = read_lockfile(lockfile_path)
        if lockfile is None:
            logger.info(
                'Additional wraps remain in subprojects/. Collider cannot safely '
                'determine which are orphaned without existing ownership metadata. '
                'Remove them manually.'
            )
            return

        managed_transitives = extra_wraps & set(lockfile.all_packages.keys())
        if not managed_transitives:
            return

        logger.info(
            'Transitive wraps were left in place. Run "collider pkg prune" to remove '
            'orphaned Collider-managed dependencies.'
        )
