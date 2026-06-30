# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for project-state helpers."""

from pathlib import Path

import pytest

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile, compute_wrap_hash
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.project_state import (
    collect_force_fallback_names,
    detect_locked_wrap_drift,
    managed_package_names,
)


_ORIGIN = 'https://wrapdb.example.com/v2/'
_HASH = 'sha256:' + 'a' * 64


_WRAP_FILE = """\
[wrap-file]
directory = {directory}
source_url = https://example.invalid/{directory}.tar.gz
source_filename = {directory}.tar.gz
source_hash = {hash}
"""


def _wrap_file_text(directory: str, provides: dict[str, str] | None = None) -> str:
    """Build well-formed [wrap-file] text, optionally with a [provide] section."""
    text = _WRAP_FILE.format(directory=directory, hash='0' * 64)
    if provides:
        text += '\n[provide]\n' + ''.join(f'{name} = {var}\n' for name, var in provides.items())
    return text


def _write_wrap(subprojects: Path, stem: str, text: str) -> None:
    """Write a wrap file into a subprojects directory, creating it if needed."""
    subprojects.mkdir(parents=True, exist_ok=True)
    (subprojects / f'{stem}.wrap').write_text(text, encoding='utf-8')


def test_collect_returns_stem_and_provide_keys(tmp_path: Path) -> None:
    """A wrap contributes both its stem and every name in its [provide] section."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(
        subprojects,
        'catch2',
        _wrap_file_text(
            'Catch2-3.4.0',
            {'catch2': 'catch2_dep', 'catch2-with-main': 'catch2_with_main_dep'},
        ),
    )
    assert collect_force_fallback_names(subprojects) == ['catch2', 'catch2-with-main']


def test_collect_unions_multiple_wraps(tmp_path: Path) -> None:
    """Names from every present wrap are merged and sorted."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(subprojects, 'fmt', _wrap_file_text('fmt-11', {'fmt': 'fmt_dep'}))
    _write_wrap(subprojects, 'spdlog', _wrap_file_text('spdlog-1.14', {'spdlog': 'spdlog_dep'}))
    assert collect_force_fallback_names(subprojects) == ['fmt', 'spdlog']


def test_collect_wrap_without_provide_uses_stem(tmp_path: Path) -> None:
    """A wrap with no [provide] section still forces its subproject by stem."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(subprojects, 'zlib', _wrap_file_text('zlib-1.3'))
    assert collect_force_fallback_names(subprojects) == ['zlib']


def test_collect_non_wrap_file_uses_stem_only(tmp_path: Path) -> None:
    """Redirect/git wraps lack [wrap-file]; the stem alone forces the subproject."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(
        subprojects,
        'mydep',
        '[wrap-git]\nurl = https://example.invalid/mydep.git\nrevision = head\n',
    )
    assert collect_force_fallback_names(subprojects) == ['mydep']


def test_collect_git_wrap_with_provide_includes_aliases(tmp_path: Path) -> None:
    """A git/redirect wrap carrying a [provide] section forces both its stem and its aliases."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(
        subprojects,
        'catch2',
        '[wrap-git]\n'
        'url = https://example.invalid/catch2.git\n'
        'revision = head\n'
        '\n[provide]\n'
        'catch2-with-main = catch2_with_main_dep\n',
    )
    assert collect_force_fallback_names(subprojects) == ['catch2', 'catch2-with-main']


def test_collect_malformed_ini_wrap_uses_stem_only(tmp_path: Path) -> None:
    """A wrap that is not parseable INI (no section header) is tolerated; the stem forces it."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(subprojects, 'broken', 'not an ini file, no section header\n')
    assert collect_force_fallback_names(subprojects) == ['broken']


def test_collect_missing_directory_returns_empty(tmp_path: Path) -> None:
    """A non-existent subprojects directory yields no names."""
    assert collect_force_fallback_names(tmp_path / 'nope') == []


def test_collect_empty_subprojects_returns_empty(tmp_path: Path) -> None:
    """A subprojects directory with no wraps yields no names."""
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    assert collect_force_fallback_names(subprojects) == []


