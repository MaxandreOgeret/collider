# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ

import argparse
import base64
import hashlib
import io
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.Context import Context
from collider.repository.implementation.Filesystem import Filesystem
from collider.subcommand.Serve import (
    _COLLIDER_PUSH_PATH,
    _MAX_PUSH_BODY_BYTES,
    BearerTokenAuthProvider,
    DisabledPushAuthProvider,
    Serve,
    WrapApiHandler,
    is_allowed_wrap_path,
    is_collider_delete_path,
    is_collider_push_path,
    is_loopback_host,
)


def _make_context() -> Context:
    return MagicMock(spec=Context)


def _serve_args(
    path: Path | str = 'repo',
    host: str = '127.0.0.1',
    port: int = 8000,
    push_token: str | None = None,
    push_token_env: str = 'COLLIDER_PUSH_TOKEN',
    publish_url: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=Path(path),
        host=host,
        port=port,
        push_token=push_token,
        push_token_env=push_token_env,
        publish_url=publish_url,
    )


def _start_server(
    repo: Filesystem,
    auth_provider=None,
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = partial(
        WrapApiHandler,
        directory=repo.path.as_posix(),
        repo=repo,
        push_lock=threading.Lock(),
        auth_provider=auth_provider or DisabledPushAuthProvider(),
    )
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f'http://{host}:{port}'


def _request_json(
    base: str,
    path: str,
    payload: dict[str, object],
    token: str | None = None,
    authorization_header: str | None = None,
) -> tuple[int, dict[str, object]]:
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        f'{base}{path}',
        data=data,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Content-Length': str(len(data)),
        },
    )
    if token is not None:
        request.add_header('Authorization', f'Bearer {token}')
    if authorization_header is not None:
        request.add_header('Authorization', authorization_header)

    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode('utf-8')
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8')
        return exc.code, json.loads(body)


def _push_payload(
    name: str = 'demo',
    version: str = '1.0.0',
    source_data: bytes = b'archive',
) -> dict[str, object]:
    source_filename = f'{name}-{version}.tar.xz'
    source_hash = hashlib.sha256(source_data).hexdigest()
    wrap_text = (
        '[wrap-file]\n'
        f'source_url=https://example.com/{source_filename}\n'
        f'source_filename={source_filename}\n'
        f'source_hash={source_hash}\n'
    )
    return {
        'name': name,
        'version': version,
        'wrap_text': wrap_text,
        'source_filename': source_filename,
        'source_archive_base64': base64.b64encode(source_data).decode('ascii'),
    }


def _request_raw_json(
    base: str,
    path: str,
    body: bytes,
    token: str | None = None,
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f'{base}{path}',
        data=body,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Content-Length': str(len(body)),
        },
    )
    if token is not None:
        request.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            response_body = response.read().decode('utf-8')
            return response.status, json.loads(response_body)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode('utf-8')
        return exc.code, json.loads(response_body)


