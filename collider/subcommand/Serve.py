# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Serve a filesystem repository over HTTP."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import http.server
import ipaddress
import json
import os
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Protocol

from collider.Context import Context
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.RepositoryInterface import PackageAlreadyExistsError
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


_ALLOWED_WRAP_PATHS = (
    re.compile(r'^/v2/releases\.json$'),
    re.compile(r'^/v2/[^/]+_[^/]+/[^/]+\.wrap$'),
    re.compile(r'^/v2/archives/[^/]+_[^/]+/[^/]+$'),
)
_WRAP_API_PREFIX = '/v2'
_COLLIDER_PUSH_PATH = '/v2/_collider/v1/push'
_COLLIDER_DELETE_PATH_PATTERN = re.compile(r'^/v2/_collider/v1/packages/([^/]+)/([^/]+)$')
_MAX_PUSH_BODY_BYTES = 128 * 1024 * 1024

_UNSAFE_PATH_SEGMENTS = ('', '.', '..')


def is_allowed_wrap_path(path: str) -> bool:
    """Restrict served paths to the Wrap API surface."""
    if not path or path == '/':
        return False

    split = urllib.parse.urlsplit(path)
    if split.query or split.fragment:
        return False
    decoded_path = urllib.parse.unquote(split.path)
    if not decoded_path.startswith('/'):
        return False

    segments = [segment for segment in decoded_path.split('/') if segment]
    if any(segment in ('.', '..') for segment in segments):
        return False

    return any(pattern.match(decoded_path) for pattern in _ALLOWED_WRAP_PATHS)


def is_collider_push_path(path: str) -> bool:
    """Match the collider write extension path exactly."""
    split = urllib.parse.urlsplit(path)
    return split.path == _COLLIDER_PUSH_PATH and not split.query and not split.fragment


def is_collider_delete_path(path: str) -> Optional[tuple[str, str]]:
    """
    Match DELETE /v2/_collider/v1/packages/<name>/<version> and return (name, version).

    Returns None if the path does not match or name/version are unsafe.
    """
    split = urllib.parse.urlsplit(path)
    if split.query or split.fragment:
        return None
    decoded_path = urllib.parse.unquote(split.path)
    match = _COLLIDER_DELETE_PATH_PATTERN.match(decoded_path)
    if not match:
        return None
    name, version = match.group(1), match.group(2)
    if name in _UNSAFE_PATH_SEGMENTS or version in _UNSAFE_PATH_SEGMENTS:
        return None
    return (name, version)


def is_loopback_host(host: str) -> bool:
    """Return True when host binds to localhost only."""
    normalized = host.strip().lower()
    if normalized.startswith('[') and normalized.endswith(']'):
        normalized = normalized[1:-1]
    if normalized == 'localhost':
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class AuthResult:
    """Result of authorizing a push request."""

    allowed: bool
    status: int
    message: str


class PushAuthProvider(Protocol):  # pylint: disable=too-few-public-methods
    """Auth provider interface so stronger auth can be plugged in later."""

    def authorize(self, authorization_header: Optional[str]) -> AuthResult:
        """Authorize a request using request auth headers."""


class DisabledPushAuthProvider:  # pylint: disable=too-few-public-methods
    """Read-only mode provider used when push auth is not configured."""

    def authorize(self, authorization_header: Optional[str]) -> AuthResult:
        """Reject all push requests when push auth is not configured."""
        del authorization_header
        return AuthResult(False, 403, 'Push endpoint is disabled.')


class BearerTokenAuthProvider:  # pylint: disable=too-few-public-methods
    """Simple bearer token auth provider."""

    def __init__(self, token: str):
        self.token = token

    def authorize(self, authorization_header: Optional[str]) -> AuthResult:
        """Validate a single shared bearer token."""
        if not authorization_header:
            return AuthResult(False, 401, 'Missing Authorization header.')

        prefix = 'Bearer '
        if not authorization_header.startswith(prefix):
            return AuthResult(False, 401, 'Authorization header must use Bearer token.')

        token = authorization_header[len(prefix) :]
        if not hmac.compare_digest(token, self.token):
            return AuthResult(False, 401, 'Invalid bearer token.')
        return AuthResult(True, 200, 'Authorized.')


