# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Plugin registry base."""

from __future__ import annotations

import inspect

from abc import ABC, abstractmethod
from typing import Generic, Mapping, Type, TypeVar, final


T = TypeVar('T')


class _RegistryValue:
    """Enum-like value that keeps the implementation class attached."""

    def __init__(self, name: str, cls: Type, registry_cls: Type):
        self.name = name
        self.value = name
        self.cls = cls
        self.registry_cls = registry_cls

    def __repr__(self):
        return f'{self.registry_cls.__name__}.{self.name}'

    def __call__(self, *args, **kwargs):
        """Convenience to instantiate the implementation directly."""
        return self.cls(*args, **kwargs)


class Registry(ABC, Generic[T]):
    """Base registry for plugin-style implementations."""

    _registry_values: dict[str, _RegistryValue] = {}

    @classmethod
    @abstractmethod
    def get_impls(cls) -> Mapping[str, Type[T]]:
        """Return implementation classes keyed by their registry name."""

    @classmethod
    @final
    def get(cls, name: str) -> _RegistryValue:
        """
        Resolve a registry entry by name.
        :param name: Registry key (e.g. plugin name).
        :return: Registry value for the given name.
        """
        return cls._registry_values[name]

    def __init_subclass__(cls, **kwargs):
        """Populate registry values once for each concrete registry subclass."""
        super().__init_subclass__(**kwargs)
        cls._registry_values = {}
        if not inspect.isabstract(cls):
            try:
                for type_name, type_class in cls.get_impls().items():
                    registry_value = _RegistryValue(type_name, type_class, cls)
                    setattr(cls, type_name, registry_value)
                    cls._registry_values[type_name] = registry_value
            except (NotImplementedError, TypeError, AttributeError):
                pass


def is_registry_value(value: object) -> bool:
    """
    Guard for registry-backed values in config deserialization.
    :param value: Object to check.
    :return: True if value is a registry entry (e.g. from Registry.get).
    """
    return isinstance(value, _RegistryValue)
