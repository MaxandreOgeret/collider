# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the delete subcommand."""

import argparse
import io
import os
import threading
import urllib.error
import urllib.parse

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.Context import Context
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.Serve import BearerTokenAuthProvider, WrapApiHandler
from collider.subcommand.Unpublish import Unpublish
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


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


def test_delete_registers_arguments() -> None:
    """Unpublish subcommand registers repository, package, version, and --push-token-env."""
    parser = argparse.ArgumentParser()
    Unpublish.register(parser)
    args = parser.parse_args(['my-repo', 'foo', '1.0.0'])
    assert args.repository == 'my-repo'
    assert args.package == 'foo'
    assert args.version == '1.0.0'
    assert hasattr(args, 'push_token_env')


def _start_delete_server(
    repo: Filesystem, token: str
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Start HTTP server with push/delete handler; return (server, thread, base_url)."""
    handler = partial(
        WrapApiHandler,
        directory=repo.path.as_posix(),
        repo=repo,
        push_lock=threading.Lock(),
        auth_provider=BearerTokenAuthProvider(token),
    )
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f'http://{host}:{port}'


def test_delete_execute_removes_package_from_filesystem_repo(tmp_path: Path) -> None:
    """Delete removes the package and updates the repository."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())
    repo.add_package(package)

    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    assert repo_key in repo.packages
    wrap_path = repo_path / 'foo_1.0.0' / 'foo.wrap'
    assert wrap_path.exists()

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='local',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    assert cmd.execute() == os.EX_OK
    assert repo_key not in repo.packages
    assert not wrap_path.exists()


def test_delete_execute_package_not_in_repo_returns_ex_noinput(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Delete returns EX_NOINPUT when the package is not in the repository."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='local',
        package='nonexistent',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    exit_code = cmd.execute()
    assert exit_code == os.EX_NOINPUT
    assert 'does not exist' in caplog.text


def test_delete_execute_repository_not_filesystem_or_collider_returns_ex_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete returns EX_USAGE when the repository is a read-only wrap repo."""
    packages = {
        make_repo_key('foo', '1.0.0', PackageType.WRAP): RepoPackageEntry(
            'foo', '1.0.0', PackageType.WRAP
        ),
    }
    remote_repo = Wrap(urllib.parse.urlparse('https://wrapdb.example/v2/'), packages)
    config = MagicMock()
    config.repositories = {'wrapdb': remote_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='wrapdb',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    exit_code = cmd.execute()
    assert exit_code == os.EX_USAGE
    assert 'filesystem or collider' in caplog.text


def test_delete_execute_repository_missing_returns_ex_noinput(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete returns EX_NOINPUT when the repository is not in config."""
    config = MagicMock()
    config.repositories = {}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='missing',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    exit_code = cmd.execute()
    assert exit_code == os.EX_NOINPUT
    assert 'not found' in caplog.text


def test_delete_execute_collider_without_token_returns_ex_usage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Delete to collider repo without push token returns EX_USAGE."""
    packages = {
        make_repo_key('foo', '1.0.0', PackageType.WRAP): RepoPackageEntry(
            'foo', '1.0.0', PackageType.WRAP
        ),
    }
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), packages)
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {}, clear=True):
        exit_code = cmd.execute()
    assert exit_code == os.EX_USAGE
    assert 'COLLIDER_PUSH_TOKEN' in caplog.text
    assert 'export COLLIDER_PUSH_TOKEN=<token>' in caplog.text


def test_delete_execute_collider_success(tmp_path: Path) -> None:
    """Delete to collider repo removes package via DELETE endpoint."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    repo.add_package(package)

    server, thread, base_url = _start_delete_server(repo, token='secret')
    try:
        collider_repo = Collider(
            urllib.parse.urlparse(f'{base_url}/v2/'),
            dict(repo.packages),
        )
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        args = argparse.Namespace(
            repository='remote',
            package='foo',
            version='1.0.0',
            push_token_env='COLLIDER_PUSH_TOKEN',
        )
        cmd = Unpublish(args, context)

        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
            assert cmd.execute() == os.EX_OK
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert not (repo_path / 'foo_1.0.0' / 'foo.wrap').exists()
    assert len(repo.packages) == 0
    assert len(collider_repo.packages) == 0


def test_delete_execute_collider_remote_404_returns_ex_noinput(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete to collider repo when remote returns 404 returns EX_NOINPUT."""
    packages = {}
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), packages)
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='nonexistent',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            err = urllib.error.HTTPError(
                'https://packages.example.com/v2/_collider/v1/packages/nonexistent/1.0.0',
                404,
                'Not Found',
                {},
                io.BytesIO(b'{"error": "Package not found."}'),
            )
            mock_urlopen.side_effect = err
            exit_code = cmd.execute()
    assert exit_code == os.EX_NOINPUT
    assert 'not found on remote' in caplog.text


@pytest.mark.parametrize('status_code', [401, 403])
def test_delete_execute_collider_auth_errors_return_ex_noperm(
    status_code: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Delete to collider repo returns EX_NOPERM for authentication and authorization failures."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            err = urllib.error.HTTPError(
                'https://packages.example.com/v2/_collider/v1/packages/foo/1.0.0',
                status_code,
                'Auth error.',
                {},
                io.BytesIO(b'{"error": "Auth failed."}'),
            )
            mock_urlopen.side_effect = err
            exit_code = cmd.execute()
    assert exit_code == os.EX_NOPERM
    assert f'HTTP {status_code}' in caplog.text


def test_delete_execute_collider_remote_500_returns_ex_ioerr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete to collider repo when remote returns 500 returns EX_IOERR."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            err = urllib.error.HTTPError(
                'https://packages.example.com/v2/_collider/v1/packages/foo/1.0.0',
                500,
                'Server error.',
                {},
                io.BytesIO(b'{"error": "Internal server error."}'),
            )
            mock_urlopen.side_effect = err
            exit_code = cmd.execute()
    assert exit_code == os.EX_IOERR
    assert 'HTTP 500' in caplog.text


def test_delete_execute_filesystem_get_package_failure_returns_ex_ioerr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Delete returns EX_IOERR when loading package from filesystem repository fails."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())
    repo.add_package(package)

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='local',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.object(repo, 'get_package', side_effect=OSError('Read failed.')):
        exit_code = cmd.execute()

    assert exit_code == os.EX_IOERR
    assert 'Failed to load package "foo" "1.0.0" from repository' in caplog.text


def test_delete_execute_filesystem_remove_package_failure_returns_ex_ioerr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Delete returns EX_IOERR when removing package from filesystem repository fails."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())
    repo.add_package(package)

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='local',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.object(repo, 'remove_package', side_effect=OSError('Delete failed.')):
        exit_code = cmd.execute()

    assert exit_code == os.EX_IOERR
    assert 'Failed to remove package' in caplog.text


def test_delete_execute_collider_url_encodes_segments() -> None:
    """Delete URL-encodes package and version path segments for remote requests."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo#bar',
        version='1.0.0?rc1',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 204
            mock_urlopen.return_value = response
            exit_code = cmd.execute()

    assert exit_code == os.EX_OK
    request = mock_urlopen.call_args[0][0]
    assert (
        request.full_url
        == 'https://packages.example.com/v2/_collider/v1/packages/foo%23bar/1.0.0%3Frc1'
    )


def test_delete_execute_collider_url_encodes_slashes_in_segments() -> None:
    """Delete URL-encodes slash-like characters in package and version segments."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo/bar',
        version='1.0.0/rc1',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 204
            mock_urlopen.return_value = response
            exit_code = cmd.execute()

    assert exit_code == os.EX_OK
    request = mock_urlopen.call_args[0][0]
    assert (
        request.full_url
        == 'https://packages.example.com/v2/_collider/v1/packages/foo%2Fbar/1.0.0%2Frc1'
    )


def test_delete_execute_collider_unexpected_http_status_returns_ex_ioerr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete to collider repo returns EX_IOERR on unexpected non-success HTTP status."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 202
            mock_urlopen.return_value = response
            exit_code = cmd.execute()

    assert exit_code == os.EX_IOERR
    assert 'unexpected HTTP status 202' in caplog.text


def test_delete_execute_collider_urlerror_returns_ex_ioerr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete to collider repo returns EX_IOERR when URL open fails with URLError."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='COLLIDER_PUSH_TOKEN',
    )
    cmd = Unpublish(args, context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError('Connection reset.')
            exit_code = cmd.execute()

    assert exit_code == os.EX_IOERR
    assert 'Failed to reach delete endpoint' in caplog.text


def test_delete_execute_collider_empty_push_token_env_name_returns_ex_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Delete to collider repo with empty token env variable name returns EX_USAGE."""
    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(
        repository='remote',
        package='foo',
        version='1.0.0',
        push_token_env='',
    )
    cmd = Unpublish(args, context)

    exit_code = cmd.execute()
    assert exit_code == os.EX_USAGE
    assert 'Push token env var name must not be empty.' in caplog.text
    assert '--push-token-env' in caplog.text
