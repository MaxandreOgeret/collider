# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import json

from pathlib import Path

import pytest

from collider.file_model.configfile import ConfigFile, RepoEntry
from collider.repository import RepoImplRegistry


def test_repo_entry_instantiation():
    """
    Test RepoEntry instantiation.
    """
    entry = RepoEntry(
        name='local',
        type=RepoImplRegistry.filesystem,
        url='/tmp/repo',
        publish_url='file:///tmp/repo',
    )
    assert entry.name == 'local'
    assert entry.type == RepoImplRegistry.filesystem
    assert entry.url == '/tmp/repo'


def test_config_file_instantiation():
    """
    Test ConfigFile instantiation.
    """
    config = ConfigFile()
    assert config.repositories == []


def test_config_file_as_dict():
    """
    Test ConfigFile serialization to dict.
    """
    entry = RepoEntry(
        name='local',
        type=RepoImplRegistry.filesystem,
        url='/tmp/repo',
        publish_url='file:///tmp/repo',
    )
    config = ConfigFile(repositories=[entry])

    expected = {
        'repositories': [
            {
                'name': 'local',
                'type': 'filesystem',
                'url': '/tmp/repo',
                'publish_url': 'file:///tmp/repo',
            }
        ]
    }
    assert config.as_dict() == expected


def test_config_file_as_dict_with_publish_url():
    entry = RepoEntry(
        name='local',
        type=RepoImplRegistry.filesystem,
        url='/tmp/repo',
        publish_url='https://packages.example.com/collider/',
    )
    config = ConfigFile(repositories=[entry])

    expected = {
        'repositories': [
            {
                'name': 'local',
                'type': 'filesystem',
                'url': '/tmp/repo',
                'publish_url': 'https://packages.example.com/collider/',
            }
        ]
    }
    assert config.as_dict() == expected


def test_config_file_from_dict():
    """
    Test ConfigFile deserialization from dict.
    """
    data = {
        'repositories': [
            {
                'name': 'local',
                'type': 'filesystem',
                'url': '/tmp/repo',
                'publish_url': 'file:///tmp/repo',
            }
        ]
    }
    config = ConfigFile.from_dict(ConfigFile, data)

    assert len(config.repositories) == 1
    assert config.repositories[0].name == 'local'
    assert config.repositories[0].type == RepoImplRegistry.filesystem
    assert config.repositories[0].url == '/tmp/repo'


def test_config_file_from_dict_with_publish_url():
    data = {
        'repositories': [
            {
                'name': 'local',
                'type': 'filesystem',
                'url': '/tmp/repo',
                'publish_url': 'https://packages.example.com/collider/',
            }
        ]
    }
    config = ConfigFile.from_dict(ConfigFile, data)

    assert len(config.repositories) == 1
    assert config.repositories[0].publish_url == 'https://packages.example.com/collider/'


def test_config_file_save_load(tmp_path: Path):
    """
    Test saving and loading ConfigFile from path.
    """
    config_path = tmp_path / 'config.json'
    entry = RepoEntry(
        name='local',
        type=RepoImplRegistry.filesystem,
        url='/tmp/repo',
        publish_url='file:///tmp/repo',
    )
    config = ConfigFile(repositories=[entry])

    config.save(config_path)
    assert config_path.exists()

    loaded_config = ConfigFile.from_path(config_path)
    assert loaded_config.repositories == config.repositories


def test_config_file_validation():
    """
    Test ConfigFile validation against schema.
    """
    entry = RepoEntry(
        name='local',
        type=RepoImplRegistry.filesystem,
        url='/tmp/repo',
        publish_url='file:///tmp/repo',
    )
    config = ConfigFile(repositories=[entry])

    assert config.validate() is True


def test_config_file_validation_requires_publish_url():
    entry = RepoEntry(name='local', type=RepoImplRegistry.filesystem, url='/tmp/repo')
    config = ConfigFile(repositories=[entry])

    assert config.validate() is False


def test_config_file_validation_failure(tmp_path: Path):
    """
    Test ConfigFile validation failure with invalid data.
    """
    config_path = tmp_path / 'invalid_config.json'
    # 'type' is missing in one of the entries
    invalid_data = {'repositories': [{'name': 'test', 'url': '/tmp/repo'}]}
    config_path.write_text(json.dumps(invalid_data))

    # from_path calls validate() which should fail for invalid data
    # However, it seems prepare_ctor_kwargs raises TypeError if a required field is missing
    # before validate() is even called on the instance.

    with pytest.raises(TypeError):
        ConfigFile.from_path(config_path)
