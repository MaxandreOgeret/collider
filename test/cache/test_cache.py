# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import hashlib
import io
import shutil
import tempfile

from pathlib import Path

import pytest

from collider.cache import WrapCache
from collider.Package import WrapPackage
from collider.utils import network


class _DummyResponse:
    def __init__(self, data: bytes):
        self._fp = io.BytesIO(data)

    def __enter__(self):
        return self._fp

    def __exit__(self, exc_type, exc, tb):
        self._fp.close()
        return False


def _wrap_text(source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def test_wrap_cache_store_and_load(tmp_path: Path):
    cache = WrapCache(tmp_path)
    wrap_text = _wrap_text('deadbeef')
    package = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)

    cache.store_wrap(package)
    loaded = cache.load_wrap('foo', '1.0.0')
    assert loaded is not None
    assert loaded.source_url == 'https://example.com/foo.tar.xz'


def test_wrap_cache_store_rejects_unsafe_name(tmp_path: Path):
    """store_wrap refuses a traversal package name instead of writing outside the cache."""
    cache = WrapCache(tmp_path / 'cache')
    package = WrapPackage.from_wrap_text('../evil', '1.0.0', _wrap_text('deadbeef'))

    with pytest.raises(ValueError):
        cache.store_wrap(package)

    assert not (tmp_path / 'evil_1.0.0.wrap').exists()


def test_wrap_cache_store_scan_rejects_unsafe_name(tmp_path: Path):
    """store_scan refuses a traversal name instead of writing outside the scans dir."""
    from collider.utils.meson.scan import ScannedDependency

    cache = WrapCache(tmp_path / 'cache')

    with pytest.raises(ValueError):
        cache.store_scan('../../evil', '1.0.0', [ScannedDependency(name='zlib', required=True)])

    assert not (tmp_path / 'evil_1.0.0.json').exists()
    assert not (tmp_path.parent / 'evil_1.0.0.json').exists()


def test_wrap_cache_load_returns_none_on_unsafe_name(tmp_path: Path):
    """Reads treat a traversal name as a cache miss (no crash) instead of raising."""
    cache = WrapCache(tmp_path / 'cache')
    assert cache.load_wrap('../../evil', '1.0.0') is None
    assert cache.load_scan('../../evil', '1.0.0') is None
    assert cache.has_package('../../evil', '1.0.0') is False


def test_wrap_cache_ensure_archive_rejects_unsafe_hash(tmp_path: Path):
    """A traversal source_hash cannot make the archive path escape the cache."""
    cache = WrapCache(tmp_path / 'cache')
    package = WrapPackage.from_wrap_text(
        'foo',
        '1.0.0',
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=../../evil\n',
    )

    with pytest.raises(ValueError):
        cache.prepare_packagecache(package, tmp_path / 'subprojects', offline=True)

    assert not (tmp_path.parent / 'evil-foo.tar.xz').exists()


def test_wrap_cache_ensure_archive_rejects_non_http_scheme(tmp_path: Path, monkeypatch):
    """A non-http(s) source_url is rejected before any network fetch happens (SSRF guard)."""
    cache = WrapCache(tmp_path / 'cache')
    source_hash = hashlib.sha256(b'x').hexdigest()
    package = WrapPackage.from_wrap_text(
        'foo',
        '1.0.0',
        '[wrap-file]\n'
        'source_url=ftp://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n',
    )

    def _fail_urlopen(*_args, **_kwargs):
        raise AssertionError('urlopen must not be reached for a non-http(s) scheme.')

    monkeypatch.setattr('collider.utils.network.safe_urlopen', _fail_urlopen)

    with pytest.raises(ValueError, match='Unsupported archive URL scheme'):
        cache.prepare_packagecache(package, tmp_path / 'subprojects', offline=False)


