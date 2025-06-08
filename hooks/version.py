# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""MkDocs hook that reads the project version from pyproject.toml."""

from pathlib import Path

import tomllib


def on_config(config):
    pyproject = Path(config['config_file_path']).parent / 'pyproject.toml'
    with open(pyproject, 'rb') as f:
        data = tomllib.load(f)
    config['extra']['version'] = data['project']['version']
