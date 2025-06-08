# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Repository entry types derived from releases.json."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from collider.utils.meson.infoTypes import WrapDbReleases
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key, parse_repo_key
from collider.utils.packaging.types import RepoKey


@dataclass(frozen=True)
class RepoPackageEntry:
    """Minimal package metadata used for search and install."""

    name: str
    version: str
    package_type: PackageType = PackageType.WRAP
    dependency_names: Optional[list[str]] = None

    @classmethod
    def from_repo_key(cls, repo_key: RepoKey) -> 'RepoPackageEntry':
        """
        Build an entry from a repository key.
        :param repo_key: Encoded repo key string.
        :return: RepoPackageEntry for the given key.
        """
        name, version, package_type = parse_repo_key(repo_key)
        return cls(name, version, PackageType(package_type))


def add_wrap_entry(
    packages: dict[RepoKey, RepoPackageEntry],
    name: str,
    version: str,
    dependency_names: Optional[list[str]] = None,
) -> None:
    """
    Ensure wrap entries share the same key and metadata shape.
    :param packages: Dictionary to update (modified in place).
    :param name: Package name.
    :param version: Package version.
    :param dependency_names: Optional list of dependency names.
    """
    repo_key = make_repo_key(name, version, PackageType.WRAP)
    packages[repo_key] = RepoPackageEntry(
        name,
        version,
        PackageType.WRAP,
        dependency_names=dependency_names,
    )


def packages_from_releases(
    releases: WrapDbReleases,
) -> dict[RepoKey, RepoPackageEntry]:
    """
    Build a package index from a WrapDB-compatible releases.json map.
    :param releases: Raw releases map (e.g. from releases.json).
    :return: Map from repo key to RepoPackageEntry.
    """
    packages: dict[RepoKey, RepoPackageEntry] = {}
    for name, entry in releases.items():
        versions = entry.get('versions', [])
        dependency_names = entry.get('dependency_names')
        if dependency_names is not None:
            dependency_names = sorted(set(dependency_names))
        for version in versions:
            add_wrap_entry(packages, name, version, dependency_names)
    return packages
