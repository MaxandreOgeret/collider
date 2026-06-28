# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider patch command.

One test per documented os.EX_* return path. patch is not dispatchable via
run_subcommand (no PATCH member in the Subcommand enum), so -- like the existing
test/subcommand/test_patch.py tests -- these instantiate Patch directly with a
MagicMock args namespace and patch pathlib.Path.cwd to a controlled directory.
"""

import json
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.subcommand.Patch import Patch


def _write_projectinfo(builddir: Path, name: str = 'demo', version: str = '1.0.0') -> None:
    info_dir = builddir / 'meson-info'
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / 'intro-projectinfo.json').write_text(
        json.dumps(
            {
                'descriptive_name': name,
                'license': ['MIT'],
                'license_files': [],
                'subproject_dir': 'subprojects',
                'subprojects': [],
                'version': version,
            }
        ),
        encoding='utf-8',
    )


def _write_mesoninfo(builddir: Path, source_dir: Path) -> None:
    info_dir = builddir / 'meson-info'
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / 'meson-info.json').write_text(
        json.dumps(
            {
                'build_files_updated': False,
                'directories': {
                    'build': builddir.as_posix(),
                    'info': (builddir / 'meson-info').as_posix(),
                    'source': source_dir.as_posix(),
                },
                'error': False,
                'introspection': {'information': {}, 'version': {}},
                'meson_version': {'full': '1.10.0', 'major': 1, 'minor': 10, 'patch': 0},
            }
        ),
        encoding='utf-8',
    )


def _init_meson_project(root: Path) -> Path:
    """Create meson.build and colliderfile in root, marking it a valid project cwd."""
    (root / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    Colliderfile(dependencies=[]).save(root / Colliderfile.get_filename())
    return root


def _patch_args(builddir: Path, output: Path | None = None) -> object:
    return MagicMock(
        builddir=builddir,
        base='HEAD',
        include_uncommitted=True,
        output=output,
        list_only=False,
    )


def _mock_run_git_modified(self, args: list, cwd: Path, capture: bool = True):
    """Mock git so the success path sees one modified, includable file."""
    if args[:2] == ['rev-parse', '--show-toplevel']:
        return MagicMock(returncode=0, stdout=str(cwd), stderr='')
    if args[:2] == ['diff', '--name-status']:
        return MagicMock(returncode=0, stdout='M\tmodified.txt\n', stderr='')
    if args[:3] == ['ls-files', '--others', '--exclude-standard']:
        return MagicMock(returncode=0, stdout='', stderr='')
    return MagicMock(returncode=1, stdout='', stderr='')


def test_patch_ex_ok_archive_written(tmp_path: Path) -> None:
    """patch returns EX_OK when the patch archive is written to the output path."""
    source_dir = _init_meson_project(tmp_path)
    (source_dir / 'modified.txt').write_text('content', encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, source_dir)
    out_archive = tmp_path / 'out.tar.xz'

    cmd = Patch(_patch_args(builddir=builddir, output=out_archive), MagicMock(spec=Context))

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', _mock_run_git_modified),
    ):
        result = cmd.execute()
    assert result == os.EX_OK
    assert out_archive.exists()


def test_patch_ex_noinput_missing_meson_build(tmp_path: Path) -> None:
    """patch returns EX_NOINPUT when cwd has no meson.build (project validation fails)."""
    # colliderfile present, meson.build absent: the missing-meson.build guard fires.
    Colliderfile(dependencies=[]).save(tmp_path / Colliderfile.get_filename())
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, tmp_path)

    cmd = Patch(_patch_args(builddir=builddir), MagicMock(spec=Context))

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_NOINPUT


def test_patch_ex_dataerr_missing_builddir_info(tmp_path: Path) -> None:
    """patch returns EX_DATAERR when builddir lacks meson-info project metadata."""
    _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    # No meson-info under builddir: load_project_metadata returns (None, None, None).

    cmd = Patch(_patch_args(builddir=builddir), MagicMock(spec=Context))

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_DATAERR


def test_patch_ex_ioerr_archive_write_fails(tmp_path: Path) -> None:
    """patch returns EX_IOERR when writing the patch archive raises OSError."""
    source_dir = _init_meson_project(tmp_path)
    (source_dir / 'modified.txt').write_text('content', encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, source_dir)
    out_archive = tmp_path / 'out.tar.xz'

    cmd = Patch(_patch_args(builddir=builddir, output=out_archive), MagicMock(spec=Context))

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', _mock_run_git_modified),
        patch('collider.subcommand.Patch.tarfile.open', side_effect=OSError('disk full')),
    ):
        result = cmd.execute()
    assert result == os.EX_IOERR