def _request_delete(
    base: str,
    path: str,
    token: str | None = None,
) -> tuple[int, dict[str, object] | None]:
    """Send DELETE request; returns (status, body). Body is None for 204."""
    request = urllib.request.Request(f'{base}{path}', method='DELETE')
    if token is not None:
        request.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body_bytes = response.read()
            if response.status == 204 or len(body_bytes) == 0:
                return response.status, None
            return response.status, json.loads(body_bytes.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        body = None
        if body_bytes:
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return exc.code, body


def _dummy_handler_request(content_length: str | None, body: bytes):
    class _Dummy:
        headers: dict[str, str]
        rfile: io.BytesIO

    dummy = _Dummy()
    headers: dict[str, str] = {}
    if content_length is not None:
        headers['Content-Length'] = content_length
    dummy.headers = headers
    dummy.rfile = io.BytesIO(body)
    return dummy


def test_serve_register() -> None:
    parser = argparse.ArgumentParser()
    Serve.register(parser)

    args = parser.parse_args(['/tmp/repo'])
    assert args.path == Path('/tmp/repo')
    assert args.host == '127.0.0.1'
    assert args.port == 8000
    assert args.push_token is None
    assert args.push_token_env == 'COLLIDER_PUSH_TOKEN'
    assert args.publish_url is None

    args = parser.parse_args(
        [
            '/tmp/repo',
            '--host',
            '0.0.0.0',
            '--port',
            '8080',
            '--push-token',
            'token',
            '--publish-url',
            'https://packages.example.com/collider/',
        ]
    )
    assert args.path == Path('/tmp/repo')
    assert args.host == '0.0.0.0'
    assert args.port == 8080
    assert args.push_token == 'token'
    assert args.publish_url == 'https://packages.example.com/collider/'


def test_serve_reads_token_from_environment() -> None:
    with patch.dict(os.environ, {'MY_PUSH_TOKEN': 'secret'}):
        cmd = Serve(
            _serve_args(push_token=None, push_token_env='MY_PUSH_TOKEN'),
            _make_context(),
        )
    assert cmd.push_token == 'secret'


def test_serve_creates_missing_repo_path(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', return_value=server):
        assert cmd.execute() == os.EX_OK

    assert repo_path.is_dir()
    assert (repo_path / 'archives').is_dir()
    assert json.loads((repo_path / 'releases.json').read_text(encoding='utf-8')) == {}
    assert 'Validated new repository endpoints successfully.' in caplog.text


def test_serve_rejects_non_directory_repo_path(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo-file'
    repo_path.write_text('not a repo', encoding='utf-8')
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    assert cmd.execute() == os.EX_USAGE
    assert 'exists but is not a directory' in caplog.text


def test_serve_new_repo_fails_when_endpoint_validation_fails(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    with patch.object(
        Serve,
        '_validate_new_repo_endpoints',
        side_effect=RuntimeError('validation smoke test failed'),
    ):
        assert cmd.execute() == os.EX_IOERR

    assert 'New repository endpoint validation failed: validation smoke test failed' in caplog.text


def test_serve_startup_failure(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', side_effect=OSError('boom')):
        assert cmd.execute() == os.EX_IOERR

    assert 'Failed to start HTTP server' in caplog.text


def test_serve_starts_and_stops(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', return_value=server) as httpd:
        assert cmd.execute() == os.EX_OK

    httpd.assert_called_once()
    server.server_close.assert_called_once()


def test_wrap_paths_allow_only_api_surface() -> None:
    assert is_allowed_wrap_path('/v2/releases.json')
    assert is_allowed_wrap_path('/v2/foo_1.2.3/foo.wrap')
    assert is_allowed_wrap_path('/v2/archives/foo_1.2.3/foo-1.2.3.tar.xz')

    assert not is_allowed_wrap_path('/')
    assert not is_allowed_wrap_path('/releases.json')
    assert not is_allowed_wrap_path('/foo_1.2.3/foo.wrap')
    assert not is_allowed_wrap_path('/archives/foo_1.2.3/foo-1.2.3.tar.xz')
    assert not is_allowed_wrap_path('/archives/')
    assert not is_allowed_wrap_path('/foo_1.2.3/')
    assert not is_allowed_wrap_path('/foo_1.2.3/foo.txt')
    assert not is_allowed_wrap_path('/other/path')
    assert not is_allowed_wrap_path('/../v2/releases.json')
    assert not is_allowed_wrap_path('/v2/archives/foo_1.2.3/../secret.txt')
    assert not is_allowed_wrap_path('/v2/releases.json?x=1')
    assert not is_allowed_wrap_path('/v2/releases.json#frag')


def test_push_path_matcher() -> None:
    assert is_collider_push_path(_COLLIDER_PUSH_PATH)
    assert not is_collider_push_path(f'{_COLLIDER_PUSH_PATH}?x=1')
    assert not is_collider_push_path('/v2/_collider/v1/other')


def test_delete_path_matcher() -> None:
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo/1.0.0') == ('foo', '1.0.0')
    assert is_collider_delete_path('/v2/_collider/v1/packages/my-pkg/2.3.4') == (
        'my-pkg',
        '2.3.4',
    )
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo/1.0.0?x=1') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo/1.0.0#frag') is None
    assert is_collider_delete_path('/v2/_collider/v1/push') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo/1.0.0/extra') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/../foo/1.0.0') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/foo/..') is None
    assert is_collider_delete_path('/v2/_collider/v1/packages/./1.0.0') is None


def test_loopback_host_matcher() -> None:
    assert is_loopback_host('127.0.0.1')
    assert is_loopback_host('::1')
    assert is_loopback_host('[::1]')
    assert is_loopback_host('localhost')
    assert not is_loopback_host('0.0.0.0')
    assert not is_loopback_host('example.com')


def test_serve_http_routes(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    archives_dir = repo_path / 'archives' / 'demo_1.0.0'
    wrap_dir = repo_path / 'demo_1.0.0'
    archives_dir.mkdir(parents=True)
    wrap_dir.mkdir(parents=True)

    (repo_path / 'releases.json').write_text('{"demo": {"versions": ["1.0.0"]}}')
    (wrap_dir / 'demo.wrap').write_text('[wrap-file]\\n')
    (archives_dir / 'demo-1.0.0.tar.xz').write_text('archive')

    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    server, thread, base = _start_server(repo)

    try:
        with urllib.request.urlopen(f'{base}/v2/releases.json', timeout=2) as response:
            assert response.status == 200
            assert response.read() == b'{"demo": {"versions": ["1.0.0"]}}'

        with urllib.request.urlopen(f'{base}/v2/demo_1.0.0/demo.wrap', timeout=2) as response:
            assert response.status == 200
            assert response.read() == b'[wrap-file]\\n'

        with urllib.request.urlopen(
            f'{base}/v2/archives/demo_1.0.0/demo-1.0.0.tar.xz', timeout=2
        ) as response:
            assert response.status == 200
            assert response.read() == b'archive'

        for path in (
            '/',
            '/releases.json',
            '/archives/',
            '/v2/archives/',
            '/demo_1.0.0/',
            '/not-allowed.txt',
            _COLLIDER_PUSH_PATH,
        ):
            try:
                urllib.request.urlopen(f'{base}{path}', timeout=2)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError(f'Expected 404 for {path}')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_head_routes(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    archives_dir = repo_path / 'archives' / 'demo_1.0.0'
    wrap_dir = repo_path / 'demo_1.0.0'
    archives_dir.mkdir(parents=True)
    wrap_dir.mkdir(parents=True)

    (repo_path / 'releases.json').write_text('{"demo": {"versions": ["1.0.0"]}}')
    (wrap_dir / 'demo.wrap').write_text('[wrap-file]\\n')
    (archives_dir / 'demo-1.0.0.tar.xz').write_text('archive')

    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    server, thread, base = _start_server(repo)

    try:
        for path in (
            '/v2/releases.json',
            '/v2/demo_1.0.0/demo.wrap',
            '/v2/archives/demo_1.0.0/demo-1.0.0.tar.xz',
        ):
            request = urllib.request.Request(f'{base}{path}', method='HEAD')
            with urllib.request.urlopen(request, timeout=2) as response:
                assert response.status == 200

        for path in (
            '/',
            '/releases.json',
            '/not-allowed.txt',
            '/v2/archives/demo_1.0.0/%2E%2E/secret.txt',
        ):
            request = urllib.request.Request(f'{base}{path}', method='HEAD')
            try:
                urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError(f'Expected 404 for HEAD {path}')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_post_rejects_non_push_routes(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    request = urllib.request.Request(
        f'{base}/v2/releases.json',
        data=b'{}',
        method='POST',
        headers={'Content-Type': 'application/json', 'Content-Length': '2'},
    )
    try:
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('Expected 404 for POST /v2/releases.json')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_push_endpoint_disabled_without_token(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, DisabledPushAuthProvider())

    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 403
    assert body['error'] == 'Push endpoint is disabled.'


def test_push_endpoint_requires_valid_bearer_token(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status_missing, body_missing = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload())
        status_bad, body_bad = _request_json(
            base,
            _COLLIDER_PUSH_PATH,
            _push_payload(),
            token='wrong',
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status_missing == 401
    assert body_missing['error'] == 'Missing Authorization header.'
    assert status_bad == 401
    assert body_bad['error'] == 'Invalid bearer token.'


def test_delete_endpoint_401_without_token(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_delete(base, '/v2/_collider/v1/packages/foo/1.0.0')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 401
    assert body is not None and body['error'] == 'Missing Authorization header.'


def test_delete_endpoint_403_when_push_disabled(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, DisabledPushAuthProvider())

    try:
        status, body = _request_delete(base, '/v2/_collider/v1/packages/foo/1.0.0', token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 403
    assert body is not None and body['error'] == 'Push endpoint is disabled.'


def test_delete_endpoint_404_package_not_found(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_delete(
            base, '/v2/_collider/v1/packages/nonexistent/1.0.0', token='secret'
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 404
    assert body is not None and 'not found' in body['error']


def test_delete_endpoint_204_success(tmp_path: Path) -> None:
    from collider.Package import WrapPackage

    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        '\n[provide]\nfoo = foo_dep\n'
    )
    package = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    repo.add_package(package)

    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_delete(base, '/v2/_collider/v1/packages/foo/1.0.0', token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 204
    assert body is None
    assert not (repo_path / 'foo_1.0.0' / 'foo.wrap').exists()
    assert len(repo.packages) == 0


def test_delete_endpoint_404_for_invalid_path(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, _ = _request_delete(base, '/v2/_collider/v1/packages/../foo/1.0.0', token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 404


def test_push_endpoint_rejects_non_bearer_auth_scheme(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_json(
            base,
            _COLLIDER_PUSH_PATH,
            _push_payload(),
            authorization_header='bearer secret',
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 401
    assert body['error'] == 'Authorization header must use Bearer token.'


def test_push_endpoint_rejects_invalid_json_body(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_raw_json(base, _COLLIDER_PUSH_PATH, b'{', token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == 'Request body must be valid UTF-8 JSON.'


def test_push_endpoint_rejects_non_object_json(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_raw_json(base, _COLLIDER_PUSH_PATH, b'[]', token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == 'Request body must be a JSON object.'


def test_push_endpoint_rejects_missing_required_fields(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, {'name': 'demo'}, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == 'Missing or invalid "version".'


def test_push_endpoint_rejects_source_filename_mismatch(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    payload = _push_payload()
    payload['source_filename'] = 'other.tar.xz'
    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, payload, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == 'source_filename must match wrap source_filename.'


def test_push_endpoint_rejects_invalid_base64_source(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    payload = _push_payload()
    payload['source_archive_base64'] = 'not-base64'
    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, payload, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == '"source_archive_base64" must be valid base64.'


def test_push_endpoint_rejects_incomplete_patch_payload(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    payload = _push_payload()
    payload['patch_filename'] = 'demo.patch'
    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, payload, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == (
        'Patch payload is incomplete. Provide both patch_filename and patch_archive_base64.'
    )


def test_push_endpoint_rejects_unsafe_package_name(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    payload = _push_payload()
    payload['name'] = '../demo'
    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, payload, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == '"name" must be a safe path segment.'


def test_push_endpoint_rejects_unsafe_package_version(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    payload = _push_payload()
    payload['version'] = '1.0.0/../../evil'
    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, payload, token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body['error'] == '"version" must be a safe path segment.'


def test_push_endpoint_concurrent_duplicate_publish(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    statuses: list[int] = []
    bodies: list[dict[str, object]] = []
    status_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _worker() -> None:
        barrier.wait(timeout=2)
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload(), token='secret')
        with status_lock:
            statuses.append(status)
            bodies.append(body)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    try:
        assert sorted(statuses) == [201, 409]
        assert any(body.get('status') == 'ok' for body in bodies)
        assert any('already exists in repository' in str(body.get('error', '')) for body in bodies)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_read_push_payload_requires_content_length() -> None:
    dummy = _dummy_handler_request(content_length=None, body=b'{}')
    with pytest.raises(ValueError, match='Missing Content-Length header'):
        WrapApiHandler._read_push_payload(dummy)  # type: ignore[arg-type]


def test_read_push_payload_rejects_invalid_content_length() -> None:
    dummy = _dummy_handler_request(content_length='abc', body=b'{}')
    with pytest.raises(ValueError, match='Invalid Content-Length header'):
        WrapApiHandler._read_push_payload(dummy)  # type: ignore[arg-type]


def test_read_push_payload_rejects_oversized_body() -> None:
    dummy = _dummy_handler_request(content_length=str(_MAX_PUSH_BODY_BYTES + 1), body=b'')
    with pytest.raises(RuntimeError, match='Request body is too large'):
        WrapApiHandler._read_push_payload(dummy)  # type: ignore[arg-type]


def test_read_push_payload_rejects_truncated_body() -> None:
    dummy = _dummy_handler_request(content_length='4', body=b'{}')
    with pytest.raises(ValueError, match='Request body is truncated'):
        WrapApiHandler._read_push_payload(dummy)  # type: ignore[arg-type]


def test_push_endpoint_redacts_unexpected_errors(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        with patch.object(repo, 'add_package', side_effect=Exception('secret /tmp/path leaked')):
            status, body = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload(), token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 500
    assert body['error'] == 'Internal server error.'


def test_push_endpoint_publishes_package(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status, body = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload(), token='secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 201
    assert body['status'] == 'ok'
    assert (repo_path / 'demo_1.0.0' / 'demo.wrap').exists()
    assert (repo_path / 'archives' / 'demo_1.0.0' / 'demo-1.0.0.tar.xz').exists()

    releases = json.loads((repo_path / 'releases.json').read_text(encoding='utf-8'))
    assert releases['demo']['versions'] == ['1.0.0']


def test_push_endpoint_rejects_duplicate_package(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base = _start_server(repo, BearerTokenAuthProvider('secret'))

    try:
        status_first, _ = _request_json(base, _COLLIDER_PUSH_PATH, _push_payload(), token='secret')
        status_second, body_second = _request_json(
            base,
            _COLLIDER_PUSH_PATH,
            _push_payload(),
            token='secret',
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status_first == 201
    assert status_second == 409
    assert 'already exists in repository' in body_second['error']


def test_serve_warns_when_push_token_enabled(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    cmd = Serve(_serve_args(path=repo_path, push_token='secret'), _make_context())

    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', return_value=server):
        assert cmd.execute() == os.EX_OK

    assert 'Push endpoint uses static bearer token auth.' in caplog.text
    assert (
        'No --publish-url provided; pushed wraps will reference file:// archive URLs.'
        in caplog.text
    )
    assert 'non-loopback host' not in caplog.text


def test_serve_warns_when_push_token_on_non_loopback_host(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    cmd = Serve(_serve_args(path=repo_path, host='0.0.0.0', push_token='secret'), _make_context())

    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', return_value=server):
        assert cmd.execute() == os.EX_OK

    assert 'Push endpoint uses static bearer token auth.' in caplog.text
    assert 'Push endpoint is bound to non-loopback host "0.0.0.0".' in caplog.text