class WrapApiHandler(SimpleHTTPRequestHandler):
    """HTTP handler that only serves the Wrap API surface."""

    def __init__(
        self,
        *args,
        directory: Optional[str] = None,
        repo: Optional[Filesystem] = None,
        push_lock: Optional[threading.Lock] = None,
        auth_provider: Optional[PushAuthProvider] = None,
        **kwargs,
    ):
        self.repo = repo
        self.push_lock = push_lock or threading.Lock()
        self.auth_provider: PushAuthProvider = auth_provider or DisabledPushAuthProvider()
        super().__init__(*args, directory=directory, **kwargs)

    def do_HEAD(self) -> None:
        """Serve HEAD requests for allowed Wrap API read paths only."""
        if not is_allowed_wrap_path(self.path):
            self.send_error(404, 'Not Found')
            return
        super().do_HEAD()

    def do_GET(self) -> None:
        """Serve GET requests for allowed Wrap API read paths only."""
        if not is_allowed_wrap_path(self.path):
            self.send_error(404, 'Not Found')
            return
        super().do_GET()

    def list_directory(self, path: str):  # type: ignore[override]
        """Disable directory listing for all routes."""
        self.send_error(404, 'Not Found')

    def do_POST(self) -> None:
        """Handle optional Collider push extension endpoint."""
        if not is_collider_push_path(self.path):
            self.send_error(404, 'Not Found')
            return

        auth_result = self.auth_provider.authorize(self.headers.get('Authorization'))
        if not auth_result.allowed:
            self._send_json(auth_result.status, {'error': auth_result.message})
            return

        if self.repo is None:
            self._send_json(500, {'error': 'Repository is not configured.'})
            return

        temp_dir: Optional[Path] = None
        try:
            payload = self._read_push_payload()
            package, source_archive, patch_archive, temp_dir = self._materialize_push_payload(
                payload
            )
            with self.push_lock:
                self.repo.add_package(
                    package,
                    source_archive=source_archive,
                    patch_archive=patch_archive,
                )
        except PackageAlreadyExistsError as exc:
            self._send_json(409, {'error': str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {'error': str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(413, {'error': str(exc)})
            return
        except Exception as exc:  # pragma: no cover - fallback protection
            logger.exception('Unhandled exception while processing push request: %s', exc)
            self._send_json(500, {'error': 'Internal server error.'})
            return
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self._send_json(
            201,
            {
                'status': 'ok',
                'name': package.name,
                'version': package.version,
            },
        )

    def do_DELETE(self) -> None:
        """Handle optional Collider delete extension endpoint."""
        parsed = is_collider_delete_path(self.path)
        if parsed is None:
            self.send_error(404, 'Not Found')
            return

        name, version = parsed
        auth_result = self.auth_provider.authorize(self.headers.get('Authorization'))
        if not auth_result.allowed:
            self._send_json(auth_result.status, {'error': auth_result.message})
            return

        if self.repo is None:
            self._send_json(500, {'error': 'Repository is not configured.'})
            return

        try:
            with self.push_lock:
                repo_key = make_repo_key(name, version, PackageType.WRAP)
                if repo_key not in self.repo.packages:
                    self._send_json(
                        404,
                        {'error': f'Package "{name}" version "{version}" not found.'},
                    )
                    return
                package = self.repo.get_package(repo_key)
                if package is None:
                    self._send_json(500, {'error': 'Failed to load package.'})
                    return
                self.repo.remove_package(package)
        except ValueError as exc:
            self._send_json(400, {'error': str(exc)})
            return
        except Exception as exc:  # pragma: no cover - fallback protection
            logger.exception('Unhandled exception while processing delete request: %s', exc)
            self._send_json(500, {'error': 'Internal server error.'})
            return

        self.send_response(204)
        self.end_headers()

    def _read_push_payload(self) -> dict[str, object]:
        content_length_header = self.headers.get('Content-Length')
        if content_length_header is None:
            raise ValueError('Missing Content-Length header.')

        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise ValueError('Invalid Content-Length header.') from exc
        if content_length <= 0:
            raise ValueError('Request body must not be empty.')
        if content_length > _MAX_PUSH_BODY_BYTES:
            raise RuntimeError('Request body is too large.')

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            raise ValueError('Request body is truncated.')

        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('Request body must be valid UTF-8 JSON.') from exc

        if not isinstance(payload, dict):
            raise ValueError('Request body must be a JSON object.')

        return payload

    @staticmethod
    def _materialize_push_payload(
        payload: dict[str, object],
    ) -> tuple[WrapPackage, Path, Optional[Path], Path]:
        name = WrapApiHandler._required_package_segment(payload, 'name')
        version = WrapApiHandler._required_package_segment(payload, 'version')
        wrap_text = WrapApiHandler._required_str(payload, 'wrap_text')
        source_filename = WrapApiHandler._required_filename(payload, 'source_filename')
        source_archive_b64 = WrapApiHandler._required_str(payload, 'source_archive_base64')

        patch_filename = WrapApiHandler._optional_filename(payload, 'patch_filename')
        patch_archive_b64 = WrapApiHandler._optional_str(payload, 'patch_archive_base64')
        if (patch_filename is None) != (patch_archive_b64 is None):
            raise ValueError(
                'Patch payload is incomplete. Provide both patch_filename and patch_archive_base64.'
            )

        package = WrapPackage.from_wrap_text(name, version, wrap_text)
        if package.source_filename != source_filename:
            raise ValueError('source_filename must match wrap source_filename.')
        if patch_filename is not None and package.patch_filename != patch_filename:
            raise ValueError('patch_filename must match wrap patch_filename.')

        source_bytes = WrapApiHandler._decode_b64(source_archive_b64, 'source_archive_base64')
        patch_bytes: Optional[bytes] = None
        if patch_archive_b64 is not None:
            patch_bytes = WrapApiHandler._decode_b64(patch_archive_b64, 'patch_archive_base64')

        temp_dir = tempfile.mkdtemp(prefix='collider-push-')
        temp_root = Path(temp_dir)
        source_archive = temp_root / source_filename
        source_archive.write_bytes(source_bytes)

        patch_archive: Optional[Path] = None
        if patch_filename is not None and patch_bytes is not None:
            patch_archive = temp_root / patch_filename
            patch_archive.write_bytes(patch_bytes)

        return package, source_archive, patch_archive, temp_root

    @staticmethod
    def _required_str(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Missing or invalid "{key}".')
        return value

    @staticmethod
    def _required_package_segment(payload: dict[str, object], key: str) -> str:
        value = WrapApiHandler._required_str(payload, key)
        if value in ('.', '..') or Path(value).name != value or '\\' in value or '\x00' in value:
            raise ValueError(f'"{key}" must be a safe path segment.')
        return value

    @staticmethod
    def _optional_str(payload: dict[str, object], key: str) -> Optional[str]:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Invalid "{key}".')
        return value

    @staticmethod
    def _required_filename(payload: dict[str, object], key: str) -> str:
        filename = WrapApiHandler._required_str(payload, key)
        if Path(filename).name != filename:
            raise ValueError(f'"{key}" must not contain path components.')
        return filename

    @staticmethod
    def _optional_filename(payload: dict[str, object], key: str) -> Optional[str]:
        filename = WrapApiHandler._optional_str(payload, key)
        if filename is None:
            return None
        if Path(filename).name != filename:
            raise ValueError(f'"{key}" must not contain path components.')
        return filename

    @staticmethod
    def _decode_b64(raw: str, key: str) -> bytes:
        try:
            return base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f'"{key}" must be valid base64.') from exc

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path: str) -> str:
        """Map /v2 read paths to repository-root files for static file serving."""
        split = urllib.parse.urlsplit(path)
        if split.path.startswith(f'{_WRAP_API_PREFIX}/'):
            path = urllib.parse.urlunsplit(
                ('', '', split.path[len(_WRAP_API_PREFIX) :], split.query, split.fragment)
            )
        return super().translate_path(path)


class Serve(SubcommandInterface):
    """Serve a filesystem repository over HTTP."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Serve a filesystem repository over HTTP.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider serve --help`."""
        del cls
        return (
            'Expose a filesystem repository through Wrap API endpoints.\n'
            'Optionally enable authenticated push and delete endpoints with token options.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        return """
Examples:
  ‣ collider serve /srv/collider/repo
  ‣ collider serve /srv/collider/repo --host 0.0.0.0 --port 8080
  ‣ collider serve /srv/collider/repo --publish-url https://packages.example.com/collider/ --push-token "$COLLIDER_PUSH_TOKEN"
"""

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('path', type=Path, help='Path to the filesystem repository to serve.')
        parser.add_argument(
            '--host',
            type=str,
            default='127.0.0.1',
            help='Interface address to bind to.',
        )
        parser.add_argument(
            '--port',
            type=int,
            default=8000,
            help='Port to listen on.',
        )
        parser.add_argument(
            '--push-token',
            type=str,
            default=None,
            help='Enable push endpoint with this bearer token.',
        )
        parser.add_argument(
            '--push-token-env',
            type=str,
            default='COLLIDER_PUSH_TOKEN',
            help='Environment variable to read push token from when --push-token is not set.',
        )
        parser.add_argument(
            '--publish-url',
            type=str,
            default=None,
            help='Base URL used to rewrite archive URLs for push payloads.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store serve arguments and context.
        :param args: Parsed CLI arguments (path, host, port, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.repository_path: Path = args.path
        self.host: str = args.host
        self.port: int = args.port
        push_token = args.push_token
        if push_token is None and args.push_token_env:
            push_token = os.environ.get(args.push_token_env)
        self.push_token: Optional[str] = push_token
        self.publish_url: Optional[str] = args.publish_url

    @override
    def execute(self) -> int:
        """Run the serve command.
        :return: Exit code.
        """
        repo_path = self.repository_path.expanduser().resolve()
        repo_was_created = not repo_path.exists()
        if repo_path.exists() and not repo_path.is_dir():
            logger.critical(
                f'Repository path "{repo_path.as_posix()}" exists but is not a directory.'
            )
            return os.EX_USAGE

        if not repo_path.exists():
            repo_path.mkdir(parents=True, exist_ok=True)
            logger.info(f'Created repository directory "{repo_path.as_posix()}".')

        archives_path = repo_path / Filesystem.ARCHIVE_DIR
        archives_path.mkdir(parents=True, exist_ok=True)

        releases_path = repo_path / 'releases.json'
        if not releases_path.exists():
            releases_path.write_text('{}\n', encoding='utf-8')

        publish_url = self.publish_url or repo_path.as_uri()
        try:
            repo = Filesystem.from_url(repo_path.as_uri(), publish_url=publish_url)
        except ValueError as exc:
            logger.critical(
                f'Failed to initialize filesystem repository at "{repo_path.as_posix()}": {exc}'
            )
            return os.EX_USAGE

        auth_provider: PushAuthProvider
        if self.push_token:
            auth_provider = BearerTokenAuthProvider(self.push_token)
            logger.info(f'Push endpoint enabled at "{_COLLIDER_PUSH_PATH}".')
            logger.warning(
                'Push endpoint uses static bearer token auth. For strong auth, put Collider '
                'behind a reverse proxy that enforces TLS and OIDC/mTLS.'
            )
            if not is_loopback_host(self.host):
                logger.warning(
                    f'Push endpoint is bound to non-loopback host "{self.host}". '
                    'Expose it only through a trusted reverse proxy.'
                )
            if self.publish_url is None:
                logger.warning(
                    'No --publish-url provided; pushed wraps will reference file:// archive URLs.'
                )
        else:
            auth_provider = DisabledPushAuthProvider()
            logger.info('Push endpoint disabled (no push token configured).')

        handler = partial(
            WrapApiHandler,
            directory=repo_path.as_posix(),
            repo=repo,
            push_lock=threading.Lock(),
            auth_provider=auth_provider,
        )

        if repo_was_created:
            try:
                self._validate_new_repo_endpoints(handler, push_enabled=self.push_token is not None)
            except RuntimeError as exc:
                logger.critical(f'New repository endpoint validation failed: {exc}')
                return os.EX_IOERR
            logger.info('Validated new repository endpoints successfully.')

        try:
            server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            logger.critical(f'Failed to start HTTP server: {exc}')
            return os.EX_IOERR

        logger.info(
            f'Serving repository path "{repo_path.as_posix()}" on http://{self.host}:{self.port}/'
        )
        logger.info('Press Ctrl+C to stop.')

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info('Server stopped.')
        finally:
            server.server_close()

        return os.EX_OK

    @staticmethod
    def _validate_new_repo_endpoints(handler, push_enabled: bool) -> None:
        temp_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=temp_server.serve_forever, daemon=True)
        thread.start()
        port = temp_server.server_address[1]
        base_url = f'http://127.0.0.1:{port}'

        try:
            status, body = Serve._request(
                f'{base_url}/v2/releases.json',
                method='GET',
            )
            if status != 200:
                raise RuntimeError(f'GET /v2/releases.json returned {status}, expected 200.')
            try:
                parsed = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError('GET /v2/releases.json did not return valid JSON.') from exc
            if not isinstance(parsed, dict):
                raise RuntimeError('GET /v2/releases.json did not return a JSON object.')

            status, _ = Serve._request(f'{base_url}/', method='GET')
            if status != 404:
                raise RuntimeError(f'GET / returned {status}, expected 404.')

            status, _ = Serve._request(f'{base_url}/releases.json', method='GET')
            if status != 404:
                raise RuntimeError(f'GET /releases.json returned {status}, expected 404.')

            status, _ = Serve._request(f'{base_url}{_COLLIDER_PUSH_PATH}', method='GET')
            if status != 404:
                raise RuntimeError(f'GET {_COLLIDER_PUSH_PATH} returned {status}, expected 404.')

            status, _ = Serve._request(
                f'{base_url}/v2/releases.json',
                method='POST',
                body=b'{}',
                content_type='application/json',
            )
            if status != 404:
                raise RuntimeError(f'POST /v2/releases.json returned {status}, expected 404.')

            expected_push_status = 401 if push_enabled else 403
            status, _ = Serve._request(
                f'{base_url}{_COLLIDER_PUSH_PATH}',
                method='POST',
                body=b'{}',
                content_type='application/json',
            )
            if status != expected_push_status:
                raise RuntimeError(
                    f'POST {_COLLIDER_PUSH_PATH} returned {status}, '
                    f'expected {expected_push_status}.'
                )

            expected_delete_status = 401 if push_enabled else 403
            status, _ = Serve._request(
                f'{base_url}/v2/_collider/v1/packages/dummy/1.0.0',
                method='DELETE',
            )
            if status != expected_delete_status:
                raise RuntimeError(
                    'DELETE /v2/_collider/v1/packages/dummy/1.0.0 returned '
                    f'{status}, expected {expected_delete_status}.'
                )
        finally:
            temp_server.shutdown()
            temp_server.server_close()
            thread.join(timeout=2)

    @staticmethod
    def _request(
        url: str,
        *,
        method: str,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> tuple[int, bytes]:
        headers: dict[str, str] = {}
        if body is not None:
            headers['Content-Length'] = str(len(body))
        if content_type is not None:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
