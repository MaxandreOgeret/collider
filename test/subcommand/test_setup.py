# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Test the setup subcommand."""

import argparse
import logging
import os
import re

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from collider.Context import Context
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.subcommand.Setup import Setup
from collider.utils import meson
from test.common.common import Subcommand, run_subcommand


def _force_fallback_args(sourcedir: Path, meson_args: list[str] | None = None) -> list[str]:
    """Drive Setup._force_fallback_args in isolation, without invoking Meson."""
    args = argparse.Namespace(
        sourcedir=sourcedir,
        builddir=Path('build'),
        meson_setup_args=(['--', *meson_args] if meson_args else []),
    )
    return Setup(args, MagicMock(spec=Context))._force_fallback_args()


def _write_wrapped_foo_project(sourcedir: Path) -> None:
    """Create a project whose `dependency('foo')` is backed by a present local wrap."""
    subprojects = sourcedir / 'subprojects'
    subprojects.mkdir(parents=True)
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text(
        "project('p', 'c', version: '1.0.0', license: 'MIT')\nfoo_dep = dependency('foo')\n"
    )
    # The subproject directory already exists, so Meson resolves 'foo' locally (no download).
    (subprojects / 'foo.wrap').write_text(
        '[wrap-file]\n'
        'directory = foo\n'
        'source_url = https://example.invalid/foo.tar.gz\n'
        'source_filename = foo.tar.gz\n'
        f'source_hash = {"0" * 64}\n'
        '\n[provide]\n'
        'foo = foo_dep\n'
    )
    foo_dir = subprojects / 'foo'
    foo_dir.mkdir()
    (foo_dir / 'meson.build').write_text(
        "project('foo', 'c', version: '1.2.3')\nfoo_dep = declare_dependency()\n"
    )


def test_setup_meson_unavailable_returns_ex_unavailable(tmp_path: Path, monkeypatch) -> None:
    """`collider setup` returns EX_UNAVAILABLE when Meson is missing or too old."""

    def _raise_unavailable() -> None:
        raise meson.MesonUnavailableError('Could not locate "meson" executable.')

    monkeypatch.setattr(meson, 'validate', _raise_unavailable)

    assert (
        run_subcommand(Subcommand.SETUP, ['--builddir', str(tmp_path / 'build')])
        == os.EX_UNAVAILABLE
    )


def test_execute_success(meson_project: Path, tmp_path: Path) -> None:
    """Execute the subcommand."""
    # Skip 'shared' when system lacks libmd (optional system dependency).
    if meson_project.name == 'shared':
        result = run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path)],
        )
        if result != os.EX_OK:
            pytest.skip('shared project requires system libmd')
        # Fall through to assertions below.
    else:
        assert (
            run_subcommand(
                Subcommand.SETUP,
                ['--sourcedir', str(meson_project), '--builddir', str(tmp_path)],
            )
            == os.EX_OK
        )

    for required_file in [
        'build.ninja',
        'compile_commands.json',
        'meson-info',
        'meson-logs',
        'meson-private',
    ]:
        assert (tmp_path / required_file).exists()


def test_execute_missing_sourcedir(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Execute the subcommand with a missing source directory."""

    missing_sourcedir = tmp_path / 'missing_sourcedir'
    assert not missing_sourcedir.exists()
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(missing_sourcedir), '--builddir', str(tmp_path)],
        )
        == os.EX_NOINPUT
    )

    stdout, stderr = capfd.readouterr()
    assert re.search(r'Source directory .* does not exist', stderr)


def test_execute_missing_mesonbuild(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Execute the subcommand with a missing source directory."""

    empty_sourcedir = tmp_path / 'empty_sourcedir'
    empty_sourcedir.mkdir()
    assert empty_sourcedir.exists()
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(empty_sourcedir), '--builddir', str(tmp_path)],
        )
        == os.EX_NOINPUT
    )

    stdout, stderr = capfd.readouterr()
    assert 'No "meson.build" file found' in stderr


