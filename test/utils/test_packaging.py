# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for collider.utils.packaging."""

from collider.file_model.colliderfile import Colliderfile
from collider.utils.packaging import validate_dependencies
from collider.utils.packaging.Dependency import Dependency, DependencySource


def test_validate_dependencies_subproject_names_not_superfluous() -> None:
    """A Colliderfile dep that is only in subproject_names is not considered superfluous."""
    colliderfile = Colliderfile(
        dependencies=[Dependency('tclap', DependencySource.COLLIDER, '1.2.4-4')]
    )
    dependencies_info: list[dict] = []
    subproject_names = {'tclap'}

    result = validate_dependencies(
        colliderfile, dependencies_info, subproject_names=subproject_names
    )

    assert result is True
