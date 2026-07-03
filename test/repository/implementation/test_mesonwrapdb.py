# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from collider.errors import ColliderUserError
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Wrap import (
    _RELEASES_TTL_SECONDS,
    Wrap,
    _ensure_v2_url,
    _get_pkg_wrap_url,
    _releases_cache_path,
    _wrap_releases_to_packages,
)
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


def _cache_file_for(cache_path, url_str: str):
    """Resolve the releases.json cache path the way production code does."""
    return _releases_cache_path(cache_path, _ensure_v2_url(urllib.parse.urlparse(url_str)))


class _DummyResponse:
    def __init__(self, text: str):
        self._fp = io.BytesIO(text.encode('utf-8'))

    def __enter__(self):
        return self._fp

    def __exit__(self, exc_type, exc, tb):
        self._fp.close()
        return False


def test_wrap_from_url_fetches_releases(monkeypatch):
    releases = {
        'foo': {'versions': ['1.0.0', '2.0.0']},
        'bar': {'versions': ['0.1.0']},
    }
    payload = json.dumps(releases)
    called = {}

    def _fake_urlopen(url, **_kwargs):
        called['url'] = url
        return _DummyResponse(payload)

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/')
    assert isinstance(repo, Wrap)

    expected_url = urllib.parse.urljoin('https://wrapdb.mesonbuild.com/v2/', 'releases.json')
    assert called['url'] == expected_url

    keys = repo.packages.keys()
    assert make_repo_key('foo', '1.0.0', PackageType.WRAP) in keys
    assert make_repo_key('foo', '2.0.0', PackageType.WRAP) in keys
    assert make_repo_key('bar', '0.1.0', PackageType.WRAP) in keys


def test_wrap_from_url_warns_missing_v2(monkeypatch, caplog):
    releases = {'foo': {'versions': ['1.0.0']}}
    payload = json.dumps(releases)
    called = {}

    def _fake_urlopen(url, **_kwargs):
        called['url'] = url
        return _DummyResponse(payload)

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    caplog.set_level('WARNING')
    repo = Wrap.from_url('https://wrapdb.mesonbuild.com')

    assert 'missing "/v2/"' in caplog.text
    assert called['url'] == 'https://wrapdb.mesonbuild.com/v2/releases.json'
    assert repo.url.geturl() == 'https://wrapdb.mesonbuild.com/v2/'


def test_wrap_from_url_warns_http(monkeypatch, caplog):
    releases = {'foo': {'versions': ['1.0.0']}}
    payload = json.dumps(releases)

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(payload)

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    caplog.set_level('WARNING')
    repo = Wrap.from_url('http://wrapdb.mesonbuild.com/v2/')

    assert isinstance(repo, Wrap)
    assert 'HTTP WrapDB URLs are allowed but insecure' in caplog.text


def test_wrap_releases_to_packages_multiple_versions():
    releases = {
        'foo': {'versions': ['1.0.0', '2.0.0']},
        'bar': {'versions': ['0.1.0']},
    }
    packages, rejected = _wrap_releases_to_packages(releases)
    assert len(packages) == 3
    assert rejected == []

    key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    entry = packages[key]
    assert entry.name == 'foo'
    assert entry.version == '1.0.0'
    assert entry.package_type == PackageType.WRAP


def test_wrap_releases_to_packages_empty_versions():
    releases = {
        'foo': {'versions': []},
    }
    packages, rejected = _wrap_releases_to_packages(releases)
    assert packages == {}
    assert rejected == []


def test_wrap_releases_to_packages_skips_unsafe_name_and_version():
    """Traversal names/versions from an untrusted releases.json are dropped at the boundary."""
    releases = {
        '../../evil': {'versions': ['1.0.0']},
        'foo': {'versions': ['1.0.0', '../../evil']},
    }
    packages, rejected = _wrap_releases_to_packages(releases)

    names = {entry.name for entry in packages.values()}
    versions = {entry.version for entry in packages.values()}
    assert names == {'foo'}
    assert versions == {'1.0.0'}
    # Both the unsafe name and the unsafe version are recorded as rejects.
    assert {r.reason.value for r in rejected} == {'unsafe_name', 'unsafe_version'}


def test_wrap_url_builder_rejects_unsafe_name():
    """A traversal name cannot redirect the fetch path outside the repo API prefix."""
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    entry = RepoPackageEntry('../../../etc', '1.2.3', PackageType.WRAP)
    with pytest.raises(ValueError):
        _get_pkg_wrap_url(url, entry)


def test_wrap_url_builder_rejects_unsafe_version():
    """A traversal version cannot redirect the fetch path outside the repo API prefix."""
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    entry = RepoPackageEntry('foo', '../../secret', PackageType.WRAP)
    with pytest.raises(ValueError):
        _get_pkg_wrap_url(url, entry)


def test_wrap_url_builder_rejects_scheme_injection():
    """A colon in the name must not flip the wrap URL to a local or cross-protocol scheme."""
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    entry = RepoPackageEntry('file:evil', '1.0', PackageType.WRAP)
    with pytest.raises(ValueError):
        _get_pkg_wrap_url(url, entry)


