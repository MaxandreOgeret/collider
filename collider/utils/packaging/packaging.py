# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""File hashing and dependency validation."""

from __future__ import annotations

import hashlib

from pathlib import Path
from typing import TYPE_CHECKING

from collider.log import logger
from collider.utils.meson.infoTypes import DependencyInfo


if TYPE_CHECKING:
    from collider.file_model.colliderfile import Colliderfile


def compute_file_hash(file: Path) -> str:
    """
    Compute a SHA256 hash for a file.
    :param file: File path to hash.
    :return: Hexadecimal hash string.
    """
    h = hashlib.sha256()
    with open(file, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_dependencies(
    colliderfile: 'Colliderfile',
    dependencies_info: list[DependencyInfo],
    subproject_names: set[str] | None = None,
) -> bool:
    """
    Compares dependencies between the Colliderfile and a list of dependencies
    from Meson info to ensure consistency.
    :param colliderfile: Colliderfile instance to validate.
    :param dependencies_info: Dependency list from Meson info.
    :param subproject_names: Names of subprojects from intro-projectinfo.json; deps with these names are not considered superfluous.
    :return: True if colliderfile dependencies are a subset of meson dependencies, False otherwise.
    """
    mesonfile_deps = {dep['name'] for dep in dependencies_info} | (subproject_names or set())
    colliderfile_deps = {dep.name for dep in colliderfile.dependencies}

    # Meson introspection may include optional/system deps; Collider only manages explicit ones.
    missing = mesonfile_deps - colliderfile_deps
    superfluous = colliderfile_deps - mesonfile_deps

    if missing:
        missing_str = ', '.join(f'"{name}"' for name in sorted(missing))
        logger.warning(f'Missing dependencies in Colliderfile: {missing_str}. ')
        logger.warning(
            'These come from Meson introspection and are informational only; '
            'only add them to collider.json if you want Collider to manage them.'
        )

    if superfluous:
        superfluous_str = ', '.join(f'"{name}"' for name in sorted(superfluous))
        logger.error(f'Superfluous dependencies in Colliderfile: {superfluous_str}')
        return False

    return True
