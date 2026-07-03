# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the `collider pkg add` command.

Each test pins one documented os.EX_* return path of pkg add against its
exact trigger. See scratchpad exitmap/collider_pkg_add.json for the targets.
"""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packaging.specifiers import SpecifierSet

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Add import Add
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from test.common.common import Subcommand, run_subcommand


def _make_context(tmp_path: Path, repos: dict[str, RepositoryInterface]) -> Context:
    """
    Build a minimal context backed by a temporary wrap cache.
    :param tmp_path: Temporary directory for the cache.
    :param repos: Repository mapping exposed via the config.
    :return: Context usable for direct Add(args, context) construction.
    """
    config = MagicMock()
    config.repositories = repos
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def _add_args(package: str, *, offline: bool = False, version: object = None) -> argparse.Namespace:
    """
    Build the argparse namespace pkg add expects.
    :param package: Package name to add.
    :param offline: Whether to run in offline mode.
    :param version: Optional parsed version constraint (SpecifierSet).
    :return: Namespace with all flags Add reads.
    """
    return argparse.Namespace(
        package=package,
        offline=offline,
        version=version,
        include=None,
        exclude=None,
        include_conditional=False,
        exclude_optional=False,
        force=False,
    )


def test_pkg_add_ex_noinput_no_meson_build(tmp_path: Path) -> None:
    """pkg add returns EX_NOINPUT when no meson.build exists in the working directory."""
    os.chdir(tmp_path)
    assert run_subcommand(Subcommand.PKG, ['add', 'some-package']) == os.EX_NOINPUT


def test_pkg_add_ex_dataerr_colliderfile_is_directory(tmp_path: Path) -> None:
    """pkg add returns EX_DATAERR when collider.json exists on disk as a directory."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    (tmp_path / Colliderfile.get_filename()).mkdir()
    os.chdir(tmp_path)
    assert run_subcommand(Subcommand.PKG, ['add', 'foo']) == os.EX_DATAERR


def test_pkg_add_ex_ok_existing_transitive_wrap_promoted(tmp_path: Path) -> None:
    """pkg add returns EX_OK when an installed transitive wrap is promoted to a direct dependency."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    # A lockfile entry proves the wrap is already required transitively.
    Lockfile(
        packages={
            'protobuf': LockedPackage(
                version='25.2-4',
                wrap_hash='sha256:' + '0' * 64,
                origin='https://wrapdb.example.com/v2/',
            )
        }
    ).save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(_add_args('protobuf'), context)

    os.chdir(tmp_path)
    assert cmd.execute() == os.EX_OK
    repo.get_package.assert_not_called()
    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == ['protobuf']


def test_pkg_add_ex_unavailable_no_matching_package(tmp_path: Path) -> None:
    """pkg add returns EX_UNAVAILABLE when no repository yields a matching package."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(_add_args('ghost'), context)

    os.chdir(tmp_path)
    with patch('collider.subcommand.pkg.Add.search_packages', return_value={}):
        assert cmd.execute() == os.EX_UNAVAILABLE
    assert not (tmp_path / Colliderfile.get_filename()).exists()


def test_pkg_add_ex_unavailable_all_versions_invalid(tmp_path: Path) -> None:
    """pkg add returns EX_UNAVAILABLE when every matching package has an unparsable version."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(_add_args('broken'), context)

    broken_key = make_repo_key('broken', 'not-a-version', PackageType.WRAP)
    broken_entry = RepoPackageEntry('broken', 'not-a-version', PackageType.WRAP)

    os.chdir(tmp_path)
    with patch(
        'collider.subcommand.pkg.Add.search_packages',
        return_value={'repo1': {broken_key: broken_entry}},
    ):
        assert cmd.execute() == os.EX_UNAVAILABLE
    assert not (tmp_path / Colliderfile.get_filename()).exists()


def test_pkg_add_lists_available_versions_when_pin_matches_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A version pin that matches nothing surfaces the versions the repositories actually offer."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(_add_args('zlib', version=SpecifierSet('==1.2.13')), context)

    zlib_key = make_repo_key('zlib', '1.2.13-1', PackageType.WRAP)
    zlib_entry = RepoPackageEntry('zlib', '1.2.13-1', PackageType.WRAP)

    def search_side_effect(_repos, _pattern, version_spec=None):
        # The constrained search excludes 1.2.13-1 (==1.2.13.post1); the unconstrained
        # re-search surfaces it so the guidance can point the user at the real tag.
        if version_spec is None:
            return {'repo1': {zlib_key: zlib_entry}}
        return {}

    os.chdir(tmp_path)
    with patch('collider.subcommand.pkg.Add.search_packages', side_effect=search_side_effect):
        assert cmd.execute() == os.EX_UNAVAILABLE

    assert 'No package "zlib" found matching version constraint "==1.2.13".' in caplog.text
    assert '1.2.13-1' in caplog.text


def test_pkg_add_ex_ioerr_offline_missing_cache(tmp_path: Path) -> None:
    """pkg add returns EX_IOERR when an offline install finds the package uncached."""
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = True
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Add(_add_args('demo', offline=True), context)

    demo_key = make_repo_key('demo', '1.0.0', PackageType.WRAP)
    demo_entry = RepoPackageEntry('demo', '1.0.0', PackageType.WRAP)

    os.chdir(tmp_path)
    with patch(
        'collider.subcommand.pkg.Add.search_packages',
        return_value={'repo1': {demo_key: demo_entry}},
    ):
        assert cmd.execute() == os.EX_IOERR
