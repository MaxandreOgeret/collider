# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""File hashing and dependency validation."""

from __future__ import annotations

import hashlib

from pathlib import Path
from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet

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


def parse_version_constraint(text: str, *, prefix: bool = False) -> SpecifierSet:
    """
    Parse a version constraint, promoting a bare version to a specifier.
    Users naturally write `--version 1.2.13`, but packaging requires an operator. For pinning
    (add/upgrade) a bare version becomes an exact `==1.2.13`. For discovery (search) `prefix`
    promotes it to `==1.2.13.*` so revision-suffixed releases like `1.2.13-1` are still matched;
    versions that cannot form a prefix match (e.g. a full `1.2.13-1` tag) fall back to exact.
    :param text: Raw version constraint (e.g. "1.2.13", "==1.2.13", ">=1,<2").
    :param prefix: Promote a bare version to a prefix match (`==X.*`) instead of an exact pin.
    :return: Parsed specifier set.
    :raises InvalidSpecifier: When the text is empty or neither a valid specifier nor a bare version.
    """
    if not text.strip():
        # An empty constraint would parse to a match-everything specifier and persist as "";
        # reject it so the user gets a clear error instead of a meaningless stored intent.
        raise InvalidSpecifier(f'Empty version constraint "{text}".')
    try:
        return SpecifierSet(text)
    except InvalidSpecifier:
        if prefix:
            try:
                return SpecifierSet(f'=={text}.*')
            except InvalidSpecifier:
                # A version with a pre/post/local segment cannot prefix-match; pin it exactly.
                pass
        return SpecifierSet(f'=={text}')


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
