# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for collider.utils.packaging."""

import pytest

from packaging.specifiers import InvalidSpecifier

from collider.file_model.colliderfile import Colliderfile
from collider.utils.packaging import parse_version_constraint, validate_dependencies
from collider.utils.packaging.Dependency import Dependency, DependencySource


def test_parse_version_constraint_promotes_bare_version_to_exact_pin() -> None:
    """A bare version is treated as an exact (==) pin instead of being rejected."""
    assert str(parse_version_constraint('1.2.13')) == '==1.2.13'


def test_parse_version_constraint_preserves_explicit_specifiers() -> None:
    """A constraint that already carries operators is parsed unchanged."""
    assert str(parse_version_constraint('>=1,<2')) == '<2,>=1'


def test_parse_version_constraint_rejects_unparseable_text() -> None:
    """Text that is neither a valid specifier nor a bare version raises InvalidSpecifier."""
    with pytest.raises(InvalidSpecifier):
        parse_version_constraint('not-a-version')


def test_parse_version_constraint_rejects_empty_text() -> None:
    """An empty or whitespace-only constraint is rejected rather than matching everything."""
    with pytest.raises(InvalidSpecifier):
        parse_version_constraint('   ')


def test_parse_version_constraint_prefix_promotes_bare_version_to_prefix_match() -> None:
    """In prefix mode a bare version matches every revision-suffixed release."""
    spec = parse_version_constraint('1.2.13', prefix=True)
    assert str(spec) == '==1.2.13.*'
    assert spec.contains('1.2.13-1')


def test_parse_version_constraint_prefix_falls_back_to_exact_for_suffixed_version() -> None:
    """A bare version that cannot form a prefix match (e.g. a full tag) pins exactly."""
    assert str(parse_version_constraint('1.2.13-1', prefix=True)) == '==1.2.13-1'


def test_parse_version_constraint_prefix_leaves_explicit_specifiers_untouched() -> None:
    """Prefix mode does not alter a constraint that already carries an operator."""
    assert str(parse_version_constraint('>=1.0.0', prefix=True)) == '>=1.0.0'


def test_validate_dependencies_subproject_names_not_superfluous() -> None:
    """A Colliderfile dep that is only in subproject_names is not considered superfluous."""
    colliderfile = Colliderfile(
        dependencies=[Dependency('tclap', DependencySource.COLLIDER, '1.2.4-4')]
    )
    dependencies_info: list[dict] = []
    subproject_names = {'tclap'}

    result = validate_dependencies(
        colliderfile, dependencies_info, subproject_names=subproject_names
    )

    assert result is True