def test_wrap_cache_ensure_archive_rejects_blocked_host(tmp_path: Path, monkeypatch):
    """An archive host resolving to a blocked address is rejected before any fetch (SSRF guard)."""
    cache = WrapCache(tmp_path / 'cache')
    source_hash = hashlib.sha256(b'x').hexdigest()
    package = WrapPackage.from_wrap_text(
        'foo',
        '1.0.0',
        '[wrap-file]\n'
        'source_url=https://metadata.internal/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n',
    )

    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: ['169.254.169.254'])

    def _fail_fetch(*_args, **_kwargs):
        raise AssertionError('The network must not be reached for a blocked host.')

    monkeypatch.setattr(network, 'safe_urlopen', _fail_fetch)

    with pytest.raises(ValueError, match='blocked'):
        cache.prepare_packagecache(package, tmp_path / 'subprojects', offline=False)


def test_wrap_cache_ensure_archive_cleans_temp_on_blocked_redirect(tmp_path: Path, monkeypatch):
    """A redirect blocked mid-download surfaces the security error and leaves no temp file."""
    cache = WrapCache(tmp_path / 'cache')
    source_hash = hashlib.sha256(b'x').hexdigest()
    package = WrapPackage.from_wrap_text(
        'foo',
        '1.0.0',
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={source_hash}\n',
    )

    def _redirect_blocked(*_args, **_kwargs):
        raise ValueError('Refusing redirect to blocked host in "https://169.254.169.254/x".')

    monkeypatch.setattr(network, 'safe_urlopen', _redirect_blocked)
    before = set(Path(tempfile.gettempdir()).glob('tmp*'))

    with pytest.raises(ValueError, match='Refusing redirect'):
        cache.prepare_packagecache(package, tmp_path / 'subprojects', offline=False)

    leaked = set(Path(tempfile.gettempdir()).glob('tmp*')) - before
    assert leaked == set()


def test_wrap_cache_has_package(tmp_path: Path):
    cache = WrapCache(tmp_path / 'cache')

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text(content_hash))

    cache.store_wrap(package)
    assert cache.has_package('foo', '1.0.0') is False

    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache.archives_dir / f'{content_hash}-foo.tar.xz'
    archive_path.write_bytes(content)

    assert cache.has_package('foo', '1.0.0') is True


def test_wrap_cache_has_package_rejects_unsafe_hash(tmp_path: Path):
    """has_package treats an unsafe source_hash as not cached instead of crashing."""
    cache = WrapCache(tmp_path / 'cache')
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text('../../evil'))

    cache.store_wrap(package)
    assert cache.has_package('foo', '1.0.0') is False


def test_wrap_cache_prepare_packagecache_downloads(tmp_path: Path, monkeypatch):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text(content_hash))

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(content)

    monkeypatch.setattr('collider.utils.network.safe_urlopen', _fake_urlopen)

    cache.prepare_packagecache(package, subprojects, offline=False)

    cached_file = subprojects / 'packagecache' / 'foo.tar.xz'
    assert cached_file.exists()
    assert cached_file.read_bytes() == content


def test_wrap_cache_prepare_packagecache_handles_cross_device(tmp_path: Path, monkeypatch):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text(content_hash))

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(content)

    monkeypatch.setattr('collider.utils.network.safe_urlopen', _fake_urlopen)

    moved = {'value': False}
    original_move = shutil.move

    def _fake_replace(self, target):
        raise OSError(18, 'Invalid cross-device link')

    def _fake_move(src, dst):
        moved['value'] = True
        return original_move(src, dst)

    monkeypatch.setattr(Path, 'replace', _fake_replace, raising=True)
    monkeypatch.setattr(shutil, 'move', _fake_move)

    cache.prepare_packagecache(package, subprojects, offline=False)

    cached_file = subprojects / 'packagecache' / 'foo.tar.xz'
    assert cached_file.exists()
    assert cached_file.read_bytes() == content
    assert moved['value'] is True


def test_wrap_cache_offline_missing_archive(tmp_path: Path):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text('deadbeef'))

    with pytest.raises(FileNotFoundError):
        cache.prepare_packagecache(package, subprojects, offline=True)


