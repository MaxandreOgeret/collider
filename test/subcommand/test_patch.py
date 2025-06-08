# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the patch subcommand."""

import json
import os
import tarfile

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


def _init_meson_project(tmp_path: Path, source_dir: Path | None = None) -> Path:
    """Create meson.build and colliderfile in tmp_path (or source_dir). Return source dir."""
    root = source_dir if source_dir is not None else tmp_path
    (root / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    Colliderfile(dependencies=[]).save(root / Colliderfile.get_filename())
    return root


def _patch_args(
    builddir: Path,
    base: str = 'HEAD',
    include_uncommitted: bool = True,
    output: Path | None = None,
    list_only: bool = False,
) -> object:
    return MagicMock(
        builddir=builddir,
        base=base,
        include_uncommitted=include_uncommitted,
        output=output,
        list_only=list_only,
    )


def test_patch_fails_without_meson_build(tmp_path: Path) -> None:
    Colliderfile(dependencies=[]).save(tmp_path / Colliderfile.get_filename())
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, tmp_path)

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_NOINPUT


def test_patch_fails_without_colliderfile(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text("project('demo', 'c')", encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir)
    _write_mesoninfo(builddir, tmp_path)

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_NOINPUT


def test_patch_fails_without_builddir_info(tmp_path: Path) -> None:
    _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    # No meson-info under builddir

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_DATAERR


def test_patch_fails_when_not_git_repo(tmp_path: Path) -> None:
    source_dir = _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with patch('pathlib.Path.cwd', return_value=tmp_path):
        result = cmd.execute()
    assert result == os.EX_DATAERR


def test_patch_fails_when_git_not_installed(tmp_path: Path) -> None:
    source_dir = _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    def mock_run_git_raises(self, args: list, cwd: Path, capture: bool = True):
        raise FileNotFoundError('git not found')

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', mock_run_git_raises),
    ):
        result = cmd.execute()
    assert result == os.EX_DATAERR


def test_patch_deleted_file_errors(tmp_path: Path) -> None:
    source_dir = _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    def mock_run_git(self, args: list, cwd: Path, capture: bool = True):
        if args[:2] == ['rev-parse', '--show-toplevel']:
            return MagicMock(returncode=0, stdout=str(cwd), stderr='')
        if args[:2] == ['diff', '--name-status']:
            return MagicMock(returncode=0, stdout='D\tdeleted_file.txt\n', stderr='')
        if args[:3] == ['ls-files', '--others', '--exclude-standard']:
            return MagicMock(returncode=0, stdout='', stderr='')
        return MagicMock(returncode=1, stdout='', stderr='')

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir)
    cmd = Patch(args, context)

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', mock_run_git),
    ):
        result = cmd.execute()
    assert result == os.EX_DATAERR


def test_patch_list_only_prints_paths(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source_dir = _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)

    def mock_run_git(self, args: list, cwd: Path, capture: bool = True):
        if args[:2] == ['rev-parse', '--show-toplevel']:
            return MagicMock(returncode=0, stdout=str(cwd), stderr='')
        if args[:2] == ['diff', '--name-status']:
            return MagicMock(returncode=0, stdout='M\tmodified.txt\n', stderr='')
        if args[:3] == ['ls-files', '--others', '--exclude-standard']:
            return MagicMock(returncode=0, stdout='', stderr='')
        return MagicMock(returncode=1, stdout='', stderr='')

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir, list_only=True)
    cmd = Patch(args, context)

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', mock_run_git),
    ):
        result = cmd.execute()
    assert result == os.EX_OK
    out = capsys.readouterr().out
    assert 'modified.txt' in out


def test_patch_creates_tar_xz_with_correct_root(tmp_path: Path) -> None:
    source_dir = _init_meson_project(tmp_path)
    (source_dir / 'modified.txt').write_text('content', encoding='utf-8')
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)
    out_archive = tmp_path / 'out.tar.xz'

    def mock_run_git(self, args: list, cwd: Path, capture: bool = True):
        if args[:2] == ['rev-parse', '--show-toplevel']:
            return MagicMock(returncode=0, stdout=str(cwd), stderr='')
        if args[:2] == ['diff', '--name-status']:
            return MagicMock(returncode=0, stdout='M\tmodified.txt\n', stderr='')
        if args[:3] == ['ls-files', '--others', '--exclude-standard']:
            return MagicMock(returncode=0, stdout='', stderr='')
        return MagicMock(returncode=1, stdout='', stderr='')

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir, output=out_archive)
    cmd = Patch(args, context)

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', mock_run_git),
    ):
        result = cmd.execute()
    assert result == os.EX_OK
    assert out_archive.exists()
    with tarfile.open(out_archive, 'r:xz') as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    assert names == ['demo-1.0.0/modified.txt']
    with tarfile.open(out_archive, 'r:xz') as tf:
        member = tf.getmember('demo-1.0.0/modified.txt')
        assert tf.extractfile(member).read().decode() == 'content'


def test_patch_empty_changes_skips_archive(tmp_path: Path) -> None:
    source_dir = _init_meson_project(tmp_path)
    builddir = tmp_path / 'build'
    _write_projectinfo(builddir, name='demo', version='1.0.0')
    _write_mesoninfo(builddir, source_dir)
    out_archive = tmp_path / 'out.tar.xz'

    def mock_run_git(self, args: list, cwd: Path, capture: bool = True):
        if args[:2] == ['rev-parse', '--show-toplevel']:
            return MagicMock(returncode=0, stdout=str(cwd), stderr='')
        if args[:2] == ['diff', '--name-status']:
            return MagicMock(returncode=0, stdout='', stderr='')
        if args[:3] == ['ls-files', '--others', '--exclude-standard']:
            return MagicMock(returncode=0, stdout='', stderr='')
        return MagicMock(returncode=1, stdout='', stderr='')

    context = MagicMock(spec=Context)
    args = _patch_args(builddir=builddir, output=out_archive)
    cmd = Patch(args, context)

    with (
        patch('pathlib.Path.cwd', return_value=tmp_path),
        patch.object(Patch, '_run_git', mock_run_git),
    ):
        result = cmd.execute()
    assert result == os.EX_OK
    assert not out_archive.exists()
