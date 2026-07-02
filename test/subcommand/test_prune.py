# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg prune command."""

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
from collider.subcommand.pkg.Prune import Prune
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


def test_prune_removes_orphaned_wraps(tmp_path: Path) -> None:
    """Orphaned wraps listed in the lockfile are removed."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
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

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'abseil-cpp.wrap').exists()
    assert not (subprojects / 're2.wrap').exists()


def test_prune_keeps_needed_transitive_deps(tmp_path: Path) -> None:
    """Wraps still needed by remaining direct deps are kept."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('other', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'other.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'shared-lib.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'grpc-only.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
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

    resolved_mapping = {
        'other': Candidate('other', '1.0.0', 'repo1'),
        'shared-lib': Candidate('shared-lib', '2.0.0', 'repo1'),
    }

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Prune.build_dep_name_index',
                return_value={'some_dep': 'shared-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Prune.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'other.wrap').exists()
    assert (subprojects / 'shared-lib.wrap').exists()
    assert not (subprojects / 'grpc-only.wrap').exists()


def test_prune_preserves_manual_wraps(tmp_path: Path) -> None:
    """Wraps not listed in the lockfile are never removed."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'orphan.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'manual.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        packages={
            'orphan': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'orphan.wrap').exists()
    assert (subprojects / 'manual.wrap').exists()


def test_prune_warns_without_lockfile(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Without a lockfile, prune warns and preserves all wraps."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert 'cannot safely determine which transitive wraps are orphaned' in caplog.text
    assert 'may still need to be removed manually' in caplog.text
    # The trailing summary is the last line so scripts can detect the skip.
    assert caplog.records[-1].message == 'prune skipped: no lockfile; run "collider lock".'


def test_prune_warns_on_corrupt_lockfile(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A corrupt lockfile triggers a warning and preserves all wraps."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile_path = tmp_path / Lockfile.get_filename()
    lockfile_path.write_text('not valid json{{{{', encoding='utf-8')

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert caplog.records[-1].message == 'prune skipped: unreadable lockfile; run "collider lock".'


def test_prune_dry_run_lists_without_deleting(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """--dry-run lists orphaned wraps without deleting them."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
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

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert (subprojects / 're2.wrap').exists()
    assert 'abseil-cpp' in caplog.text
    assert 're2' in caplog.text


def test_prune_sequential_shared_transitive(tmp_path: Path) -> None:
    """Remove A then prune keeps shared C; remove B then prune removes C."""
    from collider.subcommand.pkg.Remove import Remove

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

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        # Step 1: remove grpc, then prune. shared-lib kept (other still needs it).
        cmd_rm1 = Remove(argparse.Namespace(package='grpc', prune=False), context)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'some_dep': 'shared-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                return_value=_make_resolution_result(
                    {
                        'other': Candidate('other', '1.0.0', 'repo1'),
                        'shared-lib': Candidate('shared-lib', '2.0.0', 'repo1'),
                    }
                ),
            ),
        ):
            assert cmd_rm1.execute() == os.EX_OK
        assert not (subprojects / 'grpc.wrap').exists()

        remaining_after_grpc = {
            'other': Candidate('other', '1.0.0', 'repo1'),
            'shared-lib': Candidate('shared-lib', '2.0.0', 'repo1'),
        }
        cmd_prune1 = Prune(argparse.Namespace(dry_run=False), context)
        with (
            patch(
                'collider.subcommand.pkg.Prune.build_dep_name_index',
                return_value={'some_dep': 'shared-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Prune.resolve_all_dependencies',
                return_value=_make_resolution_result(remaining_after_grpc),
            ),
        ):
            assert cmd_prune1.execute() == os.EX_OK

        assert (subprojects / 'other.wrap').exists()
        assert (subprojects / 'shared-lib.wrap').exists()
        assert not (subprojects / 'grpc-only.wrap').exists()

        # Step 2: remove other, then prune. shared-lib now orphaned.
        cmd_rm2 = Remove(argparse.Namespace(package='other', prune=False), context)
        assert cmd_rm2.execute() == os.EX_OK
        assert not (subprojects / 'other.wrap').exists()

        cmd_prune2 = Prune(argparse.Namespace(dry_run=False), context)
        assert cmd_prune2.execute() == os.EX_OK

        assert not (subprojects / 'shared-lib.wrap').exists()
    finally:
        os.chdir(cwd)


def test_prune_no_orphans_is_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """When nothing is orphaned, prune exits cleanly without logging removals."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1-1', 'repo1'),
    }

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Prune.build_dep_name_index',
                return_value={'grpc_dep': 'grpc'},
            ),
            patch(
                'collider.subcommand.pkg.Prune.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'grpc.wrap').exists()
    assert 'Removing' not in caplog.text
    assert 'collider.lock still contains pruned packages' not in caplog.text


def test_prune_uses_conservative_resolution_flags(tmp_path: Path) -> None:
    """Prune resolves with include_conditional=True and exclude_optional=False."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('dep-a', DependencySource.COLLIDER, None, exclude_optional=True),
            Dependency('dep-b', DependencySource.COLLIDER, None, include_conditional=False),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'dep-a.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'dep-b.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'optional-lib.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'dep-a': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
            'dep-b': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN),
        },
        packages={
            'optional-lib': LockedPackage(
                version='1.0.0', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    resolved_mapping = {
        'dep-a': Candidate('dep-a', '1.0.0', 'repo1'),
        'dep-b': Candidate('dep-b', '1.0.0', 'repo1'),
        'optional-lib': Candidate('optional-lib', '1.0.0', 'repo1'),
    }

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Prune.build_dep_name_index',
                return_value={'opt': 'optional-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Prune.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ) as mock_resolve,
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs['include_conditional'] is True
    assert call_kwargs['exclude_optional'] is False
    assert (subprojects / 'optional-lib.wrap').exists()


def test_prune_warns_lockfile_stale_after_deletion(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """After pruning wraps, a lockfile-stale warning is emitted."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'orphan.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        packages={
            'orphan': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'orphan.wrap').exists()
    assert 'collider.lock still contains pruned packages' in caplog.text


def test_prune_dry_run_no_lockfile_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """--dry-run does not emit a lockfile-stale warning."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'orphan.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        packages={
            'orphan': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'orphan.wrap').exists()
    assert 'collider.lock still contains pruned packages' not in caplog.text


def test_prune_no_remaining_wraps_is_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With no installed wraps, prune exits without stale-lock warnings."""
    _init_project(tmp_path, dependencies=[])
    lockfile = Lockfile(
        packages={
            'orphan': LockedPackage(version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Removing unused transitive dependencies' not in caplog.text
    assert 'collider.lock still contains pruned packages' not in caplog.text


def test_prune_preserves_include_exclude_filtered_dependencies(tmp_path: Path) -> None:
    """Prune forwards per-root include/exclude filters into the needed-set resolution."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency(
                'consumer',
                DependencySource.COLLIDER,
                None,
                exclude=['legacy-dep'],
                include=['feature-dep'],
            ),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'consumer.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'feature-lib.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'consumer': LockedPackage(
                version='1.0.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
        },
        packages={
            'feature-lib': LockedPackage(
                version='1.0.0', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    resolved_mapping = {
        'consumer': Candidate('consumer', '1.0.0', 'repo1'),
        'feature-lib': Candidate('feature-lib', '1.0.0', 'repo1'),
    }

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Prune.build_dep_name_index',
                return_value={'feature_dep': 'feature-lib'},
            ),
            patch(
                'collider.subcommand.pkg.Prune.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ) as mock_resolve,
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    roots = mock_resolve.call_args.kwargs['roots']
    assert len(roots) == 1
    assert roots[0].include_names == {'feature-dep'}
    assert roots[0].exclude_names == {'legacy-dep'}
    assert (subprojects / 'feature-lib.wrap').exists()


def test_prune_with_no_remaining_direct_deps_removes_all_managed_wraps(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When no direct Collider deps remain, all managed wraps are prune candidates."""
    _init_project(tmp_path, dependencies=[])
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'manual.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        packages={
            'abseil-cpp': LockedPackage(
                version='20250814.1-1', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
            'protobuf': LockedPackage(
                version='25.2-4', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN
            ),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Prune(argparse.Namespace(dry_run=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'abseil-cpp.wrap').exists()
    assert not (subprojects / 'protobuf.wrap').exists()
    assert (subprojects / 'manual.wrap').exists()
    assert 'collider.lock still contains pruned packages' in caplog.text
