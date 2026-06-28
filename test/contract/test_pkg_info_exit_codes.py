# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider ``pkg info`` command."""

import argparse
import os
import urllib.parse

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.pkg.Info import Info
from collider.utils.packaging.Dependency import Dependency, DependencySource


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    Colliderfile(dependencies=dependencies or []).save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repositories: dict[str, object]) -> Context:
    config = MagicMock()
    config.repositories = repositories
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def test_pkg_info_ex_noinput_unknown_repository(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``pkg info`` returns EX_NOINPUT when a -r repository is absent from config.repositories."""
    config = MagicMock()
    config.repositories = {'only_repo': MagicMock()}
    context = MagicMock(spec=Context)
    context.config = config

    cmd = Info(
        argparse.Namespace(package='anypkg', repository=['only_repo', 'missing_repo']), context
    )

    assert cmd.execute() == os.EX_NOINPUT
    assert 'Missing' in caplog.text


def test_pkg_info_ex_unavailable_no_package_match(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``pkg info`` returns EX_UNAVAILABLE when no resolved repository has the package."""
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


def test_pkg_info_ex_ok_reports_package(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """``pkg info`` returns EX_OK on the happy path when a matching package is reported."""
    _init_project(tmp_path, [Dependency('demo', DependencySource.COLLIDER, '>=1.0')])
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

    assert 'Candidate: 1.0.0 (wrapdb)' in caplog.text
