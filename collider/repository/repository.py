# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Package search and repository registry."""

import re

from functools import cache
from typing import Mapping, Optional, Type

from packaging.specifiers import SpecifierSet

import collider.repository.implementation as repository_implementation

from collider.log import logger
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.core import discover_plugins
from collider.utils.packaging.types import RepoKey
from collider.utils.Registry import Registry


def search_packages(
    repositories: Mapping[str, RepositoryInterface],
    name_pattern: re.Pattern,
    version_spec: Optional[SpecifierSet] = None,
) -> Mapping[str, Mapping[RepoKey, RepoPackageEntry]]:
    """
    Aggregate search results so CLI output stays consistent across repos.
    :param repositories: Map of repository name to interface.
    :param name_pattern: Regex pattern to match package names.
    :param version_spec: Optional version specifier to filter results.
    :return: Map of repo name to (repo_key -> RepoPackageEntry) matches.
    """
    results: dict[str, dict[RepoKey, RepoPackageEntry]] = {}

    for repo_name, repo in repositories.items():
        matches = repo.search(name_pattern, version_spec)
        if matches:
            logger.debug(f'Repository "{repo_name}" returned {len(matches)} matches.')
            results[repo_name] = matches

    return results


class RepoImplRegistry(Registry[RepositoryInterface]):
    """Registry for repository backends discovered at runtime."""

    @classmethod
    @cache
    def get_impls(cls) -> Mapping[str, Type[RepositoryInterface]]:
        """Discover implementations once to keep registry stable."""
        impls = discover_plugins(repository_implementation, RepositoryInterface)  # ty:ignore[invalid-argument-type]

        if not impls:
            raise RuntimeError('No repository implementations found')

        return impls
