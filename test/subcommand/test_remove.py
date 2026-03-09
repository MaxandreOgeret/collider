# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg remove/rm command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Remove import Remove
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.resolver import (
    Candidate,
    ResolutionResult,
    ResolutionSummary,
)


ORIGIN = 'https://wrapdb.example.com/v2/'


def _init_project(tmp_path: Path, dependencies: list[Dependency] | None = None) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n', encoding='utf-8')
    colliderfile = Colliderfile(dependencies=dependencies or [])
    colliderfile.save(tmp_path / Colliderfile.get_filename())


def _make_context(tmp_path: Path, repos: dict | None = None) -> Context:
    config = MagicMock()
    config.repositories = repos or {}
    config.offline = False
    return Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)


def _make_resolution_result(mapping: dict) -> ResolutionResult:
    return ResolutionResult(
        mapping=mapping,
        summary=ResolutionSummary(
            skipped_conditional=set(),
            skipped_optional=set(),
            included_optional=set(),
            unmapped_system=set(),
            skipped_conditional_by_pkg={},
            skipped_optional_by_pkg={},
        ),
    )


def test_remove_deletes_dependency_and_installed_state(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Removing a package updates collider.json and deletes local wrap state."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'shared.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'shared').mkdir()

    lockfile = Lockfile(
        dependencies={
            'shared': LockedPackage(
                version='1.0.0',
                wrap_hash='sha256:' + 'a' * 64,
                origin=ORIGIN,
            ),
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    cmd = Remove(argparse.Namespace(package='shared'), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies == []
    assert not (subprojects / 'shared.wrap').exists()
    assert not (subprojects / 'shared').exists()
    assert 'run "collider lock" to refresh it' in caplog.text


def test_remove_succeeds_when_only_declared_dependency_exists(tmp_path: Path) -> None:
    """Removing a declared package should succeed even if no wrap is installed."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    cmd = Remove(argparse.Namespace(package='shared'), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies == []


def test_remove_missing_package_returns_noinput(tmp_path: Path) -> None:
    """Removing an unknown package should fail cleanly."""
    _init_project(tmp_path, dependencies=[])

    cmd = Remove(argparse.Namespace(package='missing'), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_remove_does_not_warn_when_lockfile_has_no_entry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Removing a package should not warn when collider.lock is already clean."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )
    lockfile = Lockfile(
        dependencies={
            'other': LockedPackage(
                version='1.0.0',
                wrap_hash='sha256:' + 'b' * 64,
                origin=ORIGIN,
            )
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    cmd = Remove(argparse.Namespace(package='shared'), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'run "collider lock" to refresh it' not in caplog.text


def test_remove_cleans_up_orphaned_transitive_deps(tmp_path: Path) -> None:
    """Removing a direct dep also removes its orphaned transitive wraps."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
        },
        packages={
            'abseil-cpp': LockedPackage(
                version='20250814.1-1', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
            're2': LockedPackage(
                version='20230301-3', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)
    cmd = Remove(argparse.Namespace(package='grpc'), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert not (subprojects / 'abseil-cpp.wrap').exists()
    assert not (subprojects / 're2.wrap').exists()


def test_remove_keeps_shared_transitive_deps(tmp_path: Path) -> None:
    """Removing one direct dep keeps transitives still needed by another."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('other', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'other.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'shared-lib.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'grpc-only.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
            'other': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN),
        },
        packages={
            'shared-lib': LockedPackage(
                version='2.0.0', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN
            ),
            'grpc-only': LockedPackage(
                version='1.0.0', wrap_hash='sha256:' + 'd' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'other': Candidate('other', '1.0.0', 'repo1'),
        'shared-lib': Candidate('shared-lib', '2.0.0', 'repo1'),
    }

    cmd = Remove(argparse.Namespace(package='grpc'), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'some_dep': 'shared-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'other.wrap').exists()
    assert (subprojects / 'shared-lib.wrap').exists()
    assert not (subprojects / 'grpc-only.wrap').exists()


def test_remove_skips_cleanup_when_no_dep_index(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup warns and skips when no dep_name_index is available."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('other', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'orphan.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
            'other': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN),
        },
        packages={
            'orphan': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    cmd = Remove(argparse.Namespace(package='grpc'), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Remove.build_dep_name_index',
            return_value={},
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'orphan.wrap').exists()
    assert 'Could not determine orphaned' in caplog.text


def test_remove_cleanup_with_no_remaining_deps(tmp_path: Path) -> None:
    """Removing the last direct dep removes managed transitive wraps but preserves manual ones."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'zlib.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'manual.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
        },
        packages={
            'abseil-cpp': LockedPackage(
                version='20250814.1-1', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
            're2': LockedPackage(
                version='20230301-3', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN
            ),
            'zlib': LockedPackage(version='1.3.2-1', wrap_hash='sha256:' + 'd' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Remove(argparse.Namespace(package='grpc'), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert not (subprojects / 'abseil-cpp.wrap').exists()
    assert not (subprojects / 're2.wrap').exists()
    assert not (subprojects / 'zlib.wrap').exists()
    assert (subprojects / 'manual.wrap').exists()


def test_remove_skips_cleanup_without_lockfile(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a lockfile, cleanup warns and preserves all remaining wraps."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    context = _make_context(tmp_path)
    cmd = Remove(argparse.Namespace(package='grpc'), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert (subprojects / 're2.wrap').exists()
    assert 'No lockfile found' in caplog.text
