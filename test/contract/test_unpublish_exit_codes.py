# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `unpublish` command."""

import argparse
import io
import os
import urllib.error
import urllib.parse

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.Context import Context
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.Unpublish import Unpublish
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from test.common.common import Subcommand, run_subcommand


def _wrap_text() -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        '\n'
        '[provide]\n'
        'foo = foo_dep\n'
    )


def _args(repository: str) -> argparse.Namespace:
    return argparse.Namespace(
        repository=repository,
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )


def _context(repositories: dict) -> Context:
    config = MagicMock()
    config.repositories = repositories
    context = MagicMock(spec=Context)
    context.config = config
    return context


def test_unpublish_ex_noinput_repository_not_in_config() -> None:
    """`unpublish` returns EX_NOINPUT when the named repository is absent from config."""
    # Deterministic via run_subcommand: a bootstrapped empty config has no such repo.
    exit_code = run_subcommand(
        Subcommand.UNPUBLISH,
        ['nonexistent-repo', 'some-package', '1.0.0'],
    )

    assert exit_code == os.EX_NOINPUT


def test_unpublish_ex_usage_wrap_repository() -> None:
    """`unpublish` returns EX_USAGE when the resolved repository is a read-only wrap repo."""
    packages = {
        make_repo_key('foo', '1.0.0', PackageType.WRAP): RepoPackageEntry(
            'foo', '1.0.0', PackageType.WRAP
        ),
    }
    repo = Wrap(urllib.parse.urlparse('https://wrapdb.example/v2/'), packages)
    cmd = Unpublish(_args('wrapdb'), _context({'wrapdb': repo}))

    assert cmd.execute() == os.EX_USAGE


def test_unpublish_ex_ioerr_get_package_failure(tmp_path: Path) -> None:
    """`unpublish` returns EX_IOERR when loading the package from a filesystem repo raises OSError."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    repo.add_package(WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text()))
    cmd = Unpublish(_args('local'), _context({'local': repo}))

    with patch.object(repo, 'get_package', side_effect=OSError('Read failed.')):
        assert cmd.execute() == os.EX_IOERR


def test_unpublish_ex_ok_filesystem_removal(tmp_path: Path) -> None:
    """`unpublish` returns EX_OK when the package is loaded and removed from a filesystem repo."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    repo.add_package(WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text()))

    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    wrap_path = repo_path / 'foo_1.0.0' / 'foo.wrap'
    assert repo_key in repo.packages
    assert wrap_path.exists()

    cmd = Unpublish(_args('local'), _context({'local': repo}))

    assert cmd.execute() == os.EX_OK
    assert repo_key not in repo.packages
    assert not wrap_path.exists()


@pytest.mark.parametrize('status_code', [401, 403])
def test_unpublish_ex_noperm_collider_auth_error(status_code: int) -> None:
    """`unpublish` returns EX_NOPERM when a collider remote answers with HTTP 401 or 403."""
    repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    cmd = Unpublish(_args('remote'), _context({'remote': repo}))

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('collider.subcommand.Unpublish.safe_urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                'https://packages.example.com/v2/_collider/v1/packages/foo/1.0.0',
                status_code,
                'Auth error.',
                {},
                io.BytesIO(b'{"error": "Auth failed."}'),
            )
            assert cmd.execute() == os.EX_NOPERM
