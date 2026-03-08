# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import logging
import subprocess
import sys

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collider.entrypoint import _get_installed_version, entrypoint, error_handler
from collider.log import Level, logger


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_entrypoint_help_exits_zero() -> None:
    """Test that collider --help exits with code 0."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', '--help'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0


def test_entrypoint_subcommand_help_exits_zero() -> None:
    """Test that collider init --help exits with code 0."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', 'init', '--help'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0


def test_entrypoint_install_subcommand_help_exits_zero() -> None:
    """Test that collider install --help exits with code 0."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', 'install', '--help'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0


def test_entrypoint_lock_subcommand_help_exits_zero() -> None:
    """Test that collider lock --help exits with code 0."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', 'lock', '--help'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0


def test_entrypoint_installall_subcommand_is_not_exposed() -> None:
    """Test that legacy installall is not exposed as a top-level CLI command."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', 'installall', '--help'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode != 0
    assert 'invalid choice' in result.stderr.lower()


def test_entrypoint_pkg_push_subcommand_is_not_exposed() -> None:
    """Test that repo-only push is not exposed under pkg."""
    result = subprocess.run(
        [sys.executable, '-m', 'collider.entrypoint', 'pkg', 'push', 'local'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_PROJECT_ROOT),
        env={**__import__('os').environ, 'PYTHONPATH': str(_PROJECT_ROOT)},
    )
    assert result.returncode != 0
    assert 'invalid choice' in result.stderr.lower()


def test_error_handler_basic(caplog) -> None:
    """Test the error handler with a basic exception."""
    e = Exception('Test Error')

    with patch('importlib.metadata.metadata') as mock_metadata:
        # Mock metadata to return a dummy project URL
        mock_info = MagicMock()
        mock_info.get.return_value = 'Project-URL, https://github.com/MaxandreOgeret/collider'
        mock_metadata.return_value = mock_info

        with caplog.at_level(logging.CRITICAL, logger='collider'):
            error_handler(e)

    assert 'Rerun collider with the --verbose flag' in caplog.text


def test_error_handler_verbose(caplog) -> None:
    """Test the error handler in verbose mode."""
    e = Exception('Verbose Error')

    # Set logger to DEBUG
    original_level = logger.level
    logger.setLevel(Level.DEBUG.value)

    try:
        with patch('importlib.metadata.metadata') as mock_metadata:
            mock_info = MagicMock()
            mock_info.get.return_value = 'Project-URL, https://github.com/MaxandreOgeret/collider'
            mock_metadata.return_value = mock_info

            with caplog.at_level(logging.DEBUG, logger='collider'):
                error_handler(e)

        assert 'Oi oi oi päkapikk!' in caplog.text
        # In verbose mode, the "Rerun collider..." message should be absent
        assert 'Rerun collider with the --verbose flag' not in caplog.text
        assert 'Verbose Error' in caplog.text
    finally:
        logger.setLevel(original_level)


def test_get_installed_version_uses_distribution_name() -> None:
    """Version lookup should use the published distribution name."""
    with patch('importlib.metadata.version', return_value='1.0.0') as mock_version:
        assert _get_installed_version() == '1.0.0'

    mock_version.assert_called_once_with('collider-wraps')


def test_entrypoint_unhandled_exception_exits_nonzero() -> None:
    """Unhandled exceptions should terminate with a non-zero exit code."""
    with patch('collider.entrypoint.main', side_effect=Exception('boom')):
        with pytest.raises(SystemExit) as exc:
            entrypoint()
    assert exc.value.code == 1
