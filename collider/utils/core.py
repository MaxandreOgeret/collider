# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""
Utilities required for the basic functioning of collider.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import types

from pathlib import Path
from typing import Optional, Type, TypeVar

from collider.log import logger


T = TypeVar('T', bound=type)


def is_safe_path_segment(value: str) -> bool:
    """
    Report whether a value is safe to use as a single filesystem path segment.
    Rejects empty values, '.'/'..', and anything containing a separator or NUL,
    so untrusted names/versions cannot escape their target directory.
    :param value: Value to check.
    :return: True when the value is a safe path segment.
    """
    if not value:
        return False
    return (
        value not in ('.', '..')
        and Path(value).name == value
        and '\\' not in value
        and '\x00' not in value
    )


def assert_safe_path_segment(value: str, kind: str = 'name') -> str:
    """
    Validate that an untrusted value is safe to use as a single path segment.
    Package names and versions come from repository metadata and are turned into
    wrap and subproject paths, so reject anything that could escape the target
    directory before it reaches the filesystem.
    :param value: Value to validate.
    :param kind: Label used in error messages, e.g. "name" or "version".
    :return: The validated value unchanged.
    :raises ValueError: When the value is empty or not a safe path segment.
    """
    if not value:
        raise ValueError(f'Package {kind} must not be empty.')
    if not is_safe_path_segment(value):
        raise ValueError(f'Package {kind} must be a safe path segment.')
    return value


def discover_plugins(
    package: types.ModuleType, interface: Optional[Type[T]] = None
) -> dict[str, Type[T]]:
    """
    Discover all subclasses of `interface` (or `package.Interface` if not provided) from `package`.
    :param package: Package to scan for plugins.
    :param interface: Optional base class that plugins must inherit from. If None, uses `package.Interface`.
    :return: Dictionary mapping plugin names to their classes.
    """

    if not isinstance(package, types.ModuleType):
        logger.critical(msg := 'Plugin discovery requires `package` to be a module.')
        raise TypeError(msg)

    if interface is None:
        try:
            interface = package.Interface
        except AttributeError as e:
            raise AttributeError(f'Package {package.__name__} has no `Interface` attribute.') from e

    plugins: dict[str, Type[T]] = {}

    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        try:
            module = importlib.import_module(f'{package.__name__}.{module_name}')
        except ImportError as e:
            logger.error(f'Warning: Failed to import {module_name!r}: {e}')
            raise e

        for _, cls in inspect.getmembers(module, inspect.isclass):
            # Check if it's a concrete implementation of the interface.
            if (
                issubclass(cls, interface)
                and cls is not interface
                and cls.__module__ == module.__name__
            ):
                # Use module name as the plugin identifier to avoid conflicts.
                plugin_name = module_name.lower()
                if plugin_name in plugins:
                    logger.error(f"Warning: Duplicate plugin name '{plugin_name!r}' found")
                plugins[plugin_name] = cls

    logger.debug(f'Discovered {len(plugins)} plugin(s) in package "{package.__name__}".')
    return plugins
