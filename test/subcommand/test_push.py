# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import argparse
import configparser
import json
import os
import socket
import tarfile
import threading
import urllib.parse

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.Context import Context
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.Publish import Publish
from collider.subcommand.Serve import BearerTokenAuthProvider, WrapApiHandler
from collider.utils.packaging import compute_file_hash


def _write_projectinfo(builddir: Path, name: str = 'demo', version: str = '1.0.0') -> None:
    info_dir = builddir / 'meson-info'
    info_dir.mkdir(parents=True, exist_ok=True)
    info_path = info_dir / 'intro-projectinfo.json'
    info_path.write_text(
        json.dumps(
            {
                'descriptive_name': name,
                'license': ['MIT'],
                'license_files': [],
                'subproject_dir': 'subprojects',
                'subprojects': [],
                'version': version,
            }
        ),
        encoding='utf-8',
    )


def _write_mesoninfo(builddir: Path, source_dir: Path) -> None:
    info_dir = builddir / 'meson-info'
    info_dir.mkdir(parents=True, exist_ok=True)
    info_path = info_dir / 'meson-info.json'
    info_path.write_text(
        json.dumps(
            {
                'build_files_updated': False,
                'directories': {
                    'build': builddir.as_posix(),
                    'info': info_dir.as_posix(),
                    'source': source_dir.as_posix(),
                },
                'error': False,
                'introspection': {
                    'information': {
                        'benchmarks': {'file': '', 'updated': False},
                        'buildoptions': {'file': '', 'updated': False},
                        'buildsystem_files': {'file': '', 'updated': False},
                        'compilers': {'file': '', 'updated': False},
                        'dependencies': {'file': '', 'updated': False},
                        'install_plan': {'file': '', 'updated': False},
                        'installed': {'file': '', 'updated': False},
                        'machines': {'file': '', 'updated': False},
                        'projectinfo': {'file': '', 'updated': False},
                        'targets': {'file': '', 'updated': False},
                        'tests': {'file': '', 'updated': False},
                    },
                    'version': {'full': '1.10.0', 'major': 1, 'minor': 10, 'patch': 0},
                },
                'meson_version': {'full': '1.10.0', 'major': 1, 'minor': 10, 'patch': 0},
            }
        ),
        encoding='utf-8',
    )


