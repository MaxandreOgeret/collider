# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Search wraps across configured repositories."""

from __future__ import annotations

import argparse
import os
import re

from typing import Optional

import packaging.version

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion

from collider.Context import Context
from collider.log import logger
from collider.repository.entries import RepoPackageEntry
from collider.repository.repository import search_packages
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.packaging import parse_version_constraint
from collider.utils.repository_selection import add_repository_filter_argument, resolve_repositories


def parse_search_version(text: str) -> SpecifierSet:
    """
    Argparse type for `search --version`: a bare version becomes a prefix match.
    A named wrapper (rather than functools.partial) keeps argparse's error message clean.
    :param text: Raw version constraint.
    :return: Parsed specifier set, with a bare version widened to `==X.*`.
    """
    return parse_version_constraint(text, prefix=True)


class Search(SubcommandInterface):
    """Search wraps across configured repositories."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Search packages in configured repositories.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg search --help`."""
        del cls
        return (
            'Search repository indexes, or local cache, for package names and versions.\n'
            'Use regex patterns and repository filters to narrow the results.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            '--cache',
            action='store_true',
            help='Search only cached wraps without accessing repositories.',
        )
        add_repository_filter_argument(parser)

        parser.add_argument(
            '--version',
            '-v',
            type=parse_search_version,
            required=False,
            help='Version pattern to search for (PEP 440 specifiers; a bare version like '
            '1.2.13 matches every 1.2.13.* revision, including 1.2.13-1).',
        )

        parser.add_argument(
            'pattern',
            type=str,
            help='Regex pattern to search for. Use ".*" to match all.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store search arguments and context.
        :param args: Parsed CLI arguments (pattern, repository filter, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)

        self.repository_names: Optional[list[str]] = args.repository
        self.version_pattern: Optional[SpecifierSet] = args.version
        self.cache_only: bool = bool(args.cache)

        try:
            self.name_pattern: re.Pattern = re.compile(args.pattern)
        except re.error as e:
            logger.critical(f'Invalid regex pattern: {e}')
            raise e

    @override
    def execute(self) -> int:
        """Run the search command.
        :return: Exit code.
        """
        logger.debug(
            f'Search pattern="{self.name_pattern.pattern}" '
            f'version="{self.version_pattern or "any"}".'
        )
        if self.cache_only:
            cached_matches = self._search_cache()
            if not cached_matches:
                return os.EX_UNAVAILABLE
            logger.info('')
            logger.info('‣ cache')
            for match in cached_matches:
                logger.info(f'  ‣ {match.name} ({match.version}) [cached]')
            return os.EX_OK

        repos_to_search = resolve_repositories(self.context, self.repository_names)
        if repos_to_search is None:
            return os.EX_NOINPUT

        logger.debug(f'Searching {len(repos_to_search)} repositories.')
        all_matches = search_packages(repos_to_search, self.name_pattern, self.version_pattern)

        if not all_matches:
            return os.EX_UNAVAILABLE

        for repo_name, matches in all_matches.items():
            logger.info('')
            logger.info(f'‣ {repo_name}')
            for match in matches.values():
                cached = self.context.cache.has_package(match.name, match.version)
                suffix = ' [cached]' if cached else ''
                logger.info(f'  ‣ {match.name} ({match.version}){suffix}')

        return os.EX_OK

    def _search_cache(self) -> list[RepoPackageEntry]:
        cached_entries: list[RepoPackageEntry] = []
        for name, version in self.context.cache.list_cached_wraps():
            if not self.name_pattern.match(name):
                continue
            if self.version_pattern is not None:
                try:
                    if not self.version_pattern.contains(version):
                        continue
                except InvalidVersion:
                    logger.warning(
                        f'Skipping cached wrap "{name}" with invalid version "{version}".'
                    )
                    continue
            if not self.context.cache.has_package(name, version):
                continue
            cached_entries.append(RepoPackageEntry(name, version))

        return self._sort_cache_entries(cached_entries)

    @staticmethod
    def _sort_cache_entries(entries: list[RepoPackageEntry]) -> list[RepoPackageEntry]:
        def _key(entry: RepoPackageEntry) -> tuple[str, packaging.version.Version | str]:
            try:
                parsed = packaging.version.parse(entry.version)
            except InvalidVersion:
                parsed = entry.version
            return (entry.name, parsed)

        return sorted(entries, key=_key, reverse=False)