def test_execute_invalid_mesonbuild(tmp_path: Path, capfd: pytest.CaptureFixture):
    sourcedir = tmp_path / 'empty_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')

    mesonbuild = sourcedir / 'meson.build'
    mesonbuild.touch(exist_ok=True)
    assert mesonbuild.exists()

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path)]
        )
        == os.EX_SOFTWARE
    )

    stdout, stderr = capfd.readouterr()
    assert 'meson setup failed' in stderr


def test_pass_args_to_meson(tmp_path: Path, meson_project: Path, capfd: pytest.CaptureFixture):
    with pytest.raises(ValueError):
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path), '--', '--help'],
            verbose=True,
        )

    stdout, stderr = capfd.readouterr()
    assert re.search(r'Running command: \[\'meson\', \'setup\'.*\'--help\']', stderr)
    assert 'usage: meson setup' in stdout


def test_missing_separator(tmp_path: Path, meson_project: Path, capfd: pytest.CaptureFixture):
    """Test that missing -- separator for extra arguments causes failure."""
    with pytest.raises(ValueError):
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path), 'reconfigure'],
        )
    stdout, stderr = capfd.readouterr()
    assert 'Expected "--" separator' in stderr


def test_builddir_cleanup_on_failure(tmp_path: Path):
    """Test that build directory is removed if meson setup fails."""
    sourcedir = tmp_path / 'empty_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    mesonbuild = sourcedir / 'meson.build'
    mesonbuild.write_text('invalid content')
    builddir = tmp_path / 'failed_build'

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_SOFTWARE
    )
    assert not builddir.exists()


def test_preexisting_builddir_not_removed_on_failure(tmp_path: Path):
    """Do not remove a build directory that existed before setup started."""
    sourcedir = tmp_path / 'broken_srcdir'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text('invalid content')

    builddir = tmp_path / 'existing_builddir'
    builddir.mkdir()
    sentinel = builddir / 'keep-me.txt'
    sentinel.write_text('preserve')

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_SOFTWARE
    )
    assert builddir.exists()
    assert sentinel.exists()


def test_setup_forces_fallback_for_present_wraps(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Without a lock, collider forces all present wraps and warns about scoping."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build')],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert '--force-fallback-for=foo' in stderr
    assert 'No collider.lock found' in stderr
    # The lock-scoped exclusion notice must not fire when there is no lock.
    assert 'not in collider.lock' not in stderr


def test_setup_with_lock_scopes_force_to_managed_packages(
    tmp_path: Path, capfd: pytest.CaptureFixture
):
    """With a lock, only managed packages are forced; an unmanaged wrap is left alone."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    # bar is an unmanaged wrap (not in the lock); it must not be forced.
    (sourcedir / 'subprojects' / 'bar.wrap').write_text(
        '[wrap-file]\ndirectory = bar\nsource_url = https://example.invalid/bar.tar.gz\n'
        f'source_filename = bar.tar.gz\nsource_hash = {"0" * 64}\n\n[provide]\nbar = bar_dep\n'
    )
    # The lock must record foo's real hash, otherwise the drift gate aborts before force-scoping.
    foo_hash = compute_wrap_hash((sourcedir / 'subprojects' / 'foo.wrap').read_text())
    Lockfile(
        dependencies={
            'foo': LockedPackage(
                version='1.2.3', wrap_hash=foo_hash, origin='https://wrapdb.example/v2/'
            )
        },
    ).save(sourcedir / Lockfile.get_filename())

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build')],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert '--force-fallback-for=foo' in stderr
    assert '--force-fallback-for=bar' not in stderr
    assert 'foo,bar' not in stderr and 'bar,foo' not in stderr
    assert 'No collider.lock found' not in stderr
    # The unmanaged wrap is surfaced, not silently dropped.
    assert 'not in collider.lock' in stderr and 'bar' in stderr


def test_setup_empty_lock_surfaces_unscoped_wraps(tmp_path: Path, capfd: pytest.CaptureFixture):
    """An empty/stale lock that omits a present wrap reports it instead of silently dropping it."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    Lockfile().save(sourcedir / Lockfile.get_filename())

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build')],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert '--force-fallback-for' not in stderr  # nothing forced under an empty lock
    assert 'not in collider.lock' in stderr and 'foo' in stderr


def test_setup_malformed_lock_errors(tmp_path: Path, capfd: pytest.CaptureFixture):
    """A malformed collider.lock aborts setup before Meson runs."""
    sourcedir = tmp_path / 'project'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text("project('p', 'c', version: '1.0.0', license: 'MIT')\n")
    (sourcedir / 'collider.lock').write_text('{ not valid json', encoding='utf-8')

    builddir = tmp_path / 'build'
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(builddir)],
        )
        == os.EX_DATAERR
    )

    stdout, stderr = capfd.readouterr()
    assert 'collider.lock is malformed' in stderr
    assert not builddir.exists()


