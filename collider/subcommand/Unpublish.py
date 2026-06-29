# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Remove a published package version from a repository."""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.parse
import urllib.request

from collider.Context import Context
from collider.log import logger
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.network import DEFAULT_NETWORK_TIMEOUT, may_send_push_token, safe_urlopen
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


_DEFAULT_PUSH_TOKEN_ENV = 'COLLIDER_PUSH_TOKEN'
_COLLIDER_DELETE_ENDPOINT = '_collider/v1/packages/'


class Unpublish(SubcommandInterface):
    """Remove a published package version from a repository."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Remove a published package version from a repository.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider unpublish --help`."""
        del cls
        return (
            'Remove a package version from a filesystem or collider repository.\n'
            'For collider remotes, authentication is read from --push-token-env.'
        )

    @staticmethod
    def epilog() -> str | None:
        """Optional examples appended to the help output."""
        return '  ‣ collider unpublish my-repo foo 1.0.0\n'

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument(
            'repository',
            type=str,
            help='Name of the repository.',
        )
        parser.add_argument(
            'package',
            type=str,
            help='Name of the package to remove.',
        )
        parser.add_argument(
            'version',
            type=str,
            help='Version of the package to remove.',
        )
        parser.add_argument(
            '--push-token-env',
            type=str,
            default=_DEFAULT_PUSH_TOKEN_ENV,
            help='Environment variable to read push token from for collider repositories.',
        )
        parser.add_argument(
            '--insecure',
            action='store_true',
            default=False,
            help='Allow deleting from a non-https repository, sending the token in cleartext.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store unpublish arguments and context.
        :param args: Parsed CLI arguments (repository, package, version).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.repository_name: str = args.repository
        self.package_name: str = args.package
        self.version: str = args.version
        self.push_token_env: str = args.push_token_env
        self.insecure: bool = getattr(args, 'insecure', False)

    @override
    def execute(self) -> int:
        """Run the unpublish command.
        :return: Exit code.
        """
        if self.repository_name not in self.context.config.repositories:
            logger.critical(f'Repository "{self.repository_name}" not found in config.')
            return os.EX_NOINPUT

        repo = self.context.config.repositories[self.repository_name]

        if isinstance(repo, Filesystem):
            return self._delete_filesystem(repo)
        if isinstance(repo, Collider):
            return self._delete_collider(repo)
        if isinstance(repo, Wrap):
            logger.critical('Unpublish is only supported for filesystem or collider repositories.')
            return os.EX_USAGE

        logger.critical('Unpublish is only supported for filesystem or collider repositories.')
        return os.EX_USAGE

    def _delete_filesystem(self, repo: Filesystem) -> int:
        """Remove package from a filesystem repository."""
        repo_key = make_repo_key(self.package_name, self.version, PackageType.WRAP)
        if repo_key not in repo.packages:
            logger.critical(
                f'Package "{self.package_name}" version "{self.version}" does not exist '
                f'in repository "{self.repository_name}".'
            )
            return os.EX_NOINPUT

        try:
            package = repo.get_package(repo_key)
        except (OSError, ValueError) as exc:
            logger.critical(
                f'Failed to load package "{self.package_name}" "{self.version}" '
                f'from repository "{self.repository_name}" '
                f'({type(exc).__name__}: {exc}).'
            )
            return os.EX_IOERR
        except Exception as exc:
            logger.critical(
                f'Unexpected error while loading package "{self.package_name}" "{self.version}" '
                f'from repository "{self.repository_name}" '
                f'({type(exc).__name__}: {exc}).'
            )
            return os.EX_IOERR
        if package is None:
            logger.critical(
                f'Failed to load package "{self.package_name}" "{self.version}" from repository.'
            )
            return os.EX_IOERR

        try:
            repo.remove_package(package)
        except (OSError, ValueError) as exc:
            logger.critical(
                f'Failed to remove package "{self.package_name}" "{self.version}" '
                f'from repository "{self.repository_name}" '
                f'({type(exc).__name__}: {exc}).'
            )
            return os.EX_IOERR
        except Exception as exc:
            logger.critical(
                f'Unexpected error while removing package "{self.package_name}" "{self.version}" '
                f'from repository "{self.repository_name}" '
                f'({type(exc).__name__}: {exc}).'
            )
            return os.EX_IOERR

        logger.info(
            f'Package "{self.package_name}" version "{self.version}" removed from '
            f'repository "{self.repository_name}".'
        )
        return os.EX_OK

    def _delete_collider(self, repo: Collider) -> int:
        """Remove package from a remote collider repository via DELETE endpoint."""
        if not self.push_token_env:
            logger.critical(
                'Push token env var name must not be empty. '
                f'Pass --push-token-env <ENV_VAR> or omit it to use "{_DEFAULT_PUSH_TOKEN_ENV}".'
            )
            return os.EX_USAGE

        token = os.environ.get(self.push_token_env)
        if not token:
            logger.critical(
                f'Deleting from collider repository requires bearer token in "{self.push_token_env}". '
                f'Export it first, e.g. `export {self.push_token_env}=<token>`.'
            )
            return os.EX_USAGE

        if not may_send_push_token(repo.url, insecure=self.insecure):
            return os.EX_USAGE

        repo_key = make_repo_key(self.package_name, self.version, PackageType.WRAP)
        encoded_package_name = urllib.parse.quote(self.package_name, safe='')
        encoded_version = urllib.parse.quote(self.version, safe='')
        path = f'{_COLLIDER_DELETE_ENDPOINT}{encoded_package_name}/{encoded_version}'
        url = urllib.parse.urljoin(repo.url.geturl(), path)
        request = urllib.request.Request(url, method='DELETE')
        request.add_header('Authorization', f'Bearer {token}')

        try:
            with safe_urlopen(request, timeout=DEFAULT_NETWORK_TIMEOUT) as response:
                if response.status in (200, 204):
                    # Keep local remote-index cache in sync after successful delete.
                    repo.packages.pop(repo_key, None)
                    logger.info(
                        f'Package "{self.package_name}" version "{self.version}" removed from '
                        f'repository "{self.repository_name}".'
                    )
                    return os.EX_OK
                logger.critical(
                    f'Remote delete failed with unexpected HTTP status {response.status}.'
                )
                return os.EX_IOERR
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            if exc.code == 404:
                logger.critical(
                    f'Package "{self.package_name}" version "{self.version}" not found on remote.'
                )
                return os.EX_NOINPUT
            if exc.code in (401, 403):
                logger.critical(f'Remote delete failed with HTTP {exc.code}: {body}')
                return os.EX_NOPERM
            logger.critical(f'Remote delete failed with HTTP {exc.code}: {body}')
            return os.EX_IOERR
        except urllib.error.URLError as exc:
            logger.critical(f'Failed to reach delete endpoint: {exc}')
            return os.EX_IOERR
