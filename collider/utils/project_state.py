# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Helpers for project-scoped dependency mutations."""

from __future__ import annotations

import configparser
import shutil

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile
from collider.log import logger
from collider.Package import get_provide_names
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


def managed_package_names(sourcedir: Path) -> Optional[set[str]]:
    """
    Return collider's authoritative managed package set, or None when it is unknown.
    The lockfile is the only record of transitive installs, so a present collider.lock
    yields the precise set (direct + transitive) plus the colliderfile's direct entries.
    None signals the caller to fall back to wrap presence, since before a lock exists a
    transitive wrap is recorded nowhere but the wrap file itself.
    :param sourcedir: Project source directory.
    :return: Managed package names, or None when collider.lock is absent.
    :raises ValueError: When collider.lock exists but cannot be parsed.
    """
    lock_path = sourcedir / Lockfile.get_filename()
    if not lock_path.exists():
        return None

    try:
        lockfile = Lockfile.from_path(lock_path)
    except Exception as exc:
        raise ValueError(f'collider.lock is malformed: {exc}') from exc

    names = set(lockfile.all_packages)

    # Cover direct deps added since the last lock; the lockfile owns transitive ones.
    colliderfile_path = sourcedir / Colliderfile.get_filename()
    if colliderfile_path.exists():
        try:
            colliderfile = Colliderfile.from_path(colliderfile_path)
        except Exception as exc:
            # The lockfile is authoritative; a broken colliderfile is caught later by setup
            # validation. Scope to lock-only names here rather than abort.
            logger.debug(f'Ignoring unreadable collider.json for force-fallback scoping: {exc}')
            return names
        names |= {
            dep.name for dep in colliderfile.dependencies if dep.source == DependencySource.COLLIDER
        }
    return names


def collect_force_fallback_names(
    subprojects_dir: Path, managed: Optional[set[str]] = None
) -> list[str]:
    """
    Collect the names to force to wrap fallback at `meson setup`.
    Each in-scope wrap contributes its stem (the subproject name) and any names it declares
    in [provide], so Meson uses collider's wraps instead of system copies and the build
    matches the locked versions. Both forms independently force the fallback, and reading
    [provide] keeps the catch2/catch2-with-main dependency-name vs package-name mismatch
    correct. When ``managed`` is given, only wraps for those collider-managed packages are
    forced; when it is None (no lockfile yet), every present wrap is forced as a best effort.
    :param subprojects_dir: Path to the subprojects directory.
    :param managed: Collider-managed package names to restrict forcing to, or None.
    :return: Sorted, de-duplicated names for `meson setup --force-fallback-for`.
    """
    if not subprojects_dir.exists():
        return []

    names: set[str] = set()
    for wrap_path in subprojects_dir.glob('*.wrap'):
        if not wrap_path.is_file():
            continue
        if managed is not None and wrap_path.stem not in managed:
            continue
        names.add(wrap_path.stem)
        try:
            names.update(get_provide_names(wrap_path.read_text(encoding='utf-8')))
        except (ValueError, OSError, configparser.Error):
            # Redirect/git or malformed wraps lack a parseable [wrap-file]; the stem alone
            # still forces the subproject.
            continue
    return sorted(names)


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