def test_setup_drifted_wrap_aborts_before_meson(tmp_path: Path, capfd: pytest.CaptureFixture):
    """A wrap whose bytes differ from the lock aborts setup with EX_DATAERR, before Meson runs."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    # Lock foo with a hash that cannot match the on-disk foo.wrap, forcing drift.
    Lockfile(
        dependencies={
            'foo': LockedPackage(
                version='1.2.3', wrap_hash='sha256:' + '0' * 64, origin='https://wrapdb.example/v2/'
            )
        },
    ).save(sourcedir / Lockfile.get_filename())

    builddir = tmp_path / 'build'
    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(builddir)],
        )
        == os.EX_DATAERR
    )

    stdout, stderr = capfd.readouterr()
    assert '"foo.wrap" differs from the hash recorded in collider.lock' in stderr
    assert '--allow-drift' in stderr
    # The gate must fail fast: Meson is never configured.
    assert not builddir.exists()


def test_setup_drifted_wrap_allowed_with_flag(tmp_path: Path, capfd: pytest.CaptureFixture):
    """--allow-drift downgrades drift to a warning and lets the build proceed."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    Lockfile(
        dependencies={
            'foo': LockedPackage(
                version='1.2.3', wrap_hash='sha256:' + '0' * 64, origin='https://wrapdb.example/v2/'
            )
        },
    ).save(sourcedir / Lockfile.get_filename())

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(sourcedir), '--builddir', str(tmp_path / 'build'), '--allow-drift'],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert 'Continuing despite wrap drift' in stderr
    assert '--force-fallback-for=foo' in stderr


def test_setup_defers_to_user_supplied_force_fallback(tmp_path: Path, capfd: pytest.CaptureFixture):
    """A user --force-fallback-for takes over: collider must not inject its own (Meson last-wins)."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)

    assert (
        run_subcommand(
            Subcommand.SETUP,
            [
                '--sourcedir',
                str(sourcedir),
                '--builddir',
                str(tmp_path / 'build'),
                '--',
                '--force-fallback-for=bar',
            ],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert '--force-fallback-for=bar' in stderr
    assert '--force-fallback-for=foo' not in stderr
    assert 'collider will not force' in stderr


def test_setup_no_wraps_injects_no_fallback_flag(
    tmp_path: Path, meson_project: Path, capfd: pytest.CaptureFixture
):
    """Without any wraps, collider must not inject a --force-fallback-for flag."""
    if meson_project.name == 'shared':
        pytest.skip('shared project requires system libmd')

    assert (
        run_subcommand(
            Subcommand.SETUP,
            ['--sourcedir', str(meson_project), '--builddir', str(tmp_path / 'build')],
            verbose=True,
        )
        == os.EX_OK
    )

    stdout, stderr = capfd.readouterr()
    assert '--force-fallback-for' not in stderr


def test_force_args_no_lock_forces_all_and_warns(tmp_path: Path, caplog) -> None:
    """No lock: force every present wrap and warn about imprecise scoping."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    with caplog.at_level(logging.INFO, logger='collider'):
        assert _force_fallback_args(sourcedir) == ['--force-fallback-for=foo']
    assert 'No collider.lock found' in caplog.text


