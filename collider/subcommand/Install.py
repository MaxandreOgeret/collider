# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Install all dependencies from lockfile or collider.json."""

from __future__ import annotations

import argparse
import os
import re

from pathlib import Path
from typing import Optional

import packaging.version
import resolvelib

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository import search_packages
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.meson.project import validate_meson_project_cwd
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from collider.utils.packaging.resolver import (
    RootSpec,
    build_dep_name_index,
    resolve_all_dependencies,
)


class Install(SubcommandInterface):
    """Install all dependencies from lockfile or collider.json."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Install project dependencies.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider install --help`."""
        del cls
        return (
            'Install dependencies declared in collider.json, preferring collider.lock when '
            'present.\nUse `collider lock` to write collider.lock explicitly. '
            'Use --frozen to enforce lockfile-only installs and fail on lock drift.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            '--offline',
            action='store_true',
            help='Disable network access and rely on local cache.',
        )
        parser.add_argument(
            '--frozen',
            action='store_true',
            help='Refuse to modify the lockfile; fail if lock is missing or stale.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store install arguments and context.
        :param args: Parsed CLI arguments.
        :param context: Application context.
        """
        super().__init__(args, context)
        self.offline: bool = bool(getattr(args, 'offline', False) or context.offline)
        self.frozen: bool = bool(getattr(args, 'frozen', False))

    @override
    def execute(self) -> int:
        """
        Run the install command.
        :return: Exit code.
        """
        if not validate_meson_project_cwd():
            return os.EX_NOINPUT

        colliderfile_path = Path.cwd() / Colliderfile.get_filename()
        colliderfile = Colliderfile.from_path(colliderfile_path)

        lockfile_path = Path.cwd() / Lockfile.get_filename()
        has_lockfile = lockfile_path.exists()

        if self.frozen and not has_lockfile:
            logger.critical('Frozen mode requires a lockfile but none was found.')
            return os.EX_NOINPUT

        if has_lockfile:
            lockfile = Lockfile.from_path(lockfile_path)
            constraints_valid, lockfile_clean = self._check_incompatibilities(
                colliderfile, lockfile
            )
            if not constraints_valid:
                return os.EX_DATAERR
            if self.frozen and not lockfile_clean:
                logger.critical('Frozen mode requires collider.lock to match collider.json.')
                return os.EX_DATAERR
            return self._restore_from_lockfile(lockfile)

        return self._resolve_from_colliderfile(colliderfile)

    def _check_incompatibilities(
        self, colliderfile: Colliderfile, lockfile: Lockfile
    ) -> tuple[bool, bool]:
        """
        Warn about mismatches between collider.json and collider.lock.
        :param colliderfile: Declared dependencies.
        :param lockfile: Pinned resolution state.
        :return: Tuple of (constraints_valid, lockfile_clean).
        """
        collider_deps = {
            dep.name: dep
            for dep in colliderfile.dependencies
            if dep.source == DependencySource.COLLIDER
        }
        locked_names = set(lockfile.dependencies.keys())
        declared_names = set(collider_deps.keys())
        lockfile_clean = True

        for name in locked_names - declared_names:
            logger.warning(f'Locked dependency "{name}" is not declared in collider.json.')
            lockfile_clean = False

        for name in declared_names - locked_names:
            logger.warning(
                f'Declared dependency "{name}" has no lock entry; run "collider pkg add {name}".'
            )
            lockfile_clean = False

        for name in declared_names & locked_names:
            dep = collider_deps[name]
            locked = lockfile.dependencies[name]
            if dep.version is None:
                continue
            try:
                spec = SpecifierSet(dep.version)
                if not spec.contains(locked.version):
                    logger.warning(
                        f'Locked version "{locked.version}" for "{name}" does not satisfy '
                        f'declared constraint "{dep.version}".'
                    )
                    lockfile_clean = False
            except InvalidSpecifier:
                logger.critical(f'Invalid version constraint "{dep.version}" for "{name}".')
                return False, False

        return True, lockfile_clean

    def _restore_from_lockfile(self, lockfile: Lockfile) -> int:
        """
        Re-install every package from the lockfile.
        :param lockfile: Pinned resolution state.
        :return: Exit code.
        """
        repos = dict(self.context.config.repositories.items())

        for name, locked in lockfile.all_packages.items():
            if self._is_already_installed(name, locked):
                logger.info(f'Package "{name}" is up to date.')
                continue

            package = self._fetch_locked_package(name, locked, repos)
            if package is None:
                return os.EX_UNAVAILABLE

            actual_hash = compute_wrap_hash(package.wrap_text)
            if actual_hash != locked.wrap_hash:
                logger.critical(
                    f'Wrap hash mismatch for "{name}": '
                    f'expected {locked.wrap_hash}, got {actual_hash}.'
                )
                if self.frozen:
                    return os.EX_DATAERR
                logger.warning('Proceeding despite hash mismatch (not frozen).')

            if not self._do_install(name, package):
                return os.EX_IOERR

        return os.EX_OK

    def _resolve_from_colliderfile(self, colliderfile: Colliderfile) -> int:
        """
        Resolve and install dependencies (including transitive) from collider.json.
        :param colliderfile: Declared dependencies.
        :return: Exit code.
        """
        repos = dict(self.context.config.repositories.items())

        collider_deps = [
            dep for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        ]
        if not collider_deps:
            logger.info('No collider dependencies declared.')
            return os.EX_OK

        for dep in collider_deps:
            _, error_code = self._parse_version_spec(dep.name, dep.version)
            if error_code is not None:
                return error_code

        dep_name_index = build_dep_name_index(repos)
        use_transitive = bool(dep_name_index)

        if use_transitive:
            root_specs = [
                RootSpec(
                    name=dep.name,
                    version_spec=dep.version,
                    include_names=set(dep.include) if dep.include else None,
                    exclude_names=set(dep.exclude) if dep.exclude else None,
                )
                for dep in collider_deps
            ]

            try:
                resolution = resolve_all_dependencies(
                    roots=root_specs,
                    repos=repos,
                    offline=self.offline,
                    wrap_cache=self.context.cache,
                    include_conditional=any(dep.include_conditional for dep in collider_deps),
                    exclude_optional=any(dep.exclude_optional for dep in collider_deps),
                )
            except (
                resolvelib.RequirementsConflicted,
                resolvelib.ResolutionImpossible,
                resolvelib.ResolutionTooDeep,
            ) as e:
                logger.critical(f'Dependency resolution failed: {e}')
                return os.EX_UNAVAILABLE

            for pkg_name, candidate in resolution.mapping.items():
                repo = repos.get(candidate.repo_name)
                if repo is None:
                    logger.warning(
                        f'Repository "{candidate.repo_name}" unavailable for "{pkg_name}".'
                    )
                    continue

                repo_key = make_repo_key(pkg_name, candidate.version, PackageType.WRAP)
                entry = RepoPackageEntry(pkg_name, candidate.version, PackageType.WRAP)
                package = self._fetch_package(entry, repo, repo_key)
                if package is None:
                    return os.EX_IOERR

                if not self._do_install(pkg_name, package):
                    return os.EX_IOERR
        else:
            for dep in collider_deps:
                version_spec, _ = self._parse_version_spec(dep.name, dep.version)
                result = self._resolve_newest(dep.name, repos, version_spec)
                if result is None:
                    constraint_suffix = (
                        f' satisfying "{dep.version}"' if dep.version is not None else ''
                    )
                    logger.critical(f'No package matching "{dep.name}"{constraint_suffix}.')
                    return os.EX_UNAVAILABLE

                entry, _repo_name, repo, repo_key = result
                package = self._fetch_package(entry, repo, repo_key)
                if package is None:
                    return os.EX_IOERR

                if not self._do_install(entry.name, package):
                    return os.EX_IOERR

        return os.EX_OK

    @staticmethod
    def _parse_version_spec(
        package_name: str,
        version_text: Optional[str],
    ) -> tuple[Optional[SpecifierSet], Optional[int]]:
        """
        Parse a declared version constraint.
        :param package_name: Package being resolved.
        :param version_text: Raw version constraint from collider.json.
        :return: Parsed specifier and optional exit code on failure.
        """
        if version_text is None:
            return None, None

        try:
            return SpecifierSet(version_text), None
        except InvalidSpecifier:
            logger.critical(f'Invalid version constraint "{version_text}" for "{package_name}".')
            return None, os.EX_DATAERR

    def _is_already_installed(self, name: str, locked: LockedPackage) -> bool:
        """
        Check whether the installed wrap matches the locked hash.
        :param name: Package name.
        :param locked: Locked package entry.
        :return: True if the installed wrap matches.
        """
        wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{name}.wrap'
        if not wrap_path.exists():
            return False
        installed_text = wrap_path.read_text(encoding='utf-8')
        return compute_wrap_hash(installed_text) == locked.wrap_hash

    def _fetch_locked_package(
        self,
        name: str,
        locked: LockedPackage,
        repos: dict[str, RepositoryInterface],
    ) -> Optional[WrapPackage]:
        """
        Fetch a package by name+version from any configured repository.
        :param name: Package name.
        :param locked: Locked entry with version.
        :param repos: Available repositories.
        :return: Fetched WrapPackage or None.
        """
        repo_key = make_repo_key(name, locked.version, PackageType.WRAP)

        for _repo_name, repo in repos.items():
            if self.offline and repo.requires_network():
                continue
            package = repo.get_package(repo_key)
            if package is not None and isinstance(package, WrapPackage):
                self.context.cache.store_wrap(package)
                return package

        package = self.context.cache.load_wrap(name, locked.version)
        if package is not None:
            logger.info(f'Using cached wrap for "{name}".')
            return package

        if self.offline:
            logger.critical(f'Package "{name}" not found in cache for offline install.')
        else:
            logger.critical(
                f'Package "{name}" version "{locked.version}" not found in any repository.'
            )
        return None

    def _resolve_newest(
        self,
        package_name: str,
        repos: dict[str, RepositoryInterface],
        version_spec: Optional[SpecifierSet] = None,
    ) -> Optional[tuple[RepoPackageEntry, str, RepositoryInterface, str]]:
        """
        Find the newest version of a package across repositories.
        :param package_name: Name of the package to resolve.
        :param repos: Available repositories.
        :return: Tuple of (entry, repo_name, repo, repo_key) or None.
        """
        all_matches = search_packages(
            repos,
            re.compile(f'^{re.escape(package_name)}$'),
            version_spec,
        )
        if not all_matches:
            return None

        best: Optional[
            tuple[packaging.version.Version, RepoPackageEntry, str, RepositoryInterface, str]
        ] = None
        for rname, packages in all_matches.items():
            for rkey, pkg in packages.items():
                try:
                    parsed = packaging.version.parse(pkg.version)
                except InvalidVersion:
                    continue
                if best is None or parsed > best[0]:
                    best = (parsed, pkg, rname, repos[rname], rkey)

        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def _fetch_package(
        self,
        entry: RepoPackageEntry,
        repo: RepositoryInterface,
        repo_key: str,
    ) -> Optional[WrapPackage]:
        """
        Fetch a package from a repository.
        :param entry: Package entry.
        :param repo: Repository to fetch from.
        :param repo_key: Encoded repo key.
        :return: WrapPackage on success, None on failure.
        """
        if self.offline and repo.requires_network():
            package = self.context.cache.load_wrap(entry.name, entry.version)
            if package is None:
                logger.critical(f'Package "{entry.name}" not found in cache for offline install.')
                return None
            return package

        fetched = repo.get_package(repo_key)
        if fetched is None or not isinstance(fetched, WrapPackage):
            logger.critical(f'Failed to fetch package "{entry.name}" from repository.')
            return None
        self.context.cache.store_wrap(fetched)
        return fetched

    def _do_install(self, name: str, package: WrapPackage) -> bool:
        """
        Write the wrap file and populate the package cache.
        :param name: Package name.
        :param package: WrapPackage to install.
        :return: True on success.
        """
        subproject_path = Path.cwd() / SUBPROJECTS_DIR / name
        if subproject_path.exists():
            logger.critical(f'Subproject directory "{subproject_path}" already exists.')
            return False

        try:
            self.context.cache.prepare_packagecache(
                package,
                Path.cwd() / SUBPROJECTS_DIR,
                offline=self.offline,
            )
        except (FileNotFoundError, ValueError) as e:
            logger.critical(str(e))
            return False

        try:
            package.install_to_subproject(subproject_path)
        except FileExistsError as exc:
            logger.critical(str(exc))
            return False

        logger.info(f'Installed "{name}" version "{package.version}".')
        return True
