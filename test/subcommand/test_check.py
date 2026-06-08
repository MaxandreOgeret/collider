# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the check subcommand."""

import os

from pathlib import Path
from unittest.mock import patch

from collider.file_model.colliderfile import Colliderfile
from collider.utils.meson.scan import ScannedDependency
from collider.utils.packaging.Dependency import Dependency, DependencySource
from test.common.common import Subcommand, run_subcommand


def _make_project(tmp_path: Path, deps: list[Dependency]) -> None:
    """Write meson.build and collider.json with the given dependencies."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')
    Colliderfile(dependencies=deps).save(tmp_path / Colliderfile.get_filename())


def _run_check(tmp_path: Path, extra_args: list[str] | None = None) -> int:
    """Run collider check with --sourcedir pointing at tmp_path."""
    return run_subcommand(Subcommand.CHECK, ['--sourcedir', str(tmp_path), *(extra_args or [])])


def _mock_scan(deps: list[ScannedDependency]):
    """Patch scan_dependencies in the Check module."""
    return patch('collider.subcommand.Check.scan_dependencies', return_value=deps)


def test_check_clean(tmp_path: Path) -> None:
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([ScannedDependency('fmt', required=True)]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_untracked(tmp_path: Path) -> None:
    """Dep used in meson.build but absent from collider.json is untracked."""
    _make_project(tmp_path, [])

    with _mock_scan([ScannedDependency('fmt', required=True)]):
        assert _run_check(tmp_path) == os.EX_DATAERR


def test_check_stale(tmp_path: Path) -> None:
    """Collider-managed dep in collider.json but absent from meson.build is stale."""
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([]):
        assert _run_check(tmp_path) == os.EX_DATAERR


def test_check_conditional_not_stale(tmp_path: Path) -> None:
    """Conditional dep tracked in collider.json must not be flagged stale.

    fmt appears in the raw scan (conditional=True) so it is NOT stale even
    though filter_dependencies drops it from .included when --include-conditional
    is not set.
    """
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([ScannedDependency('fmt', required=True, conditional=True)]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_system_dep_not_stale(tmp_path: Path) -> None:
    """System-source deps in collider.json are never reported as stale."""
    _make_project(tmp_path, [Dependency('zlib', DependencySource.SYSTEM, None)])

    with _mock_scan([]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_include_conditional(tmp_path: Path) -> None:
    """With --include-conditional, conditional deps count as untracked when absent."""
    _make_project(tmp_path, [])

    with _mock_scan([ScannedDependency('fmt', required=True, conditional=True)]):
        assert _run_check(tmp_path, ['--include-conditional']) == os.EX_DATAERR


def test_check_missing_meson_build(tmp_path: Path) -> None:
    Colliderfile().save(tmp_path / Colliderfile.get_filename())

    assert _run_check(tmp_path) == os.EX_NOINPUT


def test_check_missing_collider_json(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    assert _run_check(tmp_path) == os.EX_NOINPUT
