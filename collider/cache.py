# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Local wrap and archive cache."""

from __future__ import annotations

import errno
import json
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path
from typing import Optional

from collider.log import logger
from collider.Package import WrapPackage
from collider.utils import network
from collider.utils.core import assert_safe_path_segment, is_safe_path_segment
from collider.utils.fs import atomic_write_text
from collider.utils.meson.scan import ScannedDependency
from collider.utils.network import DEFAULT_NETWORK_TIMEOUT
from collider.utils.packaging import compute_file_hash


class WrapCache:
    """Local cache for wrap files and archives."""

    def __init__(self, root: Path) -> None:
        """
        Create a cache rooted at the given directory.
        :param root: Cache root directory; wraps and archives subdirs are created under it.
        """
        self.root = root
        self.wraps_dir = self.root / 'wraps'
        self.archives_dir = self.root / 'archives'
        self.scans_dir = self.root / 'scans'

    def store_wrap(self, package: WrapPackage) -> Path:
        """Persist a wrap so installs can proceed without network access."""
        # Name and version come from repository metadata and become a path here.
        assert_safe_path_segment(package.name)
        assert_safe_path_segment(package.version, 'version')
        self.wraps_dir.mkdir(parents=True, exist_ok=True)
        wrap_path = self.wraps_dir / f'{package.name}_{package.version}.wrap'
        # Wrap cache decouples installs from repository availability.
        atomic_write_text(wrap_path, package.wrap_text, encoding='utf-8')
        return wrap_path

    def load_wrap(self, name: str, version: str) -> Optional[WrapPackage]:
        """Load a cached wrap to reuse the same validation rules."""
        # An unsafe name can never have been cached (writes reject it), so treat
        # it as a miss rather than touching a traversed path or raising.
        if not (is_safe_path_segment(name) and is_safe_path_segment(version)):
            return None
        wrap_path = self.wraps_dir / f'{name}_{version}.wrap'
        if not wrap_path.exists():
            return None
        # Re-parse to enforce the same validation rules as fresh downloads.
        wrap_text = wrap_path.read_text(encoding='utf-8')
        return WrapPackage.from_wrap_text(name, version, wrap_text)

    def list_cached_wraps(self) -> list[tuple[str, str]]:
        """List cached wraps by name and version."""
        if not self.wraps_dir.exists():
            return []

        entries: list[tuple[str, str]] = []
        for wrap_path in self.wraps_dir.glob('*.wrap'):
            stem = wrap_path.stem
            if '_' not in stem:
                continue
            name, version = stem.rsplit('_', 1)
            if not name or not version:
                continue
            entries.append((name, version))
        return sorted(entries)

    def find_wrap_versions(self, name: str, wrap_text: str) -> list[str]:
        """Match a wrap file against cached wraps to recover its version."""
        if not self.wraps_dir.exists():
            return []

        versions: list[str] = []
        prefix = f'{name}_'
        for wrap_path in self.wraps_dir.glob('*.wrap'):
            if not wrap_path.name.startswith(prefix):
                continue
            if wrap_path.read_text(encoding='utf-8') != wrap_text:
                continue
            version = wrap_path.stem[len(prefix) :]
            if version:
                versions.append(version)
        return sorted(versions)

    def _archive_cached(self, expected_hash: str, filename: str) -> bool:
        # The hash comes from untrusted wrap metadata and becomes a path segment.
        if not is_safe_path_segment(expected_hash):
            return False
        safe_name = Path(filename).name
        cached_path = self.archives_dir / f'{expected_hash}-{safe_name}'
        return cached_path.exists()

    def has_package(self, name: str, version: str) -> bool:
        """Report whether a package is fully cached for offline installs."""
        package = self.load_wrap(name, version)
        if package is None:
            return False
        return self.is_fully_cached(package)

    def is_fully_cached(self, package: WrapPackage) -> bool:
        """Verify wraps and referenced archives are already present locally."""
        if not self._archive_cached(package.source_hash, package.source_filename):
            return False

        if package.patch_url:
            if not package.patch_filename or not package.patch_hash:
                return False
            if not self._archive_cached(package.patch_hash, package.patch_filename):
                return False

        return True

    def store_scan(
        self,
        name: str,
        version: str,
        scanned: list[ScannedDependency],
    ) -> Path:
        """Persist dependency scan results so future resolutions skip introspection."""
        # Name and version come from repository metadata and become a path here.
        assert_safe_path_segment(name)
        assert_safe_path_segment(version, 'version')
        self.scans_dir.mkdir(parents=True, exist_ok=True)
        scan_path = self.scans_dir / f'{name}_{version}.json'
        data = [
            {
                'name': dep.name,
                'required': dep.required,
                'version': dep.version,
                'has_fallback': dep.has_fallback,
                'conditional': dep.conditional,
            }
            for dep in scanned
        ]
        atomic_write_text(scan_path, json.dumps(data), encoding='utf-8')
        return scan_path

    def load_scan(self, name: str, version: str) -> Optional[list[ScannedDependency]]:
        """Load cached scan results, returning None on miss or corruption."""
        # Unsafe names are never cached; treat as a miss without touching disk.
        if not (is_safe_path_segment(name) and is_safe_path_segment(version)):
            return None
        scan_path = self.scans_dir / f'{name}_{version}.json'
        if not scan_path.exists():
            return None
        try:
            raw: list[dict] = json.loads(scan_path.read_text(encoding='utf-8'))
            return [
                ScannedDependency(
                    name=entry['name'],
                    required=entry.get('required', True),
                    version=entry.get('version', []),
                    has_fallback=entry.get('has_fallback', False),
                    conditional=entry.get('conditional', False),
                )
                for entry in raw
                if entry.get('name')
            ]
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.debug(f'Corrupt scan cache for "{name}" {version}, ignoring.')
            return None

    def verify_archives(self, package: WrapPackage, offline: bool) -> None:
        """
        Download/cache and verify archive hashes without copying to packagecache.
        :param package: Package whose archives to verify.
        :param offline: If True, only check the local cache.
        :raises ValueError: On hash mismatch.
        :raises FileNotFoundError: When archive is missing in offline mode.
        """
        self._ensure_archive(
            package.source_url, package.source_filename, package.source_hash, offline
        )
        if package.patch_url:
            if not package.patch_filename or not package.patch_hash:
                raise ValueError('Wrap file patch metadata is incomplete.')
            self._ensure_archive(
                package.patch_url, package.patch_filename, package.patch_hash, offline
            )

    def prepare_packagecache(
        self, package: WrapPackage, subprojects_dir: Path, offline: bool
    ) -> None:
        """Populate Meson packagecache so wrap resolution can stay offline."""
        # Reject unsafe names before callers install the package into subprojects.
        assert_safe_path_segment(package.name)
        packagecache = subprojects_dir / 'packagecache'
        packagecache.mkdir(parents=True, exist_ok=True)

        # Meson only looks at subprojects/packagecache for offline resolution.
        source_path = self._ensure_archive(
            package.source_url,
            package.source_filename,
            package.source_hash,
            offline,
        )
        shutil.copy2(source_path, packagecache / package.source_filename)

        if package.patch_url:
            if not package.patch_filename or not package.patch_hash:
                raise ValueError('Wrap file patch metadata is incomplete.')
            patch_path = self._ensure_archive(
                package.patch_url,
                package.patch_filename,
                package.patch_hash,
                offline,
            )
            shutil.copy2(patch_path, packagecache / package.patch_filename)

    def _ensure_archive(self, url: str, filename: str, expected_hash: str, offline: bool) -> Path:
        """Resolve archives into the cache while enforcing hashes and protocols."""
        # The hash comes from untrusted wrap metadata and becomes a path segment.
        assert_safe_path_segment(expected_hash, 'hash')
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        # Use the basename to avoid path traversal from wrap metadata.
        safe_name = Path(filename).name
        # Hash prefix deduplicates identical archives across different wraps.
        cached_path = self.archives_dir / f'{expected_hash}-{safe_name}'

        if cached_path.exists():
            cached_hash = compute_file_hash(cached_path)
            if cached_hash == expected_hash:
                return cached_path
            logger.warning(
                f'Cached archive "{safe_name}" is corrupt: '
                f'expected {expected_hash}, got {cached_hash}. Re-downloading.'
            )
            cached_path.unlink(missing_ok=True)

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == 'http':
            logger.warning('HTTP archive URLs are allowed but insecure; prefer HTTPS.')
        if parsed.scheme in ('', 'file'):
            # Local archives are allowed in offline mode because they avoid network access entirely.
            if parsed.scheme == 'file':
                local_path = Path(urllib.request.url2pathname(parsed.path))
            else:
                local_path = Path(url)

            if not local_path.exists() or not local_path.is_file():
                raise FileNotFoundError(f'Archive not found at "{local_path}".')

            file_hash = compute_file_hash(local_path)
            if file_hash != expected_hash:
                raise ValueError(
                    f'Archive hash mismatch for "{safe_name}": '
                    f'expected {expected_hash}, got {file_hash}.'
                )

            cached_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, cached_path)
            return cached_path

        # Untrusted wrap metadata must never reach a non-http(s) network sink (SSRF).
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f'Unsupported archive URL scheme "{parsed.scheme}" for "{safe_name}".')

        if offline:
            # Offline mode must be explicit about missing archives.
            raise FileNotFoundError(f'Archive not found in cache: {safe_name}')

        # The URL comes from untrusted wrap metadata: refuse hosts in blocked ranges and
        # keep every redirect hop governed so the fetch cannot pivot into internal services.
        network.assert_fetchable_url(url)

        with tempfile.NamedTemporaryFile('wb+', delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            try:
                with network.safe_urlopen(
                    url, timeout=DEFAULT_NETWORK_TIMEOUT, check_redirect_hosts=True
                ) as response:
                    tmp_file.write(response.read())
            except urllib.error.URLError as exc:
                tmp_path.unlink(missing_ok=True)
                raise FileNotFoundError(
                    f'Failed to download archive "{safe_name}" from "{url}": {exc}'
                ) from exc
            except ValueError:
                # A redirect into a blocked host or scheme raises ValueError from the SSRF
                # guard; clean up the temp file and let the security error propagate.
                tmp_path.unlink(missing_ok=True)
                raise

        file_hash = compute_file_hash(tmp_path)
        if file_hash != expected_hash:
            tmp_path.unlink(missing_ok=True)
            raise ValueError(
                f'Archive hash mismatch for "{safe_name}": '
                f'expected {expected_hash}, got {file_hash}.'
            )

        cached_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp_path.replace(cached_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # /tmp and cache may live on different filesystems, so fall back to a move.
            shutil.move(tmp_path, cached_path)
        return cached_path
