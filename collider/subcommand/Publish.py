# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Publish a Meson project to a repository."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path
from typing import Any

from collider.Context import Context
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.RepositoryInterface import PackageAlreadyExistsError
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson.info import load_project_metadata
from collider.utils.meson.meson import DEFAULT_BUILD_DIR
from collider.utils.network import DEFAULT_NETWORK_TIMEOUT
from collider.utils.packaging import compute_file_hash


_DEFAULT_PUSH_TOKEN_ENV = 'COLLIDER_PUSH_TOKEN'
_COLLIDER_PUSH_ENDPOINT = '_collider/v1/push'
_REMOTE_PLACEHOLDER_BASE_URL = 'https://example.invalid/'


class Publish(SubcommandInterface):
    """Publish a Meson project to a repository."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Publish the current project to a repository.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider publish --help`."""
        del cls
        return (
            'Package the current Meson project and publish it to a filesystem or collider '
            'repository.\nUse this to distribute wraps and optional patch archives.'
        )

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('repository', type=str, help='Name of the repository to publish to.')
        # pylint: disable=duplicate-code  # CLI wiring must stay in subcommand.
        parser.add_argument(
            '--builddir',
            type=Path,
            default=DEFAULT_BUILD_DIR,
            help='Meson build directory used to read project metadata.',
        )
        parser.add_argument(
            '--patch-archive',
            type=Path,
            required=False,
            help='Path to the patch archive to store in the repository.',
        )
        parser.add_argument(
            '--push-token-env',
            type=str,
            default=_DEFAULT_PUSH_TOKEN_ENV,
            help='Environment variable to read push token from for collider repositories.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Validate and package without writing to the repository.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store publish arguments and context.
        :param args: Parsed CLI arguments (repository name, builddir, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.repository_name: str = getattr(args, 'repository')
        self.builddir: Path = args.builddir
        self.patch_archive: Path | None = args.patch_archive
        self.push_token_env: str = args.push_token_env
        self.dry_run: bool = args.dry_run

    @override
    def execute(self) -> int:
        """Run the publish command.
        :return: Exit code.
        """
        action = 'Dry run: validating' if self.dry_run else 'Publishing'
        logger.info(f'{action} publish to repository "{self.repository_name}".')

        for archive_path in (self.patch_archive,):
            if archive_path is None:
                continue
            if not archive_path.exists():
                logger.critical(f'Archive "{archive_path}" does not exist.')
                return os.EX_NOINPUT
            if not archive_path.is_file():
                logger.critical(f'Archive "{archive_path}" is not a file.')
                return os.EX_DATAERR

        package_name, version, source_dir = load_project_metadata(self.builddir)
        if package_name is None or version is None or source_dir is None:
            return os.EX_DATAERR

        if self.repository_name not in self.context.config.repositories:
            logger.critical(f'Repository "{self.repository_name}" not found in config.')
            return os.EX_NOINPUT

        repo = self.context.config.repositories[self.repository_name]
        is_filesystem_repo = isinstance(repo, Filesystem)
        is_collider_repo = isinstance(repo, Collider)
        if not is_filesystem_repo and not is_collider_repo:
            logger.critical('Publishing is only supported for filesystem or collider repositories.')
            return os.EX_USAGE

        temp_archive: tempfile.TemporaryDirectory[str] | None = None
        try:
            source_archive, temp_archive = self._build_source_archive(
                source_dir, package_name, version
            )
            if is_filesystem_repo:
                assert isinstance(repo, Filesystem)
                wrap_text = self._build_wrap_text(
                    repo.publish_url,
                    package_name,
                    version,
                    source_archive,
                    self.patch_archive,
                )
                package = WrapPackage.from_wrap_text(package_name, version, wrap_text)
                if self.dry_run:
                    self._report_dry_run(
                        package_name,
                        version,
                        source_archive,
                        self.repository_name,
                        repo.path.as_uri(),
                    )
                else:
                    repo.add_package(
                        package,
                        source_archive=source_archive,
                        patch_archive=self.patch_archive,
                    )
            else:
                assert isinstance(repo, Collider)
                wrap_text = self._build_wrap_text(
                    _REMOTE_PLACEHOLDER_BASE_URL,
                    package_name,
                    version,
                    source_archive,
                    self.patch_archive,
                )
                package = WrapPackage.from_wrap_text(package_name, version, wrap_text)
                if self.dry_run:
                    self._report_dry_run(
                        package_name,
                        version,
                        source_archive,
                        self.repository_name,
                        repo.url.geturl(),
                    )
                else:
                    push_token = self._load_remote_push_token()
                    if push_token is None:
                        return os.EX_USAGE
                    payload = self._build_remote_push_payload(
                        package, source_archive, self.patch_archive
                    )
                    self._push_remote_wrap_repo(repo, push_token, payload)
        except PackageAlreadyExistsError:
            logger.critical(
                f'Version "{version}" of "{package_name}" already exists in repository '
                f'"{self.repository_name}". Unpublish the existing version first, or bump '
                'the project version.'
            )
            return os.EX_CANTCREAT
        except Exception as e:
            logger.critical(f'Failed to add package to repository: {e}')
            return os.EX_IOERR
        finally:
            if temp_archive is not None:
                temp_archive.cleanup()

        if not self.dry_run:
            logger.info(f'Package "{package.name}" published successfully.')
        return os.EX_OK

    @staticmethod
    def _report_dry_run(name: str, version: str, archive: Path, repo_name: str, dest: str) -> None:
        """
        Log a dry-run summary without writing anything.
        :param name: Package name.
        :param version: Package version.
        :param archive: Path to the built source archive.
        :param repo_name: Name of the target repository.
        :param dest: URL or path of the target repository.
        """
        size_mb = archive.stat().st_size / 1_048_576
        logger.info(f'[dry-run] Would publish: {name} {version}')
        logger.info(f'[dry-run] Archive: {archive.name} ({size_mb:.1f} MB)')
        logger.info(f'[dry-run] Destination: {repo_name} ({dest})')
        logger.info('[dry-run] No files written.')

    def _build_source_archive(
        self, source_dir: Path, name: str, version: str
    ) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
        """Archive source under <name>-<version> to keep wrap directory and root in sync."""
        archive_root = f'{name}-{version}'
        temp_dir = tempfile.TemporaryDirectory()
        archive_path = Path(temp_dir.name) / f'{archive_root}.tar.xz'

        exclude_names = {
            '.git',
            '.hg',
            '.svn',
            '.idea',
            '.vscode',
            '.pytest_cache',
            '__pycache__',
            '.venv',
            '.direnv',
        }

        build_rel: tuple[str, ...] | None = None
        try:
            build_rel = self.builddir.resolve().relative_to(source_dir.resolve()).parts
        except ValueError:
            build_rel = None

        def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            rel = Path(tarinfo.name)
            if rel.parts and rel.parts[0] == archive_root:
                rel = Path(*rel.parts[1:])
            if not rel.parts:
                return tarinfo
            # Match at any depth so nested VCS/cache directories are excluded too.
            if any(part in exclude_names for part in rel.parts):
                return None
            # Drop Meson's downloaded archive cache; it bundles other packages' sources.
            if rel.parts[:2] == ('subprojects', 'packagecache'):
                return None
            if build_rel and tuple(rel.parts[: len(build_rel)]) == build_rel:
                return None
            return tarinfo

        with tarfile.open(archive_path, mode='w:xz') as tar:
            tar.add(source_dir, arcname=archive_root, filter=_tar_filter)

        return archive_path, temp_dir

    def _build_wrap_text(
        self,
        base_url: str,
        name: str,
        version: str,
        source_archive: Path,
        patch_archive: Path | None,
    ) -> str:
        """Generate a wrap that is self-contained against the repository base URL."""
        archive_root = f'{name}-{version}'
        source_filename = source_archive.name
        source_hash = compute_file_hash(source_archive)

        normalized_base_url = base_url.rstrip('/') + '/'
        source_url = urllib.parse.urljoin(
            normalized_base_url, f'archives/{name}_{version}/{source_filename}'
        )

        lines = [
            '[wrap-file]',
            f'directory = {archive_root}',
            f'source_url = {source_url}',
            f'source_filename = {source_filename}',
            f'source_hash = {source_hash}',
        ]

        if patch_archive is not None:
            patch_filename = patch_archive.name
            patch_hash = compute_file_hash(patch_archive)
            patch_url = urllib.parse.urljoin(
                normalized_base_url, f'archives/{name}_{version}/{patch_filename}'
            )
            lines.extend(
                [
                    f'patch_url = {patch_url}',
                    f'patch_filename = {patch_filename}',
                    f'patch_hash = {patch_hash}',
                ]
            )

        provide_var = self._default_provide_var(name)
        lines.extend(
            [
                '',
                '[provide]',
                f'{name} = {provide_var}',
            ]
        )

        return '\n'.join(lines) + '\n'

    @staticmethod
    def _default_provide_var(name: str) -> str:
        """Meson variable names are stricter than project names, so sanitize them."""
        safe_name = re.sub(r'[^A-Za-z0-9_]', '_', name)
        if not re.match(r'^[A-Za-z_]', safe_name):
            safe_name = f'_{safe_name}'
        return f'{safe_name}_dep'

    def _load_remote_push_token(self) -> str | None:
        """Read remote push bearer token from configured environment variable."""
        if not self.push_token_env:
            logger.critical('Push token env var name must not be empty.')
            return None
        token = os.environ.get(self.push_token_env)
        if not token:
            logger.critical(
                f'Pushing to collider repository requires bearer token in "{self.push_token_env}".'
            )
            return None
        return token

    @staticmethod
    def _build_remote_push_payload(
        package: WrapPackage,
        source_archive: Path,
        patch_archive: Path | None,
    ) -> dict[str, Any]:
        """Build the JSON payload expected by collider serve push endpoint."""
        payload: dict[str, Any] = {
            'name': package.name,
            'version': package.version,
            'wrap_text': package.wrap_text,
            'source_filename': package.source_filename,
            'source_archive_base64': base64.b64encode(source_archive.read_bytes()).decode('ascii'),
        }
        if patch_archive is not None:
            if not package.patch_filename:
                raise ValueError('Wrap patch_filename is missing for patch archive push.')
            payload['patch_filename'] = package.patch_filename
            payload['patch_archive_base64'] = base64.b64encode(patch_archive.read_bytes()).decode(
                'ascii'
            )
        return payload

    @staticmethod
    def _push_remote_wrap_repo(repo: Wrap, token: str, payload: dict[str, Any]) -> None:
        """Push a package payload to a collider repository (wrap repo with push endpoint)."""
        endpoint = urllib.parse.urljoin(repo.url.geturl(), _COLLIDER_PUSH_ENDPOINT)
        request_body = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            endpoint,
            data=request_body,
            method='POST',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Content-Length': str(len(request_body)),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_NETWORK_TIMEOUT) as response:
                if response.status != 201:
                    raise ValueError(
                        f'Remote push failed with unexpected HTTP status {response.status}.'
                    )
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode('utf-8', errors='replace')
            if exc.code == 409:
                raise PackageAlreadyExistsError(
                    f'Remote push failed with HTTP 409: {response_body}'
                ) from exc
            raise ValueError(f'Remote push failed with HTTP {exc.code}: {response_body}') from exc
        except urllib.error.URLError as exc:
            raise ValueError(f'Failed to reach remote push endpoint "{endpoint}": {exc}') from exc
