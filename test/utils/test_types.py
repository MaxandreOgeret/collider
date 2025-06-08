# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import inspect

import collider.repository.entries as repo_entries
import collider.repository.implementation.RepositoryInterface as repo_interface

from collider.utils.packaging.types import RepoKey


def test_repo_key_alias_exists() -> None:
    assert RepoKey is str


def test_repo_key_used_in_annotations() -> None:
    assert 'RepoKey' in inspect.getsource(repo_entries)
    assert 'RepoKey' in inspect.getsource(repo_interface)
