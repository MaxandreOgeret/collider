# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Manage repository configuration."""

from __future__ import annotations

import argparse
import os
import urllib.parse

from pathlib import Path
from typing import Optional, cast

from collider import config
from collider.errors import ColliderUserError
from collider.file_model.configfile import ConfigFile, RepoEntry
from collider.log import logger
from collider.repository import RepoImplRegistry
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override


def _repo_subcommands() -> tuple[str, ...]:
    """Return the canonical list of repo actions exposed by the CLI."""
    return ('add', 'remove', 'list', 'ls', 'rm')


def _repo_help_summary() -> str:
    """Build the help line from available repo subcommands."""
    return 'Repository configuration: add, list, ls, remove, rm.'


class Repo(SubcommandInterface):
    """Manage repository configuration."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return _repo_help_summary()

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider repo --help`."""
        del cls
        return 'Add, remove, and list repository entries in collider config.'

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        subparsers = parser.add_subparsers(dest='repo_subcommand', required=True)

        add_parser = subparsers.add_parser(
            'add',
            help='Add a repository to config.',
            description=(
                'Register a repository in collider config so package commands can use it.\n'
                'Filesystem repositories require --publish-url.'
            ),
        )
        add_parser.add_argument('name', type=str, help='Repository name in config.')
        add_parser.add_argument(
            'type',
            choices=sorted(RepoImplRegistry.get_impls().keys()),
            help='Repository backend type.',
        )
        add_parser.add_argument('url', type=str, help='Repository URL.')
        add_parser.add_argument(
            '--publish-url',
            type=str,
            default=None,
            help='Required for filesystem repositories; ignored for wrap repositories.',
        )
        add_parser.set_defaults(repo_action='add')

        remove_parser = subparsers.add_parser(
            'remove',
            aliases=['rm'],
            help='Remove a repository from config.',
            description='Remove a repository entry from collider config.',
        )
        remove_parser.add_argument('name', type=str, help='Repository name to remove.')
        remove_parser.set_defaults(repo_action='remove')

        list_parser = subparsers.add_parser(
            'list',
            aliases=['ls'],
            help='List configured repositories.',
            description='List the repositories configured in collider config.',
        )
        list_parser.set_defaults(repo_action='list')

    @override
    def execute(self) -> int:
        """Run the repo command.
        :return: Exit code.
        """
        action = getattr(self.args, 'repo_action', None)
        if action == 'add':
            return self._execute_add()
        if action == 'remove':
            return self._execute_remove()
        if action == 'list':
            return self._execute_list()

        logger.critical(f'Unknown repo subcommand: {action}')
        return os.EX_USAGE

    def _execute_add(self) -> int:
        repo_name = self.args.name
        repo_type_name = self.args.type
        repo_url = self.args.url
        publish_url = self.args.publish_url

        try:
            RepoImplRegistry.get(repo_type_name)
        except KeyError:
            logger.critical(f'Unknown repository type "{repo_type_name}".')
            return os.EX_USAGE

        if repo_type_name == 'filesystem' and not publish_url:
            logger.critical('Filesystem repositories require --publish-url.')
            return os.EX_USAGE

        if repo_type_name != 'filesystem' and publish_url is not None:
            logger.warning('Ignoring --publish-url for non-filesystem repository.')
            publish_url = None

        config_path = config.get_config_path()
        config_file = self._load_or_create_config(config_path)
        if config_file is None:
            return os.EX_DATAERR

        if any(entry.name == repo_name for entry in config_file.repositories):
            logger.critical(f'Repository "{repo_name}" already exists in config.')
            return os.EX_DATAERR

        normalized_repo_url = self._normalize_repo_url(repo_url)
        duplicate_url_names = [
            entry.name
            for entry in config_file.repositories
            if self._normalize_repo_url(entry.url) == normalized_repo_url
        ]
        if duplicate_url_names:
            duplicates = ', '.join(sorted(duplicate_url_names))
            logger.warning(f'Repository URL "{repo_url}" is already configured by: {duplicates}.')

        config_file.repositories.append(
            RepoEntry(
                name=repo_name,
                type=cast(RepoImplRegistry, RepoImplRegistry.get(repo_type_name)),
                url=repo_url,
                publish_url=publish_url,
            )
        )

        try:
            config_file.save(config_path)
        except TypeError as exc:
            logger.critical(f'Failed to validate config while adding repository: {exc}')
            return os.EX_DATAERR
        except OSError as exc:
            logger.critical(f'Failed to save config at "{config_path.as_posix()}": {exc}')
            return os.EX_IOERR

        logger.info(f'Added repository "{repo_name}" ({repo_type_name}).')
        return os.EX_OK

    def _execute_remove(self) -> int:
        repo_name = self.args.name

        config_path = config.get_config_path()
        config_file = self._load_or_create_config(config_path)
        if config_file is None:
            return os.EX_DATAERR

        original_count = len(config_file.repositories)
        config_file.repositories = [
            entry for entry in config_file.repositories if entry.name != repo_name
        ]

        if len(config_file.repositories) == original_count:
            logger.critical(f'Repository "{repo_name}" not found in config.')
            return os.EX_NOINPUT

        try:
            config_file.save(config_path)
        except TypeError as exc:
            logger.critical(f'Failed to validate config while removing repository: {exc}')
            return os.EX_DATAERR
        except OSError as exc:
            logger.critical(f'Failed to save config at "{config_path.as_posix()}": {exc}')
            return os.EX_IOERR

        logger.info(f'Removed repository "{repo_name}".')
        return os.EX_OK

    def _execute_list(self) -> int:
        config_path = config.get_config_path()
        config_file = self._load_or_create_config(config_path)
        if config_file is None:
            return os.EX_DATAERR

        if not config_file.repositories:
            logger.info('No repositories configured.')
            return os.EX_OK

        for entry in sorted(config_file.repositories, key=lambda item: item.name):
            logger.info(f'{entry.name}:')
            logger.info(f'  type: {entry.type.name}')
            logger.info(f'  url: {entry.url}')
            if entry.publish_url is not None:
                logger.info(f'  publish_url: {entry.publish_url}')

        return os.EX_OK

    @staticmethod
    def _load_or_create_config(config_path: Path) -> Optional[ConfigFile]:
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            default_config = ConfigFile()
            try:
                default_config.save(config_path)
            except Exception as exc:
                logger.critical(f'Failed to initialize config at "{config_path.as_posix()}": {exc}')
                return None

        try:
            return ConfigFile.from_path(config_path)
        except ColliderUserError:
            # from_path already reported the problem; Repo degrades to a clean error return.
            return None

    @staticmethod
    def _normalize_repo_url(url: str) -> str:
        """Normalize URLs so duplicate backends are detected consistently."""
        split = urllib.parse.urlsplit(url)
        path = split.path
        if path.endswith('/') and path != '/':
            path = path.rstrip('/')
        return urllib.parse.urlunsplit(
            (
                split.scheme.lower(),
                split.netloc.lower(),
                path,
                split.query,
                split.fragment,
            )
        )
