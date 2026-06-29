# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Helpers for project-scoped dependency mutations."""

from __future__ import annotations

import json
import shutil

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile
from collider.log import logger
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.packaging.Dependency import Dependency, DependencySource


def load_colliderfile() -> Colliderfile:
    """Load collider.json from the current project root."""
    return Colliderfile.from_path(Path.cwd() / Colliderfile.get_filename())


def read_lockfile(lockfile_path: Path, *, warn_on_error: bool = True) -> Optional[Lockfile]:
    """
    Read collider.lock with explicit diagnostics for parse and IO failures.
    :param lockfile_path: Path to collider.lock.
    :param warn_on_error: When True, log a warning before returning None.
    :return: Loaded lockfile, or None when the file is missing or unreadable.
    """
    try:
        with open(lockfile_path, 'r', encoding='UTF-8') as lockfile_stream:
            return Lockfile.from_stream(lockfile_stream)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        if warn_on_error:
            logger.warning('collider.lock could not be read. Run "collider lock" to regenerate it.')
    except OSError as exc:
        if warn_on_error:
            logger.warning(
                f'Could not read collider.lock: {exc}. Run "collider lock" to regenerate it.'
            )

    return None


def find_collider_dependency(colliderfile: Colliderfile, package_name: str) -> Optional[Dependency]:
    """Find a Collider-managed dependency by package name."""
    for dep in colliderfile.dependencies:
        if dep.name == package_name and dep.source == DependencySource.COLLIDER:
            return dep
    return None


def remove_collider_dependency(colliderfile: Colliderfile, package_name: str) -> bool:
    """Remove a Collider-managed dependency from collider.json."""
    original_len = len(colliderfile.dependencies)
    colliderfile.dependencies = [
        dep
        for dep in colliderfile.dependencies
        if not (dep.name == package_name and dep.source == DependencySource.COLLIDER)
    ]
    if len(colliderfile.dependencies) == original_len:
        return False
    colliderfile.save()
    return True


def update_collider_dependency_version(
    colliderfile: Colliderfile,
    package_name: str,
    version_text: Optional[str],
) -> bool:
    """Update the stored version constraint for a Collider-managed dependency."""
    dep = find_collider_dependency(colliderfile, package_name)
    if dep is None or dep.version == version_text:
        return False
    dep.version = version_text
    colliderfile.save()
    return True


def remove_installed_artifacts(package_name: str) -> bool:
    """Remove installed wrap state from subprojects/ for a package."""
    removed_any = False
    wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{package_name}.wrap'
    if wrap_path.exists() or wrap_path.is_symlink():
        wrap_path.unlink()
        removed_any = True

    subproject_dir = Path.cwd() / SUBPROJECTS_DIR / package_name
    if subproject_dir.is_symlink() or subproject_dir.is_file():
        subproject_dir.unlink()
        removed_any = True
    elif subproject_dir.is_dir():
        shutil.rmtree(subproject_dir)
        removed_any = True

    return removed_any


def scan_wraps(subprojects_dir: Path) -> list[str]:
    """Return stem names of all .wrap files in subprojects/."""
    if not subprojects_dir.exists():
        return []
    return [p.stem for p in subprojects_dir.glob('*.wrap') if p.is_file()]


def warn_if_lockfile_needs_refresh(package_name: str) -> None:
    """Warn when collider.lock still contains state for a changed package."""
    lockfile_path = Path.cwd() / Lockfile.get_filename()
    if not lockfile_path.exists():
        return

    lockfile = read_lockfile(lockfile_path, warn_on_error=False)
    if lockfile is None:
        return

    if package_name in lockfile.all_packages:
        logger.warning(
            f'collider.lock was not updated for "{package_name}"; '
            'run "collider lock" to refresh it.'
        )