def test_wrap_cache_download_hash_mismatch(tmp_path: Path, monkeypatch):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    content = b'payload'
    wrong_hash = hashlib.sha256(b'other').hexdigest()
    package = WrapPackage.from_wrap_text('foo', '1.0.0', _wrap_text(wrong_hash))

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(content)

    monkeypatch.setattr('collider.utils.network.safe_urlopen', _fake_urlopen)

    with pytest.raises(ValueError, match='Archive hash mismatch'):
        cache.prepare_packagecache(package, subprojects, offline=False)


def test_wrap_cache_offline_allows_file_url(tmp_path: Path):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    content = b'payload'
    source_archive = tmp_path / 'foo.tar.xz'
    source_archive.write_bytes(content)
    content_hash = hashlib.sha256(content).hexdigest()

    wrap_text = (
        '[wrap-file]\n'
        f'source_url={source_archive.as_uri()}\n'
        'source_filename=foo.tar.xz\n'
        f'source_hash={content_hash}\n'
    )
    package = WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)

    cache.prepare_packagecache(package, subprojects, offline=True)

    cached_file = subprojects / 'packagecache' / 'foo.tar.xz'
    assert cached_file.exists()


def test_wrap_cache_warns_http_url(tmp_path: Path, monkeypatch, caplog):
    cache = WrapCache(tmp_path / 'cache')
    subprojects = tmp_path / 'project' / 'subprojects'
    subprojects.mkdir(parents=True)

    package = WrapPackage(
        name='foo',
        version='1.0.0',
        wrap_text='[wrap-file]\n',
        source_url='http://example.com/foo.tar.xz',
        source_filename='foo.tar.xz',
        source_hash='deadbeef',
    )

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(b'payload')

    monkeypatch.setattr('collider.utils.network.safe_urlopen', _fake_urlopen)

    caplog.set_level('WARNING')
    with pytest.raises(ValueError, match='Archive hash mismatch'):
        cache.prepare_packagecache(package, subprojects, offline=False)
    assert 'HTTP archive URLs are allowed but insecure' in caplog.text


# -- Scan cache ---------------------------------------------------------------


def test_scan_cache_store_and_load_roundtrip(tmp_path: Path):
    """Stored scan results can be loaded back with identical data."""
    from collider.utils.meson.scan import ScannedDependency

    cache = WrapCache(tmp_path)
    scanned = [
        ScannedDependency(name='zlib', required=True, version=['>=1.2']),
        ScannedDependency(name='openssl', required=False, has_fallback=True),
        ScannedDependency(name='cond_dep', required=True, conditional=True),
    ]

    cache.store_scan('grpc', '1.59.1', scanned)
    loaded = cache.load_scan('grpc', '1.59.1')

    assert loaded is not None
    assert len(loaded) == 3
    assert loaded[0].name == 'zlib'
    assert loaded[0].required is True
    assert loaded[0].version == ['>=1.2']
    assert loaded[1].name == 'openssl'
    assert loaded[1].required is False
    assert loaded[1].has_fallback is True
    assert loaded[2].name == 'cond_dep'
    assert loaded[2].conditional is True


def test_scan_cache_load_returns_none_on_miss(tmp_path: Path):
    """Loading a non-existent scan returns None."""
    cache = WrapCache(tmp_path)
    assert cache.load_scan('nonexistent', '1.0') is None


def test_scan_cache_load_returns_none_on_corrupt_json(tmp_path: Path):
    """Corrupt JSON in the scan cache returns None instead of raising."""
    cache = WrapCache(tmp_path)
    cache.scans_dir.mkdir(parents=True)
    (cache.scans_dir / 'grpc_1.59.1.json').write_text('not json', encoding='utf-8')

    assert cache.load_scan('grpc', '1.59.1') is None


def test_scan_cache_overwrites_on_store(tmp_path: Path):
    """A second store overwrites the first."""
    from collider.utils.meson.scan import ScannedDependency

    cache = WrapCache(tmp_path)
    cache.store_scan('grpc', '1.0', [ScannedDependency(name='old', required=True)])
    cache.store_scan('grpc', '1.0', [ScannedDependency(name='new', required=True)])

    loaded = cache.load_scan('grpc', '1.0')
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].name == 'new'
