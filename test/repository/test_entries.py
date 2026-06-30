# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for releases.json indexing and its fail-soft rejection of malformed metadata."""

from collider.repository.entries import RejectReason, packages_from_releases


def test_well_formed_releases_index_without_rejects() -> None:
    """A clean releases map indexes every version and rejects nothing."""
    packages, rejected = packages_from_releases(
        {'foo': {'versions': ['1.0.0', '2.0.0'], 'dependency_names': ['libfoo']}}
    )
    assert len(packages) == 2
    assert rejected == []


def test_non_dict_releases_is_rejected_not_raised() -> None:
    """A releases.json that is not a JSON object yields no packages and a structural reject."""
    packages, rejected = packages_from_releases(['not', 'an', 'object'])  # type: ignore[arg-type]
    assert packages == {}
    assert [r.reason for r in rejected] == [RejectReason.STRUCTURE]


def test_entry_not_dict_is_rejected_individually() -> None:
    """A single bad entry is dropped while the rest of the repo still indexes."""
    packages, rejected = packages_from_releases(
        {'good': {'versions': ['1.0.0']}, 'bad': 'oops'}  # type: ignore[dict-item]
    )
    assert {e.name for e in packages.values()} == {'good'}
    assert len(rejected) == 1
    assert rejected[0].name == 'bad'
    assert rejected[0].reason is RejectReason.STRUCTURE


def test_versions_not_list_is_structural_reject() -> None:
    """An entry whose versions field is not a list is a structural reject, not a crash."""
    packages, rejected = packages_from_releases({'foo': {'versions': 'oops'}})  # type: ignore[dict-item]
    assert packages == {}
    assert rejected[0].reason is RejectReason.STRUCTURE


def test_unsafe_name_recorded_as_unsafe_name() -> None:
    """A traversal name is dropped at the boundary and recorded as UNSAFE_NAME."""
    packages, rejected = packages_from_releases({'../../evil': {'versions': ['1.0.0']}})
    assert packages == {}
    assert rejected[0].reason is RejectReason.UNSAFE_NAME


def test_unsafe_version_keeps_safe_siblings() -> None:
    """An unsafe version is dropped while safe versions of the same package survive."""
    packages, rejected = packages_from_releases(
        {'foo': {'versions': ['1.0.0', 'a/b'], 'dependency_names': ['libfoo']}}
    )
    assert {e.version for e in packages.values()} == {'1.0.0'}
    assert rejected[0].reason is RejectReason.UNSAFE_VERSION


def test_malformed_dep_names_do_not_crash_indexing() -> None:
    """A non-list dependency_names is ignored for the valid entry, not indexed as provides."""
    packages, rejected = packages_from_releases(
        {'foo': {'versions': ['1.0.0'], 'dependency_names': 'libfoo'}}  # type: ignore[dict-item]
    )
    # The version still indexes; the malformed provides are simply dropped (no crash).
    assert {e.version for e in packages.values()} == {'1.0.0'}
    assert next(iter(packages.values())).dependency_names is None
    assert rejected == []
