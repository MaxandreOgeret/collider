# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""WrapDB-compatible remote repository."""

import hashlib
import json
import time
import urllib.parse
import urllib.request

from pathlib import Path
from typing import Optional

from collider.log import logger
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry, packages_from_releases
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.fs import atomic_write_text
from collider.utils.meson.infoTypes import WrapDbReleasesEntry
from collider.utils.network import DEFAULT_NETWORK_TIMEOUT
from collider.utils.packaging.types import RepoKey


_RELEASES_FILENAME = 'releases.json'
_RELEASES_TTL_SECONDS = 300


def _get_pkg_wrap_url(url: urllib.parse.ParseResult, package: RepoPackageEntry) -> str:
    path = f'{package.name}_{package.version}/{package.name}.wrap'
    return urllib.parse.urljoin(url.geturl(), path)


def _wrap_releases_to_packages(
    wrap_releases: dict[str, WrapDbReleasesEntry],
) -> dict[RepoKey, RepoPackageEntry]:
    """Convert WrapDB releases to an in-memory package index."""
    return packages_from_releases(wrap_releases)


def _releases_cache_path(cache_path: Path, url: urllib.parse.ParseResult) -> Path:
    """
    Derive a collision-free cache file path for a repository's releases.json.

    Keying on the full URL (not just the host) prevents repositories that share
    a host but differ by path from clobbering or stale-serving each other.
    :param cache_path: Cache root directory.
    :param url: Effective repository URL the releases were fetched from.
    :return: Path to the cached releases.json for this repository.
    """
    digest = hashlib.sha256(url.geturl().encode('utf-8')).hexdigest()[:16]
    return Path(cache_path) / 'wrapdb' / f'{url.netloc}-{digest}' / _RELEASES_FILENAME


def _ensure_v2_url(url: urllib.parse.ParseResult) -> urllib.parse.ParseResult:
    path = url.path.rstrip('/')
    if not path.endswith('/v2'):
        logger.warning('WrapDB URL missing "/v2/"; assuming v2 API.')
        path = f'{path}/v2' if path else '/v2'

    if not path.endswith('/'):
        path = f'{path}/'

    return url._replace(path=path)


class Wrap(RepositoryInterface):
    """Read-only WrapDB-compatible repository client."""

    def __init__(
        self, url: urllib.parse.ParseResult, packages: dict[RepoKey, RepoPackageEntry]
    ) -> None:
        """
        Build a Wrap repository from a base URL and preloaded package index.
        :param url: Base URL for the WrapDB-compatible API (e.g. wrapdb.mesonbuild.com/v2/).
        :param packages: Preloaded package index (e.g. from releases.json).
        """
        super().__init__(packages)
        self.url = url

    # Repo operations.

    @classmethod
    def _from_url_impl(
        cls,
        url: urllib.parse.ParseResult,
        cache_path: Optional[Path] = None,
        offline: bool = False,
        **kwargs,
    ) -> 'Wrap':
        if url.scheme not in ('https', 'http'):
            raise ValueError(f'Unsupported URL scheme: {url.scheme}')
        if url.scheme == 'http':
            logger.warning('HTTP WrapDB URLs are allowed but insecure; prefer HTTPS.')

        effective_url = _ensure_v2_url(url)
        releases_url = urllib.parse.urljoin(effective_url.geturl(), _RELEASES_FILENAME)
        cache_file: Optional[Path] = None
        if cache_path is not None:
            cache_file = _releases_cache_path(cache_path, effective_url)

        releases: dict[str, WrapDbReleasesEntry]
        if offline:
            if cache_file is None or not cache_file.exists():
                raise ValueError('Offline mode requires cached wrap releases.')
            releases = json.loads(cache_file.read_text(encoding='utf-8'))
        elif (
            cache_file is not None
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < _RELEASES_TTL_SECONDS
        ):
            logger.debug('Using cached releases.json (within TTL).')
            releases = json.loads(cache_file.read_text(encoding='utf-8'))
        else:
            try:
                with urllib.request.urlopen(
                    releases_url, timeout=DEFAULT_NETWORK_TIMEOUT
                ) as response:
                    releases = json.load(response)
                if cache_file is not None:
                    atomic_write_text(cache_file, json.dumps(releases), encoding='utf-8')
            except Exception as e:
                if cache_file is None or not cache_file.exists():
                    raise e
                logger.warning('Failed to refresh wrap releases; using cached data.')
                releases = json.loads(cache_file.read_text(encoding='utf-8'))

        packages = _wrap_releases_to_packages(releases)
        return cls(effective_url, packages)

    def _update_impl(self) -> None:
        pass

    # Package operations.

    def _add_package_impl(
        self,
        package: WrapPackage,
        *,
        source_archive: Optional[Path] = None,
        patch_archive: Optional[Path] = None,
    ) -> RepoPackageEntry:
        raise NotImplementedError('Wrap repository does not support adding packages.')

    def _remove_package_impl(self, package: WrapPackage, entry: RepoPackageEntry) -> None:
        raise NotImplementedError('Wrap repository does not support removing packages.')

    def _get_package_impl(self, repo_key: RepoKey) -> Optional[WrapPackage]:
        entry = self.packages[repo_key]

        wrap_url = _get_pkg_wrap_url(self.url, entry)
        with urllib.request.urlopen(wrap_url, timeout=DEFAULT_NETWORK_TIMEOUT) as response:
            wrap_bytes = response.read()

        try:
            wrap_text = wrap_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(f'Failed to decode wrap file for "{repo_key}".') from e

        return WrapPackage.from_wrap_text(entry.name, entry.version, wrap_text)

    def requires_network(self) -> bool:
        """Whether this repository needs network access for search and fetch."""
        return True
