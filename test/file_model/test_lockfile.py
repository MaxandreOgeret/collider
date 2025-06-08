# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

import json

from pathlib import Path

import pytest

from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash


HASH_A = 'sha256:' + 'a' * 64
HASH_B = 'sha256:' + 'b' * 64
HASH_C = 'sha256:' + 'c' * 64
HASH_D = 'sha256:' + 'd' * 64
HASH_X = 'sha256:' + '0' * 64
HASH_OLD = 'sha256:' + '1' * 64
HASH_NEW = 'sha256:' + '2' * 64


def test_locked_package_instantiation() -> None:
    """Test LockedPackage instantiation with required fields."""
    pkg = LockedPackage(version='1.2.3', wrap_hash=HASH_A)
    assert pkg.version == '1.2.3'
    assert pkg.wrap_hash == HASH_A


def test_lockfile_instantiation_defaults() -> None:
    """Test Lockfile instantiation with default values."""
    lf = Lockfile()
    assert lf.version == 1
    assert lf.dependencies == {}
    assert lf.packages == {}


def test_lockfile_instantiation_with_packages() -> None:
    """Test Lockfile instantiation with dependencies and packages."""
    deps = {
        'foo': LockedPackage(version='1.0', wrap_hash=HASH_A),
    }
    pkgs = {
        'bar': LockedPackage(version='2.0', wrap_hash=HASH_B),
    }
    lf = Lockfile(dependencies=deps, packages=pkgs)
    assert len(lf.dependencies) == 1
    assert lf.dependencies['foo'].version == '1.0'
    assert len(lf.packages) == 1
    assert lf.packages['bar'].version == '2.0'


def test_lockfile_all_packages() -> None:
    """Test all_packages property merges dependencies and packages."""
    lf = Lockfile(
        dependencies={'foo': LockedPackage(version='1.0', wrap_hash=HASH_A)},
        packages={'bar': LockedPackage(version='2.0', wrap_hash=HASH_B)},
    )
    all_pkgs = lf.all_packages
    assert len(all_pkgs) == 2
    assert 'foo' in all_pkgs
    assert 'bar' in all_pkgs


def test_lockfile_get_filename() -> None:
    """Test fixed filename for Lockfile."""
    assert Lockfile.get_filename() == 'collider.lock'


def test_lockfile_as_dict() -> None:
    """Test Lockfile serialization to dict."""
    lf = Lockfile(
        dependencies={
            'pkg': LockedPackage(version='3.0', wrap_hash=HASH_C),
        },
    )
    d = lf.as_dict()
    assert d['version'] == 1
    assert d['dependencies']['pkg']['version'] == '3.0'
    assert d['dependencies']['pkg']['wrap_hash'] == HASH_C
    assert 'repository' not in d['dependencies']['pkg']


def test_lockfile_as_dict_empty() -> None:
    """Test Lockfile serialization with no packages."""
    lf = Lockfile()
    d = lf.as_dict()
    assert d == {'version': 1, 'dependencies': {}, 'packages': {}}


def test_lockfile_save_load(tmp_path: Path) -> None:
    """Test saving and loading Lockfile from path."""
    path = tmp_path / 'collider.lock'
    lf = Lockfile(
        dependencies={
            'x': LockedPackage(version='1.0', wrap_hash=HASH_X),
        },
    )
    lf.save(path)
    assert path.exists()

    loaded = Lockfile.from_path(path)
    assert len(loaded.dependencies) == 1
    assert loaded.dependencies['x'].version == '1.0'
    assert loaded.dependencies['x'].wrap_hash == HASH_X


def test_lockfile_save_load_multiple(tmp_path: Path) -> None:
    """Test round-trip with dependencies and transitive packages."""
    path = tmp_path / 'collider.lock'
    lf = Lockfile(
        dependencies={
            'a': LockedPackage(version='1.0', wrap_hash=HASH_A),
        },
        packages={
            'b': LockedPackage(version='2.0', wrap_hash=HASH_B),
        },
    )
    lf.save(path)
    loaded = Lockfile.from_path(path)
    assert loaded.dependencies['a'].wrap_hash == HASH_A
    assert loaded.packages['b'].wrap_hash == HASH_B


def test_lockfile_validation() -> None:
    """Test Lockfile validation against schema."""
    lf = Lockfile(
        dependencies={
            'd': LockedPackage(version='1.0', wrap_hash=HASH_D),
        },
    )
    assert lf.validate() is True


def test_lockfile_validation_empty() -> None:
    """Test Lockfile validation with empty packages."""
    lf = Lockfile()
    assert lf.validate() is True


def test_lockfile_validation_failure_invalid_json(tmp_path: Path) -> None:
    """Test loading invalid JSON raises."""
    path = tmp_path / 'collider.lock'
    path.write_text('not json')
    with pytest.raises((json.JSONDecodeError, TypeError)):
        Lockfile.from_path(path)


def test_lockfile_filename_mismatch(tmp_path: Path) -> None:
    """Test from_path rejects wrong filename."""
    path = tmp_path / 'wrong_name.json'
    path.write_text('{"version": 1, "dependencies": {}, "packages": {}}')
    with pytest.raises(ValueError, match='File name mismatch'):
        Lockfile.from_path(path)


def test_compute_wrap_hash_deterministic() -> None:
    """Test that compute_wrap_hash is deterministic for the same input."""
    wrap_text = '[wrap-file]\nsource_url = https://example.com/foo.tar.xz\n'
    h1 = compute_wrap_hash(wrap_text)
    h2 = compute_wrap_hash(wrap_text)
    assert h1 == h2
    assert h1.startswith('sha256:')
    assert len(h1) == len('sha256:') + 64


def test_compute_wrap_hash_differs_for_different_input() -> None:
    """Test that compute_wrap_hash produces different hashes for different input."""
    h1 = compute_wrap_hash('wrap content A')
    h2 = compute_wrap_hash('wrap content B')
    assert h1 != h2


def test_lockfile_update_package(tmp_path: Path) -> None:
    """Test updating a dependency entry in an existing lockfile."""
    path = tmp_path / 'collider.lock'
    lf = Lockfile(
        dependencies={
            'pkg': LockedPackage(version='1.0', wrap_hash=HASH_OLD),
        },
    )
    lf.save(path)

    loaded = Lockfile.from_path(path)
    loaded.dependencies['pkg'] = LockedPackage(version='2.0', wrap_hash=HASH_NEW)
    loaded.save()

    reloaded = Lockfile.from_path(path)
    assert reloaded.dependencies['pkg'].version == '2.0'
    assert reloaded.dependencies['pkg'].wrap_hash == HASH_NEW
