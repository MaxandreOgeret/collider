# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Create or refresh collider.lock from collider.json."""

from __future__ import annotations

import argparse
import os

from pathlib import Path
from typing import Optional

import resolvelib

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.log import logger
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.Install import Install as InstallSubcommand
from collider.utils.compat import override
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from collider.utils.packaging.resolver import (
    Candidate,
    MalformedRepositoryMetadata,
    RootSpec,
    build_dep_name_index,
    resolve_all_dependencies,
)
from collider.utils.url import normalize_url


class Lock(InstallSubcommand):
    """Create or refresh collider.lock from collider.json."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Create or refresh collider.lock.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider lock --help`."""
        del cls
        return (
            'Resolve dependencies declared in collider.json and write collider.lock.\n'
            'This is the only command that creates or updates the lockfile.\n'
            'Origin URLs are normalized (lowercased scheme/host, trailing slash stripped) '
            'when writing the lockfile.'
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

    @override
    def execute(self) -> int:
        """
        Resolve declared dependencies and write collider.lock.
        :return: Exit code.
        """
        if not self._validate_cwd():
            return os.EX_NOINPUT

        colliderfile_path = Path.cwd() / Colliderfile.get_filename()
        colliderfile = Colliderfile.from_path(colliderfile_path)

        return self._write_lockfile(colliderfile)

    def _write_lockfile(self, colliderfile: Colliderfile) -> int:
        """
        Resolve dependencies (including transitive) and persist collider.lock.
        :param colliderfile: Declared dependency intent.
        :return: Exit code.
        """
        repos = dict(self.context.config.repositories.items())
        lockfile = Lockfile(path=Path.cwd() / Lockfile.get_filename())

        collider_deps = [
            dep for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        ]

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
                    strict=True,
                    wrap_cache=self.context.cache,
                    include_conditional=any(dep.include_conditional for dep in collider_deps),
                    exclude_optional=any(dep.exclude_optional for dep in collider_deps),
                )
            except MalformedRepositoryMetadata as e:
                return self._reject_malformed_metadata(e, 'lock')
            except (
                resolvelib.RequirementsConflicted,
                resolvelib.ResolutionImpossible,
                resolvelib.ResolutionTooDeep,
            ) as e:
                logger.critical(f'Dependency resolution failed: {e}')
                return os.EX_UNAVAILABLE

            direct_names = {dep.name for dep in collider_deps}
            error = self._lock_resolved_packages(resolution.mapping, direct_names, repos, lockfile)
            if error is not None:
                return error
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

                try:
                    self.context.cache.verify_archives(package, offline=self.offline)
                except (ValueError, FileNotFoundError) as e:
                    logger.critical(f'Archive verification failed for "{entry.name}": {e}')
                    return os.EX_DATAERR

                lockfile.dependencies[entry.name] = LockedPackage.from_wrap_text(
                    entry.version, package.wrap_text, normalize_url(repo.origin_url)
                )

        lockfile.save()
        logger.info('Lockfile written.')
        return os.EX_OK

    def _lock_resolved_packages(
        self,
        mapping: dict[str, Candidate],
        direct_names: set[str],
        repos: dict[str, RepositoryInterface],
        lockfile: Lockfile,
    ) -> Optional[int]:
        """
        Fetch and pin each resolved candidate into the lockfile.
        :param mapping: Resolved package name to selected candidate.
        :param direct_names: Names of directly-declared dependencies.
        :param repos: Configured repositories.
        :param lockfile: Lockfile to populate in place.
        :return: An error exit code on failure, else None.
        """
        for pkg_name, candidate in mapping.items():
            repo = repos.get(candidate.repo_name)
            if repo is None:
                logger.warning(f'Repository "{candidate.repo_name}" unavailable for "{pkg_name}".')
                continue

            repo_key = make_repo_key(pkg_name, candidate.version, PackageType.WRAP)
            entry = RepoPackageEntry(pkg_name, candidate.version, PackageType.WRAP)
            package = self._fetch_package(entry, repo, repo_key)
            if package is None:
                return os.EX_IOERR

            try:
                self.context.cache.verify_archives(package, offline=self.offline)
            except (ValueError, FileNotFoundError) as e:
                logger.critical(f'Archive verification failed for "{pkg_name}": {e}')
                return os.EX_DATAERR

            locked = LockedPackage.from_wrap_text(
                candidate.version, package.wrap_text, normalize_url(repo.origin_url)
            )
            if pkg_name in direct_names:
                lockfile.dependencies[pkg_name] = locked
            else:
                lockfile.packages[pkg_name] = locked
        return None

    @staticmethod
    def _validate_cwd() -> bool:
        project_root = Path.cwd()
        if not project_root.joinpath('meson.build').exists():
            logger.critical('No meson.build file found in current directory.')
            return False
        if not project_root.joinpath(Colliderfile.get_filename()).exists():
            logger.critical('No colliderfile found in current directory.')
            return False
        return True
