# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test utils/core functionalities."""

from subprocess import CalledProcessError

import pytest

import collider.utils.command as command
import collider.utils.core as core
import test

from collider.utils.command import StdStream
from test.assets import plugins as test_plugins
from test.assets.plugins.TestInterface import TestInterface
from test.assets.plugins.TestPlugin import TestPlugin


def test_discover_plugins() -> None:
    """Test discovering plugins."""

    plugins: dict[str, type] = core.discover_plugins(test_plugins)
    assert len(plugins) == 1

    discovered_cls = next(iter(plugins.values()))
    assert discovered_cls is TestPlugin


def test_discover_plugins_with_iface() -> None:
    """Test discovering plugins by specifying an interface."""

    plugins = core.discover_plugins(package=test_plugins, interface=TestInterface)  # ty:ignore[invalid-argument-type]

    assert len(plugins) == 1

    discovered_cls = next(iter(plugins.values()))
    assert discovered_cls is TestPlugin


def test_discover_plugin_wrong_module() -> None:
    """Test discovering with wrong parameter."""

    with pytest.raises(TypeError):
        core.discover_plugins(None)  # ty:ignore[invalid-argument-type]


def test_discover_no_plugins() -> None:
    """Test discovering when no plugins are present."""

    with pytest.raises(AttributeError):
        core.discover_plugins(test)


def test_discover_plugins_import_error(caplog) -> None:
    """Test discovering plugins when one fails to import."""
    import types

    from unittest.mock import patch

    # Create a mock package
    mock_package = types.ModuleType('mock_package')
    mock_package.__path__ = ['/fake/path']
    mock_package.Interface = object  # ty:ignore[unresolved-attribute]

    # Mock pkgutil.iter_modules to return one module
    with patch('pkgutil.iter_modules', return_value=[(None, 'broken_plugin', False)]):
        # Mock import_module to raise ImportError
        with patch('importlib.import_module', side_effect=ImportError('Mocked import error')):
            with pytest.raises(ImportError, match='Mocked import error'):
                plugins = core.discover_plugins(mock_package)


def test_run_command_success() -> None:
    """Test running a command."""
    command.run(['true'])


def test_run_command_failure() -> None:
    """Test running a command."""
    with pytest.raises(CalledProcessError):
        command.run(['false'])


def test_run_command_capture() -> None:
    """Test running a command with output captured."""
    stdout = command.run(['echo', ' test '], capture=StdStream.STDOUT)
    assert stdout == 'test'


def test_run_command_capture_nostrip() -> None:
    """Test running a command with raw output captured."""
    stdout = command.run(['echo', ' test '], capture=StdStream.STDOUT, strip=False)
    assert stdout == ' test \n'


def test_run_command_cwd_uses_current_directory(tmp_path, monkeypatch) -> None:
    """Ensure command.run defaults to the current working directory."""
    target_dir = tmp_path / 'cwd-target'
    target_dir.mkdir()
    marker = target_dir / 'marker.txt'
    marker.write_text('ok', encoding='utf-8')

    monkeypatch.chdir(target_dir)
    stdout = command.run(['pwd'], capture=StdStream.STDOUT)
    assert stdout == str(target_dir)