def test_wrap_url_builder_allows_colon_in_version():
    """A colon in the version is a valid segment and must stay pinned to the repo scheme."""
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    entry = RepoPackageEntry('foo', 'ftp:1.0', PackageType.WRAP)

    wrap_url = _get_pkg_wrap_url(url, entry)
    assert wrap_url == 'https://wrapdb.mesonbuild.com/v2/foo_ftp:1.0/foo.wrap'
    assert urllib.parse.urlparse(wrap_url).scheme == 'https'


def test_wrap_url_builders():
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    entry = RepoPackageEntry('foo', '1.2.3', PackageType.WRAP)

    assert _get_pkg_wrap_url(url, entry) == 'https://wrapdb.mesonbuild.com/v2/foo_1.2.3/foo.wrap'


def test_wrap_add_remove_not_supported():
    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    repo = Wrap(url, {})

    with pytest.raises(NotImplementedError, match='does not support adding'):
        repo.add_package(
            WrapPackage.from_wrap_text(
                'foo',
                '1.0.0',
                '[wrap-file]\nsource_url=x\nsource_filename=y\nsource_hash=z\n',
            )
        )

    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    repo.packages[repo_key] = RepoPackageEntry('foo', '1.0.0', PackageType.WRAP)

    with pytest.raises(NotImplementedError, match='does not support removing'):
        repo.remove_package(
            WrapPackage.from_wrap_text(
                'foo',
                '1.0.0',
                '[wrap-file]\nsource_url=x\nsource_filename=y\nsource_hash=z\n',
            )
        )


def test_wrap_get_package_parses_wrap(monkeypatch):
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        'patch_url=https://wrapdb.mesonbuild.com/v2/foo_1.0.0/get_patch\n'
        'patch_filename=foo-1.0.0-1.patch\n'
        'patch_hash=beefdead\n'
    )

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(wrap_text)

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    url = urllib.parse.urlparse('https://wrapdb.mesonbuild.com/v2/')
    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    packages = {repo_key: RepoPackageEntry('foo', '1.0.0', PackageType.WRAP)}
    repo = Wrap(url, packages)

    pkg = repo.get_package(repo_key)
    assert isinstance(pkg, WrapPackage)
    assert pkg.name == 'foo'
    assert pkg.version == '1.0.0'
    assert pkg.source_url == 'https://example.com/foo.tar.xz'
    assert pkg.source_filename == 'foo.tar.xz'
    assert pkg.source_hash == 'deadbeef'


def test_wrap_from_url_offline_uses_cache(tmp_path):
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({'foo': {'versions': ['1.0.0']}}), encoding='utf-8')

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path, offline=True)
    repo_key = make_repo_key('foo', '1.0.0', PackageType.WRAP)
    assert repo_key in repo.packages


def test_wrap_releases_cache_isolates_same_host_different_path(tmp_path):
    """Repositories sharing a host but differing by path must not share a cache file."""
    cache_path = tmp_path / 'cache'
    url_a = 'https://packages.example.com/team-a/v2/'
    url_b = 'https://packages.example.com/team-b/v2/'

    cache_a = _cache_file_for(cache_path, url_a)
    cache_b = _cache_file_for(cache_path, url_b)
    assert cache_a != cache_b

    cache_a.parent.mkdir(parents=True, exist_ok=True)
    cache_b.parent.mkdir(parents=True, exist_ok=True)
    cache_a.write_text(json.dumps({'a': {'versions': ['1.0.0']}}), encoding='utf-8')
    cache_b.write_text(json.dumps({'b': {'versions': ['2.0.0']}}), encoding='utf-8')

    repo_a = Wrap.from_url(url_a, cache_path=cache_path, offline=True)
    repo_b = Wrap.from_url(url_b, cache_path=cache_path, offline=True)

    assert make_repo_key('a', '1.0.0', PackageType.WRAP) in repo_a.packages
    assert make_repo_key('b', '2.0.0', PackageType.WRAP) in repo_b.packages
    assert make_repo_key('b', '2.0.0', PackageType.WRAP) not in repo_a.packages


def test_wrap_from_url_offline_requires_cache(tmp_path):
    with pytest.raises(
        ColliderUserError, match='Offline mode requires cached wrap releases'
    ) as excinfo:
        Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=tmp_path, offline=True)
    assert excinfo.value.exit_code == os.EX_DATAERR


def test_wrap_from_url_uses_ttl_cache_when_fresh(tmp_path, monkeypatch):
    """Cached releases.json within TTL skips the HTTP fetch entirely."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({'foo': {'versions': ['1.0.0']}}), encoding='utf-8')

    http_called = False

    def _fake_urlopen(url, **_kwargs):
        nonlocal http_called
        http_called = True
        return _DummyResponse(json.dumps({'bar': {'versions': ['2.0.0']}}))

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert not http_called
    assert make_repo_key('foo', '1.0.0', PackageType.WRAP) in repo.packages


def test_wrap_from_url_corrupt_ttl_cache_refetches(tmp_path, monkeypatch):
    """A corrupt within-TTL cache is treated as a miss and refreshed from the network."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{ not valid json', encoding='utf-8')

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(json.dumps({'fresh': {'versions': ['3.0.0']}}))

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert make_repo_key('fresh', '3.0.0', PackageType.WRAP) in repo.packages


