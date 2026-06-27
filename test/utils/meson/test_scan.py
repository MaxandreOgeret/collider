# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Tests for meson introspect --scan-dependencies wrapper."""

import json
import subprocess

from pathlib import Path
from unittest.mock import patch

import pytest

from collider.utils.meson import scan


@pytest.fixture(autouse=True)
def _skip_meson_validation(monkeypatch):
    """Prevent scan_dependencies from invoking the real meson binary."""
    monkeypatch.setattr(scan, '_meson_validated', True)


SCAN_OUTPUT_GRPC = json.dumps(
    [
        {
            'name': 're2',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
        {
            'name': 'protobuf',
            'required': True,
            'version': ['>=3.21'],
            'has_fallback': True,
            'conditional': False,
        },
        {
            'name': 'openssl',
            'required': False,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
        {
            'name': 'libbaz',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': True,
        },
    ]
)

SCAN_OUTPUT_WITH_EMPTY_NAME = json.dumps(
    [
        {
            'name': 'zlib',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
        {'name': '', 'required': False, 'version': [], 'has_fallback': False, 'conditional': False},
        {
            'name': 're2',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
    ]
)

SCAN_OUTPUT_MISSING_NAME_KEY = json.dumps(
    [
        {'required': True, 'version': [], 'has_fallback': False, 'conditional': False},
        {
            'name': 'zlib',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
    ]
)

SCAN_OUTPUT_EMPTY = json.dumps([])

SCAN_OUTPUT_SINGLE = json.dumps(
    [
        {
            'name': 'zlib',
            'required': True,
            'version': [],
            'has_fallback': False,
            'conditional': False,
        },
    ]
)


@pytest.fixture()
def _all_deps(monkeypatch, tmp_path: Path) -> list[scan.ScannedDependency]:
    """Parse SCAN_OUTPUT_GRPC through scan_dependencies for use in filter tests."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_GRPC

    monkeypatch.setattr(scan.command, 'run', fake_run)
    return scan.scan_dependencies(meson_build)


def test_scan_parses_multiple_dependencies(monkeypatch, tmp_path: Path) -> None:
    """Multiple entries are parsed into ScannedDependency instances."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_GRPC

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    assert len(result) == 4
    assert result[0].name == 're2'
    assert result[0].required is True
    assert result[0].has_fallback is False
    assert result[0].conditional is False


def test_scan_parses_version_constraints(monkeypatch, tmp_path: Path) -> None:
    """Version constraint strings are preserved on the dependency."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_GRPC

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    protobuf = next(d for d in result if d.name == 'protobuf')
    assert protobuf.version == ['>=3.21']
    assert protobuf.has_fallback is True


def test_scan_empty_result(monkeypatch, tmp_path: Path) -> None:
    """An empty scan output produces an empty list."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_EMPTY

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    assert result == []


def test_scan_single_dependency(monkeypatch, tmp_path: Path) -> None:
    """A single-entry scan output produces a one-element list."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_SINGLE

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    assert len(result) == 1
    assert result[0].name == 'zlib'


def test_scan_filters_out_empty_names(monkeypatch, tmp_path: Path) -> None:
    """Entries with empty-string names are silently discarded."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_WITH_EMPTY_NAME

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    names = [d.name for d in result]
    assert '' not in names
    assert len(result) == 2
    assert names == ['zlib', 're2']


def test_scan_filters_out_missing_name_key(monkeypatch, tmp_path: Path) -> None:
    """Entries without a 'name' key at all are silently discarded."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        return SCAN_OUTPUT_MISSING_NAME_KEY

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_dependencies(meson_build)

    assert len(result) == 1
    assert result[0].name == 'zlib'


def test_scan_raises_on_missing_meson_build(tmp_path: Path) -> None:
    """FileNotFoundError is raised when the file does not exist."""
    missing = tmp_path / 'no_such_file' / 'meson.build'
    with pytest.raises(FileNotFoundError):
        scan.scan_dependencies(missing)


def test_scan_raises_on_meson_failure(monkeypatch, tmp_path: Path) -> None:
    """CalledProcessError propagates when meson introspect fails."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(scan.command, 'run', fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        scan.scan_dependencies(meson_build)


def test_scan_passes_correct_args_to_meson(monkeypatch, tmp_path: Path) -> None:
    """The correct meson introspect command line is constructed."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")
    captured_args = []

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        return SCAN_OUTPUT_EMPTY

    monkeypatch.setattr(scan.command, 'run', fake_run)
    scan.scan_dependencies(meson_build)

    assert captured_args[0] == 'meson'
    assert captured_args[1] == 'introspect'
    assert captured_args[2] == '--scan-dependencies'
    assert captured_args[3] == str(meson_build)


def test_scan_project_info_parses_output(monkeypatch, tmp_path: Path) -> None:
    """Valid introspect output is parsed and the projectinfo command is used."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")
    captured_args = []

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        return json.dumps({'descriptive_name': 'test', 'license': ['MIT']})

    monkeypatch.setattr(scan.command, 'run', fake_run)
    result = scan.scan_project_info(meson_build)

    assert result == {'descriptive_name': 'test', 'license': ['MIT']}
    assert captured_args[:3] == ['meson', 'introspect', '--projectinfo']
    assert captured_args[3] == str(meson_build)


def test_scan_project_info_returns_none_on_failure(monkeypatch, tmp_path: Path) -> None:
    """Any introspection error degrades to None instead of raising."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(scan.command, 'run', fake_run)
    assert scan.scan_project_info(meson_build) is None


def test_scan_project_info_returns_none_on_empty_output(monkeypatch, tmp_path: Path) -> None:
    """Empty introspect output yields None rather than a parse error."""
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    monkeypatch.setattr(scan.command, 'run', lambda args, **kwargs: '')
    assert scan.scan_project_info(meson_build) is None


def test_filter_default_includes_required(_all_deps) -> None:
    """Required dependencies are included by default."""
    result = scan.filter_dependencies(_all_deps)
    names = [d.name for d in result.included]
    assert 're2' in names


def test_filter_default_includes_optional(_all_deps) -> None:
    """Optional dependencies are included by default and tracked as such."""
    result = scan.filter_dependencies(_all_deps)
    names = [d.name for d in result.included]
    assert 'openssl' in names
    assert 'openssl' in result.included_optional


def test_filter_default_includes_fallback(_all_deps) -> None:
    """Dependencies with fallback are included by default."""
    result = scan.filter_dependencies(_all_deps)
    names = [d.name for d in result.included]
    assert 'protobuf' in names


def test_filter_default_excludes_conditional(_all_deps) -> None:
    """Conditional dependencies are excluded by default and tracked."""
    result = scan.filter_dependencies(_all_deps)
    names = [d.name for d in result.included]
    assert 'libbaz' not in names
    assert 'libbaz' in result.skipped_conditional


def test_filter_include_conditional(_all_deps) -> None:
    """Conditional deps are included when the flag is set."""
    result = scan.filter_dependencies(_all_deps, include_conditional=True)
    names = [d.name for d in result.included]
    assert 'libbaz' in names


def test_filter_exclude_optional(_all_deps) -> None:
    """Optional deps are excluded when the flag is set."""
    result = scan.filter_dependencies(_all_deps, exclude_optional=True)
    names = [d.name for d in result.included]
    assert 'openssl' not in names
    assert 're2' in names
    assert 'openssl' in result.skipped_optional


def test_filter_exclude_optional_keeps_fallback(_all_deps) -> None:
    """Optional deps with fallback survive exclude_optional."""
    result = scan.filter_dependencies(_all_deps, exclude_optional=True)
    names = [d.name for d in result.included]
    assert 'protobuf' in names


def test_filter_explicit_include_overrides_conditional(_all_deps) -> None:
    """An explicit include_names entry overrides the conditional flag."""
    result = scan.filter_dependencies(_all_deps, include_names={'libbaz'})
    names = [d.name for d in result.included]
    assert 'libbaz' in names


def test_filter_explicit_exclude_overrides_required(_all_deps) -> None:
    """An explicit exclude_names entry overrides required status."""
    result = scan.filter_dependencies(_all_deps, exclude_names={'re2'})
    names = [d.name for d in result.included]
    assert 're2' not in names


def test_filter_explicit_overrides_take_precedence_over_flags(_all_deps) -> None:
    """Exclude by name wins over include_conditional flag."""
    result = scan.filter_dependencies(
        _all_deps,
        include_conditional=True,
        exclude_names={'libbaz'},
    )
    names = [d.name for d in result.included]
    assert 'libbaz' not in names


def test_filter_include_name_overrides_exclude_optional(_all_deps) -> None:
    """Include by name wins over exclude_optional flag."""
    result = scan.filter_dependencies(
        _all_deps,
        exclude_optional=True,
        include_names={'openssl'},
    )
    names = [d.name for d in result.included]
    assert 'openssl' in names


def test_filter_system_dependencies() -> None:
    """Well-known system deps are silently dropped."""
    deps = [
        scan.ScannedDependency(name='threads', required=True),
        scan.ScannedDependency(name='appleframeworks', required=False),
        scan.ScannedDependency(name='zlib', required=True),
        scan.ScannedDependency(name='openmp', required=True),
    ]
    result = scan.filter_dependencies(deps)
    names = [d.name for d in result.included]
    assert 'threads' not in names
    assert 'appleframeworks' not in names
    assert 'openmp' not in names
    assert 'zlib' in names


def test_filter_system_dep_filtered_even_if_in_exclude_names() -> None:
    """System deps are removed before exclude_names is checked."""
    deps = [
        scan.ScannedDependency(name='threads', required=True),
        scan.ScannedDependency(name='zlib', required=True),
    ]
    result = scan.filter_dependencies(deps, exclude_names={'threads'})
    names = [d.name for d in result.included]
    assert 'threads' not in names
    assert 'zlib' in names


def test_filter_system_dep_not_force_included() -> None:
    """System deps cannot be force-included via include_names."""
    deps = [
        scan.ScannedDependency(name='threads', required=True),
        scan.ScannedDependency(name='zlib', required=True),
    ]
    result = scan.filter_dependencies(deps, include_names={'threads'})
    names = [d.name for d in result.included]
    assert 'threads' not in names
    assert 'zlib' in names


def test_filter_metadata_tracks_conditional_and_optional() -> None:
    """FilterResult collects skipped-conditional and included-optional metadata."""
    deps = [
        scan.ScannedDependency(name='foo', required=True, conditional=True),
        scan.ScannedDependency(name='bar', required=True, conditional=True),
        scan.ScannedDependency(name='opt', required=False),
        scan.ScannedDependency(name='zlib', required=True),
    ]
    result = scan.filter_dependencies(deps)
    assert 'foo' in result.skipped_conditional
    assert 'bar' in result.skipped_conditional
    assert 'opt' in result.included_optional
    assert len(result.included) == 2


def test_scan_validates_meson_on_first_call(monkeypatch, tmp_path: Path) -> None:
    """scan_dependencies calls meson.validate() on first invocation."""
    monkeypatch.setattr(scan, '_meson_validated', False)
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    monkeypatch.setattr(scan.command, 'run', lambda args, **kw: SCAN_OUTPUT_EMPTY)

    validate_called = False

    def _fake_validate():
        nonlocal validate_called
        validate_called = True

    with patch.object(scan._meson_mod, 'validate', _fake_validate):
        scan.scan_dependencies(meson_build)

    assert validate_called
    assert scan._meson_validated


def test_scan_skips_validation_on_subsequent_calls(monkeypatch, tmp_path: Path) -> None:
    """Once validated, scan_dependencies does not re-validate."""
    monkeypatch.setattr(scan, '_meson_validated', True)
    meson_build = tmp_path / 'meson.build'
    meson_build.write_text("project('test', 'c')")

    monkeypatch.setattr(scan.command, 'run', lambda args, **kw: SCAN_OUTPUT_EMPTY)

    validate_called = False

    def _fake_validate():
        nonlocal validate_called
        validate_called = True

    with patch.object(scan._meson_mod, 'validate', _fake_validate):
        scan.scan_dependencies(meson_build)

    assert not validate_called