def _parse_wrap(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    return parser


def _push_args(
    repository: str,
    builddir: Path,
    patch_archive: Path | None = None,
    push_token_env: str = 'COLLIDER_PUSH_TOKEN',
    dry_run: bool = False,
    insecure: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        repository=repository,
        builddir=builddir,
        patch_archive=patch_archive,
        push_token_env=push_token_env,
        dry_run=dry_run,
        insecure=insecure,
    )


def _start_push_server(
    repo: Filesystem,
    token: str,
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
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


def _unused_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_push_generates_wrap_and_archive(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    (source_dir / 'meson.build').write_text("project('my-lib', 'c')", encoding='utf-8')

    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='my-lib', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    args = _push_args(repository='local', builddir=builddir)
    cmd = Publish(args, context)

    assert cmd.execute() == os.EX_OK

    wrap_path = repo_path / 'my-lib_1.0.0' / 'my-lib.wrap'
    assert wrap_path.exists()
    wrap_parser = _parse_wrap(wrap_path.read_text(encoding='utf-8'))
    wrap_section = wrap_parser['wrap-file']

    assert wrap_section['directory'] == 'my-lib-1.0.0'
    assert wrap_section['source_filename'] == 'my-lib-1.0.0.tar.xz'
    assert (
        wrap_section['source_url']
        == 'https://packages.example.com/collider/archives/my-lib_1.0.0/my-lib-1.0.0.tar.xz'
    )

    archive_path = repo_path / repo.ARCHIVE_DIR / 'my-lib_1.0.0' / 'my-lib-1.0.0.tar.xz'
    assert archive_path.exists()
    assert wrap_section['source_hash'] == compute_file_hash(archive_path)

    assert wrap_parser['provide']['my-lib'] == 'my_lib_dep'


def test_push_archive_excludes_artifacts_but_keeps_wraps(tmp_path: Path) -> None:
    """Published source archive drops nested junk and packagecache, keeps subproject wraps."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    (source_dir / 'src').mkdir(parents=True)
    (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    (source_dir / 'src' / 'lib.c').write_text('int answer(void) { return 42; }', encoding='utf-8')

    # Build artifacts and VCS noise that must never reach the published archive.
    (source_dir / '.git').mkdir()
    (source_dir / '.git' / 'config').write_text('[core]', encoding='utf-8')
    (source_dir / 'src' / '__pycache__').mkdir()
    (source_dir / 'src' / '__pycache__' / 'cached.pyc').write_text('x', encoding='utf-8')

    # subprojects/: wraps are real source, packagecache is a build artifact.
    (source_dir / 'subprojects').mkdir()
    (source_dir / 'subprojects' / 'fmt.wrap').write_text('[wrap-file]', encoding='utf-8')
    (source_dir / 'subprojects' / 'packagecache').mkdir()
    (source_dir / 'subprojects' / 'packagecache' / 'fmt.tar.xz').write_bytes(b'archive')

    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    cmd = Publish(_push_args(repository='local', builddir=builddir), context)
    assert cmd.execute() == os.EX_OK

    archive_path = repo_path / repo.ARCHIVE_DIR / 'demo_1.0.0' / 'demo-1.0.0.tar.xz'
    with tarfile.open(archive_path) as tar:
        members = {Path(name).relative_to('demo-1.0.0').as_posix() for name in tar.getnames()}

    assert 'meson.build' in members
    assert 'src/lib.c' in members
    assert 'subprojects/fmt.wrap' in members
    assert not any(m == '.git' or m.startswith('.git/') for m in members)
    assert not any('__pycache__' in Path(m).parts for m in members)
    assert not any(m.startswith('subprojects/packagecache') for m in members)


def test_push_missing_repository(tmp_path: Path) -> None:
    config = MagicMock()
    config.repositories = {}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, source_dir)

    args = _push_args(repository='missing', builddir=builddir)
    cmd = Publish(args, context)

    assert cmd.execute() == os.EX_NOINPUT


def test_push_with_patch_archive(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    patch_archive = tmp_path / 'demo.patch'
    patch_archive.write_bytes(b'patch')

    args = _push_args(repository='local', builddir=builddir, patch_archive=patch_archive)
    cmd = Publish(args, context)

    assert cmd.execute() == os.EX_OK

    wrap_path = repo_path / 'demo_1.0.0' / 'demo.wrap'
    wrap_parser = _parse_wrap(wrap_path.read_text(encoding='utf-8'))
    wrap_section = wrap_parser['wrap-file']
    assert (
        wrap_section['patch_url']
        == 'https://packages.example.com/collider/archives/demo_1.0.0/demo.patch'
    )
    assert wrap_section['patch_filename'] == 'demo.patch'
    patch_path = repo_path / repo.ARCHIVE_DIR / 'demo_1.0.0' / 'demo.patch'
    assert patch_path.exists()
    assert wrap_section['patch_hash'] == compute_file_hash(patch_path)


def test_push_requires_project_info(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    builddir = tmp_path / 'missing'
    args = _push_args(repository='local', builddir=builddir)
    cmd = Publish(args, context)

    assert cmd.execute() == os.EX_DATAERR


def test_push_wrap_repo_read_only_returns_ex_usage(tmp_path: Path, caplog) -> None:
    """Push to a read-only wrap repo (not collider) returns EX_USAGE."""
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    wrap_repo = Wrap(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': wrap_repo}
    context = MagicMock(spec=Context)
    context.config = config

    cmd = Publish(_push_args(repository='remote', builddir=builddir), context)

    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        assert cmd.execute() == os.EX_USAGE
    assert 'filesystem or collider' in caplog.text


def test_push_collider_repo_requires_token_from_env(tmp_path: Path) -> None:
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    collider_repo = Collider(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    cmd = Publish(_push_args(repository='remote', builddir=builddir), context)

    with patch.dict(os.environ, {}, clear=True):
        assert cmd.execute() == os.EX_USAGE


def test_push_collider_repo_http_success(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    fs_repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base_url = _start_push_server(fs_repo, token='secret')
    try:
        source_dir = tmp_path / 'src'
        source_dir.mkdir()
        (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')

        builddir = tmp_path / 'build'
        _write_projectinfo(builddir, name='demo', version='1.0.0')
        _write_mesoninfo(builddir, source_dir)

        collider_repo = Collider(urllib.parse.urlparse(f'{base_url}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        cmd = Publish(_push_args(repository='remote', builddir=builddir, insecure=True), context)
        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
            assert cmd.execute() == os.EX_OK
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    wrap_path = repo_path / 'demo_1.0.0' / 'demo.wrap'
    assert wrap_path.exists()
    archive_path = repo_path / fs_repo.ARCHIVE_DIR / 'demo_1.0.0' / 'demo-1.0.0.tar.xz'
    assert archive_path.exists()


def test_push_collider_repo_http_bad_token(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    fs_repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base_url = _start_push_server(fs_repo, token='secret')
    try:
        source_dir = tmp_path / 'src'
        source_dir.mkdir()
        builddir = tmp_path / 'build'
        _write_projectinfo(builddir, name='demo', version='1.0.0')
        _write_mesoninfo(builddir, source_dir)

        collider_repo = Collider(urllib.parse.urlparse(f'{base_url}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        cmd = Publish(_push_args(repository='remote', builddir=builddir, insecure=True), context)
        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'wrong'}):
            assert cmd.execute() == os.EX_IOERR
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_push_collider_repo_uses_custom_token_env(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    fs_repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base_url = _start_push_server(fs_repo, token='secret')
    try:
        source_dir = tmp_path / 'src'
        source_dir.mkdir()
        builddir = tmp_path / 'build'
        _write_projectinfo(builddir, name='demo', version='1.0.0')
        _write_mesoninfo(builddir, source_dir)

        collider_repo = Collider(urllib.parse.urlparse(f'{base_url}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        cmd = Publish(
            _push_args(
                repository='remote',
                builddir=builddir,
                insecure=True,
                push_token_env='MY_PUSH_TOKEN',
            ),
            context,
        )
        with patch.dict(os.environ, {'MY_PUSH_TOKEN': 'secret'}, clear=True):
            assert cmd.execute() == os.EX_OK
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_push_collider_repo_http_unreachable_endpoint(tmp_path: Path) -> None:
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    closed_port = _unused_localhost_port()
    collider_repo = Collider(urllib.parse.urlparse(f'http://127.0.0.1:{closed_port}/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    cmd = Publish(_push_args(repository='remote', builddir=builddir, insecure=True), context)
    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        assert cmd.execute() == os.EX_IOERR


def test_push_filesystem_repo_duplicate_version_returns_ex_cantcreat(
    tmp_path: Path, caplog
) -> None:
    """Publishing the same name/version twice returns EX_CANTCREAT with a clear message."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    (source_dir / 'meson.build').write_text("project('my-lib', 'c')", encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='my-lib', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    args = _push_args(repository='local', builddir=builddir)
    assert Publish(args, context).execute() == os.EX_OK

    result = Publish(args, context).execute()
    assert result == os.EX_CANTCREAT
    assert '1.0.0' in caplog.text
    assert 'my-lib' in caplog.text
    assert 'local' in caplog.text
    assert 'Unpublish' in caplog.text
    assert 'Failed to add package to repository' not in caplog.text


def test_push_collider_repo_http_conflict_returns_ex_cantcreat(tmp_path: Path, caplog) -> None:
    """Publishing a version that already exists on a remote collider repo returns EX_CANTCREAT."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    fs_repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base_url = _start_push_server(fs_repo, token='secret')
    try:
        source_dir = tmp_path / 'src'
        source_dir.mkdir()
        (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
        builddir = tmp_path / 'build'
        _write_projectinfo(builddir, name='demo', version='2.0.0')
        _write_mesoninfo(builddir, source_dir)

        collider_repo = Collider(urllib.parse.urlparse(f'{base_url}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        args = _push_args(repository='remote', builddir=builddir, insecure=True)
        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
            assert Publish(args, context).execute() == os.EX_OK

        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
            result = Publish(args, context).execute()
        assert result == os.EX_CANTCREAT
        assert '2.0.0' in caplog.text
        assert 'demo' in caplog.text
        assert 'remote' in caplog.text
        assert 'Unpublish' in caplog.text
        assert 'Failed to add package to repository' not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_push_collider_repo_http_unexpected_status(tmp_path: Path) -> None:
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    request_paths: list[str] = []

    class _UnexpectedStatusHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            request_paths.append(self.path)
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), _UnexpectedStatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        collider_repo = Collider(urllib.parse.urlparse(f'http://{host}:{port}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        cmd = Publish(_push_args(repository='remote', builddir=builddir, insecure=True), context)
        with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
            assert cmd.execute() == os.EX_IOERR
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert request_paths == ['/v2/_collider/v1/push']


def test_dry_run_filesystem_repo_does_not_write(tmp_path: Path, caplog) -> None:
    """--dry-run logs a summary and does not write anything to a filesystem repository."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    config = MagicMock()
    config.repositories = {'local': repo}
    context = MagicMock(spec=Context)
    context.config = config

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    (source_dir / 'meson.build').write_text("project('my-lib', 'c')", encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='my-lib', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    result = Publish(
        _push_args(repository='local', builddir=builddir, dry_run=True), context
    ).execute()

    assert result == os.EX_OK
    assert not (repo_path / 'my-lib_1.0.0').exists()
    assert '[dry-run] Would publish: my-lib 1.0.0' in caplog.text
    assert '[dry-run] Archive:' in caplog.text
    assert '[dry-run] Destination: local' in caplog.text
    assert '[dry-run] No files written.' in caplog.text
    assert 'published successfully' not in caplog.text


def test_dry_run_collider_repo_does_not_push(tmp_path: Path, caplog) -> None:
    """--dry-run logs a summary and does not push to a collider repository (no token needed)."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    fs_repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')
    server, thread, base_url = _start_push_server(fs_repo, token='secret')
    try:
        source_dir = tmp_path / 'src'
        source_dir.mkdir()
        (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
        builddir = tmp_path / 'build'
        _write_projectinfo(builddir, name='demo', version='3.0.0')
        _write_mesoninfo(builddir, source_dir)

        collider_repo = Collider(urllib.parse.urlparse(f'{base_url}/v2/'), {})
        config = MagicMock()
        config.repositories = {'remote': collider_repo}
        context = MagicMock(spec=Context)
        context.config = config

        # No token in environment -- dry-run must not require one.
        with patch.dict(os.environ, {}, clear=True):
            result = Publish(
                _push_args(repository='remote', builddir=builddir, dry_run=True), context
            ).execute()
        assert result == os.EX_OK
        assert not (repo_path / 'demo_3.0.0').exists()
        assert '[dry-run] Would publish: demo 3.0.0' in caplog.text
        assert '[dry-run] No files written.' in caplog.text
        assert 'published successfully' not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_push_collider_repo_http_refused_without_insecure(tmp_path: Path, caplog) -> None:
    """Pushing a token to an http repo without --insecure is refused before any network call."""
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    # Unreachable port: had the gate not fired, the push would attempt a connection and return
    # EX_IOERR. EX_USAGE proves the token was never put on the wire.
    closed_port = _unused_localhost_port()
    collider_repo = Collider(urllib.parse.urlparse(f'http://127.0.0.1:{closed_port}/v2/'), {})
    config = MagicMock()
    config.repositories = {'remote': collider_repo}
    context = MagicMock(spec=Context)
    context.config = config

    cmd = Publish(_push_args(repository='remote', builddir=builddir), context)
    with patch.dict(os.environ, {'COLLIDER_PUSH_TOKEN': 'secret'}):
        assert cmd.execute() == os.EX_USAGE
    assert 'Refusing to send the push token' in caplog.text
