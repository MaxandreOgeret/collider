# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Shared helpers for repository selection in CLI commands."""

from __future__ import annotations

import argparse
import os

from typing import Optional

from collider.Context import Context
from collider.log import logger
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.RepositoryInterface import RepositoryInterface


def add_repository_filter_argument(parser: argparse.ArgumentParser) -> None:
    """Keep repository filter flags consistent across subcommands."""
    parser.add_argument(
        '--repository',
        '-r',
        type=str,
        action='append',
        required=False,
        help='Name of the repository to inspect.',
    )


def resolve_repositories(
    context: Context, repository_names: Optional[list[str]]
) -> dict[str, RepositoryInterface] | None:
    """Centralize repository selection and error messaging."""
    missing_repos = set(repository_names or []) - set(context.config.repositories)
    if missing_repos:
        quoted_repos = ', '.join(f'"{repo}"' for repo in sorted(missing_repos))
        logger.critical(f'Not all specified repositories exist. Missing: {quoted_repos}')
        return None

    return {
        name: repo
        for name, repo in context.config.repositories.items()
        if not repository_names or name in repository_names
    }


def resolve_filesystem_repository(
    context: Context,
    repository_name: str,
    *,
    action: str,
) -> tuple[Filesystem | None, int]:
    """Resolve a filesystem repository or return an error code."""
    if repository_name not in context.config.repositories:
        logger.critical(f'Repository "{repository_name}" not found in config.')
        return None, os.EX_NOINPUT

    repo = context.config.repositories[repository_name]
    if not isinstance(repo, Filesystem):
        logger.critical(f'{action} is only supported for filesystem repositories.')
        return None, os.EX_USAGE

    return repo, os.EX_OK