def test_wrap_from_url_network_failure_falls_back_to_stale_cache(tmp_path, monkeypatch, caplog):
    """A failed refresh serves the stale cache with a warning."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({'stale': {'versions': ['0.1.0']}}), encoding='utf-8')
    stale_mtime = time.time() - _RELEASES_TTL_SECONDS - 10
    os.utime(cache_file, (stale_mtime, stale_mtime))

    def _fail_urlopen(url, **_kwargs):
        raise urllib.error.URLError('network down')

    monkeypatch.setattr(urllib.request, 'urlopen', _fail_urlopen)

    with caplog.at_level('WARNING'):
        repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert make_repo_key('stale', '0.1.0', PackageType.WRAP) in repo.packages
    assert 'using cached data' in caplog.text


def test_wrap_from_url_null_body_raises_user_error(tmp_path, monkeypatch):
    """A 200 response with a `null` body is a user-facing data error, not an internal crash."""
    cache_path = tmp_path / 'cache'

    def _null_urlopen(url, **_kwargs):
        return _DummyResponse('null')

    monkeypatch.setattr(urllib.request, 'urlopen', _null_urlopen)

    with pytest.raises(ColliderUserError) as excinfo:
        Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert excinfo.value.exit_code == os.EX_DATAERR


def test_wrap_from_url_offline_non_object_cache_raises_user_error(tmp_path):
    """Offline mode with a non-object cached releases.json is a clean data error."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('[1, 2, 3]', encoding='utf-8')

    with pytest.raises(ColliderUserError) as excinfo:
        Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path, offline=True)
    assert excinfo.value.exit_code == os.EX_DATAERR


def test_wrap_from_url_network_failure_with_corrupt_cache_raises(tmp_path, monkeypatch):
    """A failed refresh re-raises the network error when the cache is unusable."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{ not valid json', encoding='utf-8')
    stale_mtime = time.time() - _RELEASES_TTL_SECONDS - 10
    os.utime(cache_file, (stale_mtime, stale_mtime))

    def _fail_urlopen(url, **_kwargs):
        raise urllib.error.URLError('network down')

    monkeypatch.setattr(urllib.request, 'urlopen', _fail_urlopen)

    with pytest.raises(urllib.error.URLError):
        Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)


def test_wrap_from_url_offline_corrupt_cache_errors(tmp_path):
    """Offline mode with a corrupt cache raises the clean offline error, not a parse crash."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{ not valid json', encoding='utf-8')

    with pytest.raises(
        ColliderUserError, match='Offline mode requires cached wrap releases'
    ) as excinfo:
        Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path, offline=True)
    assert excinfo.value.exit_code == os.EX_DATAERR


def test_wrap_from_url_fetches_when_ttl_expired(tmp_path, monkeypatch):
    """Stale cached releases.json (past TTL) triggers a fresh HTTP fetch."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({'old': {'versions': ['0.1.0']}}), encoding='utf-8')

    stale_mtime = time.time() - _RELEASES_TTL_SECONDS - 10
    os.utime(cache_file, (stale_mtime, stale_mtime))

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(json.dumps({'fresh': {'versions': ['3.0.0']}}))

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert make_repo_key('fresh', '3.0.0', PackageType.WRAP) in repo.packages
    assert make_repo_key('old', '0.1.0', PackageType.WRAP) not in repo.packages


def test_wrap_from_url_fetches_when_no_cache(tmp_path, monkeypatch):
    """Without a cached file, HTTP fetch always happens."""
    cache_path = tmp_path / 'empty_cache'

    def _fake_urlopen(url, **_kwargs):
        return _DummyResponse(json.dumps({'new': {'versions': ['1.0.0']}}))

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path)
    assert make_repo_key('new', '1.0.0', PackageType.WRAP) in repo.packages


def test_wrap_from_url_ttl_not_checked_in_offline_mode(tmp_path, monkeypatch):
    """Offline mode always uses cache regardless of age -- no TTL check."""
    cache_path = tmp_path / 'cache'
    cache_file = _cache_file_for(cache_path, 'https://wrapdb.mesonbuild.com/v2/')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({'stale': {'versions': ['0.1.0']}}), encoding='utf-8')

    stale_mtime = time.time() - _RELEASES_TTL_SECONDS - 3600
    os.utime(cache_file, (stale_mtime, stale_mtime))

    http_called = False

    def _fake_urlopen(url, **_kwargs):
        nonlocal http_called
        http_called = True
        return _DummyResponse(json.dumps({}))

    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)

    repo = Wrap.from_url('https://wrapdb.mesonbuild.com/v2/', cache_path=cache_path, offline=True)
    assert not http_called
    assert make_repo_key('stale', '0.1.0', PackageType.WRAP) in repo.packages
