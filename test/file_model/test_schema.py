# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import json

from pathlib import Path

from collider.file_model.configfile import ConfigFile
from collider.repository import RepoImplRegistry


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
