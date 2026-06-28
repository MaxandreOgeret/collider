# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Helpers for project-scoped dependency mutations."""

from __future__ import annotations

import shutil

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile
from collider.log import logger
from collider.Package import get_wrap_directory
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.packaging.Dependency import Dependency, DependencySource


def load_colliderfile() -> Colliderfile:
    """Load collider.json from the current project root."""
    return Colliderfile.from_path(Path.cwd() / Colliderfile.get_filename())


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
    subprojects_root = Path.cwd() / SUBPROJECTS_DIR
    wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{package_name}.wrap'
    wrap_directory = None
    if wrap_path.exists():
        try:
            wrap_directory = get_wrap_directory(wrap_path.read_text(encoding='utf-8'))
        except Exception:
            wrap_directory = None

    if wrap_path.exists() or wrap_path.is_symlink():
        wrap_path.unlink()
        removed_any = True

    subproject_dir = subprojects_root / package_name
    if subproject_dir.is_symlink() or subproject_dir.is_file():
        subproject_dir.unlink()
        removed_any = True
    elif subproject_dir.is_dir():
        shutil.rmtree(subproject_dir)
        removed_any = True

    if wrap_directory:
        extracted_dir = subprojects_root / Path(wrap_directory)
        if _is_safe_subproject_path(subprojects_root, extracted_dir):
            if extracted_dir.is_dir():
                if _looks_like_vcs_checkout(extracted_dir):
                    logger.warning(
                        f'Left extracted subproject directory "{extracted_dir.as_posix()}" in '
                        'place because it looks like a VCS checkout.'
                    )
                else:
                    shutil.rmtree(extracted_dir)
                    removed_any = True
        else:
            logger.warning(
                f'Ignored unsafe wrap directory "{wrap_directory}" while removing "{package_name}".'
            )

    return removed_any


def _is_safe_subproject_path(root: Path, candidate: Path) -> bool:
    """Return True when candidate stays within the subprojects root."""
    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        return False

    return resolved_candidate.is_relative_to(resolved_root)


def _looks_like_vcs_checkout(path: Path) -> bool:
    """Detect common VCS markers that should not be removed automatically."""
    return any((path / marker).exists() for marker in ('.git', '.hg', '.svn'))


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

    try:
        lockfile = Lockfile.from_path(lockfile_path)
    except Exception:
        return

    if package_name in lockfile.all_packages:
        logger.warning(
            f'collider.lock was not updated for "{package_name}"; '
            'run "collider lock" to refresh it.'
        )
