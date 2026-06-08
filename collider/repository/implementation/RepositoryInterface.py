# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Repository interface with shared package indexing."""

import re
import urllib.parse

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, final

from packaging.specifiers import SpecifierSet

from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key, parse_repo_key
from collider.utils.packaging.types import RepoKey


class PackageAlreadyExistsError(ValueError):
    """Raised when a package/version pair is already present in the repository."""


class RepositoryInterface(ABC):
    """Shared repository API so callers are backend-agnostic."""

    def __init__(self, packages: dict[RepoKey, RepoPackageEntry]) -> None:
        self.packages = packages
        self._origin_url: str = ''

    @property
    def origin_url(self) -> str:
        """The URL this repository was constructed from."""
        return self._origin_url

    # Repo operations.

    @classmethod
    @final
    def from_url(cls, url_str: str, **kwargs):
        """Factory that enforces URL parsing before init."""
        if cls is RepositoryInterface:
            raise RuntimeError('Cannot instantiate abstract class RepositoryInterface')

        parsed_url = urllib.parse.urlparse(url_str)
        if not parsed_url.scheme:
            raise ValueError(f'Invalid URL: {url_str}')

        instance = cls._from_url_impl(parsed_url, **kwargs)
        instance._origin_url = url_str  # pylint: disable=protected-access
        return instance

    @classmethod
    @abstractmethod
    def _from_url_impl(cls, url: urllib.parse.ParseResult, **kwargs) -> 'RepositoryInterface':
        """Backend hook called after URL parsing."""

    def update(self) -> None:
        """Optional sync hook for repositories that can update themselves."""
        self._update_impl()

    def _update_impl(self) -> None:
        raise NotImplementedError('update() is not supported by this repository implementation')

    # Package operations.

    def add_package(
        self,
        package: WrapPackage,
        *,
        source_archive: Optional[Path] = None,
        patch_archive: Optional[Path] = None,
    ) -> None:
        """Add a package and persist repository state in one step."""
        repo_key = self._make_repo_key(package.name, package.version)
        if repo_key in self.packages:
            raise PackageAlreadyExistsError(f'Package {repo_key} already exists in repository.')

        entry = self._add_package_impl(
            package,
            source_archive=source_archive,
            patch_archive=patch_archive,
        )
        self.packages[repo_key] = entry
        self._after_add_package(package, entry)

    @abstractmethod
    def _add_package_impl(
        self,
        package: WrapPackage,
        *,
        source_archive: Optional[Path] = None,
        patch_archive: Optional[Path] = None,
    ) -> RepoPackageEntry:
        """Backend hook that stores the package and returns its index entry."""

    def _after_add_package(self, _package: WrapPackage, _entry: RepoPackageEntry) -> None:
        """Hook for side effects after a package is added to the index."""
        return None

    @final
    def remove_package(self, package: WrapPackage) -> None:
        """Remove a package and keep the index consistent."""
        repo_key = self._make_repo_key(package.name, package.version)
        if repo_key not in self.packages:
            raise ValueError(f'Package {repo_key} does not exist in repository.')

        entry = self.packages[repo_key]
        self._remove_package_impl(package, entry)
        del self.packages[repo_key]
        self._after_remove_package(package, entry)

    @abstractmethod
    def _remove_package_impl(self, package: WrapPackage, entry: RepoPackageEntry) -> None:
        """Backend hook that removes stored package artifacts."""

    def _after_remove_package(self, _package: WrapPackage, _entry: RepoPackageEntry) -> None:
        """Hook for side effects after a package is removed from the index."""
        return None

    @final
    def get_package(self, repo_key: RepoKey) -> Optional[WrapPackage]:
        """Resolve an installable package or return None."""
        if repo_key not in self.packages:
            legacy_match = self._resolve_legacy_key(repo_key)
            if legacy_match is None:
                return None
            repo_key = legacy_match

        return self._get_package_impl(repo_key)

    @abstractmethod
    def _get_package_impl(self, repo_key: RepoKey) -> Optional[WrapPackage]:
        """Backend hook that materializes a package for install."""

    def search(
        self, name_pattern: re.Pattern, version_spec: Optional[SpecifierSet] = None
    ) -> dict[RepoKey, RepoPackageEntry]:
        """Local index search used by CLI and registry lookups."""
        return {
            k: v
            for k, v in self.packages.items()
            if name_pattern.match(v.name)
            and (version_spec is None or version_spec.contains(v.version))
        }

    def __contains__(self, item: object) -> bool:
        """Allow `in` checks for packages or repo keys."""
        if isinstance(item, WrapPackage):
            repo_key = self._make_repo_key(item.name, item.version)
            return repo_key in self.packages
        if isinstance(item, str):
            return item in self.packages or self._resolve_legacy_key(item) is not None
        return False

    def _make_repo_key(self, name: str, version: str) -> RepoKey:
        # Wrap-based repo keys keep indices stable and predictable.
        return make_repo_key(name, version, PackageType.WRAP)

    def _resolve_legacy_key(self, repo_key: RepoKey) -> Optional[RepoKey]:
        try:
            name, version, package_type = parse_repo_key(repo_key)
        except ValueError:
            return None

        for key, entry in self.packages.items():
            if (
                entry.name == name
                and entry.version == version
                and entry.package_type.value == package_type
            ):
                return key
        return None

    def requires_network(self) -> bool:
        """Signals whether the repository needs network access."""
        return False
