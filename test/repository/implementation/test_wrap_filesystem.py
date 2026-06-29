# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import hashlib
import json

from pathlib import Path
from unittest.mock import patch

import pytest

from collider.Package import WrapPackage
from collider.repository.implementation.Filesystem import Filesystem
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    path = tmp_path / 'repo'
    path.mkdir()
    return path


def _wrap_text() -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        '\n'
        '[provide]\n'
        'foo = foo_dep\n'
        'bar = bar_dep\n'
    )


def test_filesystem_repo_requires_publish_url(repo_path: Path):
    with pytest.raises(ValueError):
        Filesystem.from_url(repo_path.as_uri())


def test_filesystem_repo_warns_http_publish_url(repo_path: Path, caplog):
    caplog.set_level('WARNING')
    repo = Filesystem(repo_path, publish_url='http://packages.example.com/collider/')
    assert isinstance(repo, Filesystem)
    assert 'Publish URL uses HTTP; downloads will be insecure.' in caplog.text


def test_filesystem_from_url_scans_existing_wraps(repo_path: Path):
    wrap_dir = repo_path / 'foo_1.0.0'
    wrap_dir.mkdir()
    wrap_path = wrap_dir / 'foo.wrap'
    wrap_path.write_text(_wrap_text(), encoding='utf-8')

    repo = Filesystem.from_url(repo_path.as_uri(), publish_url=repo_path.as_uri())
    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    assert repo_key in repo.packages

    releases = json.loads((repo_path / 'releases.json').read_text(encoding='utf-8'))
    assert releases['foo']['versions'] == ['1.0.0']
    assert releases['foo']['dependency_names'] == ['bar', 'foo']


def test_filesystem_scan_skips_non_wrap_file_package(repo_path: Path, caplog):
    wrap_dir = repo_path / 'foo_1.0.0'
    wrap_dir.mkdir()
    (wrap_dir / 'foo.wrap').write_text(
        '[wrap-git]\nurl=https://example.invalid/foo.git\nrevision=head\n', encoding='utf-8'
    )

    caplog.set_level('ERROR')
    repo = Filesystem.from_url(repo_path.as_uri(), publish_url=repo_path.as_uri())

    assert repo.packages == {}
    assert 'Skipping non-wrap-file package wrap "foo.wrap".' in caplog.text
    releases = json.loads((repo_path / 'releases.json').read_text(encoding='utf-8'))
    assert releases == {}


def test_filesystem_repo_add_get_remove(repo_path: Path):
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())

    repo.add_package(package)

    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    assert repo_key in repo.packages

    wrap_path = repo_path / 'foo_1.0.0' / 'foo.wrap'
    assert wrap_path.exists()
    assert wrap_path.read_text(encoding='utf-8') == _wrap_text()

    releases_path = repo_path / 'releases.json'
    assert releases_path.exists()
    releases = json.loads(releases_path.read_text(encoding='utf-8'))
    assert releases['foo']['versions'] == ['1.0.0']
    assert releases['foo']['dependency_names'] == ['bar', 'foo']

    fetched = repo.get_package(repo_key)
    assert fetched is not None
    assert isinstance(fetched, WrapPackage)
    assert fetched.source_url == 'https://example.com/foo.tar.xz'

    repo.remove_package(package)
    assert repo_key not in repo.packages
    assert not wrap_path.exists()


def test_filesystem_write_releases_failure_preserves_existing(repo_path: Path):
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    repo.add_package(WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text()))

    releases_path = repo_path / 'releases.json'
    original = releases_path.read_text(encoding='utf-8')

    with patch(
        'collider.repository.implementation.Filesystem.atomic_write_text',
        side_effect=IOError('disk full'),
    ):
        with pytest.raises(IOError):
            repo._write_releases_json()

    assert releases_path.read_text(encoding='utf-8') == original


def test_filesystem_repo_contains(repo_path: Path):
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text())
    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)

    assert package not in repo
    assert repo_key not in repo

    repo.add_package(package)

    assert package in repo
    assert repo_key in repo


def test_filesystem_repo_stores_patch_archive(repo_path: Path):
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    source_content = b'source'
    patch_content = b'patch'
    source_archive = repo_path / 'foo.tar.xz'
    patch_archive = repo_path / 'foo.patch'
    source_archive.write_bytes(source_content)
    patch_archive.write_bytes(patch_content)

    source_hash = hashlib.sha256(source_content).hexdigest()
    patch_hash = hashlib.sha256(patch_content).hexdigest()

    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n'
        'patch_url=https://example.com/foo.patch\n'
        'patch_filename=foo.patch\n'
        f'patch_hash={patch_hash}\n'
    )
    package = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)

    repo.add_package(
        package,
        source_archive=source_archive,
        patch_archive=patch_archive,
    )

    wrap_path = repo_path / 'foo_1.0.0' / 'foo.wrap'
    stored = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_path.read_text(encoding='utf-8'))
    assert stored.patch_url == 'https://packages.example.com/collider/archives/foo_1.0.0/foo.patch'

    archive_path = repo_path / repo.ARCHIVE_DIR / 'foo_1.0.0' / 'foo.patch'
    assert archive_path.exists()


def test_filesystem_repo_uses_publish_url(repo_path: Path):
    repo = Filesystem(repo_path, publish_url='https://packages.example.com/collider/')

    source_content = b'source'
    source_archive = repo_path / 'foo.tar.xz'
    source_archive.write_bytes(source_content)
    source_hash = hashlib.sha256(source_content).hexdigest()

    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n'
    )
    package = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)

    repo.add_package(package, source_archive=source_archive)

    wrap_path = repo_path / 'foo_1.0.0' / 'foo.wrap'
    stored = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_path.read_text(encoding='utf-8'))
    assert (
        stored.source_url == 'https://packages.example.com/collider/archives/foo_1.0.0/foo.tar.xz'
    )


def test_filesystem_repo_rejects_unsafe_package_segments(repo_path: Path):
    repo = Filesystem(repo_path, publish_url=repo_path.as_uri())

    with pytest.raises(ValueError, match='Package name must be a safe path segment'):
        repo.add_package(WrapPackage.from_wrap_text('../foo', '1.0.0', _wrap_text()))

    with pytest.raises(ValueError, match='Package version must be a safe path segment'):
        repo.add_package(WrapPackage.from_wrap_text('foo', '../1.0.0', _wrap_text()))
