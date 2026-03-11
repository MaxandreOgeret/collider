# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg info command."""

import argparse
import os
import urllib.parse

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Filesystem import Filesystem
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.pkg.Info import Info
from collider.utils.packaging import compute_file_hash
from collider.utils.packaging.Dependency import Dependency, DependencySource


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    Colliderfile(dependencies=dependencies or []).save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repositories: dict[str, object]) -> Context:
    config = MagicMock()
    config.repositories = repositories
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def _store_cached_package(cache: WrapCache, name: str, version: str, archive: Path) -> WrapPackage:
    wrap_text = (
        '[wrap-file]\n'
        f'source_url=https://example.com/{name}.tar.xz\n'
        f'source_filename={name}.tar.xz\n'
        f'source_hash={compute_file_hash(archive)}\n'
    )
    package = WrapPackage.from_wrap_text(name, version, wrap_text)
    cache.store_wrap(package)
    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (cache.archives_dir / f'{package.source_hash}-{name}.tar.xz').write_bytes(archive.read_bytes())
    return package


def test_info_reports_declared_installed_candidate_and_versions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Info reports declared and installed state plus candidate metadata across repos."""
    _init_project(
        tmp_path,
        [Dependency('demo', DependencySource.COLLIDER, '>=1.0,<3.0')],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()

    wrap_entry = RepoPackageEntry('demo', '2.0.0', dependency_names=['fmt'])
    fs_entry = RepoPackageEntry('demo', '1.5.0', dependency_names=[])

    wrap_repo = Wrap(
        urllib.parse.urlparse('https://wrapdb.example.com/v2/'),
        {'demo@2.0.0#wrap': wrap_entry},
    )
    fs_repo = Filesystem(
        tmp_path / 'repo',
        publish_url='https://repo.example.com',
        packages={'demo@1.5.0#wrap': fs_entry},
    )

    context = _make_context(tmp_path, {'wrapdb': wrap_repo, 'local': fs_repo})
    archive = tmp_path / 'demo.tar.xz'
    archive.write_bytes(b'demo-archive')
    package = _store_cached_package(context.cache, 'demo', '2.0.0', archive)
    (subprojects / 'demo.wrap').write_text(package.wrap_text, encoding='utf-8')

    cmd = Info(argparse.Namespace(package='demo', repository=None), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Info.search_packages',
            return_value={
                'wrapdb': {'demo@2.0.0#wrap': wrap_entry},
                'local': {'demo@1.5.0#wrap': fs_entry},
            },
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'demo:' in caplog.text
    assert 'Installed: 2.0.0 [cached]' in caplog.text
    assert 'Declared: >=1.0,<3.0' in caplog.text
    assert 'Candidate: 2.0.0 (wrapdb)' in caplog.text
    assert '2.0.0  wrapdb (https://wrapdb.example.com/v2/) [cached] provides: fmt' in caplog.text
    assert '1.5.0  local (https://repo.example.com) provides: none' in caplog.text


def test_info_reports_system_dependency_and_missing_install(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """System declarations are shown as system, with no installed wrap."""
    _init_project(
        tmp_path,
        [Dependency('demo', DependencySource.SYSTEM, None)],
    )

    entry = RepoPackageEntry('demo', '1.0.0')
    wrap_repo = Wrap(
        urllib.parse.urlparse('https://wrapdb.example.com/v2/'),
        {'demo@1.0.0#wrap': entry},
    )
    context = _make_context(tmp_path, {'wrapdb': wrap_repo})
    cmd = Info(argparse.Namespace(package='demo', repository=None), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Info.search_packages',
            return_value={'wrapdb': {'demo@1.0.0#wrap': entry}},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Installed: none' in caplog.text
    assert 'Declared: system' in caplog.text


def test_info_reports_multiple_cached_matches_for_installed_wrap(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When the installed wrap text matches multiple cached versions, info says so."""
    _init_project(tmp_path, [Dependency('demo', DependencySource.COLLIDER, None)])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'demo.wrap').write_text(
        '[wrap-file]\nsource_url=https://example.com/demo.tar.xz\n',
        encoding='utf-8',
    )

    entry = RepoPackageEntry('demo', '2.0.0')
    wrap_repo = Wrap(
        urllib.parse.urlparse('https://wrapdb.example.com/v2/'),
        {'demo@2.0.0#wrap': entry},
    )
    context = _make_context(tmp_path, {'wrapdb': wrap_repo})
    context.cache.find_wrap_versions = MagicMock(return_value=['1.0.0', '2.0.0'])  # type: ignore[method-assign]
    cmd = Info(argparse.Namespace(package='demo', repository=None), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Info.search_packages',
            return_value={'wrapdb': {'demo@2.0.0#wrap': entry}},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Installed: unknown (multiple cached matches: 1.0.0, 2.0.0)' in caplog.text
    assert 'Declared: any' in caplog.text


def test_info_returns_unavailable_when_package_is_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Info fails cleanly when no repositories have a matching package."""
    _init_project(tmp_path)
    wrap_repo = Wrap(urllib.parse.urlparse('https://wrapdb.example.com/v2/'), {})
    context = _make_context(tmp_path, {'wrapdb': wrap_repo})
    cmd = Info(argparse.Namespace(package='missing', repository=None), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Info.search_packages', return_value={}):
            assert cmd.execute() == os.EX_UNAVAILABLE
    finally:
        os.chdir(cwd)

    assert 'No package matching query.' in caplog.text
