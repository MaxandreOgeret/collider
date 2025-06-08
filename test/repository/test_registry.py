# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

from collider.repository import RepoImplRegistry
from collider.repository.implementation.Collider import Collider
from collider.repository.implementation.Wrap import Wrap


def test_repo_registry_includes_wrap():
    assert RepoImplRegistry.wrap.cls is Wrap


def test_repo_registry_includes_collider_and_subclasses_wrap():
    assert 'collider' in RepoImplRegistry.get_impls()
    assert RepoImplRegistry.collider.cls is Collider
    assert issubclass(Collider, Wrap)
