# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

from pathlib import Path

import pytest

from collider.errors import ColliderUserError
from collider.file_model.colliderfile import Colliderfile
from collider.utils.packaging.Dependency import Dependency, DependencySource


def test_colliderfile_instantiation() -> None:
    """Test Colliderfile instantiation with optional description and dependencies."""
    cf = Colliderfile()
    assert cf.description is None
    assert cf.dependencies == []


def test_colliderfile_with_dependencies() -> None:
    """Test Colliderfile with dependencies."""
    deps = [
        Dependency(name='foo', source=DependencySource.COLLIDER, version='>=1.0.0'),
        Dependency(name='bar', source=DependencySource.SYSTEM, version=None),
    ]
    cf = Colliderfile(description='My project', dependencies=deps)
    assert cf.description == 'My project'
    assert len(cf.dependencies) == 2
    assert cf.dependencies[0].name == 'foo'
    assert cf.dependencies[1].source == DependencySource.SYSTEM


def test_colliderfile_get_filename() -> None:
    """Test fixed filename for Colliderfile."""
    assert Colliderfile.get_filename() == 'collider.json'


def test_colliderfile_as_dict() -> None:
    """Test Colliderfile serialization to dict."""
    cf = Colliderfile(
        description='Test project',
        dependencies=[
            Dependency(name='dep1', source=DependencySource.COLLIDER, version='1.0'),
        ],
    )
    d = cf.as_dict()
    assert d['description'] == 'Test project'
    assert len(d['dependencies']) == 1
    assert d['dependencies'][0]['name'] == 'dep1'
    assert d['dependencies'][0]['source'] == 'collider'
    assert d['dependencies'][0]['version'] == '1.0'


def test_colliderfile_from_dict() -> None:
    """Test Colliderfile deserialization from dict."""
    data = {
        'dependencies': [
            {'name': 'pkg', 'source': 'collider', 'version': '>=2.0'},
        ],
    }
    cf = Colliderfile.from_dict(Colliderfile, data)
    assert len(cf.dependencies) == 1
    assert cf.dependencies[0].name == 'pkg'
    assert cf.dependencies[0].source == DependencySource.COLLIDER
    assert cf.dependencies[0].version == '>=2.0'


def test_colliderfile_save_load(tmp_path: Path) -> None:
    """Test saving and loading Colliderfile from path."""
    path = tmp_path / 'collider.json'
    cf = Colliderfile(
        dependencies=[Dependency(name='x', source=DependencySource.COLLIDER, version='1.0')],
    )
    cf.save(path)
    assert path.exists()

    loaded = Colliderfile.from_path(path)
    assert len(loaded.dependencies) == 1
    assert loaded.dependencies[0].name == cf.dependencies[0].name


def test_colliderfile_validation() -> None:
    """Test Colliderfile validation against schema."""
    cf = Colliderfile(
        dependencies=[Dependency(name='d', source=DependencySource.COLLIDER, version='1.0')],
    )
    assert cf.validate() is True


def test_colliderfile_validation_failure_invalid_json(tmp_path: Path) -> None:
    """Test loading invalid JSON raises a clean user error."""
    path = tmp_path / 'collider.json'
    path.write_text('not json')
    with pytest.raises(ColliderUserError):
        Colliderfile.from_path(path)


def test_colliderfile_filename_mismatch(tmp_path: Path) -> None:
    """Test from_path rejects wrong filename."""
    path = tmp_path / 'wrong_name.json'
    path.write_text('{"dependencies": []}')
    with pytest.raises(ValueError, match='File name mismatch'):
        Colliderfile.from_path(path)
