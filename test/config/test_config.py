# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import json
import os

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from collider import config
from collider.cache import WrapCache
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap


def test_config_load_creates_default_file(mock_home: Path):
    """
    Test that calling load() creates a default config file if it does not exist.
    """
    expected_config_path = mock_home / '.config' / 'collider' / 'config.json'

    # Ensure the file doesn't exist before calling load
    assert not expected_config_path.exists()

    # Load context and configuration, which should trigger default file creation
    context = config.load()
    app_config = context.config

    # Verify that the config file was created
    assert expected_config_path.exists()

    # Verify that the returned AppConfig has the correct collider_home_path
    assert app_config.collider_home_path == expected_config_path.parent

    # Verify the content of the created file
    with open(expected_config_path, 'r') as f:
        data = json.load(f)

    # By default, repositories should be an empty dictionary in ConfigFile
    assert data == {'repositories': []}

    # Verify that the cache was initialized.
    assert context.cache.root == app_config.collider_home_path / 'cache'

    # Verify that we can load it again and it is valid
    from collider.file_model.configfile import ConfigFile

    config_file = ConfigFile.from_path(expected_config_path)
    assert config_file.repositories == []
    assert config_file.validate() is True


def test_load_no_repositories(mock_home: Path):
    """
    Test loading configuration when no repositories are defined.
    """
    # config.load() creates a default config if it doesn't exist.
    context = config.load()

    assert context.config.repositories == {}
    assert isinstance(context.cache, WrapCache)


def test_load_with_filesystem_repository(mock_home: Path):
    """
    Test loading configuration with a filesystem repository.
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    repo_path = mock_home / 'my-repo'
    repo_path.mkdir()
    repo_url = f'file://{repo_path}'

    config_data = {
        'repositories': [
            {
                'name': 'local-repo',
                'type': 'filesystem',
                'url': str(repo_url),
                'publish_url': repo_url,
            }
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config_data, f)

    context = config.load()

    assert 'local-repo' in context.config.repositories
    repo = context.config.repositories['local-repo']
    assert isinstance(repo, Filesystem)
    assert repo.path == repo_path


def test_load_multiple_repositories(mock_home: Path):
    """
    Test loading configuration with multiple repositories.
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    repo1_path = mock_home / 'repo1'
    repo1_path.mkdir()
    repo2_path = mock_home / 'repo2'
    repo2_path.mkdir()

    config_data = {
        'repositories': [
            {
                'name': 'repo1',
                'type': 'filesystem',
                'url': f'file://{repo1_path}',
                'publish_url': f'file://{repo1_path}',
            },
            {
                'name': 'repo2',
                'type': 'filesystem',
                'url': f'file://{repo2_path}',
                'publish_url': f'file://{repo2_path}',
            },
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config_data, f)

    context = config.load()

    assert len(context.config.repositories) == 2
    assert 'repo1' in context.config.repositories
    assert 'repo2' in context.config.repositories
    assert isinstance(context.config.repositories['repo1'], Filesystem)
    assert isinstance(context.config.repositories['repo2'], Filesystem)


def test_load_skips_missing_repository(mock_home: Path):
    """
    Test that invalid repositories do not abort config loading.
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    repo1_path = mock_home / 'repo1'
    repo1_path.mkdir()
    missing_path = mock_home / 'missing-repo'

    config_data = {
        'repositories': [
            {
                'name': 'repo1',
                'type': 'filesystem',
                'url': f'file://{repo1_path}',
                'publish_url': f'file://{repo1_path}',
            },
            {
                'name': 'missing',
                'type': 'filesystem',
                'url': f'file://{missing_path}',
                'publish_url': f'file://{missing_path}',
            },
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config_data, f)

    context = config.load()

    assert 'repo1' in context.config.repositories
    assert 'missing' not in context.config.repositories


def test_load_duplicate_repository_names(mock_home: Path):
    """
    Test loading configuration with duplicate repository names.
    The last one should win (standard dict behavior).
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    repo1_path = mock_home / 'repo1'
    repo1_path.mkdir()
    repo2_path = mock_home / 'repo2'
    repo2_path.mkdir()

    config_data = {
        'repositories': [
            {
                'name': 'my-repo',
                'type': 'filesystem',
                'url': f'file://{repo1_path}',
                'publish_url': f'file://{repo1_path}',
            },
            {
                'name': 'my-repo',
                'type': 'filesystem',
                'url': f'file://{repo2_path}',
                'publish_url': f'file://{repo2_path}',
            },
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config_data, f)

    context = config.load()

    assert len(context.config.repositories) == 1
    assert context.config.repositories['my-repo'].path == repo2_path  # ty:ignore[unresolved-attribute]


def test_load_with_wrap_repository(mock_home: Path, monkeypatch):
    """
    Test loading configuration with a wrap repository.
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    config_data = {
        'repositories': [
            {
                'name': 'wrapdb',
                'type': 'wrap',
                'url': 'https://wrapdb.mesonbuild.com/v2/',
            }
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config_data))

    dummy_repo = MagicMock(spec=Wrap)

    def _fake_from_url_impl(cls, url, cache_path=None, offline=False, **kwargs):
        return dummy_repo

    monkeypatch.setattr(Wrap, '_from_url_impl', classmethod(_fake_from_url_impl))

    context = config.load()

    assert context.config.repositories['wrapdb'] is dummy_repo


def test_load_with_collider_repository(mock_home: Path, monkeypatch):
    """
    Test loading configuration with a collider repository.
    """
    collider_home = config.get_default_collider_home()
    config_path = config.get_config_path()

    config_data = {
        'repositories': [
            {
                'name': 'my-collider',
                'type': 'collider',
                'url': 'https://packages.example.com/collider/v2/',
            }
        ]
    }

    collider_home.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config_data))

    dummy_repo = MagicMock(spec=Collider)

    def _fake_from_url_impl(cls, url, cache_path=None, offline=False, **kwargs):
        return dummy_repo

    monkeypatch.setattr(Collider, '_from_url_impl', classmethod(_fake_from_url_impl))

    context = config.load()

    assert context.config.repositories['my-collider'] is dummy_repo


def test_load_invalid_json_exits(mock_home: Path, caplog) -> None:
    config_path = config.get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"repositories": [}', encoding='utf-8')

    with pytest.raises(SystemExit) as excinfo:
        config.load()

    assert excinfo.value.code == os.EX_DATAERR
    assert f'File "{config_path.as_posix()}" is invalid' in caplog.text
