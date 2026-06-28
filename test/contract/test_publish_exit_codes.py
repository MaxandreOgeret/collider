# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider "publish" command.

One test per documented os.EX_* return path of :meth:`Publish.execute`. Each test
drives the EXACT documented trigger and asserts the os.EX_* constant, mirroring
the fixtures and mocking seams used in test/subcommand/test_push.py.
"""

import argparse
import json
import os
import socket
import urllib.parse

from pathlib import Path
from unittest.mock import MagicMock

from collider.Context import Context
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.Publish import Publish


def _write_projectinfo(builddir: Path, name: str = 'demo', version: str = '1.0.0') -> None:
    info_dir = builddir / 'meson-info'
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / 'intro-projectinfo.json').write_text(
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
    (info_dir / 'meson-info.json').write_text(
        json.dumps(
            {
                'directories': {
                    'build': builddir.as_posix(),
                    'info': info_dir.as_posix(),
                    'source': source_dir.as_posix(),
                },
            }
        ),
        encoding='utf-8',
    )


def _push_args(
    repository: str,
    builddir: Path,
    patch_archive: Path | None = None,
    push_token_env: str = 'COLLIDER_PUSH_TOKEN',
    dry_run: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        repository=repository,
        builddir=builddir,
        patch_archive=patch_archive,
        push_token_env=push_token_env,
        dry_run=dry_run,
    )


def _unused_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _mock_context(repositories: dict[str, object]) -> MagicMock:
    config = MagicMock()
    config.repositories = repositories
    context = MagicMock(spec=Context)
    context.config = config
    return context


def test_publish_ex_noinput_missing_patch_archive(tmp_path: Path) -> None:
    """publish returns EX_NOINPUT when --patch-archive points to a non-existent path."""
    args = _push_args(
        repository='local',
        builddir=tmp_path / 'build',
        patch_archive=tmp_path / 'missing.patch',
    )
    cmd = Publish(args, _mock_context({}))
    assert cmd.execute() == os.EX_NOINPUT


def test_publish_ex_dataerr_patch_archive_is_directory(tmp_path: Path) -> None:
    """publish returns EX_DATAERR when --patch-archive exists but is not a regular file."""
    patch_dir = tmp_path / 'archive_dir'
    patch_dir.mkdir()
    args = _push_args(
        repository='local',
        builddir=tmp_path / 'build',
        patch_archive=patch_dir,
    )
    cmd = Publish(args, _mock_context({}))
    assert cmd.execute() == os.EX_DATAERR


def test_publish_ex_usage_repo_not_publishable(tmp_path: Path) -> None:
    """publish returns EX_USAGE when the resolved repo is neither Filesystem nor Collider."""
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    wrap_repo = Wrap(urllib.parse.urlparse('https://packages.example.com/v2/'), {})
    cmd = Publish(
        _push_args(repository='remote', builddir=builddir), _mock_context({'remote': wrap_repo})
    )
    assert cmd.execute() == os.EX_USAGE


def test_publish_ex_cantcreat_duplicate_version(tmp_path: Path) -> None:
    """publish returns EX_CANTCREAT when the same name/version already exists in the repo."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    context = _mock_context({'local': repo})
    args = _push_args(repository='local', builddir=builddir)
    assert Publish(args, context).execute() == os.EX_OK
    assert Publish(args, context).execute() == os.EX_CANTCREAT


def test_publish_ex_ioerr_unreachable_endpoint(tmp_path: Path) -> None:
    """publish returns EX_IOERR when the collider push endpoint is unreachable (URLError)."""
    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    closed_port = _unused_localhost_port()
    collider_repo = Collider(urllib.parse.urlparse(f'http://127.0.0.1:{closed_port}/v2/'), {})
    cmd = Publish(
        _push_args(repository='remote', builddir=builddir), _mock_context({'remote': collider_repo})
    )

    os.environ['COLLIDER_PUSH_TOKEN'] = 'secret'
    try:
        assert cmd.execute() == os.EX_IOERR
    finally:
        del os.environ['COLLIDER_PUSH_TOKEN']


def test_publish_ex_ok_filesystem_success(tmp_path: Path) -> None:
    """publish returns EX_OK after building the archive and writing to a filesystem repo."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    source_dir = tmp_path / 'src'
    source_dir.mkdir()
    (source_dir / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    cmd = Publish(_push_args(repository='local', builddir=builddir), _mock_context({'local': repo}))
    assert cmd.execute() == os.EX_OK
