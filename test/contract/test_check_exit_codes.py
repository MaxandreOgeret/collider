# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider "check" command."""

import os

from pathlib import Path
from unittest.mock import patch

from collider.file_model.colliderfile import Colliderfile
from collider.utils.meson.meson import MesonUnavailableError
from collider.utils.meson.scan import ScannedDependency
from collider.utils.packaging.Dependency import Dependency, DependencySource
from test.common.common import Subcommand, run_subcommand


def _make_project(tmp_path: Path, deps: list[Dependency]) -> None:
    """Write meson.build and collider.json with the given dependencies."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')
    Colliderfile(dependencies=deps).save(tmp_path / Colliderfile.get_filename())


def _run_check(tmp_path: Path) -> int:
    """Run collider check with --sourcedir pointing at tmp_path."""
    return run_subcommand(Subcommand.CHECK, ['--sourcedir', str(tmp_path)])


def test_check_ex_noinput_missing_meson_build(tmp_path: Path) -> None:
    """check returns EX_NOINPUT when meson.build is missing from --sourcedir."""
    # collider.json present so the meson.build guard fires, not the collider.json one.
    Colliderfile().save(tmp_path / Colliderfile.get_filename())

    assert _run_check(tmp_path) == os.EX_NOINPUT


def test_check_ex_dataerr_untracked_dependency(tmp_path: Path) -> None:
    """check returns EX_DATAERR when a scanned dep is untracked in collider.json."""
    _make_project(tmp_path, [])

    with patch(
        'collider.subcommand.Check.scan_dependencies',
        return_value=[ScannedDependency('fmt', required=True)],
    ):
        assert _run_check(tmp_path) == os.EX_DATAERR


def test_check_ex_ok_clean(tmp_path: Path) -> None:
    """check returns EX_OK when scanned deps match collider.json with no drift."""
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with patch(
        'collider.subcommand.Check.scan_dependencies',
        return_value=[ScannedDependency('fmt', required=True)],
    ):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_ex_unavailable_meson_validation_fails(tmp_path: Path) -> None:
    """check returns EX_UNAVAILABLE when the Meson binary fails validation."""
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    # Reset the per-process cache so a prior successful validation does not short-circuit.
    with (
        patch('collider.utils.meson.scan._meson_validated', False),
        patch(
            'collider.utils.meson.scan._meson_mod.validate',
            side_effect=MesonUnavailableError('meson unavailable'),
        ),
    ):
        assert _run_check(tmp_path) == os.EX_UNAVAILABLE
