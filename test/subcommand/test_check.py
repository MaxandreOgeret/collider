# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the check subcommand."""

import argparse
import os

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.repository.entries import RepoPackageEntry
from collider.subcommand.Check import Check
from collider.utils.meson.scan import ScannedDependency
from collider.utils.packaging.Dependency import Dependency, DependencySource
from test.common.common import Subcommand, run_subcommand


def _make_project(tmp_path: Path, deps: list[Dependency]) -> None:
    """Write meson.build and collider.json with the given dependencies."""
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')
    Colliderfile(dependencies=deps).save(tmp_path / Colliderfile.get_filename())


def _run_check(tmp_path: Path, extra_args: list[str] | None = None) -> int:
    """Run collider check with --sourcedir pointing at tmp_path."""
    return run_subcommand(Subcommand.CHECK, ['--sourcedir', str(tmp_path), *(extra_args or [])])


def _mock_scan(deps: list[ScannedDependency]):
    """Patch scan_dependencies in the Check module."""
    return patch('collider.subcommand.Check.scan_dependencies', return_value=deps)


def _write_wrap(tmp_path: Path, stem: str, text: str) -> None:
    """Write a wrap file into <tmp_path>/subprojects, creating the directory if needed."""
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir(exist_ok=True)
    (subprojects / f'{stem}.wrap').write_text(text, encoding='utf-8')


def _provide_wrap(provides: list[str]) -> str:
    """Build [wrap-file] text whose [provide] section declares the given dependency names."""
    text = (
        '[wrap-file]\n'
        'source_url = https://example.invalid/x.tar.xz\n'
        'source_filename = x.tar.xz\n'
        'source_hash = deadbeef\n'
    )
    if provides:
        body = ''.join(f'{name} = {name.replace("-", "_")}_dep\n' for name in provides)
        text += f'\n[provide]\n{body}'
    return text


def _repo(*entries: RepoPackageEntry) -> SimpleNamespace:
    """Build a stub repository exposing the given package entries."""
    return SimpleNamespace(packages={f'{e.name}@{e.version}': e for e in entries})


def _make_check(
    tmp_path: Path,
    repos: dict | None = None,
    include_conditional: bool = False,
) -> Check:
    """Build a Check command bound to tmp_path with optional configured repositories."""
    config = MagicMock()
    config.repositories = repos or {}
    context = Context(config=config, cache=WrapCache(tmp_path / 'cache'), offline=False)
    args = argparse.Namespace(sourcedir=tmp_path, include_conditional=include_conditional)
    return Check(args, context)


def _index(tmp_path: Path, repos: dict | None = None) -> dict[str, str]:
    """Build the dependency-name resolution index for the given project state."""
    return _make_check(tmp_path, repos)._build_dependency_name_index()


