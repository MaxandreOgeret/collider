# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""WrapDB-compatible remote repository."""

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

from pathlib import Path
from typing import Optional

from collider.errors import ColliderUserError
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository.entries import RejectedEntry, RepoPackageEntry, packages_from_releases
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.core import assert_safe_path_segment
from collider.utils.fs import atomic_write_text
from collider.utils.meson.infoTypes import WrapDbReleasesEntry
from collider.utils.network import DEFAULT_NETWORK_TIMEOUT
from collider.utils.packaging.types import RepoKey


_RELEASES_FILENAME = 'releases.json'
_RELEASES_TTL_SECONDS = 300


def _get_pkg_wrap_url(url: urllib.parse.ParseResult, package: RepoPackageEntry) -> str:
    # Name and version come from untrusted releases.json and shape the request path.
    name = assert_safe_path_segment(package.name)
    version = assert_safe_path_segment(package.version, 'version')
    path = f'{name}_{version}/{name}.wrap'
    return urllib.parse.urljoin(url.geturl(), path)


def _wrap_releases_to_packages(
    wrap_releases: dict[str, WrapDbReleasesEntry],
) -> tuple[dict[RepoKey, RepoPackageEntry], list[RejectedEntry]]:
    """Convert WrapDB releases to an in-memory package index and its rejected entries."""
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


def _load_releases_cache(cache_file: Path) -> Optional[dict[str, WrapDbReleasesEntry]]:
    """
    Load cached releases.json, or None when the file is missing or unreadable.
    :param cache_file: Path to the cached releases.json.
    :return: Parsed releases mapping, or None to signal a cache miss.
    """
    try:
        return json.loads(cache_file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        # ValueError covers both JSONDecodeError and UnicodeDecodeError.
        logger.debug(f'Cached releases.json unusable: {exc}')
        return None


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
        self,
        url: urllib.parse.ParseResult,
        packages: dict[RepoKey, RepoPackageEntry],
        rejected_metadata: Optional[list[RejectedEntry]] = None,
    ) -> None:
        """
        Build a Wrap repository from a base URL and preloaded package index.
        :param url: Base URL for the WrapDB-compatible API (e.g. wrapdb.mesonbuild.com/v2/).
        :param packages: Preloaded package index (e.g. from releases.json).
        :param rejected_metadata: Entries dropped while parsing releases.json.
        """
        super().__init__(packages, rejected_metadata=rejected_metadata)
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

        releases: Optional[dict[str, WrapDbReleasesEntry]] = None
        if offline:
            if cache_file is None or (releases := _load_releases_cache(cache_file)) is None:
                raise ColliderUserError(
                    'Offline mode requires cached wrap releases.', os.EX_DATAERR
                )
        elif (
            cache_file is not None
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < _RELEASES_TTL_SECONDS
            # A corrupt within-TTL cache falls through to a network refresh.
            and (releases := _load_releases_cache(cache_file)) is not None
        ):
            logger.debug('Using cached releases.json (within TTL).')
        if releases is None and not offline:
            try:
                with urllib.request.urlopen(
                    releases_url, timeout=DEFAULT_NETWORK_TIMEOUT
                ) as response:
                    releases = json.load(response)
                if not isinstance(releases, dict):
                    # A 200 body that is not a JSON object (e.g. `null`, a list, or
                    # an HTML error page parsed as a string) is a repository data
                    # problem, not a Collider bug.
                    logger.critical(
                        f'WrapDB at "{effective_url.geturl()}" returned non-object releases.json.'
                    )
                    raise ColliderUserError(
                        'WrapDB returned malformed releases.json.', os.EX_DATAERR
                    )
                if cache_file is not None:
                    atomic_write_text(cache_file, json.dumps(releases), encoding='utf-8')
            except ColliderUserError:  # pylint: disable=try-except-raise
                # Re-raise before the generic handler so a malformed-releases user
                # error is not swallowed as a network failure and cache-fallback.
                raise
            except Exception:
                cached = _load_releases_cache(cache_file) if cache_file is not None else None
                if cached is None:
                    raise
                logger.warning('Failed to refresh wrap releases; using cached data.')
                releases = cached

        if not isinstance(releases, dict):
            # Defensive: covers a non-object cached releases.json (e.g. a list or
            # `null`) that slipped past the network-fetch validation above.
            logger.critical(
                f'Cached releases for "{effective_url.geturl()}" are not a JSON object'
                f'{f"; delete {cache_file}" if cache_file is not None else ""}.'
            )
            raise ColliderUserError('Cached wrap releases are malformed.', os.EX_DATAERR)
        packages, rejected = _wrap_releases_to_packages(releases)
        if rejected:
            logger.warning(
                f'{len(rejected)} releases.json entr'
                f'{"y" if len(rejected) == 1 else "ies"} from "{effective_url.geturl()}" '
                'skipped due to malformed metadata.'
            )
        return cls(effective_url, packages, rejected_metadata=rejected)

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
