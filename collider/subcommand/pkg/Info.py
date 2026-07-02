# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Report available versions, cache status, and origins for a package."""

from __future__ import annotations

import argparse
import os
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import packaging.version

from packaging.version import InvalidVersion

from collider.Context import Context
from collider.errors import ColliderUserError
from collider.file_model.colliderfile import Colliderfile
from collider.log import logger
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.repository.implementation.Wrap import Wrap
from collider.repository.repository import search_packages
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.packaging.Dependency import DependencySource
from collider.utils.packaging.types import RepoKey
from collider.utils.repository_selection import add_repository_filter_argument, resolve_repositories


@dataclass(frozen=True)
class PolicyVersionEntry:
    """Version metadata for policy output."""

    version: str
    repo_name: str
    origin: Optional[str]
    cached: bool
    provides: Optional[list[str]]


class Info(SubcommandInterface):
    """Show package versions, origins, and cache status."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Show package versions, origins, and cache status.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg info --help`."""
        del cls
        return (
            'Show installed, declared, and candidate versions for a package.\n'
            'Also report cache availability and repository origin for each version.'
        )

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('package', type=str, help='Name of the package to inspect.')
        add_repository_filter_argument(parser)

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store info arguments and context.
        :param args: Parsed CLI arguments (package name, repository filter, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.package_name: str = args.package
        self.repository_names: Optional[list[str]] = args.repository

    @override
    def execute(self) -> int:
        """Run the info command.
        :return: Exit code.
        """
        repos_to_search = resolve_repositories(self.context, self.repository_names)
        if repos_to_search is None:
            return os.EX_NOINPUT

        name_pattern = re.compile(f'^{re.escape(self.package_name)}$')
        all_matches = search_packages(repos_to_search, name_pattern)
        if not all_matches:
            logger.critical('No package matching query.')
            return os.EX_UNAVAILABLE

        versions = self._collect_versions(all_matches, repos_to_search)
        sorted_versions = self._sort_versions(versions)
        candidate = sorted_versions[0] if sorted_versions else None

        installed_wrap_text = self._read_installed_wrap_text()
        installed = self._resolve_installed(installed_wrap_text)
        declared = self._resolve_declared()
        logger.info(f'{self.package_name}:')
        logger.info(f'  Installed: {installed}')
        logger.info(f'  Declared: {declared}')
        if candidate is None:
            logger.info('  Candidate: none')
        else:
            logger.info(f'  Candidate: {candidate.version} ({candidate.repo_name})')

        logger.info('  Versions:')
        for entry in sorted_versions:
            cached_suffix = ' [cached]' if entry.cached else ''
            origin_suffix = f' ({entry.origin})' if entry.origin else ''
            provides_suffix = ''
            if entry.provides is not None:
                provides_display = ', '.join(entry.provides) if entry.provides else 'none'
                provides_suffix = f' provides: {provides_display}'
            logger.info(
                f'    ‣ {entry.version}  {entry.repo_name}'
                f'{origin_suffix}{cached_suffix}{provides_suffix}'
            )

        return os.EX_OK

    def _collect_versions(
        self,
        all_matches: Mapping[str, Mapping[RepoKey, RepoPackageEntry]],
        repos: Mapping[str, RepositoryInterface],
    ) -> list[PolicyVersionEntry]:
        versions: list[PolicyVersionEntry] = []
        for repo_name, matches in all_matches.items():
            repo = repos[repo_name]
            origin = self._repo_origin(repo)
            for match in matches.values():
                cached_wrap = self.context.cache.load_wrap(match.name, match.version)
                cached = False
                if cached_wrap is not None:
                    cached = self.context.cache.is_fully_cached(cached_wrap)
                provides = match.dependency_names
                versions.append(
                    PolicyVersionEntry(
                        version=match.version,
                        repo_name=repo_name,
                        origin=origin,
                        cached=cached,
                        provides=provides,
                    )
                )
        return versions

    @staticmethod
    def _sort_versions(entries: list[PolicyVersionEntry]) -> list[PolicyVersionEntry]:
        valid: list[tuple[packaging.version.Version, PolicyVersionEntry]] = []
        invalid: list[PolicyVersionEntry] = []
        for entry in entries:
            try:
                parsed = packaging.version.parse(entry.version)
                if isinstance(parsed, packaging.version.Version):
                    valid.append((parsed, entry))
                else:
                    invalid.append(entry)
            except InvalidVersion:
                invalid.append(entry)

        valid.sort(key=lambda item: item[0], reverse=True)
        invalid.sort(key=lambda item: item.version, reverse=True)
        return [entry for _, entry in valid] + invalid

    def _read_installed_wrap_text(self) -> Optional[str]:
        wrap_path = Path.cwd() / 'subprojects' / f'{self.package_name}.wrap'
        if not wrap_path.exists():
            return None
        try:
            return wrap_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            logger.critical(msg := f'Cannot read wrap file "{wrap_path}": {exc}')
            raise ColliderUserError(msg, os.EX_IOERR) from exc

    def _resolve_installed(self, wrap_text: Optional[str]) -> str:
        if wrap_text is None:
            return 'none'

        versions = self.context.cache.find_wrap_versions(self.package_name, wrap_text)
        if len(versions) == 1:
            version = versions[0]
            cached = self.context.cache.has_package(self.package_name, version)
            cached_suffix = ' [cached]' if cached else ''
            return f'{version}{cached_suffix}'
        if not versions:
            return 'unknown (wrap not in cache)'
        return f'unknown (multiple cached matches: {", ".join(versions)})'

    def _resolve_declared(self) -> str:
        colliderfile_path = Path.cwd() / Colliderfile.get_filename()
        if not colliderfile_path.exists():
            return 'none'

        colliderfile = Colliderfile.from_path(colliderfile_path)
        for dep in colliderfile.dependencies:
            if dep.name != self.package_name:
                continue
            if dep.source == DependencySource.COLLIDER:
                return dep.version or 'any'
            if dep.source == DependencySource.SYSTEM:
                return 'system'

        return 'none'

    @staticmethod
    def _repo_origin(repo: RepositoryInterface) -> Optional[str]:
        if isinstance(repo, Wrap):
            return repo.url.geturl()
        if isinstance(repo, Filesystem):
            return repo.publish_url or repo.path.as_uri()
        return None