def test_check_clean(tmp_path: Path) -> None:
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([ScannedDependency('fmt', required=True)]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_untracked(tmp_path: Path) -> None:
    """Dep used in meson.build but absent from collider.json is untracked."""
    _make_project(tmp_path, [])

    with _mock_scan([ScannedDependency('fmt', required=True)]):
        assert _run_check(tmp_path) == os.EX_DATAERR


def test_check_stale(tmp_path: Path) -> None:
    """Collider-managed dep in collider.json but absent from meson.build is stale."""
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([]):
        assert _run_check(tmp_path) == os.EX_DATAERR


def test_check_conditional_not_stale(tmp_path: Path) -> None:
    """Conditional dep tracked in collider.json must not be flagged stale.

    fmt appears in the raw scan (conditional=True) so it is NOT stale even
    though filter_dependencies drops it from .included when --include-conditional
    is not set.
    """
    _make_project(tmp_path, [Dependency('fmt', DependencySource.COLLIDER, None)])

    with _mock_scan([ScannedDependency('fmt', required=True, conditional=True)]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_system_dep_not_stale(tmp_path: Path) -> None:
    """System-source deps in collider.json are never reported as stale."""
    _make_project(tmp_path, [Dependency('zlib', DependencySource.SYSTEM, None)])

    with _mock_scan([]):
        assert _run_check(tmp_path) == os.EX_OK


def test_check_include_conditional(tmp_path: Path) -> None:
    """With --include-conditional, conditional deps count as untracked when absent."""
    _make_project(tmp_path, [])

    with _mock_scan([ScannedDependency('fmt', required=True, conditional=True)]):
        assert _run_check(tmp_path, ['--include-conditional']) == os.EX_DATAERR


def test_check_missing_meson_build(tmp_path: Path) -> None:
    Colliderfile().save(tmp_path / Colliderfile.get_filename())

    assert _run_check(tmp_path) == os.EX_NOINPUT


def test_check_missing_collider_json(tmp_path: Path) -> None:
    (tmp_path / 'meson.build').write_text('project("demo", "c")\n')

    assert _run_check(tmp_path) == os.EX_NOINPUT


# --- Resolution index: how dependency() names map to their owning package ---


def test_index_is_empty_without_wraps_or_repos(tmp_path: Path) -> None:
    assert _index(tmp_path) == {}


def test_index_maps_wrap_stem_and_named_provides(tmp_path: Path) -> None:
    _write_wrap(tmp_path, 'catch2', _provide_wrap(['catch2', 'catch2-with-main']))

    assert _index(tmp_path) == {'catch2': 'catch2', 'catch2-with-main': 'catch2'}


def test_index_expands_reserved_dependency_names(tmp_path: Path) -> None:
    _write_wrap(
        tmp_path,
        'catch2',
        '[wrap-file]\nsource_url = x\n\n[provide]\ndependency_names = catch2-with-main, catch2\n',
    )

    assert _index(tmp_path) == {'catch2': 'catch2', 'catch2-with-main': 'catch2'}


def test_index_reads_git_wrap_provides(tmp_path: Path) -> None:
    _write_wrap(
        tmp_path,
        'catch2',
        '[wrap-git]\nurl = https://example.invalid/catch2.git\n\n[provide]\ncatch2-with-main = m\n',
    )

    assert _index(tmp_path) == {'catch2': 'catch2', 'catch2-with-main': 'catch2'}


def test_index_excludes_program_names(tmp_path: Path) -> None:
    _write_wrap(
        tmp_path,
        'foo',
        '[wrap-file]\nsource_url = x\n\n[provide]\nfoo = foo_dep\nprogram_names = cmake\n',
    )

    assert _index(tmp_path) == {'foo': 'foo'}


def test_index_maps_stem_for_wrap_without_provide(tmp_path: Path) -> None:
    _write_wrap(tmp_path, 'zlib', _provide_wrap([]))

    assert _index(tmp_path) == {'zlib': 'zlib'}


def test_index_keeps_stem_when_provides_are_unparseable(tmp_path: Path) -> None:
    """A malformed wrap still maps its stem; only its unreadable provides are dropped."""
    _write_wrap(tmp_path, 'broken', 'not an ini file, no section header\n')

    assert _index(tmp_path) == {'broken': 'broken'}


def test_index_keeps_stem_for_undecodable_wrap(tmp_path: Path) -> None:
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'bad.wrap').write_bytes(b'\xff\xfe\x00not utf-8')

    assert _index(tmp_path) == {'bad': 'bad'}


def test_index_ignores_directory_named_like_a_wrap(tmp_path: Path) -> None:
    (tmp_path / 'subprojects').mkdir()
    (tmp_path / 'subprojects' / 'dir.wrap').mkdir()

    assert _index(tmp_path) == {}


def test_index_merges_multiple_wraps(tmp_path: Path) -> None:
    _write_wrap(tmp_path, 'fmt', _provide_wrap(['fmt']))
    _write_wrap(tmp_path, 'spdlog', _provide_wrap(['spdlog']))

    assert _index(tmp_path) == {'fmt': 'fmt', 'spdlog': 'spdlog'}


def test_index_fills_gaps_from_repo_metadata(tmp_path: Path) -> None:
    repos = {'r': _repo(RepoPackageEntry('catch2', '3.0.0', dependency_names=['catch2-with-main']))}

    assert _index(tmp_path, repos) == {'catch2-with-main': 'catch2'}


def test_index_prefers_installed_wrap_over_repo_metadata(tmp_path: Path) -> None:
    """An installed wrap's provide wins over conflicting repository metadata."""
    _write_wrap(tmp_path, 'catch2', _provide_wrap(['catch2-with-main']))
    repos = {'r': _repo(RepoPackageEntry('other', '1.0.0', dependency_names=['catch2-with-main']))}

    assert _index(tmp_path, repos)['catch2-with-main'] == 'catch2'


# --- Drift detection: where resolved names are used in the untracked/stale verdict ---


_DRIFT_SCENARIOS = [
    pytest.param(
        [('fmt', DependencySource.COLLIDER)],
        {},
        [('fmt', False)],
        False,
        os.EX_OK,
        id='direct-tracked-clean',
    ),
    pytest.param(
        [('fmt', DependencySource.COLLIDER)],
        {},
        [('spdlog', False)],
        False,
        os.EX_DATAERR,
        id='unrelated-dep-untracked-and-stale',
    ),
    pytest.param(
        [('catch2', DependencySource.COLLIDER)],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', False)],
        False,
        os.EX_OK,
        id='alias-resolves-to-tracked-package',
    ),
    pytest.param(
        [],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', False)],
        False,
        os.EX_DATAERR,
        id='alias-resolves-to-untracked-package',
    ),
    pytest.param(
        [('catch2-with-main', DependencySource.SYSTEM)],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', False)],
        False,
        os.EX_OK,
        id='alias-tracked-directly-as-system',
    ),
    pytest.param(
        [('catch2-with-main', DependencySource.COLLIDER)],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', False)],
        False,
        os.EX_OK,
        id='alias-tracked-directly-as-collider',
    ),
    pytest.param(
        [('fmt', DependencySource.COLLIDER)],
        {},
        [],
        False,
        os.EX_DATAERR,
        id='managed-unused-is-stale',
    ),
    pytest.param(
        [('foo', DependencySource.COLLIDER)],
        {'bar': ['foo']},
        [('foo', False)],
        False,
        os.EX_OK,
        id='managed-name-is-foreign-alias-used-directly',
    ),
    pytest.param(
        [('zlib', DependencySource.SYSTEM)],
        {},
        [],
        False,
        os.EX_OK,
        id='system-managed-never-stale',
    ),
    pytest.param(
        [('catch2', DependencySource.COLLIDER)],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', True)],
        False,
        os.EX_OK,
        id='conditional-alias-not-stale',
    ),
    pytest.param(
        [],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', True)],
        True,
        os.EX_DATAERR,
        id='include-conditional-resolves-untracked-alias',
    ),
    pytest.param(
        [('catch2', DependencySource.COLLIDER), ('fmt', DependencySource.COLLIDER)],
        {'catch2': ['catch2-with-main']},
        [('catch2-with-main', False), ('fmt', False)],
        False,
        os.EX_OK,
        id='multiple-mixed-clean',
    ),
]


@pytest.mark.parametrize(
    'collider_deps, wraps, scanned, include_conditional, expected', _DRIFT_SCENARIOS
)
def test_check_drift_resolution(
    tmp_path: Path,
    collider_deps: list[tuple[str, DependencySource]],
    wraps: dict[str, list[str]],
    scanned: list[tuple[str, bool]],
    include_conditional: bool,
    expected: int,
) -> None:
    """Resolved names drive the untracked/stale verdict across tracking and provide states."""
    _make_project(tmp_path, [Dependency(name, source, None) for name, source in collider_deps])
    for stem, provides in wraps.items():
        _write_wrap(tmp_path, stem, _provide_wrap(provides))
    cmd = _make_check(tmp_path, include_conditional=include_conditional)

    scanned_deps = [
        ScannedDependency(name, required=True, conditional=cond) for name, cond in scanned
    ]
    with _mock_scan(scanned_deps):
        assert cmd.execute() == expected


def test_check_resolves_alias_via_repo_metadata(tmp_path: Path) -> None:
    """With no installed wrap, repository metadata resolves the alias to its package."""
    _make_project(tmp_path, [Dependency('catch2', DependencySource.COLLIDER, None)])
    repos = {'r': _repo(RepoPackageEntry('catch2', '3.0.0', dependency_names=['catch2-with-main']))}
    cmd = _make_check(tmp_path, repos)

    with _mock_scan([ScannedDependency('catch2-with-main', required=True)]):
        assert cmd.execute() == os.EX_OK


def test_check_untracked_reports_resolved_package_name(tmp_path: Path, caplog) -> None:
    """An untracked alias is reported under its owning package, so `pkg add` is actionable."""
    _make_project(tmp_path, [])
    _write_wrap(tmp_path, 'catch2', _provide_wrap(['catch2-with-main']))

    with _mock_scan([ScannedDependency('catch2-with-main', required=True)]):
        assert _run_check(tmp_path) == os.EX_DATAERR

    assert 'collider pkg add catch2' in caplog.text
    assert 'catch2-with-main' not in caplog.text


def test_check_resolves_provide_from_installed_wrap(tmp_path: Path) -> None:
    """End-to-end: an installed wrap's [provide] alias keeps `collider check` clean."""
    _make_project(tmp_path, [Dependency('catch2', DependencySource.COLLIDER, None)])
    _write_wrap(tmp_path, 'catch2', _provide_wrap(['catch2-with-main']))

    with _mock_scan([ScannedDependency('catch2-with-main', required=True)]):
        assert _run_check(tmp_path) == os.EX_OK
