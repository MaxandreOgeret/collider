# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

import pytest

from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key, parse_repo_key
from collider.utils.packaging.types import RepoKey


class _DummyRepo(RepositoryInterface):
    def __init__(self, packages: dict[RepoKey, RepoPackageEntry]) -> None:
        super().__init__(packages)
        self.last_key: RepoKey | None = None

    @classmethod
    def _from_url_impl(cls, _url, **_kwargs):
        raise NotImplementedError

    def _add_package_impl(self, _package, **_kwargs) -> RepoPackageEntry:
        raise NotImplementedError

    def _remove_package_impl(self, _package, _entry) -> None:
        raise NotImplementedError

    def _get_package_impl(self, repo_key: RepoKey):
        self.last_key = repo_key
        return None


def test_make_repo_key_encodes_and_parse_roundtrips() -> None:
    name = 'demo @pkg'
    version = '1.0#1'
    key = make_repo_key(name, version, PackageType.WRAP)

    assert '%40' in key
    assert '%23' in key
    assert '%20' in key

    parsed = parse_repo_key(key)
    assert parsed == (name, version, PackageType.WRAP.value)


def test_parse_repo_key_invalid() -> None:
    with pytest.raises(ValueError, match='Invalid repo key format'):
        parse_repo_key('not-a-valid-key')


def test_repo_package_entry_from_key() -> None:
    key = make_repo_key('demo', '2.0.0', PackageType.WRAP)
    entry = RepoPackageEntry.from_repo_key(key)
    assert entry.name == 'demo'
    assert entry.version == '2.0.0'
    assert entry.package_type == PackageType.WRAP


def test_get_package_accepts_legacy_key() -> None:
    name = 'demo space'
    version = '1.0.0'
    encoded_key = make_repo_key(name, version, PackageType.WRAP)
    entry = RepoPackageEntry(name, version, PackageType.WRAP)
    repo = _DummyRepo({encoded_key: entry})

    legacy_key = f'{name}@{version}#wrap'
    repo.get_package(legacy_key)

    assert repo.last_key == encoded_key
