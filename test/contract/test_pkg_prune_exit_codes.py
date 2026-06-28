# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider "pkg prune" command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.subcommand.pkg.Prune import Prune
from test.common.common import Subcommand, run_subcommand


ORIGIN = 'https://wrapdb.example.com/v2/'


def _init_project(tmp_path: Path) -> None:
    """Write the minimal meson.build + collider.json so the CWD is a valid project."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    Colliderfile(dependencies=[]).save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path) -> Context:
    """Build a minimal context with an empty repository set, as the existing tests do."""
    config = MagicMock()
    config.repositories = {}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_pkg_prune_ex_noinput_not_a_project(tmp_path: Path) -> None:
    """pkg prune returns EX_NOINPUT when the CWD lacks meson.build (not a valid project)."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert run_subcommand(Subcommand.PKG, ['prune']) == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_pkg_prune_ex_ok_removes_orphaned_wraps(tmp_path: Path) -> None:
    """pkg prune returns EX_OK after removing orphaned managed wraps from a valid project."""
    _init_project(tmp_path)
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    Lockfile(
        packages={
            'abseil-cpp': LockedPackage(
                version='20250814.1-1', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
        },
    ).save(tmp_path / Lockfile.get_filename())

    cmd = Prune(argparse.Namespace(dry_run=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)