def test_force_args_scopes_to_lock(tmp_path: Path, caplog) -> None:
    """With a lock, force only managed wraps and report the unmanaged one."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    (sourcedir / 'subprojects' / 'bar.wrap').write_text('[wrap-file]\ndirectory = bar\n')
    Lockfile(
        dependencies={
            'foo': LockedPackage(version='1', wrap_hash='sha256:' + '0' * 64, origin='https://x/')
        }
    ).save(sourcedir / Lockfile.get_filename())
    with caplog.at_level(logging.INFO, logger='collider'):
        assert _force_fallback_args(sourcedir) == ['--force-fallback-for=foo']
    assert 'No collider.lock found' not in caplog.text
    assert 'not in collider.lock' in caplog.text and 'bar' in caplog.text


def test_force_args_empty_lock_forces_nothing_but_reports(tmp_path: Path, caplog) -> None:
    """An empty lock forces nothing yet still surfaces the present, unscoped wrap."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    Lockfile().save(sourcedir / Lockfile.get_filename())
    with caplog.at_level(logging.INFO, logger='collider'):
        assert _force_fallback_args(sourcedir) == []
    assert 'not in collider.lock' in caplog.text and 'foo' in caplog.text


def test_force_args_malformed_lock_raises(tmp_path: Path) -> None:
    """A malformed lock raises before any Meson interaction."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    (sourcedir / 'collider.lock').write_text('{ not json', encoding='utf-8')
    with pytest.raises(ValueError, match='malformed'):
        _force_fallback_args(sourcedir)


def test_force_args_malformed_lock_wins_over_user_override(tmp_path: Path) -> None:
    """A malformed lock aborts even when the user supplies their own --force-fallback-for."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    (sourcedir / 'collider.lock').write_text('garbage', encoding='utf-8')
    with pytest.raises(ValueError, match='malformed'):
        _force_fallback_args(sourcedir, ['--force-fallback-for=x'])


def test_force_args_defers_to_user_dashdash_spelling(tmp_path: Path, caplog) -> None:
    """A user --force-fallback-for makes collider defer."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    with caplog.at_level(logging.WARNING, logger='collider'):
        assert _force_fallback_args(sourcedir, ['--force-fallback-for=x']) == []
    assert 'collider will not force' in caplog.text


def test_force_args_defers_to_user_dash_d_spelling(tmp_path: Path, caplog) -> None:
    """A user -Dforce_fallback_for must also be detected (Meson rejects both spellings at once)."""
    sourcedir = tmp_path / 'project'
    _write_wrapped_foo_project(sourcedir)
    with caplog.at_level(logging.WARNING, logger='collider'):
        assert _force_fallback_args(sourcedir, ['-Dforce_fallback_for=x']) == []
    assert 'collider will not force' in caplog.text


def test_force_args_no_wraps_returns_empty(tmp_path: Path) -> None:
    """A project with no wraps yields no force argument."""
    sourcedir = tmp_path / 'project'
    sourcedir.mkdir()
    assert _force_fallback_args(sourcedir) == []


def test_validate_failure_invalid_version(tmp_path: Path, capfd: pytest.CaptureFixture):
    """Test _validate failure when project has an invalid version."""
    sourcedir = tmp_path / 'invalid_version_project'
    sourcedir.mkdir()
    (sourcedir / 'collider.json').write_text('{}')
    (sourcedir / 'meson.build').write_text("project('invalid', 'cpp', version: 'not-a-version')")
    builddir = tmp_path / 'invalid_build'

    assert (
        run_subcommand(
            Subcommand.SETUP, ['--sourcedir', str(sourcedir), '--builddir', str(builddir)]
        )
        == os.EX_DATAERR
    )
    assert not builddir.exists()
    stdout, stderr = capfd.readouterr()
    assert 'Project has invalid version' in stderr
    assert 'Could not validate project.' in stderr
