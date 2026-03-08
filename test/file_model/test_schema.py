# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import json
import os
import shutil
import subprocess
import sys
import zipfile

from pathlib import Path


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility.
    import tomli as tomllib

from collider.file_model.configfile import ConfigFile
from collider.repository import RepoImplRegistry


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_wheel_builder() -> str | None:
    """Pick a Python executable that can invoke pip."""
    python_location = os.environ.get('pythonLocation')
    candidates = [
        sys.executable,
        getattr(sys, '_base_executable', None),
        str(Path(sys.base_prefix) / 'bin' / 'python3'),
        str(Path(sys.base_prefix) / 'bin' / 'python'),
        str(Path(python_location) / 'bin' / 'python3') if python_location else None,
        str(Path(python_location) / 'bin' / 'python') if python_location else None,
        shutil.which('python3'),
        shutil.which('python'),
    ]
    seen: set[str] = set()

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        result = subprocess.run(
            [candidate, '-c', 'import pip'],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_PROJECT_ROOT,
        )
        if result.returncode == 0:
            return candidate

    return None


def test_configfile_schema_accepts_wrap(tmp_path: Path):
    path = tmp_path / 'config.json'
    path.write_text(
        json.dumps(
            {
                'repositories': [
                    {'name': 'wrapdb', 'type': 'wrap', 'url': 'https://wrapdb.mesonbuild.com/v2/'}
                ]
            }
        )
    )
    cfg = ConfigFile.from_path(path)
    assert cfg.repositories[0].type == RepoImplRegistry.wrap


def test_configfile_schema_accepts_collider(tmp_path: Path):
    path = tmp_path / 'config.json'
    path.write_text(
        json.dumps(
            {
                'repositories': [
                    {
                        'name': 'my-collider',
                        'type': 'collider',
                        'url': 'https://packages.example.com/collider/v2/',
                    }
                ]
            }
        )
    )
    cfg = ConfigFile.from_path(path)
    assert cfg.repositories[0].type == RepoImplRegistry.collider


def test_wheel_includes_schema_files(tmp_path: Path) -> None:
    """Published wheels must ship the JSON schemas used at runtime."""
    pyproject = tomllib.loads((_PROJECT_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    package_data = pyproject['tool']['setuptools']['package-data']['collider']
    assert 'file_model/schema/*.json' in package_data

    builder = _find_wheel_builder()
    assert builder is not None, 'No Python interpreter with pip available.'

    result = subprocess.run(
        [
            builder,
            '-m',
            'pip',
            'wheel',
            '--no-deps',
            '.',
            '-w',
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=_PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stderr

    wheel_path = next(tmp_path.glob('collider_wraps-*.whl'))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    assert 'collider/file_model/schema/configfile.schema.json' in names
    assert 'collider/file_model/schema/colliderfile.schema.json' in names
    assert 'collider/file_model/schema/lockfile.schema.json' in names
