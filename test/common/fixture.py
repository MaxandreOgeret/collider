# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

import os
import shutil

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from _pytest.fixtures import SubRequest


_POSIX_EXIT_CODES = {
    'EX_OK': 0,
    'EX_USAGE': 64,
    'EX_DATAERR': 65,
    'EX_NOINPUT': 66,
    'EX_IOERR': 74,
}

for _name, _value in _POSIX_EXIT_CODES.items():
    if not hasattr(os, _name):
        setattr(os, _name, _value)


MESON_PROJECTS = ['header', 'none', 'shared']


@pytest.fixture(params=MESON_PROJECTS)
def meson_project(pytestconfig: pytest.Config, request: SubRequest):
    return pytestconfig.rootpath / 'test' / 'assets' / 'pkg' / request.param


@pytest.fixture(autouse=True)
def mock_home(tmp_path: Path):
    """
    Mock the HOME environment variable to a temporary directory for all tests.
    This ensures that tests do not affect the user's actual home directory.
    """
    original_cwd = os.getcwd()
    with patch.dict(
        os.environ,
        {
            'HOME': str(tmp_path),
            'XDG_CONFIG_HOME': str(tmp_path / '.config'),
        },
    ):
        yield tmp_path
    os.chdir(original_cwd)


@pytest.fixture(scope='function')
def temp_empty_meson_project(pytestconfig: pytest.Config):
    empty_project = pytestconfig.rootpath / 'test' / 'assets' / 'pkg' / 'none'
    assert empty_project.exists()

    with TemporaryDirectory() as tmpdir:
        shutil.copytree(empty_project, tmpdir, dirs_exist_ok=True)
        yield Path(tmpdir)
