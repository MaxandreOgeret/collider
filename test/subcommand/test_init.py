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
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_DATAERR
    finally:
        os.chdir(cwd)

    assert not (tmp_path / Colliderfile.get_filename()).exists()


def test_init_logs_found_metadata(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """When introspection succeeds, name/version/license are logged as INFO."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    info = {'descriptive_name': 'mylib', 'version': '1.2.3', 'license': ['MIT']}
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(info), caplog.at_level(logging.INFO):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'mylib' in caplog.text
    assert '1.2.3' in caplog.text
    assert 'MIT' in caplog.text


def test_init_warns_on_undefined_version(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """version == 'undefined' triggers a WARNING."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    info = {'descriptive_name': 'mylib', 'version': 'undefined', 'license': ['MIT']}
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(info), caplog.at_level(logging.WARNING):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert any('version' in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_init_warns_on_unknown_license(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """license == ['unknown'] triggers a WARNING."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    info = {'descriptive_name': 'mylib', 'version': '1.0.0', 'license': ['unknown']}
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(info), caplog.at_level(logging.WARNING):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert any('license' in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_init_always_warns_about_description(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """description warning is always emitted regardless of introspection result."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None), caplog.at_level(logging.WARNING):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert any('description' in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_init_graceful_when_introspect_fails(tmp_path: Path) -> None:
    """init succeeds and creates collider.json even when introspection returns None."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with _mock_project_info(None):
            assert run_subcommand(Subcommand.INIT, []) == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (tmp_path / Colliderfile.get_filename()).exists()
