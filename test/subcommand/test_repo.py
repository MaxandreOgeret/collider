# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock

from collider import config
from collider.Context import Context
from collider.file_model.configfile import ConfigFile
from collider.subcommand.Repo import Repo
from test.common.common import Subcommand, run_subcommand


def _make_context() -> Context:
    return MagicMock(spec=Context)


def _repo_args(
    *,
    name: str = '',
    repo_type: str = '',
    url: str = '',
    publish_url: str | None = None,
    action: str = 'add',
) -> argparse.Namespace:
    return argparse.Namespace(
        repo_subcommand=action,
        repo_action=action,
        name=name,
        type=repo_type,
        url=url,
        publish_url=publish_url,
    )


def test_repo_register_add() -> None:
    parser = argparse.ArgumentParser()
    Repo.register(parser)

    args = parser.parse_args(
        [
            'add',
            'local',
            'filesystem',
            'file:///tmp/repo',
            '--publish-url',
            'https://packages.example.com/collider/',
        ]
    )
    assert args.repo_subcommand == 'add'
    assert args.repo_action == 'add'
    assert args.name == 'local'
    assert args.type == 'filesystem'
    assert args.url == 'file:///tmp/repo'
    assert args.publish_url == 'https://packages.example.com/collider/'

    args = parser.parse_args(['add', 'wrapdb', 'wrap', 'https://wrapdb.mesonbuild.com/v2/'])
    assert args.repo_subcommand == 'add'
    assert args.type == 'wrap'
    assert args.publish_url is None


def test_repo_register_remove_and_list() -> None:
    parser = argparse.ArgumentParser()
    Repo.register(parser)

    args = parser.parse_args(['list'])
    assert args.repo_subcommand == 'list'
    assert args.repo_action == 'list'

    args = parser.parse_args(['ls'])
    assert args.repo_subcommand == 'ls'
    assert args.repo_action == 'list'

    args = parser.parse_args(['remove', 'local'])
    assert args.repo_subcommand == 'remove'
    assert args.repo_action == 'remove'
    assert args.name == 'local'

    args = parser.parse_args(['rm', 'local'])
    assert args.repo_subcommand == 'rm'
    assert args.repo_action == 'remove'
    assert args.name == 'local'


