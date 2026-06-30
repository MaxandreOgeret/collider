# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Helpers for project-scoped dependency mutations."""

from __future__ import annotations

import configparser
import shutil

from pathlib import Path
from typing import Optional

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile, compute_wrap_hash
from collider.log import logger
from collider.Package import get_provide_names, read_wrap_directory
from collider.utils.core import is_safe_path_segment
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.packaging.Dependency import Dependency, DependencySource


# Meson's shared download cache lives under subprojects/; it is never a package's extracted tree.
_RESERVED_SUBPROJECT_DIRS = frozenset({'packagecache'})


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


def _declared_subproject_dir(wrap_path: Path, package_name: str) -> Optional[str]:
    """
    Return the extracted subproject directory a wrap declares via ``directory=``, or None.
    Meson extracts a wrap into its ``directory`` field (for collider/WrapDB wraps usually
    ``<name>-<version>``), not necessarily ``<package_name>``. Returns None when the wrap is
    absent, unreadable, declares no directory, or declares one that is not a safe path segment,
    so the caller never deletes an unexpected path.
    :param wrap_path: Path to the package's .wrap file.
    :param package_name: Package whose artifacts are being removed (for messages).
    :return: A safe directory name to remove, or None.
    """
    try:
        declared = read_wrap_directory(wrap_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        logger.debug(f'Cannot read wrap "{wrap_path}" to resolve its directory: {exc}')
        return None
    if declared is None:
        return None
    if not is_safe_path_segment(declared):
        logger.warning(
            f'Wrap for "{package_name}" declares an unsafe subproject directory '
            f'"{declared}"; leaving it in place. Remove it manually if needed.'
        )
        return None
    return declared


def _remove_subproject_tree(subprojects_dir: Path, name: str) -> bool:
    """
    Remove a single extracted subproject directory and report whether anything was removed.
    ``name`` is a safe single path segment, so the target is always a direct child of
    subprojects/; the shared packagecache is never touched, and a symlink or file is unlinked
    rather than followed into.
    :param subprojects_dir: The project's subprojects/ directory.
    :param name: Safe single-segment directory name to remove.
    :return: True when something was removed.
    """
    # Compare case-insensitively and ignore trailing dots/spaces so a wrap cannot dodge the
    # guard with `directory=PackageCache` on a case-insensitive filesystem (macOS/Windows) or a
    # Windows trailing-dot variant, where that path is still the shared packagecache.
    if name.strip(' .').casefold() in _RESERVED_SUBPROJECT_DIRS:
        logger.debug(f'Preserving reserved subproject directory "{name}".')
        return False
    target = subprojects_dir / name
    if target.is_symlink() or target.is_file():
        target.unlink()
        return True
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return False


def remove_installed_artifacts(package_name: str) -> bool:
    """
    Remove a package's installed wrap state: the .wrap descriptor and any extracted source tree.
    Meson extracts into the wrap's ``directory=`` field, so both that target and the legacy
    ``<package_name>`` directory are cleared (they collapse to one path when equal); clearing the
    legacy directory also keeps a later reinstall from tripping on a stale tree. Names are
    validated as safe single path segments first, so an irreversible delete can never escape
    subprojects/.
    :param package_name: Collider-managed package to remove.
    :return: True when any artifact was removed.
    """
    if not is_safe_path_segment(package_name):
        logger.warning(f'Refusing to remove artifacts for unsafe package name "{package_name}".')
        return False

    subprojects_dir = Path.cwd() / SUBPROJECTS_DIR
    wrap_path = subprojects_dir / f'{package_name}.wrap'

    # Resolve the extracted directory from the wrap before unlinking it.
    declared_dir = _declared_subproject_dir(wrap_path, package_name)

    removed_any = False
    if wrap_path.exists() or wrap_path.is_symlink():
        wrap_path.unlink()
        removed_any = True

    dir_names = [package_name]
    if declared_dir is not None and declared_dir != package_name:
        dir_names.append(declared_dir)
    for name in dir_names:
        if _remove_subproject_tree(subprojects_dir, name):
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


def detect_locked_wrap_drift(sourcedir: Path) -> list[str]:
    """
    Find locked wraps whose on-disk bytes no longer match collider.lock.
    Only wraps that are recorded in the lock and present on disk are compared; a missing wrap
    is not treated as drift here (Meson resolution and `collider status` cover that case). The
    hash is taken over the .wrap file text only, so this detects edits to the wrap descriptor,
    not changes to the extracted subproject source tree.
    :param sourcedir: Project source directory.
    :return: Sorted names of wraps that drifted from the lock; empty when none or no lock.
    :raises ValueError: When collider.lock exists but cannot be parsed.
    """
    lock_path = sourcedir / Lockfile.get_filename()
    if not lock_path.exists():
        return []

    try:
        lockfile = Lockfile.from_path(lock_path)
    except Exception as exc:
        raise ValueError(f'collider.lock is malformed: {exc}') from exc

    subprojects_dir = sourcedir / SUBPROJECTS_DIR
    drifted: list[str] = []
    for name, locked in lockfile.all_packages.items():
        wrap_path = subprojects_dir / f'{name}.wrap'
        if not wrap_path.is_file():
            continue
        try:
            wrap_text = wrap_path.read_text(encoding='utf-8')
        except OSError as exc:
            logger.debug(f'Cannot read "{wrap_path}" for drift check, skipping: {exc}')
            continue
        except UnicodeDecodeError:
            # A non-UTF-8 wrap can never match a UTF-8-hashed lock entry, so it is drift.
            drifted.append(name)
            continue
        if compute_wrap_hash(wrap_text) != locked.wrap_hash:
            drifted.append(name)
    return sorted(drifted)


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
