# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import dataclasses
import importlib
import sys
import types

from enum import Enum
from typing import Dict, List, Optional, Union
from unittest.mock import patch

import pytest

import collider.utils.dataclass as dataclass_module

from collider.file_model.lockfile import LockedPackage, Lockfile
from collider.utils.dataclass import (
    _get_inner_type,
    _make_json_serializable,
    _type_hint_contains,
    prepare_ctor_kwargs,
    to_json_dict,
)


class MyEnum(Enum):
    A = 'a'
    B = 'b'


@dataclasses.dataclass
class Nested:
    val: int


@dataclasses.dataclass
class ComplexDC:
    opt_int: Optional[int]
    union_val: Union[int, str]
    list_nested: List[Nested]
    dict_val: Dict[str, int]
    my_enum: MyEnum


def test_to_json_dict_success():
    inst = ComplexDC(
        opt_int=None,
        union_val='hello',
        list_nested=[Nested(val=1)],
        dict_val={'key': 2},
        my_enum=MyEnum.A,
    )
    result = to_json_dict(inst)
    assert result == {
        'opt_int': None,
        'union_val': 'hello',
        'list_nested': [{'val': 1}],
        'dict_val': {'key': 2},
        'my_enum': 'a',
    }

    # Test exclude_none
    result_exclude = to_json_dict(inst, exclude_none=True)
    assert result_exclude == {
        'union_val': 'hello',
        'list_nested': [{'val': 1}],
        'dict_val': {'key': 2},
        'my_enum': 'a',
    }


def test_to_json_dict_fail_non_dataclass():
    with pytest.raises(TypeError, match='Expected a dataclass instance'):
        to_json_dict('not a dataclass')


def test_make_json_serializable_fail_unsupported():
    with pytest.raises(TypeError, match='Cannot serialize type set'):
        _make_json_serializable({1, 2, 3})


def test_make_json_serializable_fail_dict_key():
    with pytest.raises(TypeError, match='Dictionary keys must be strings'):
        _make_json_serializable({1: 'val'})


def test_get_inner_type_fails():
    with pytest.raises(TypeError, match='is not parameterized'):
        _get_inner_type(int)

    with pytest.raises(TypeError, match='is not supported'):
        # origin is Union, not list or dict
        _get_inner_type(Optional[int])  # ty:ignore[invalid-argument-type]


def test_type_hint_contains_complex():
    assert _type_hint_contains(Optional[int], type(None), None)
    assert _type_hint_contains(Optional[int], int, 0)
    assert not _type_hint_contains(Optional[int], str, '')
    assert _type_hint_contains(List[int], list, [])
    assert not _type_hint_contains(List[int], int, 0)
    assert not _type_hint_contains(int, List[int], [0])


def test_prepare_ctor_kwargs_fail_input():
    with pytest.raises(TypeError, match='Expected a dict'):
        prepare_ctor_kwargs([], ComplexDC)  # type: ignore


def test_prepare_ctor_kwargs_fail_target():
    with pytest.raises(TypeError, match='Expected a dataclass object'):
        prepare_ctor_kwargs({}, int)  # type: ignore


def test_deserialize_value_dict_fail_key():
    from collider.utils.dataclass import _deserialize_value

    with pytest.raises(TypeError, match='Dictionary keys must be strings'):
        _deserialize_value({1: 'val'}, Dict[str, str])


@dataclasses.dataclass
class WithFactory:
    val: int = dataclasses.field(default_factory=lambda: 42)


def test_deserialize_value_factory():
    from collider.utils.dataclass import _deserialize_value

    # value is None, factory is provided
    res = _deserialize_value(None, int, factory=lambda: 42)
    assert res == 42


def test_to_json_dict_nested_none_handling():
    """Test nested dataclasses None handling."""

    @dataclasses.dataclass
    class NestedDC:
        inner: Optional[str] = None

    @dataclasses.dataclass
    class RootDC:
        nested: NestedDC
        top: Optional[int] = None

    inst = RootDC(nested=NestedDC(inner=None), top=None)

    # Keep None
    res_keep = to_json_dict(inst, exclude_none=False)
    assert res_keep == {'nested': {'inner': None}, 'top': None}

    # Exclude None
    res_exclude = to_json_dict(inst, exclude_none=True)
    assert res_exclude == {'nested': {}}


def test_prepare_ctor_kwargs_type_mismatch():
    """Test type mismatch error in prepare_ctor_kwargs."""

    @dataclasses.dataclass
    class IntDC:
        val: int

    with pytest.raises(TypeError, match='Type mismatch for field "val"'):
        prepare_ctor_kwargs({'val': 'not an int'}, IntDC)


def test_deserialize_value_union_handling():
    """Test Union (non-Optional) handling in _deserialize_value."""
    from collider.utils.dataclass import _deserialize_value

    # Union[int, str] with int value
    assert _deserialize_value(10, Union[int, str]) == 10
    # Union[int, str] with str value
    assert _deserialize_value('ten', Union[int, str]) == 'ten'


def test_deserialize_value_dict_parameterized():
    """Test parameterized dict in _deserialize_value."""
    from collider.utils.dataclass import _deserialize_value

    data = {'a': '1', 'b': '2'}
    res = _deserialize_value(data, Dict[str, int])
    # Note: _deserialize_value recursively calls itself for inner types.
    # _get_inner_type(Dict[str, int]) returns int.
    # But _deserialize_value for int returns the value as is if it's not a list/dict/etc.
    # Actually, it doesn't do type casting, it just does structural conversion.
    # So "1" remains "1" if it's not converted.
    assert res == {'a': '1', 'b': '2'}


def test_dataclass_module_imports_without_enumtype():
    fake_enum = types.ModuleType('enum')
    fake_enum.__dict__.update(sys.modules['enum'].__dict__)
    fake_enum.__dict__.pop('EnumType', None)

    with patch.dict(sys.modules, {'enum': fake_enum}):
        importlib.reload(dataclass_module)
        assert dataclass_module.EnumType is fake_enum.EnumMeta

    importlib.reload(dataclass_module)


def test_prepare_ctor_kwargs_handles_generic_alias_when_isclass_is_true():
    dependency_type = next(
        field.type for field in dataclasses.fields(Lockfile) if field.name == 'dependencies'
    )
    original_isclass = dataclass_module.isclass

    def fake_isclass(value):
        if value == dependency_type:
            return True
        return original_isclass(value)

    with patch.object(dataclass_module, 'isclass', side_effect=fake_isclass):
        kwargs = prepare_ctor_kwargs(
            {
                'version': 1,
                'dependencies': {
                    'shared': {
                        'version': '1.0',
                        'wrap_hash': 'sha256:' + 'a' * 64,
                        'origin': 'https://wrapdb.example.com/v2/',
                    }
                },
                'packages': {},
            },
            Lockfile,
        )

    assert isinstance(kwargs['dependencies']['shared'], LockedPackage)
