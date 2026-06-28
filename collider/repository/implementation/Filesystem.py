# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Local filesystem repository."""

import io
import json
import shutil
import urllib.parse
import urllib.request

from pathlib import Path
from typing import Optional

from collider.log import logger
from collider.Package import WrapPackage, _load_wrap_section, get_provide_names
from collider.repository.entries import RepoPackageEntry, add_wrap_entry
from collider.utils.core import assert_safe_path_segment
from collider.utils.fs import atomic_write_text
from collider.utils.packaging import compute_file_hash
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.types import RepoKey

from .RepositoryInterface import RepositoryInterface


class Filesystem(RepositoryInterface):
    """Filesystem-backed repo using the WrapDB-compatible layout."""

    ARCHIVE_DIR = 'archives'

    # Repo operations.

    def __init__(
        self,
        path: Path,
        publish_url: str,
        packages: Optional[dict[RepoKey, RepoPackageEntry]] = None,
    ) -> None:
        """
        Build a filesystem repository from a directory and optional package index.
        :param path: Root path of the repository (WrapDB-compatible layout).
        :param publish_url: Base URL used when publishing (e.g. for wrap file references).
        :param packages: Optional preloaded package index; if None, discovered from path.
        """
        super().__init__(packages or {})
        self.path = path
        self.publish_url = publish_url
        parsed_publish = urllib.parse.urlparse(self.publish_url)
        if not parsed_publish.scheme:
            raise ValueError('Publish URL must include a scheme (https or file).')
        if parsed_publish.scheme not in ('https', 'http', 'file'):
            raise ValueError('Publish URL must use HTTPS, HTTP, or file.')
        if parsed_publish.scheme == 'http':
            logger.warning('Publish URL uses HTTP; downloads will be insecure.')

    @classmethod
    def _from_url_impl(
        cls, url: urllib.parse.ParseResult, publish_url: Optional[str] = None, **kwargs
    ) -> 'RepositoryInterface':
        if publish_url is None:
            raise ValueError('Filesystem repositories require a publish_url.')
        # file:// URLs on Windows keep the drive letter in URL form, so convert the
        # path component back into a native filesystem path before touching disk.
        repo_path = Path(urllib.request.url2pathname(url.path))
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f'Filesystem repository path "{repo_path.as_posix()}" does not exist.')

        packages = cls._scan_packages(repo_path)
        repo = cls(repo_path, publish_url=publish_url, packages=packages)
        repo._write_releases_json()
        return repo

    # Package operations.

    def _after_add_package(self, _package: WrapPackage, _entry: RepoPackageEntry) -> None:
        self._write_releases_json()

    def _after_remove_package(self, _package: WrapPackage, _entry: RepoPackageEntry) -> None:
        self._write_releases_json()

    def _add_package_impl(
        self,
        package: WrapPackage,
        *,
        source_archive: Optional[Path] = None,
        patch_archive: Optional[Path] = None,
    ) -> RepoPackageEntry:
        """Store the wrap (and optional archives) and return an index entry."""
        # Keep a deterministic layout to make repositories easy to sync and inspect.
        dest_path = self._wrap_path(package.name, package.version)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f'Writing wrap file to "{dest_path.as_posix()}".')
        wrap_text = package.wrap_text
        if source_archive or patch_archive:
            wrap_text = self._stage_archives_and_rewrite_wrap(
                package,
                source_archive=source_archive,
                patch_archive=patch_archive,
            )

        atomic_write_text(dest_path, wrap_text, encoding='utf-8')

        return RepoPackageEntry(
            name=package.name,
            version=package.version,
            package_type=PackageType.WRAP,
        )

    def _remove_package_impl(self, package: WrapPackage, entry: RepoPackageEntry) -> None:
        """Remove the wrap and any staged archives from disk."""
        wrap_path = self._wrap_path(entry.name, entry.version)
        if wrap_path.exists():
            logger.debug(f'Removing wrap file "{wrap_path.as_posix()}".')
            wrap_path.unlink()

        wrap_dir = wrap_path.parent
        if wrap_dir.exists() and not any(wrap_dir.iterdir()):
            wrap_dir.rmdir()

        archive_dir = self.path / self.ARCHIVE_DIR / f'{package.name}_{package.version}'
        if archive_dir.exists():
            # Remove stored archives to keep the repo tidy after package removal.
            shutil.rmtree(archive_dir)

    def _get_package_impl(self, repo_key: RepoKey) -> Optional[WrapPackage]:
        """Load the wrap content from disk to build an installable package."""
        repo_pkg_entry: RepoPackageEntry = self.packages[repo_key]
        wrap_path = self._wrap_path(repo_pkg_entry.name, repo_pkg_entry.version)
        logger.debug(f'Loading wrap file from "{wrap_path.as_posix()}".')
        wrap_text = wrap_path.read_text(encoding='utf-8')
        return WrapPackage.from_wrap_text(
            repo_pkg_entry.name,
            repo_pkg_entry.version,
            wrap_text,
        )

    def _stage_archives_and_rewrite_wrap(
        self,
        package: WrapPackage,
        *,
        source_archive: Optional[Path],
        patch_archive: Optional[Path],
    ) -> str:
        safe_package_name = self._safe_package_segment(package.name, 'name')
        safe_package_version = self._safe_package_segment(package.version, 'version')

        if source_archive is None:
            raise ValueError('Source archive is required when staging archives.')

        if patch_archive is not None and not package.patch_url:
            # Avoid inventing patch metadata; wraps must be explicit.
            raise ValueError('Wrap file patch metadata is required when staging a patch archive.')

        if not source_archive.exists() or not source_archive.is_file():
            raise ValueError('Source archive must exist and be a file.')

        if patch_archive is not None and (
            not patch_archive.exists() or not patch_archive.is_file()
        ):
            raise ValueError('Patch archive must exist and be a file.')

        safe_source_name = Path(package.source_filename).name
        if safe_source_name != package.source_filename:
            raise ValueError('Wrap file source_filename must not contain path components.')

        if source_archive.name != safe_source_name:
            # The wrap filename becomes part of Meson’s packagecache entry.
            raise ValueError('Source archive filename must match wrap source_filename.')

        if package.patch_filename:
            safe_patch_name = Path(package.patch_filename).name
            if safe_patch_name != package.patch_filename:
                raise ValueError('Wrap file patch_filename must not contain path components.')

        base_url = self.publish_url.rstrip('/') + '/'

        archive_dir = self.path / self.ARCHIVE_DIR / f'{safe_package_name}_{safe_package_version}'
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Verify archives before publishing so repository URLs are trustworthy.
        source_hash = compute_file_hash(source_archive)
        if source_hash != package.source_hash:
            raise ValueError('Source archive hash does not match wrap source_hash.')

        dest_source = archive_dir / safe_source_name
        shutil.copy2(source_archive, dest_source)

        source_url = urllib.parse.urljoin(
            base_url,
            f'{self.ARCHIVE_DIR}/{safe_package_name}_{safe_package_version}/{safe_source_name}',
        )

        patch_url = None
        if patch_archive is not None:
            if not package.patch_filename or not package.patch_hash:
                raise ValueError('Wrap file patch metadata is incomplete.')

            if patch_archive.name != package.patch_filename:
                # Keep wrap metadata aligned with the physical archive name.
                raise ValueError('Patch archive filename must match wrap patch_filename.')

            patch_hash = compute_file_hash(patch_archive)
            if patch_hash != package.patch_hash:
                raise ValueError('Patch archive hash does not match wrap patch_hash.')

            dest_patch = archive_dir / package.patch_filename
            shutil.copy2(patch_archive, dest_patch)
            patch_url = urllib.parse.urljoin(
                base_url,
                f'{self.ARCHIVE_DIR}/{safe_package_name}_{safe_package_version}/{package.patch_filename}',
            )

        # Rewriting URLs makes the stored wrap self-contained against the repository base.
        return self._rewrite_wrap_urls(package.wrap_text, source_url, patch_url)

    @staticmethod
    def _rewrite_wrap_urls(wrap_text: str, source_url: str, patch_url: Optional[str]) -> str:
        parser, section = _load_wrap_section(wrap_text)
        section['source_url'] = source_url
        if patch_url is not None:
            section['patch_url'] = patch_url

        buffer = io.StringIO()
        parser.write(buffer)
        return buffer.getvalue()

    def _write_releases_json(self) -> None:
        releases: dict[str, dict[str, list[str]]] = {}
        provides: dict[str, set[str]] = {}
        for entry in self.packages.values():
            versions = releases.setdefault(entry.name, {'versions': []})['versions']
            if entry.version not in versions:
                versions.append(entry.version)
            wrap_path = self._wrap_path(entry.name, entry.version)
            if not wrap_path.exists():
                continue
            provide_names = get_provide_names(wrap_path.read_text(encoding='utf-8'))
            if provide_names:
                provides.setdefault(entry.name, set()).update(provide_names)

        for name, data in releases.items():
            data['versions'] = sorted(data['versions'])
            if name in provides and provides[name]:
                data['dependency_names'] = sorted(provides[name])

        # Keep releases.json in sync so the repo stays WrapDB-compatible.
        releases_path = self.path / 'releases.json'
        atomic_write_text(releases_path, json.dumps(releases, indent=2), encoding='utf-8')

    @classmethod
    def _scan_packages(cls, repo_path: Path) -> dict[RepoKey, RepoPackageEntry]:
        packages: dict[RepoKey, RepoPackageEntry] = {}
        for child in repo_path.iterdir():
            if not child.is_dir():
                continue
            if child.name == cls.ARCHIVE_DIR:
                continue
            for wrap_path in child.glob('*.wrap'):
                name = wrap_path.stem
                prefix = f'{name}_'
                if not child.name.startswith(prefix):
                    logger.debug(f'Skipping wrap "{wrap_path.name}" with mismatched folder.')
                    continue
                version = child.name[len(prefix) :]
                if not version:
                    logger.debug(f'Skipping wrap "{wrap_path.name}" with missing version.')
                    continue
                provide_names = get_provide_names(wrap_path.read_text(encoding='utf-8')) or None
                add_wrap_entry(packages, name, version, provide_names)
        return packages

    def _wrap_path(self, name: str, version: str) -> Path:
        safe_name = self._safe_package_segment(name, 'name')
        safe_version = self._safe_package_segment(version, 'version')
        rel_dir = Path(f'{safe_name}_{safe_version}')
        return self.path / rel_dir / f'{safe_name}.wrap'

    @staticmethod
    def _safe_package_segment(value: str, key: str) -> str:
        return assert_safe_path_segment(value, key)
