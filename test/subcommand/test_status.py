# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the status subcommand."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

import resolvelib

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.subcommand.Status import Status
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.resolver import Candidate, ResolutionResult, ResolutionSummary


ORIGIN = 'https://wrapdb.example.com/v2/'


def _init_project(tmp_path: Path, dependencies: list[Dependency]) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    Colliderfile(dependencies=dependencies).save(tmp_path / Colliderfile.get_filename())


def test_status_reports_tracked_and_untracked(tmp_path: Path, caplog) -> None:
    dependencies = [
        Dependency('alpha', DependencySource.COLLIDER, '1.0.0'),
        Dependency('beta', DependencySource.COLLIDER, None),
        Dependency('sys', DependencySource.SYSTEM, None),
    ]
    _init_project(tmp_path, dependencies)

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'alpha.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'gamma.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    config = MagicMock()
    config.repositories = {}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    args = argparse.Namespace()
    cmd = Status(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert '‣ tracked' in caplog.text
    assert 'alpha (1.0.0) [installed]' in caplog.text
    assert 'beta (any) [missing]' in caplog.text
    assert '‣ system' in caplog.text
    assert '  ‣ sys' in caplog.text
    assert '‣ untracked' in caplog.text
    assert '  ‣ gamma' in caplog.text


def test_status_requires_colliderfile(tmp_path: Path, caplog) -> None:
    (tmp_path / 'meson.build').write_text('project("dummy", "c")\n')
    context = MagicMock(spec=Context)
    args = argparse.Namespace()
    cmd = Status(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_NOINPUT
    finally:
        os.chdir(cwd)

    assert 'No colliderfile found in current directory.' in caplog.text


def test_status_reports_lock_drift_ok(tmp_path: Path, caplog) -> None:
    """Report ok when installed wrap matches the lockfile hash."""
    wrap_text = '[wrap-file]\nsource_url=https://example.com/a.tar.xz\n'
    wrap_hash = compute_wrap_hash(wrap_text)

    dependencies = [Dependency('alpha', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    lockfile = Lockfile(
        dependencies={'alpha': LockedPackage(version='1.0', wrap_hash=wrap_hash, origin=ORIGIN)},
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'alpha.wrap').write_text(wrap_text, encoding='utf-8')

    context = MagicMock(spec=Context)
    args = argparse.Namespace()
    cmd = Status(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'alpha (any -> 1.0) [installed]' in caplog.text
    assert '[ok]' in caplog.text


def test_status_reports_lock_drift_modified(tmp_path: Path, caplog) -> None:
    """Report modified when installed wrap differs from the lockfile hash."""
    dependencies = [Dependency('alpha', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    lockfile = Lockfile(
        dependencies={
            'alpha': LockedPackage(version='1.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'alpha.wrap').write_text('different content', encoding='utf-8')

    context = MagicMock(spec=Context)
    args = argparse.Namespace()
    cmd = Status(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert '[modified]' in caplog.text


def test_status_reports_lock_drift_missing(tmp_path: Path, caplog) -> None:
    """Report missing when locked package has no installed wrap."""
    dependencies = [Dependency('alpha', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    lockfile = Lockfile(
        dependencies={
            'alpha': LockedPackage(version='1.0', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    (tmp_path / 'subprojects').mkdir()

    context = MagicMock(spec=Context)
    args = argparse.Namespace()
    cmd = Status(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert '[missing]' in caplog.text


def test_status_shows_transitive_from_lockfile(tmp_path: Path, caplog) -> None:
    """With a lockfile, non-direct locked packages appear in the transitive section."""
    dependencies = [Dependency('grpc', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    lockfile = Lockfile(
        dependencies={
            'grpc': LockedPackage(version='1.59.1', wrap_hash='sha256:' + 'a' * 64, origin=ORIGIN),
        },
        packages={
            'abseil-cpp': LockedPackage(
                version='20240722.0', wrap_hash='sha256:' + 'b' * 64, origin=ORIGIN
            ),
            'zlib': LockedPackage(version='1.3.1', wrap_hash='sha256:' + 'c' * 64, origin=ORIGIN),
        },
    )
    lockfile.save(tmp_path / Lockfile.get_filename())

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'zlib.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'manual.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    context = MagicMock(spec=Context)
    cmd = Status(argparse.Namespace(), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'grpc (any -> 1.59.1) [installed]' in caplog.text
    assert '‣ transitive' in caplog.text
    assert 'abseil-cpp (20240722.0) [installed]' in caplog.text
    assert 'zlib (1.3.1) [installed]' in caplog.text
    assert '‣ untracked' in caplog.text
    assert '  ‣ manual' in caplog.text
    assert 'abseil-cpp' not in [
        line for line in caplog.text.split('\n') if 'untracked' in line or '  ‣ abseil' in line
    ]


def test_status_without_lockfile_shows_untracked(tmp_path: Path, caplog) -> None:
    """Without a lockfile and no resolution data, all non-tracked wraps appear as untracked."""
    dependencies = [Dependency('grpc', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    config = MagicMock()
    config.repositories = {}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    cmd = Status(argparse.Namespace(), context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert '‣ transitive' not in caplog.text
    assert '‣ untracked' in caplog.text
    assert '  ‣ abseil-cpp' in caplog.text


def test_status_without_lockfile_resolves_transitive(tmp_path: Path, caplog) -> None:
    """Without a lockfile, Status re-resolves to classify transitive deps."""
    dependencies = [Dependency('grpc', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'zlib.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'manual.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    resolution = ResolutionResult(
        mapping={
            'grpc': Candidate('grpc', '1.59.1', 'wrapdb'),
            'abseil-cpp': Candidate('abseil-cpp', '20240722.0', 'wrapdb'),
            'zlib': Candidate('zlib', '1.3.1', 'wrapdb'),
        },
        summary=ResolutionSummary(
            skipped_conditional=set(),
            skipped_optional=set(),
            included_optional=set(),
            unmapped_system=set(),
            skipped_conditional_by_pkg={},
            skipped_optional_by_pkg={},
        ),
    )

    config = MagicMock()
    config.repositories = {'wrapdb': MagicMock()}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    cmd = Status(argparse.Namespace(), context)

    with (
        patch(
            'collider.subcommand.Status.resolve_all_dependencies',
            return_value=resolution,
        ),
        patch(
            'collider.subcommand.Status.build_dep_name_index',
            return_value={'grpc': 'wrapdb'},
        ),
    ):
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert cmd.execute() == os.EX_OK
        finally:
            os.chdir(cwd)

    assert 'grpc (any -> 1.59.1) [installed]' in caplog.text
    assert '‣ transitive' in caplog.text
    assert 'abseil-cpp (20240722.0) [installed]' in caplog.text
    assert 'zlib (1.3.1) [installed]' in caplog.text
    assert '‣ untracked' in caplog.text
    assert '  ‣ manual' in caplog.text


def test_status_warns_when_resolution_fails(tmp_path: Path, caplog) -> None:
    """A failed resolution logs a warning instead of silently listing wraps as untracked."""
    dependencies = [Dependency('grpc', DependencySource.COLLIDER, None)]
    _init_project(tmp_path, dependencies)

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'abseil-cpp.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    config = MagicMock()
    config.repositories = {'wrapdb': MagicMock()}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    cmd = Status(argparse.Namespace(), context)

    with (
        patch(
            'collider.subcommand.Status.resolve_all_dependencies',
            side_effect=resolvelib.ResolutionTooDeep(1),
        ),
        patch(
            'collider.subcommand.Status.build_dep_name_index',
            return_value={'grpc': 'wrapdb'},
        ),
    ):
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert cmd.execute() == os.EX_OK
        finally:
            os.chdir(cwd)

    assert 'Version resolution failed' in caplog.text
    assert '‣ untracked' in caplog.text
    assert '  ‣ abseil-cpp' in caplog.text


def test_status_passes_include_conditional_from_colliderfile(
    tmp_path: Path,
    caplog,
) -> None:
    """When a dep has include_conditional=True, Status passes it to the resolver."""
    dependencies = [
        Dependency('grpc', DependencySource.COLLIDER, None, include_conditional=True),
    ]
    _init_project(tmp_path, dependencies)

    subprojects_dir = tmp_path / 'subprojects'
    subprojects_dir.mkdir()
    (subprojects_dir / 'grpc.wrap').write_text('[wrap-file]\n', encoding='utf-8')
    (subprojects_dir / 'gtest.wrap').write_text('[wrap-file]\n', encoding='utf-8')

    resolution = ResolutionResult(
        mapping={
            'grpc': Candidate('grpc', '1.59.1', 'wrapdb'),
            'gtest': Candidate('gtest', '1.17.0', 'wrapdb'),
        },
        summary=ResolutionSummary(
            skipped_conditional=set(),
            skipped_optional=set(),
            included_optional=set(),
            unmapped_system=set(),
            skipped_conditional_by_pkg={},
            skipped_optional_by_pkg={},
        ),
    )

    config = MagicMock()
    config.repositories = {'wrapdb': MagicMock()}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    cmd = Status(argparse.Namespace(), context)

    with (
        patch(
            'collider.subcommand.Status.resolve_all_dependencies',
            return_value=resolution,
        ) as mock_resolve,
        patch(
            'collider.subcommand.Status.build_dep_name_index',
            return_value={'grpc': 'wrapdb'},
        ),
    ):
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert cmd.execute() == os.EX_OK
        finally:
            os.chdir(cwd)

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        assert call_kwargs.kwargs.get('include_conditional') is True

    assert 'gtest (1.17.0) [installed]' in caplog.text
    assert '‣ transitive' in caplog.text
    assert '‣ untracked' not in caplog.text
