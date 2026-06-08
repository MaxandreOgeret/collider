# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Wrapper around `meson introspect --scan-dependencies`."""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from collider.log import logger
from collider.utils import command
from collider.utils.meson import meson as _meson_mod


@dataclass(frozen=True)
class ScannedDependency:
    """Single dependency entry from meson introspect --scan-dependencies."""

    name: str
    required: bool
    version: list[str] = field(default_factory=list)
    has_fallback: bool = False
    conditional: bool = False


@dataclass
class FilterResult:
    """Output of filter_dependencies with metadata for caller-side reporting."""

    included: list[ScannedDependency] = field(default_factory=list)
    skipped_conditional: list[str] = field(default_factory=list)
    skipped_optional: list[str] = field(default_factory=list)
    included_optional: list[str] = field(default_factory=list)


_meson_validated = False


def _ensure_meson_validated() -> None:
    """Validate the Meson binary once per process, on first actual use."""
    if not _meson_validated:
        _meson_mod.validate()
        globals()['_meson_validated'] = True


def scan_project_info(meson_build: Path) -> Optional[dict]:
    """
    Run `meson introspect --projectinfo` on a meson.build file.

    Best-effort: any failure (meson unavailable, parse error, etc.) returns None
    so callers can degrade gracefully without special-casing every error type.

    :param meson_build: Path to the meson.build file.
    :return: Raw project info dict, or None on any failure.
    """
    try:
        _ensure_meson_validated()
        output = command.run(
            ['meson', 'introspect', '--projectinfo', str(meson_build)],
            capture=command.StdStream.STDOUT,
        )
        return json.loads(output) if output else None
    except Exception:  # noqa: BLE001
        return None


def scan_dependencies(meson_build: Path) -> list[ScannedDependency]:
    """
    Run `meson introspect --scan-dependencies` on a meson.build file.
    :param meson_build: Path to the meson.build file.
    :return: List of scanned dependencies.
    :raises FileNotFoundError: When the meson.build file does not exist.
    :raises subprocess.CalledProcessError: When meson introspect fails.
    """
    if not meson_build.exists():
        raise FileNotFoundError(f'meson.build not found at "{meson_build}".')

    _ensure_meson_validated()

    logger.debug(f'Scanning dependencies in "{meson_build}".')
    output = command.run(
        ['meson', 'introspect', '--scan-dependencies', str(meson_build)],
        capture=command.StdStream.STDOUT,
    )

    assert output is not None
    raw: list[dict] = json.loads(output)

    return [
        ScannedDependency(
            name=entry['name'],
            required=entry.get('required', True),
            version=entry.get('version', []),
            has_fallback=entry.get('has_fallback', False),
            conditional=entry.get('conditional', False),
        )
        for entry in raw
        if entry.get('name')
    ]


MESON_SYSTEM_DEPS: frozenset[str] = frozenset(
    {
        'appleframeworks',
        'atomic',
        'blocks',
        'coarray',
        'cuda',
        'dl',
        'iconv',
        'intl',
        'mpi',
        'openmp',
        'threads',
    }
)


def filter_dependencies(
    deps: list[ScannedDependency],
    *,
    include_conditional: bool = False,
    exclude_optional: bool = False,
    include_names: Optional[set[str]] = None,
    exclude_names: Optional[set[str]] = None,
) -> FilterResult:
    """
    Apply filtering rules to scanned dependencies.

    Precedence: explicit include/exclude by name > broad flags > defaults.

    :param deps: Raw scan results.
    :param include_conditional: Also include deps inside if-blocks.
    :param exclude_optional: Skip optional (required: false) deps.
    :param include_names: Force-include these dep names regardless of flags.
    :param exclude_names: Force-exclude these dep names regardless of flags.
    :return: FilterResult with included deps and filtering metadata.
    """
    include_names = include_names or set()
    exclude_names = exclude_names or set()
    out = FilterResult()

    for dep in deps:
        if dep.name in MESON_SYSTEM_DEPS:
            continue

        if dep.name in exclude_names:
            continue

        if dep.name in include_names:
            out.included.append(dep)
            continue

        if dep.conditional and not include_conditional:
            out.skipped_conditional.append(dep.name)
            continue

        if not dep.required and not dep.has_fallback and exclude_optional:
            out.skipped_optional.append(dep.name)
            continue

        if not dep.required and not dep.has_fallback:
            out.included_optional.append(dep.name)

        out.included.append(dep)

    return out
