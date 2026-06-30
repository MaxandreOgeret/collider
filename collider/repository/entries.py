# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Repository entry types derived from releases.json."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from collider.log import logger
from collider.utils.core import is_safe_path_segment
from collider.utils.meson.infoTypes import WrapDbReleases
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key, parse_repo_key
from collider.utils.packaging.types import RepoKey


class RejectReason(Enum):
    """Why a releases.json entry was rejected at the index boundary."""

    STRUCTURE = 'structure'
    UNSAFE_NAME = 'unsafe_name'
    UNSAFE_VERSION = 'unsafe_version'


@dataclass(frozen=True)
class RejectedEntry:
    """A releases.json entry that could not be indexed, with the reason it was dropped."""

    name: str
    reason: RejectReason


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


def _read_dep_names(entry: Any) -> Optional[list[str]]:
    """
    Return an entry's declared provides when they are a clean list of strings, else None.
    None means "absent or untrustworthy"; it must not be confused with "declares no provides".
    :param entry: A single releases.json entry object.
    :return: Sorted, de-duplicated dependency names, or None.
    """
    dep_names = entry.get('dependency_names')
    if dep_names is None:
        return None
    if not isinstance(dep_names, list) or not all(isinstance(d, str) for d in dep_names):
        return None
    return sorted(set(dep_names))


def packages_from_releases(
    releases: WrapDbReleases,
) -> tuple[dict[RepoKey, RepoPackageEntry], list[RejectedEntry]]:
    """
    Build a package index from a WrapDB-compatible releases.json map.
    Malformed metadata is untrusted input, so a bad entry is skipped (never raised) and recorded in
    the returned reject list rather than dropping the whole repository. Names and versions become
    filesystem paths and URL segments downstream, so unsafe segments are dropped at this boundary.
    :param releases: Raw releases map (e.g. from releases.json).
    :return: A (package index, rejected entries) pair.
    """
    packages: dict[RepoKey, RepoPackageEntry] = {}
    rejected: list[RejectedEntry] = []

    if not isinstance(releases, dict):
        return packages, [RejectedEntry('', RejectReason.STRUCTURE)]

    for name, entry in releases.items():
        if not isinstance(name, str) or not is_safe_path_segment(name):
            rejected.append(RejectedEntry(str(name), RejectReason.UNSAFE_NAME))
            continue
        if not isinstance(entry, dict):
            rejected.append(RejectedEntry(name, RejectReason.STRUCTURE))
            continue

        dependency_names = _read_dep_names(entry)
        versions = entry.get('versions', [])
        if not isinstance(versions, list):
            rejected.append(RejectedEntry(name, RejectReason.STRUCTURE))
            continue

        for version in versions:
            if not isinstance(version, str) or not is_safe_path_segment(version):
                rejected.append(RejectedEntry(name, RejectReason.UNSAFE_VERSION))
                continue
            add_wrap_entry(packages, name, version, dependency_names)

    return packages, rejected
