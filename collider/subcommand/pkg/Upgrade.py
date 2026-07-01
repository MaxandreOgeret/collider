# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Upgrade project dependencies while preserving declared intent."""

from __future__ import annotations

import argparse
import os
import re

from pathlib import Path
from typing import Optional

import packaging.version

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile, compute_wrap_hash
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository import search_packages
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.core import assert_safe_path_segment
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.meson.project import validate_meson_project_cwd
from collider.utils.packaging import parse_version_constraint
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.types import RepoKey
from collider.utils.project_state import (
    find_collider_dependency,
    load_colliderfile,
    remove_installed_artifacts,
    update_collider_dependency_version,
)


class Upgrade(SubcommandInterface):
    """Upgrade project dependencies while preserving declared intent."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Upgrade one or all project dependencies.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg upgrade --help`."""
        del cls
        return (
            'Upgrade Collider-managed dependencies to the newest versions allowed by '
            'collider.json.\n'
            'When a package name is provided, `--version` can replace its declared '
            'constraint before upgrading. Use `collider lock` afterwards to refresh '
            'collider.lock explicitly.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return (
            '  ‣ collider pkg upgrade\n'
            '  ‣ collider pkg upgrade fmt\n'
            '  ‣ collider pkg upgrade fmt --version ">=10,<11"\n'
        )

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            'package',
            nargs='?',
            type=str,
            help='Optional package name. When omitted, upgrade all Collider-managed dependencies.',
        )
        parser.add_argument(
            '--version',
            '-v',
            type=parse_version_constraint,
            required=False,
            help='Version constraint to persist before upgrading the selected package '
            '(a bare version like 1.2.13 is treated as ==1.2.13).',
        )
        parser.add_argument(
            '--offline',
            action='store_true',
            help='Disable network access and rely on local cache.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """Store upgrade arguments and context."""
        super().__init__(args, context)
        self.package_name: Optional[str] = args.package
        self.version_spec: Optional[SpecifierSet] = getattr(args, 'version', None)
        self.offline: bool = bool(args.offline or context.offline)

    @override
    def execute(self) -> int:
        """Run the upgrade command."""
        if not validate_meson_project_cwd():
            return os.EX_NOINPUT
        if self.version_spec is not None and self.package_name is None:
            logger.critical('--version requires a package name for `collider pkg upgrade`.')
            return os.EX_USAGE

        colliderfile = load_colliderfile()
        targets = self._resolve_targets(colliderfile)
        if isinstance(targets, int):
            return targets
        if not targets:
            logger.info('No Collider-managed dependencies declared.')
            return os.EX_OK

        repos = dict(self.context.config.repositories.items())
        for package_name in targets:
            result = self._upgrade_one(colliderfile, package_name, repos)
            if result != os.EX_OK:
                return result
        return os.EX_OK

    def _resolve_targets(self, colliderfile: Colliderfile) -> list[str] | int:
        """Determine which dependencies should be upgraded."""
        if self.package_name is not None:
            dep = find_collider_dependency(colliderfile, self.package_name)
            if dep is None:
                logger.critical(
                    f'Package "{self.package_name}" is not a Collider-managed dependency in this project.'
                )
                return os.EX_NOINPUT
            return [self.package_name]

        return [
            dep.name for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        ]

    def _upgrade_one(
        self,
        colliderfile: Colliderfile,
        package_name: str,
        repos: dict[str, RepositoryInterface],
    ) -> int:
        """Upgrade one dependency in place."""
        dep = find_collider_dependency(colliderfile, package_name)
        assert dep is not None

        version_text = (
            str(self.version_spec)
            if self.package_name == package_name and self.version_spec
            else dep.version
        )
        try:
            version_spec = SpecifierSet(version_text) if version_text is not None else None
        except InvalidSpecifier:
            logger.critical(f'Invalid version constraint "{version_text}" for "{package_name}".')
            return os.EX_DATAERR

        result = self._find_newest_package(package_name, repos, version_spec)
        if result is None:
            constraint_suffix = (
                f' matching version constraint "{version_text}"' if version_text else ''
            )
            logger.critical(f'No package "{package_name}" found{constraint_suffix}.')
            return os.EX_UNAVAILABLE

        entry, _repo_name, repo, repo_key = result
        package = self._fetch_package(entry, repo, repo_key)
        if package is None:
            return os.EX_IOERR

        changed_constraint = update_collider_dependency_version(
            colliderfile, package_name, version_text
        )
        if self._installed_wrap_matches(package_name, package):
            logger.info(f'Package "{package_name}" is already up to date.')
            if changed_constraint:
                logger.info(f'Updated version constraint for "{package_name}" in collider.json.')
            self._warn_if_lockfile_stale(entry, package)
            return os.EX_OK

        remove_installed_artifacts(package_name)
        if not self._install_downloaded_package(entry, package):
            return os.EX_IOERR

        if changed_constraint:
            logger.info(f'Updated version constraint for "{package_name}" in collider.json.')
        self._warn_if_lockfile_stale(entry, package)
        return os.EX_OK

    def _find_newest_package(
        self,
        package_name: str,
        repos: dict[str, RepositoryInterface],
        version_spec: Optional[SpecifierSet] = None,
    ) -> Optional[tuple[RepoPackageEntry, str, RepositoryInterface, RepoKey]]:
        """Resolve the newest package for one dependency."""
        all_matches = search_packages(
            repos,
            re.compile(f'^{re.escape(package_name)}$'),
            version_spec,
        )
        if not all_matches:
            return None

        best: Optional[
            tuple[packaging.version.Version, str, RepoPackageEntry, RepositoryInterface, RepoKey]
        ] = None
        for repo_name, packages in all_matches.items():
            for repo_key, package in packages.items():
                try:
                    candidate_version = packaging.version.parse(package.version)
                except InvalidVersion:
                    logger.warning(
                        f'Skipping package "{package.name}" with invalid version '
                        f'"{package.version}".'
                    )
                    continue

                if best is None or candidate_version > best[0]:
                    best = (candidate_version, repo_name, package, repos[repo_name], repo_key)

        if best is None:
            return None
        return best[2], best[1], best[3], best[4]

    def _fetch_package(
        self,
        entry: RepoPackageEntry,
        repo: RepositoryInterface,
        repo_key: RepoKey,
    ) -> Optional[WrapPackage]:
        """Fetch the chosen package, honoring offline mode."""
        # Names from repository metadata become subproject and cache paths.
        try:
            assert_safe_path_segment(entry.name)
        except ValueError as e:
            logger.critical(str(e))
            return None

        if self.offline and repo.requires_network():
            package = self.context.cache.load_wrap(entry.name, entry.version)
            if package is None:
                logger.critical(f'Package "{entry.name}" not found in cache for offline upgrade.')
                return None
            return package

        fetched = repo.get_package(repo_key)
        if fetched is None or not isinstance(fetched, WrapPackage):
            logger.critical(f'Failed to fetch package "{entry.name}" from repository.')
            return None

        self.context.cache.store_wrap(fetched)
        return fetched

    @staticmethod
    def _installed_wrap_matches(package_name: str, package: WrapPackage) -> bool:
        """Check whether the current wrap file already matches the fetched package."""
        wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{package_name}.wrap'
        return wrap_path.exists() and wrap_path.read_text(encoding='utf-8') == package.wrap_text

    def _install_downloaded_package(self, entry: RepoPackageEntry, package: WrapPackage) -> bool:
        """Write a fetched package into subprojects/."""
        subproject_path = Path.cwd() / SUBPROJECTS_DIR / entry.name
        try:
            self.context.cache.prepare_packagecache(
                package,
                Path.cwd() / SUBPROJECTS_DIR,
                offline=self.offline,
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.critical(str(exc))
            return False

        try:
            package.install_to_subproject(subproject_path)
        except FileExistsError as exc:
            logger.critical(str(exc))
            return False

        logger.info(f'Upgraded "{entry.name}" to version "{package.version}".')
        return True

    @staticmethod
    def _warn_if_lockfile_stale(entry: RepoPackageEntry, package: WrapPackage) -> None:
        """Warn when an existing lockfile no longer matches the upgraded package."""
        lockfile_path = Path.cwd() / Lockfile.get_filename()
        if not lockfile_path.exists():
            return

        try:
            lockfile = Lockfile.from_path(lockfile_path)
        except Exception:
            return
        locked = lockfile.all_packages.get(entry.name)
        actual_hash = compute_wrap_hash(package.wrap_text)

        if locked is None or locked.version != entry.version or locked.wrap_hash != actual_hash:
            logger.warning(
                f'collider.lock was not updated for "{entry.name}"; '
                'run "collider lock" to refresh it.'
            )
