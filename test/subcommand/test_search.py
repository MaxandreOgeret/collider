# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import argparse
import os
import re

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packaging.specifiers import SpecifierSet

from collider.cache import WrapCache
from collider.Context import Context
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Search import Search
from collider.utils.packaging import compute_file_hash


@pytest.fixture
def mock_context():
    """Create a mock context with repositories."""
    mock_repo1 = MagicMock(spec=RepositoryInterface)
    mock_repo2 = MagicMock(spec=RepositoryInterface)

    config = MagicMock()
    config.repositories = {'repo1': mock_repo1, 'repo2': mock_repo2}

    context = MagicMock(spec=Context)
    context.config = config
    context.cache = MagicMock(spec=WrapCache)
    context.cache.has_package.return_value = False
    return context


def test_search_register():
    """Test that the search subcommand registers its arguments correctly."""
    parser = argparse.ArgumentParser()
    Search.register(parser)

    # Test with required pattern only
    args = parser.parse_args(['my-pkg'])
    assert args.pattern == 'my-pkg'
    assert args.repository is None
    assert args.version is None

    # Test with all arguments
    args = parser.parse_args(['-r', 'repo1', '.*', '--version', '>=1.0.0'])
    assert args.pattern == '.*'
    assert args.repository == ['repo1']
    assert isinstance(args.version, SpecifierSet)
    assert str(args.version) == '>=1.0.0'

    args = parser.parse_args(['.*', '-r', 'repo1', '-r', 'repo2'])
    assert args.pattern == '.*'
    assert args.repository == ['repo1', 'repo2']


def test_search_init(mock_context):
    """Test Search initialization and regex compilation."""
    # Test valid initialization
    args = argparse.Namespace(
        pattern='my-pkg.*',
        repository=['repo1'],
        version=SpecifierSet('>=1.0.0'),
        cache=False,
    )
    search_cmd = Search(args, mock_context)

    assert search_cmd.repository_names == ['repo1']
    assert search_cmd.version_pattern == SpecifierSet('>=1.0.0')
    assert search_cmd.name_pattern.pattern == 'my-pkg.*'

    # Test invalid regex (should fail when user fixes the implementation bug)
    args_invalid = argparse.Namespace(pattern='[', repository=None, version=None, cache=False)
    with pytest.raises(re.error):
        Search(args_invalid, mock_context)


def test_search_execute_all_repos(mock_context, caplog):
    """Test execution when searching in all repositories."""
    args = argparse.Namespace(pattern='.*', repository=None, version=None, cache=False)
    search_cmd = Search(args, mock_context)

    entry = RepoPackageEntry('demo', '1.0.0')
    for repo in mock_context.config.repositories.values():
        repo.search.return_value = {'demo@1.0.0#wrap': entry}

    exit_code = search_cmd.execute()

    assert exit_code == os.EX_OK
    # Verify that all repositories were searched
    for repo in mock_context.config.repositories.values():
        repo.search.assert_called_once()
    assert '<wrap>' not in caplog.text
    assert '#wrap' not in caplog.text


def test_search_execute_specific_repo(mock_context, caplog):
    """Test execution when searching in a specific repository."""
    args = argparse.Namespace(pattern='.*', repository=['repo1'], version=None, cache=False)
    search_cmd = Search(args, mock_context)

    entry = RepoPackageEntry('demo', '1.0.0')
    mock_context.config.repositories['repo1'].search.return_value = {'demo@1.0.0#wrap': entry}

    exit_code = search_cmd.execute()

    assert exit_code == os.EX_OK
    mock_context.config.repositories['repo1'].search.assert_called_once()
    mock_context.config.repositories['repo2'].search.assert_not_called()
    assert '<wrap>' not in caplog.text
    assert '#wrap' not in caplog.text


def test_search_marks_cached_packages(mock_context, caplog):
    args = argparse.Namespace(pattern='.*', repository=['repo1'], version=None, cache=False)
    search_cmd = Search(args, mock_context)

    entry = RepoPackageEntry('demo', '1.0.0')
    mock_context.config.repositories['repo1'].search.return_value = {'demo@1.0.0#wrap': entry}
    mock_context.cache.has_package.return_value = True

    exit_code = search_cmd.execute()

    assert exit_code == os.EX_OK
    assert '[cached]' in caplog.text


def test_search_cache_only(tmp_path: Path, caplog):
    cache = WrapCache(tmp_path / 'cache')

    content = b'payload'
    archive = tmp_path / 'demo.tar.xz'
    archive.write_bytes(content)
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/demo.tar.xz\n'
        'source_filename=demo.tar.xz\n'
        f'source_hash={compute_file_hash(archive)}\n'
    )
    package = WrapPackage.from_wrap_text('demo', '1.0.0', wrap_text)
    cache.store_wrap(package)
    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (cache.archives_dir / f'{package.source_hash}-demo.tar.xz').write_bytes(content)

    config = MagicMock()
    config.repositories = {}
    context = MagicMock(spec=Context)
    context.config = config
    context.cache = cache

    args = argparse.Namespace(pattern='demo', repository=None, version=None, cache=True)
    search_cmd = Search(args, context)

    exit_code = search_cmd.execute()

    assert exit_code == os.EX_OK
    assert '‣ cache' in caplog.text
    assert 'demo (1.0.0) [cached]' in caplog.text


def test_search_execute_repo_not_found(mock_context, caplog):
    """Test execution when a specified repository is not found."""
    args = argparse.Namespace(pattern='.*', repository=['nonexistent'], version=None, cache=False)
    search_cmd = Search(args, mock_context)

    exit_code = search_cmd.execute()

    # It should probably return an error code or just skip and log a warning
    # Following KISS, let's assume it logs a warning and returns an error code if none found
    assert exit_code != os.EX_OK
    assert 'Not all specified repositories exist. Missing: "nonexistent"' in caplog.text