def test_collect_restricts_to_managed_packages(tmp_path: Path) -> None:
    """When a managed set is given, an unmanaged (e.g. hand-placed) wrap is left alone."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(subprojects, 'fmt', _wrap_file_text('fmt-11', {'fmt': 'fmt_dep'}))
    _write_wrap(subprojects, 'bar', _wrap_file_text('bar-1', {'bar': 'bar_dep'}))
    assert collect_force_fallback_names(subprojects, {'fmt'}) == ['fmt']


def test_collect_empty_managed_set_forces_nothing(tmp_path: Path) -> None:
    """An empty managed set forces nothing, even with wraps present."""
    subprojects = tmp_path / 'subprojects'
    _write_wrap(subprojects, 'fmt', _wrap_file_text('fmt-11', {'fmt': 'fmt_dep'}))
    assert collect_force_fallback_names(subprojects, set()) == []


def test_managed_names_returns_none_without_lock(tmp_path: Path) -> None:
    """No lockfile means the managed set is unknown, signalling a wrap-presence fallback."""
    assert managed_package_names(tmp_path) is None


def test_managed_names_unions_lock_and_colliderfile(tmp_path: Path) -> None:
    """The managed set merges lock direct + transitive packages with colliderfile direct deps."""
    Lockfile(
        dependencies={'foo': LockedPackage(version='1.0', wrap_hash=_HASH, origin=_ORIGIN)},
        packages={'fmt': LockedPackage(version='11.0', wrap_hash=_HASH, origin=_ORIGIN)},
    ).save(tmp_path / Lockfile.get_filename())
    Colliderfile(dependencies=[Dependency('baz', DependencySource.COLLIDER, '1.0')]).save(
        tmp_path / Colliderfile.get_filename()
    )
    assert managed_package_names(tmp_path) == {'foo', 'fmt', 'baz'}


def test_managed_names_raises_on_malformed_lock(tmp_path: Path) -> None:
    """A malformed lockfile is a hard error, not a silent fallback."""
    (tmp_path / Lockfile.get_filename()).write_text('{ not valid json', encoding='utf-8')
    with pytest.raises(ValueError):
        managed_package_names(tmp_path)


def test_managed_names_tolerates_malformed_colliderfile(tmp_path: Path) -> None:
    """A valid lock with a corrupt colliderfile yields the lock-only managed set."""
    Lockfile(
        dependencies={'foo': LockedPackage(version='1.0', wrap_hash=_HASH, origin=_ORIGIN)},
    ).save(tmp_path / Lockfile.get_filename())
    (tmp_path / Colliderfile.get_filename()).write_text('{ not valid json', encoding='utf-8')
    assert managed_package_names(tmp_path) == {'foo'}


def _lock_with(tmp_path: Path, **packages: str) -> None:
    """Save a lockfile pinning each package name to the given wrap_hash."""
    Lockfile(
        dependencies={
            name: LockedPackage(version='1.0', wrap_hash=wrap_hash, origin=_ORIGIN)
            for name, wrap_hash in packages.items()
        }
    ).save(tmp_path / Lockfile.get_filename())


def test_drift_returns_empty_without_lock(tmp_path: Path) -> None:
    """No lock means nothing to compare against, even when a wrap is present."""
    _write_wrap(tmp_path / 'subprojects', 'foo', _wrap_file_text('foo'))
    assert detect_locked_wrap_drift(tmp_path) == []


def test_drift_empty_when_hash_matches(tmp_path: Path) -> None:
    """A wrap whose bytes match the locked hash is not drift."""
    text = _wrap_file_text('foo')
    _write_wrap(tmp_path / 'subprojects', 'foo', text)
    _lock_with(tmp_path, foo=compute_wrap_hash(text))
    assert detect_locked_wrap_drift(tmp_path) == []


def test_drift_detects_modified_wrap(tmp_path: Path) -> None:
    """A wrap whose bytes differ from the locked hash is reported as drift."""
    _write_wrap(tmp_path / 'subprojects', 'foo', _wrap_file_text('foo'))
    _lock_with(tmp_path, foo='sha256:' + '0' * 64)
    assert detect_locked_wrap_drift(tmp_path) == ['foo']


def test_drift_ignores_missing_wrap(tmp_path: Path) -> None:
    """A locked package with no wrap on disk is not drift (missing is a separate concern)."""
    _lock_with(tmp_path, foo='sha256:' + '0' * 64)
    assert detect_locked_wrap_drift(tmp_path) == []


def test_drift_reports_only_modified_sorted(tmp_path: Path) -> None:
    """Only mismatching wraps are reported, sorted, with matching ones excluded."""
    subprojects = tmp_path / 'subprojects'
    fmt_text = _wrap_file_text('fmt')
    _write_wrap(subprojects, 'fmt', fmt_text)
    _write_wrap(subprojects, 'bar', _wrap_file_text('bar'))
    _write_wrap(subprojects, 'baz', _wrap_file_text('baz'))
    _lock_with(
        tmp_path,
        fmt=compute_wrap_hash(fmt_text),  # matches: not drift
        bar='sha256:' + '0' * 64,  # drift
        baz='sha256:' + '1' * 64,  # drift
    )
    assert detect_locked_wrap_drift(tmp_path) == ['bar', 'baz']


def test_drift_treats_non_utf8_wrap_as_drift(tmp_path: Path) -> None:
    """A non-UTF-8 wrap cannot match a UTF-8-hashed lock entry, so it counts as drift."""
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(parents=True)
    (subprojects / 'foo.wrap').write_bytes(b'\xff\xfe[wrap-file]\x00')
    _lock_with(tmp_path, foo='sha256:' + '0' * 64)
    assert detect_locked_wrap_drift(tmp_path) == ['foo']


def test_drift_raises_on_malformed_lock(tmp_path: Path) -> None:
    """A malformed lock is a hard error, mirroring managed_package_names."""
    (tmp_path / Lockfile.get_filename()).write_text('{ not valid json', encoding='utf-8')
    with pytest.raises(ValueError, match='malformed'):
        detect_locked_wrap_drift(tmp_path)