def test_repo_add_filesystem_persists_config(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    cmd = Repo(
        _repo_args(
            name='local',
            repo_type='filesystem',
            url=repo_path.as_uri(),
            publish_url='https://packages.example.com/collider/',
        ),
        _make_context(),
    )

    assert cmd.execute() == os.EX_OK

    config_file = ConfigFile.from_path(config.get_config_path())
    assert len(config_file.repositories) == 1
    entry = config_file.repositories[0]
    assert entry.name == 'local'
    assert entry.type.name == 'filesystem'
    assert entry.url == repo_path.as_uri()
    assert entry.publish_url == 'https://packages.example.com/collider/'


def test_repo_add_filesystem_requires_publish_url(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    cmd = Repo(
        _repo_args(
            name='local',
            repo_type='filesystem',
            url=repo_path.as_uri(),
            publish_url=None,
        ),
        _make_context(),
    )

    assert cmd.execute() == os.EX_USAGE
    assert 'Filesystem repositories require --publish-url.' in caplog.text


def test_repo_add_wrap_ignores_publish_url(caplog) -> None:
    cmd = Repo(
        _repo_args(
            name='wrapdb',
            repo_type='wrap',
            url='https://wrapdb.mesonbuild.com/v2/',
            publish_url='https://ignored.example.com/',
        ),
        _make_context(),
    )

    assert cmd.execute() == os.EX_OK
    assert 'Ignoring --publish-url for non-filesystem repository.' in caplog.text

    config_file = ConfigFile.from_path(config.get_config_path())
    assert len(config_file.repositories) == 1
    entry = config_file.repositories[0]
    assert entry.name == 'wrapdb'
    assert entry.type.name == 'wrap'
    assert entry.publish_url is None


def test_repo_add_duplicate_name_rejected(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    first = Repo(
        _repo_args(
            name='local',
            repo_type='filesystem',
            url=repo_path.as_uri(),
            publish_url='https://packages.example.com/collider/',
        ),
        _make_context(),
    )
    second = Repo(
        _repo_args(
            name='local',
            repo_type='wrap',
            url='https://wrapdb.mesonbuild.com/v2/',
        ),
        _make_context(),
    )

    assert first.execute() == os.EX_OK
    assert second.execute() == os.EX_DATAERR
    assert 'Repository "local" already exists in config.' in caplog.text


def test_repo_add_duplicate_url_warns(caplog) -> None:
    first = Repo(
        _repo_args(
            name='wrapdb',
            repo_type='wrap',
            url='https://wrapdb.mesonbuild.com/v2/',
        ),
        _make_context(),
    )
    second = Repo(
        _repo_args(
            name='mirror',
            repo_type='wrap',
            url='https://wrapdb.mesonbuild.com/v2',
        ),
        _make_context(),
    )

    assert first.execute() == os.EX_OK
    assert second.execute() == os.EX_OK
    assert (
        'Repository URL "https://wrapdb.mesonbuild.com/v2" is already configured by: wrapdb.'
        in caplog.text
    )

    config_file = ConfigFile.from_path(config.get_config_path())
    assert [entry.name for entry in config_file.repositories] == ['wrapdb', 'mirror']


def test_repo_list_shows_configured_repositories(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    add_cmd = Repo(
        _repo_args(
            name='local',
            repo_type='filesystem',
            url=repo_path.as_uri(),
            publish_url='https://packages.example.com/collider/',
        ),
        _make_context(),
    )
    assert add_cmd.execute() == os.EX_OK

    list_cmd = Repo(_repo_args(action='list'), _make_context())
    assert list_cmd.execute() == os.EX_OK
    assert 'local:' in caplog.text
    assert 'type: filesystem' in caplog.text
    assert f'url: {repo_path.as_uri()}' in caplog.text
    assert 'publish_url: https://packages.example.com/collider/' in caplog.text


def test_repo_list_empty_config(caplog) -> None:
    cmd = Repo(_repo_args(action='list'), _make_context())

    assert cmd.execute() == os.EX_OK
    assert 'No repositories configured.' in caplog.text


def test_repo_remove_existing_repository(tmp_path: Path, caplog) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    add_cmd = Repo(
        _repo_args(
            name='local',
            repo_type='filesystem',
            url=repo_path.as_uri(),
            publish_url='https://packages.example.com/collider/',
        ),
        _make_context(),
    )
    assert add_cmd.execute() == os.EX_OK

    config_file = ConfigFile.from_path(config.get_config_path())
    assert len(config_file.repositories) == 1

    remove_cmd = Repo(_repo_args(name='local', action='remove'), _make_context())
    assert remove_cmd.execute() == os.EX_OK
    assert 'Removed repository "local".' in caplog.text

    config_file = ConfigFile.from_path(config.get_config_path())
    assert len(config_file.repositories) == 0


def test_repo_remove_nonexistent_repository(caplog) -> None:
    cmd = Repo(_repo_args(name='nonexistent', action='remove'), _make_context())

    assert cmd.execute() == os.EX_NOINPUT
    assert 'Repository "nonexistent" not found in config.' in caplog.text


def test_repo_add_integrates_with_entrypoint(tmp_path: Path) -> None:
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()

    exit_code = run_subcommand(
        Subcommand.REPO,
        [
            'add',
            'local',
            'filesystem',
            repo_path.as_uri(),
            '--publish-url',
            'https://packages.example.com/collider/',
        ],
    )
    assert exit_code == os.EX_OK

    config_file = ConfigFile.from_path(config.get_config_path())
    assert any(
        entry.name == 'local' and entry.type.name == 'filesystem'
        for entry in config_file.repositories
    )


def test_repo_list_integrates_with_entrypoint() -> None:
    exit_code = run_subcommand(Subcommand.REPO, ['list'])
    assert exit_code == os.EX_OK


def test_publish_integrates_with_entrypoint() -> None:
    exit_code = run_subcommand(
        Subcommand.PUBLISH,
        ['nonexistent-repo', '--builddir', '__definitely_missing_builddir__'],
    )
    assert exit_code == os.EX_DATAERR


def test_unpublish_integrates_with_entrypoint() -> None:
    exit_code = run_subcommand(
        Subcommand.UNPUBLISH,
        ['nonexistent-repo', 'some-package', '1.0.0'],
    )
    assert exit_code == os.EX_NOINPUT
