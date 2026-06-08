# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the init subcommand."""

import logging
import os

from pathlib import Path
from unittest.mock import patch

import pytest

from collider.file_model.colliderfile import Colliderfile
from collider.utils.packaging.Dependency import Dependency, DependencySource
from test.common.common import Subcommand, run_subcommand


def _mock_project_info(info: dict | None):
    """Patch scan_project_info in the Init module."""
    return patch('collider.subcommand.Init.scan_project_info', return_value=info)


def test_init_creates_colliderfile(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile_path = tmp_path / Colliderfile.get_filename()
    assert colliderfile_path.exists()
    colliderfile = Colliderfile.from_path(colliderfile_path)
    assert colliderfile.dependencies == []

    assert not (tmp_path / 'subprojects').exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    colliderfile_path = tmp_path / Colliderfile.get_filename()
    Colliderfile(
        description='Demo project',
        dependencies=[Dependency('sys', DependencySource.SYSTEM, None)],
    ).save(colliderfile_path)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    reloaded = Colliderfile.from_path(colliderfile_path)
    assert reloaded.description == 'Demo project'
    assert reloaded.dependencies == [Dependency('sys', DependencySource.SYSTEM, None)]


def test_init_requires_meson_build(tmp_path: Path) -> None:
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.INIT, []) == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert not (tmp_path / Colliderfile.get_filename()).exists()


def test_init_warns_when_license_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A WARNING is emitted when meson.build has no license field."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    info = {'descriptive_name': 'demo', 'version': '1.0.0', 'license': ['unknown']}
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(info), caplog.at_level(logging.WARNING):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert any('license' in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_init_no_warning_when_license_set(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No license WARNING when meson.build declares a license."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    info = {'descriptive_name': 'demo', 'version': '1.0.0', 'license': ['MIT']}
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(info), caplog.at_level(logging.WARNING):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not any('license' in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_init_graceful_when_introspect_fails(tmp_path: Path) -> None:
    """init succeeds even when introspection returns None."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / Colliderfile.get_filename()).exists()
