# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Dataclass helpers that keep config serialization and validation consistent."""

import dataclasses

from abc import ABCMeta
from inspect import isclass
from types import NoneType
from typing import Any, Callable, Optional, Type, Union, get_args, get_origin

from collider.utils.Registry import Registry, is_registry_value


try:
    from enum import Enum, EnumType  # ty: ignore[unresolved-import]
except ImportError:
    from enum import Enum, EnumMeta

    EnumType = EnumMeta


# JSON-compatible primitive types.
BASIC_TYPES = (str, int, float, bool, type(None), list, dict)


def _make_json_serializable(value: Any, exclude_none: bool = False) -> Any:
    """Normalize values into JSON-safe primitives for schema-validated output."""
    if value is None:
        return None

    if isinstance(value, Enum):
        return str(value.value)

    if isinstance(value, list):
        return [
            _make_json_serializable(item, exclude_none)
            for item in value
            if not (exclude_none and item is None)
        ]

    if isinstance(value, dict):
        for key in value.keys():
            if not isinstance(key, str):
                raise TypeError(
                    'Dictionary keys must be strings for JSON serialization. '
                    f'Found key {key!r} of type {type(key).__name__}'
                )
        return {
            key: _make_json_serializable(val, exclude_none)
            for key, val in value.items()
            if not (exclude_none and val is None)
        }

    if isinstance(value, BASIC_TYPES):
        return value

    if is_registry_value(value):
        return value.name

    raise TypeError(
        f'Cannot serialize type {type(value).__name__} to JSON. '
        f'Value: {value!r}. Supported types are: str, int, float, bool, None, '
        'list, dict, Enum, and dataclasses.'
    )


def to_json_dict(inst: object, exclude_none: bool = False) -> dict:
    """
    Serialize dataclass instances for config files and schema validation.
    :param inst: Dataclass instance to serialize.
    :param exclude_none: If True, omit keys whose value is None.
    :return: Dictionary suitable for JSON serialization.
    :raises TypeError: When inst is not a dataclass.
    """
    if not dataclasses.is_dataclass(inst):
        raise TypeError(f'Expected a dataclass instance, got {type(inst).__name__}')

    as_dict = dataclasses.asdict(inst)  # ty:ignore[invalid-argument-type]
    return _make_json_serializable(as_dict, exclude_none)


def _type_hint_contains(haystack: Type | ABCMeta | EnumType, needle: Type, value: Any) -> bool:
    """Match type hints during config hydration without losing registry semantics."""

    needle_type = needle if isinstance(needle, type) else type(needle)

    origin = get_origin(haystack)
    if origin is Union:
        return any(_type_hint_contains(arg, needle_type, value) for arg in get_args(haystack))

    if origin is not None:
        # Check if the base (dict, list) is supported and matches
        if origin in BASIC_TYPES and needle_type is origin:
            return True
        return False

    if haystack in BASIC_TYPES:
        try:
            return issubclass(needle_type, haystack)
        except TypeError:
            return False

    if issubclass(haystack, Registry):
        return is_registry_value(value) and value.registry_cls is haystack

    try:
        return issubclass(needle_type, haystack)
    except TypeError:
        return False


def _get_inner_type(type_hint: type) -> type:
    """Extract inner types for container fields during deserialization."""
    args = get_args(type_hint)

    if not args:
        raise TypeError(f'Type hint is not parameterized: {type_hint}. Cannot extract inner type.')

    origin = get_origin(type_hint)

    if origin is list:
        return args[0]

    if origin is dict:
        if len(args) != 2:
            raise TypeError('Dictionary type hints must have exactly two arguments.')
        return args[1]

    raise TypeError(f'Type "{type_hint}" is not supported.')


def _deserialize_value(
    value: Optional[Any],
    target_type: Type | ABCMeta | EnumType,
    default: Optional[Any] = None,
    factory: Callable | dataclasses._MISSING_TYPE | None = None,
) -> Any:
    """Hydrate config values into typed fields with registry and Optional support."""

    if value is None:
        if default not in (None, dataclasses.MISSING):
            return default
        if factory not in (None, dataclasses.MISSING) and callable(factory):
            return factory()
        return None

    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin is None and isclass(target_type) and issubclass(target_type, Enum):
        return target_type(value)

    if origin is Union:
        # Handle Optional[T] (which is Union[T, NoneType])
        if NoneType in args:
            actual_type = next(arg for arg in args if arg is not NoneType)
            return _deserialize_value(value, actual_type, default, factory)

    if isinstance(value, list):
        return [_deserialize_value(item, _get_inner_type(target_type)) for item in value]

    if isinstance(value, dict):
        for key in value.keys():
            if not isinstance(key, str):
                raise TypeError(
                    f'Dictionary keys must be strings for JSON serialization. '
                    f'Found key {key!r} of type {type(key).__name__}'
                )

        if origin is None and isclass(target_type):
            # For dataclass targets, recursively prepare constructor kwargs
            if dataclasses.is_dataclass(target_type):
                nested_kwargs = prepare_ctor_kwargs(value, target_type)
                return target_type(**nested_kwargs)
            # Otherwise, assume direct construction is valid
            return target_type(**value)

        return {
            key: _deserialize_value(val, _get_inner_type(target_type)) for key, val in value.items()
        }

    if origin is None and isclass(target_type) and issubclass(target_type, Registry):
        if isinstance(value, str):
            return target_type.get(value)

    return value


def prepare_ctor_kwargs(json_dict: dict[str, Any], dataclass_class: Type[object]) -> dict[str, Any]:
    """
    Prepares a dictionary of keyword arguments for a dataclass constructor.
    This function processes a JSON dictionary by deserializing values according to
    the field types of the target dataclass and performing type validation.
    :param json_dict: Raw dictionary containing data to deserialize.
    :param dataclass_class: The dataclass type used to define fields and expected types.
    :return: A dictionary of processed field names and values suitable for `dataclass_class(**kwargs)`.
    :raises TypeError: If input is not a dict, target is not a dataclass, or a type mismatch occurs.
    """
    if not isinstance(json_dict, dict):
        raise TypeError(f'Expected a dict, got {type(json_dict).__name__}')

    if not dataclasses.is_dataclass(dataclass_class):
        raise TypeError(f'Expected a dataclass object, got {type(dataclass_class).__name__}')

    kwargs = {}
    for field in dataclasses.fields(dataclass_class):
        value = _deserialize_value(
            json_dict.get(field.name), field.type, field.default, field.default_factory
        )

        if not _type_hint_contains(field.type, type(value), value):
            raise TypeError(
                f'Type mismatch for field "{field.name}": expected "{field.type}", got "{value}" ({type(value)}))'
            )

        kwargs[field.name] = value

    return kwargs
