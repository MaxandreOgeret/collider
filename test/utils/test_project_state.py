# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for project-state helpers."""

from pathlib import Path

import pytest

from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.project_state import collect_force_fallback_names, managed_package_names


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
