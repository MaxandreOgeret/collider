# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `repo` command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from collider.Context import Context
from collider.errors import ColliderUserError
from collider.file_model.configfile import ConfigFile
from collider.subcommand.Repo import Repo


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


def test_repo_ex_usage_unknown_subcommand() -> None:
    """`repo` returns EX_USAGE when repo_action is not add/remove/list."""
    # Not reachable via run_subcommand: subparsers are required=True, so argparse
    # exits 2 before execute() runs. Must drive execute() directly.
    cmd = Repo(_repo_args(action='bogus'), _make_context())

    assert cmd.execute() == os.EX_USAGE


def test_repo_ex_dataerr_duplicate_name() -> None:
    """`repo add` returns EX_DATAERR when the repository name already exists."""
    first = Repo(
        _repo_args(name='wrapdb', repo_type='wrap', url='https://wrapdb.mesonbuild.com/v2/'),
        _make_context(),
    )
    second = Repo(
        _repo_args(name='wrapdb', repo_type='wrap', url='https://other.example.com/v2/'),
        _make_context(),
    )

    assert first.execute() == os.EX_OK
    assert second.execute() == os.EX_DATAERR


def test_repo_ex_ioerr_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repo add` returns EX_IOERR when saving the config to disk raises OSError."""
    # Seed an existing config first so _load_or_create_config does not save (and
    # fail) before the targeted config_file.save() in _execute_add.
    seed = Repo(
        _repo_args(name='wrapdb', repo_type='wrap', url='https://wrapdb.mesonbuild.com/v2/'),
        _make_context(),
    )
    assert seed.execute() == os.EX_OK

    def _raise(self: ConfigFile, path: Path) -> None:
        raise OSError('disk full')

    monkeypatch.setattr(ConfigFile, 'save', _raise)

    cmd = Repo(
        _repo_args(name='mirror', repo_type='wrap', url='https://mirror.example.com/v2/'),
        _make_context(),
    )

    assert cmd.execute() == os.EX_IOERR


def test_repo_ex_ok_add_succeeds() -> None:
    """`repo add` returns EX_OK when the repository is added and config saved."""
    cmd = Repo(
        _repo_args(name='wrapdb', repo_type='wrap', url='https://wrapdb.mesonbuild.com/v2/'),
        _make_context(),
    )

    assert cmd.execute() == os.EX_OK


def test_repo_ex_noinput_remove_nonexistent() -> None:
    """`repo remove` returns EX_NOINPUT when no entry matches the requested name."""
    cmd = Repo(_repo_args(name='nonexistent', action='remove'), _make_context())

    assert cmd.execute() == os.EX_NOINPUT


def test_repo_corrupt_config_propagates_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`repo list` re-raises ColliderUserError with the carried exit code on a corrupt config."""
    config_path = tmp_path / 'config.json'
    config_path.write_text('{ not valid json', encoding='utf-8')
    monkeypatch.setattr('collider.config.get_config_path', lambda: config_path)

    cmd = Repo(_repo_args(action='list'), _make_context())

    with pytest.raises(ColliderUserError) as excinfo:
        cmd.execute()
    assert excinfo.value.exit_code == os.EX_DATAERR
