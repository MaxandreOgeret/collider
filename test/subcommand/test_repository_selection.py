# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

from unittest.mock import MagicMock

from collider.Context import Context
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.repository_selection import (
    add_repository_filter_argument,
    resolve_filesystem_repository,
    resolve_repositories,
)


def test_add_repository_filter_argument() -> None:
    """Test that repository filter argument is added to parser."""
    import argparse

    parser = argparse.ArgumentParser()
    add_repository_filter_argument(parser)
    args = parser.parse_args([])
    assert args.repository is None
    args = parser.parse_args(['--repository', 'a', '--repository', 'b'])
    assert args.repository == ['a', 'b']
    args = parser.parse_args(['-r', 'only'])
    assert args.repository == ['only']


def test_add_repository_filter_argument_does_not_consume_positionals() -> None:
    """A repository filter should not swallow the search pattern."""
    import argparse

    parser = argparse.ArgumentParser()
    add_repository_filter_argument(parser)
    parser.add_argument('pattern')

    args = parser.parse_args(['-r', 'local', '.*'])
    assert args.repository == ['local']
    assert args.pattern == '.*'


def test_resolve_repositories_all_when_none_specified(caplog) -> None:
    """Test resolve_repositories returns all repos when repository_names is None."""
    repo_a = MagicMock(spec=RepositoryInterface)
    repo_b = MagicMock(spec=RepositoryInterface)
    config = MagicMock()
    config.repositories = {'a': repo_a, 'b': repo_b}
    context = MagicMock(spec=Context)
    context.config = config

    result = resolve_repositories(context, None)
    assert result is not None
    assert result == {'a': repo_a, 'b': repo_b}


def test_resolve_repositories_subset_when_names_specified() -> None:
    """Test resolve_repositories returns only requested repos."""
    repo_a = MagicMock(spec=RepositoryInterface)
    repo_b = MagicMock(spec=RepositoryInterface)
    config = MagicMock()
    config.repositories = {'a': repo_a, 'b': repo_b}
    context = MagicMock(spec=Context)
    context.config = config

    result = resolve_repositories(context, ['a'])
    assert result is not None
    assert result == {'a': repo_a}


def test_resolve_repositories_missing_returns_none(caplog) -> None:
    """Test resolve_repositories returns None when a requested repo is missing."""
    config = MagicMock()
    config.repositories = {'only': MagicMock()}
    context = MagicMock(spec=Context)
    context.config = config

    result = resolve_repositories(context, ['only', 'missing'])
    assert result is None
    assert 'Missing' in caplog.text and 'missing' in caplog.text


def test_resolve_filesystem_repository_found(tmp_path) -> None:
    """Test resolve_filesystem_repository returns repo and EX_OK when found and Filesystem."""
    repo = Filesystem(tmp_path / 'repo', publish_url='file:///tmp/repo')
    (tmp_path / 'repo').mkdir(parents=True, exist_ok=True)
    config = MagicMock()
    config.repositories = {'fs': repo}
    context = MagicMock(spec=Context)
    context.config = config

    resolved, code = resolve_filesystem_repository(context, 'fs', action='push')
    assert resolved is repo
    assert code == 0


def test_resolve_filesystem_repository_not_found(caplog) -> None:
    """Test resolve_filesystem_repository returns None and EX_NOINPUT when repo missing."""
    import os

    config = MagicMock()
    config.repositories = {}
    context = MagicMock(spec=Context)
    context.config = config

    resolved, code = resolve_filesystem_repository(context, 'nonexistent', action='push')
    assert resolved is None
    assert code == os.EX_NOINPUT
    assert 'not found' in caplog.text.lower()


def test_resolve_filesystem_repository_wrong_type(caplog) -> None:
    """Test resolve_filesystem_repository returns None and EX_USAGE when repo is not Filesystem."""
    import os

    config = MagicMock()
    config.repositories = {'wrap': MagicMock(spec=RepositoryInterface)}
    context = MagicMock(spec=Context)
    context.config = config

    resolved, code = resolve_filesystem_repository(context, 'wrap', action='push')
    assert resolved is None
    assert code == os.EX_USAGE
    assert 'filesystem' in caplog.text.lower()
