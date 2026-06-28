# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Remove orphaned Collider-managed transitive wraps from the project."""

from __future__ import annotations

import argparse
import os

from pathlib import Path
from typing import Optional

import resolvelib

from collider.Context import Context
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
    remove_installed_artifacts,
    scan_wraps,
)


class Prune(SubcommandInterface):
    """Remove orphaned Collider-managed transitive wraps from the project."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Remove orphaned transitive dependencies.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg prune --help`."""
        del cls
        return (
            'Scan the project for transitive wraps that are no longer needed by '
            'any declared Collider dependency and remove them.\n'
            'Only wraps proven to be Collider-managed (via collider.lock) are '
            'eligible for removal; manually placed wraps are never touched.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return '  \u2023 collider pkg prune\n  \u2023 collider pkg prune --dry-run\n'

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List orphaned wraps without removing them.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """Store prune arguments and context."""
        super().__init__(args, context)
        self.dry_run: bool = args.dry_run

    @override
    def execute(self) -> int:
        """Run the prune command."""
        return run_prune(self.context, dry_run=self.dry_run)


def run_prune(context: Context, dry_run: bool = False) -> int:
    """
    Remove orphaned Collider-managed wraps from the project.

    Callable directly from ``Prune.execute()`` and from
    ``Remove.execute()`` when the ``--prune`` flag is passed.

    :param context: Application context (config, cache, offline).
    :param dry_run: When True, list orphans without deleting.
    :return: Process exit code.
    """
    if not validate_meson_project_cwd():
        return os.EX_NOINPUT

    colliderfile = load_colliderfile()

    lockfile_path = Path.cwd() / Lockfile.get_filename()
    if not lockfile_path.exists():
        logger.warning(
            'No lockfile found. Collider cannot safely determine which transitive wraps '
            'are orphaned without existing ownership metadata.\n'
            'Run "collider lock" to create ownership metadata for future operations; '
            'existing leftover wraps may still need to be removed manually.'
        )
        logger.warning('prune skipped: no lockfile; run "collider lock"')
        return os.EX_OK

    try:
        lockfile = Lockfile.from_path(lockfile_path)
    except Exception:
        logger.warning('collider.lock could not be read. Run "collider lock" to regenerate it.')
        return os.EX_OK

    managed = set(lockfile.all_packages.keys())

    subprojects_dir = Path.cwd() / SUBPROJECTS_DIR
    remaining_wraps = scan_wraps(subprojects_dir)
    if not remaining_wraps:
        return os.EX_OK

    remaining_deps = [
        dep for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
    ]
    direct_names = {dep.name for dep in remaining_deps}

    needed: set[str] = set(direct_names)
    if remaining_deps:
        repos = dict(context.config.repositories.items())
        dep_name_index = build_dep_name_index(repos)
        if not dep_name_index:
            logger.warning(
                'Could not determine orphaned transitive dependencies. '
                'Remove unused wraps manually.'
            )
            return os.EX_OK

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
                offline=bool(context.offline),
                wrap_cache=context.cache,
                include_conditional=True,
                exclude_optional=False,
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
            return os.EX_OK

    orphaned = sorted(name for name in remaining_wraps if name not in needed and name in managed)
    if not orphaned:
        return os.EX_OK

    if dry_run:
        logger.info('Orphaned wraps that would be removed:')
        logger.info(f'  {" ".join(orphaned)}')
        return os.EX_OK

    logger.info('')
    logger.info('Removing unused transitive dependencies:')
    logger.info(f'  {" ".join(orphaned)}')

    for name in orphaned:
        remove_installed_artifacts(name)

    logger.warning(
        'collider.lock still contains pruned packages; run "collider lock" to refresh it.'
    )

    return os.EX_OK
