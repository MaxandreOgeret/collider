# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg remove/rm command."""

import argparse
import hashlib
import os
import urllib.request

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import resolvelib

from collider.cache import WrapCache
from collider.Context import Context
from collider.errors import ColliderUserError
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.pkg.Add import Add
from collider.subcommand.pkg.Prune import PruneLockUnreadableError
from collider.subcommand.pkg.Remove import Remove
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from collider.utils.packaging.resolver import (
    Candidate,
    ResolutionResult,
    ResolutionSummary,
)


ORIGIN = 'https://wrapdb.example.com/v2/'


class _DummyResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


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


def _wrap_text(name: str, source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        f'source_url=https://example.com/{name}.tar.xz\n'
        f'source_filename={name}.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def _make_package(name: str, version: str) -> tuple[WrapPackage, bytes]:
    content = f'payload-{name}-{version}'.encode()
    content_hash = hashlib.sha256(content).hexdigest()
    package = WrapPackage.from_wrap_text(name, version, _wrap_text(name, content_hash))
    return package, content


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

    cmd = Remove(argparse.Namespace(package='shared', prune=False), _make_context(tmp_path))

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

    cmd = Remove(argparse.Namespace(package='shared', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert colliderfile.dependencies == []


def test_remove_with_prune_and_no_lockfile_ends_with_skip_summary(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`remove --prune` without a lockfile ends with the script-detectable skip summary."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    cmd = Remove(argparse.Namespace(package='shared', prune=True), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert caplog.records[-1].message == 'prune skipped: no lockfile; run "collider lock".'


def test_remove_with_prune_propagates_deletion_failure(tmp_path: Path) -> None:
    """`remove --prune` tolerates only the lock skip: real prune failures propagate."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    cmd = Remove(argparse.Namespace(package='shared', prune=True), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Remove.run_prune',
            side_effect=ColliderUserError('undeletable orphan', os.EX_IOERR),
        ):
            with pytest.raises(ColliderUserError) as excinfo:
                cmd.execute()
    finally:
        os.chdir(cwd)

    assert excinfo.value.exit_code == os.EX_IOERR


def test_remove_with_prune_tolerates_lock_skip(tmp_path: Path) -> None:
    """`remove --prune` exits EX_OK when pruning is skipped over an unreadable lock."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('shared', DependencySource.COLLIDER, None)],
    )

    cmd = Remove(argparse.Namespace(package='shared', prune=True), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Remove.run_prune',
            side_effect=PruneLockUnreadableError('lock unreadable', os.EX_DATAERR),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)


def test_remove_missing_package_returns_noinput(tmp_path: Path) -> None:
    """Removing an unknown package should fail cleanly."""
    _init_project(tmp_path, dependencies=[])

    cmd = Remove(argparse.Namespace(package='missing', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)


def test_remove_rejects_transitive_wrap_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A transitive wrap alone is not removable via pkg remove."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('spdlog', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'catch2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    cmd = Remove(argparse.Namespace(package='catch2', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)

    assert (subprojects / 'catch2.wrap').exists()
    assert 'not a Collider-managed dependency' in caplog.text


def test_remove_workflow_promoted_transitive_stays_quiet_when_still_needed(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Removing a promoted transitive dependency should not warn about leftovers when it is still needed.
    :param tmp_path: Temporary project path.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param caplog: Captured log fixture.
    """
    _init_project(tmp_path, dependencies=[])

    grpc_pkg, grpc_content = _make_package('grpc', '1.59.1')
    protobuf_pkg, protobuf_content = _make_package('protobuf', '25.2-4')
    abseil_pkg, abseil_content = _make_package('abseil-cpp', '20250814.1-1')
    re2_pkg, re2_content = _make_package('re2', '20230301-3')

    packages = {
        make_repo_key('grpc', '1.59.1', PackageType.WRAP): grpc_pkg,
        make_repo_key('protobuf', '25.2-4', PackageType.WRAP): protobuf_pkg,
        make_repo_key('abseil-cpp', '20250814.1-1', PackageType.WRAP): abseil_pkg,
        make_repo_key('re2', '20230301-3', PackageType.WRAP): re2_pkg,
    }

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repo.requires_network.return_value = False
    repo.get_package.side_effect = lambda repo_key: packages.get(repo_key)
    context = _make_context(tmp_path, {'repo1': repo})

    def _urlopen(url, **kwargs):
        del kwargs
        if 'grpc' in url:
            return _DummyResponse(grpc_content)
        if 'protobuf' in url:
            return _DummyResponse(protobuf_content)
        if 'abseil-cpp' in url:
            return _DummyResponse(abseil_content)
        if 're2' in url:
            return _DummyResponse(re2_content)
        return _DummyResponse(b'')

    monkeypatch.setattr(urllib.request, 'urlopen', _urlopen)

    grpc_mapping = {
        'grpc': Candidate('grpc', '1.59.1', 'repo1'),
        'protobuf': Candidate('protobuf', '25.2-4', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20250814.1-1', 'repo1'),
        're2': Candidate('re2', '20230301-3', 'repo1'),
    }

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        add_grpc = Add(
            argparse.Namespace(
                package='grpc',
                offline=False,
                version=None,
                include=None,
                exclude=None,
                include_conditional=False,
                exclude_optional=False,
                force=True,
            ),
            context,
        )
        with (
            patch(
                'collider.subcommand.pkg.Add.search_packages',
                return_value={
                    'repo1': {
                        make_repo_key('grpc', '1.59.1', PackageType.WRAP): RepoPackageEntry(
                            'grpc', '1.59.1', PackageType.WRAP
                        )
                    }
                },
            ),
            patch(
                'collider.subcommand.pkg.Add.resolve_dependencies',
                return_value=_make_resolution_result(grpc_mapping),
            ),
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={
                    'protobuf_dep': 'protobuf',
                    'absl_dep': 'abseil-cpp',
                    're2_dep': 're2',
                },
            ),
        ):
            assert add_grpc.execute() == os.EX_OK

        add_protobuf = Add(
            argparse.Namespace(
                package='protobuf',
                offline=False,
                version=None,
                include=None,
                exclude=None,
                include_conditional=False,
                exclude_optional=False,
                force=False,
            ),
            context,
        )
        with (
            patch(
                'collider.subcommand.pkg.Add.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Add.resolve_all_dependencies',
                return_value=_make_resolution_result(grpc_mapping),
            ),
        ):
            assert add_protobuf.execute() == os.EX_OK

        caplog.clear()
        remove_protobuf = Remove(argparse.Namespace(package='protobuf', prune=False), context)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={
                    'protobuf_dep': 'protobuf',
                    'absl_dep': 'abseil-cpp',
                    're2_dep': 're2',
                },
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                return_value=_make_resolution_result(grpc_mapping),
            ),
        ):
            assert remove_protobuf.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == ['grpc']
    assert 'Kept installed wrap state for "protobuf"' in caplog.text
    assert 'Additional wraps remain in subprojects/' not in caplog.text
    assert 'Transitive wraps were left in place' not in caplog.text


def test_remove_keeps_artifacts_when_package_is_still_needed_transitively(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Removing a direct dep keeps its wrap installed when another direct dep still needs it."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('protobuf', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 're2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    repos = {'repo1': repo}
    context = _make_context(tmp_path, repos)

    resolved_mapping = {
        'grpc': Candidate('grpc', '1.59.1-1', 'repo1'),
        'protobuf': Candidate('protobuf', '25.2-4', 'repo1'),
        'abseil-cpp': Candidate('abseil-cpp', '20250814.1-1', 'repo1'),
        're2': Candidate('re2', '20230301-3', 'repo1'),
    }

    cmd = Remove(argparse.Namespace(package='protobuf', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                return_value=_make_resolution_result(resolved_mapping),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    dep_names = [dep.name for dep in colliderfile.dependencies]
    assert dep_names == ['grpc']
    assert (subprojects / 'protobuf.wrap').exists()
    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert (subprojects / 're2.wrap').exists()
    assert 'Kept installed wrap state for "protobuf"' in caplog.text
    assert 'Additional wraps remain in subprojects/' not in caplog.text


def test_remove_keeps_wrap_when_resolution_fails_after_lock_refresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Remove keeps the wrap when refreshed lockfile metadata cannot prove it is safe to delete."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('protobuf', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
            'protobuf': LockedPackage(
                version='25.2-4', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Remove(argparse.Namespace(package='protobuf', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                side_effect=resolvelib.ResolutionImpossible([]),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    colliderfile = Colliderfile.from_path(tmp_path / Colliderfile.get_filename())
    assert [dep.name for dep in colliderfile.dependencies] == ['grpc']
    assert (subprojects / 'protobuf.wrap').exists()
    assert 'Could not determine whether "protobuf" is still needed' in caplog.text
    assert 'Removed installed wrap state for "protobuf".' not in caplog.text


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

    cmd = Remove(argparse.Namespace(package='shared', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'run "collider lock" to refresh it' not in caplog.text


def test_remove_does_not_touch_transitive_wraps(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without --prune, transitive wraps are left in place and a cleanup hint is shown."""
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

    cmd = Remove(argparse.Namespace(package='grpc', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'abseil-cpp.wrap').exists()
    assert (subprojects / 're2.wrap').exists()
    assert 'Transitive wraps were left in place' in caplog.text


def test_remove_without_lockfile_warns_that_additional_wraps_remain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without lockfile provenance, remove warns that leftover wraps must be handled manually."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'catch2.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    cmd = Remove(argparse.Namespace(package='grpc', prune=False), _make_context(tmp_path))

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'catch2.wrap').exists()
    assert 'Additional wraps remain in subprojects/' in caplog.text
    assert 'cannot safely determine which are orphaned' in caplog.text
    assert 'Remove them manually.' in caplog.text


def test_remove_with_prune_flag_calls_prune(tmp_path: Path) -> None:
    """--prune chains to the prune command after removal."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

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
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path)
    cmd = Remove(argparse.Namespace(package='grpc', prune=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch(
            'collider.subcommand.pkg.Remove.run_prune',
            return_value=os.EX_OK,
        ) as mock_prune:
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    mock_prune.assert_called_once_with(context)


def test_remove_with_prune_warns_on_corrupt_lockfile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The remove --prune flow remains safe when collider.lock is unreadable."""
    _init_project(
        tmp_path,
        dependencies=[Dependency('grpc', DependencySource.COLLIDER, None)],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile_path = tmp_path / Lockfile.get_filename()
    lockfile_path.write_text('not valid json{{{{', encoding='utf-8')

    context = _make_context(tmp_path)
    cmd = Remove(argparse.Namespace(package='grpc', prune=True), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert not (subprojects / 'grpc.wrap').exists()
    assert (subprojects / 'abseil-cpp.wrap').exists()
    # The trailing summary is the last line so scripts can detect the skip.
    assert caplog.records[-1].message == 'prune skipped: unreadable lockfile; run "collider lock".'
    # A successful command must not leave CRITICAL lines in its output (issue #79).
    assert not [r for r in caplog.records if r.levelname == 'CRITICAL']


def test_remove_keeps_artifacts_when_dependency_index_is_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Remove keeps wraps in place when Collider cannot build a dependency index safely."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('protobuf', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    context = _make_context(tmp_path, {'repo1': MagicMock(spec=RepositoryInterface)})
    cmd = Remove(argparse.Namespace(package='protobuf', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with patch('collider.subcommand.pkg.Remove.build_dep_name_index', return_value={}):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'protobuf.wrap').exists()
    assert 'Could not determine whether "protobuf" is still needed' in caplog.text


def test_remove_keeps_artifacts_when_resolution_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Remove keeps wraps in place when transitive resolution fails unexpectedly."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('protobuf', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    context = _make_context(tmp_path, {'repo1': MagicMock(spec=RepositoryInterface)})
    cmd = Remove(argparse.Namespace(package='protobuf', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                side_effect=resolvelib.ResolutionImpossible([]),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'protobuf.wrap').exists()
    assert 'Could not determine whether "protobuf" is still needed' in caplog.text


def test_remove_keeps_artifacts_when_resolution_fails_and_lockfile_lists_dependency(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Remove keeps wraps in place when resolution fails and the lockfile still lists the package.
    :param tmp_path: Temporary project path.
    :param caplog: Captured log fixture.
    """
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('grpc', DependencySource.COLLIDER, None),
            Dependency('protobuf', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'protobuf.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(
                version='1.59.1-1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN
            ),
            'protobuf': LockedPackage(
                version='25.2-4', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
        }
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    context = _make_context(tmp_path, {'repo1': MagicMock(spec=RepositoryInterface)})
    cmd = Remove(argparse.Namespace(package='protobuf', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'protobuf_dep': 'protobuf'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                side_effect=resolvelib.ResolutionImpossible([]),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert (subprojects / 'protobuf.wrap').exists()
    assert 'Could not determine whether "protobuf" is still needed' in caplog.text
    assert 'Removed installed wrap state for "protobuf".' not in caplog.text


def test_remove_without_prune_stays_silent_when_only_direct_wraps_remain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No orphan hint is shown when the only remaining wraps are still direct dependencies."""
    _init_project(
        tmp_path,
        dependencies=[
            Dependency('alpha', DependencySource.COLLIDER, None),
            Dependency('beta', DependencySource.COLLIDER, None),
        ],
    )
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'alpha.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects / 'beta.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = {}
    context = _make_context(tmp_path, {'repo1': repo})
    cmd = Remove(argparse.Namespace(package='alpha', prune=False), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with (
            patch(
                'collider.subcommand.pkg.Remove.build_dep_name_index',
                return_value={'beta_dep': 'beta'},
            ),
            patch(
                'collider.subcommand.pkg.Remove.resolve_all_dependencies',
                return_value=_make_resolution_result({'beta': Candidate('beta', '1.0.0', 'repo1')}),
            ),
        ):
            assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Additional wraps remain in subprojects/' not in caplog.text
    assert 'Transitive wraps were left in place' not in caplog.text
