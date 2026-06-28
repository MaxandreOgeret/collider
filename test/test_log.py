# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Tests for logging helpers."""

from collider.log import Level, logger, should_disable_progress


class _Stream:
    """Small stream stub for progress output detection tests."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_progress_enabled_for_interactive_info_output(monkeypatch) -> None:
    """Interactive INFO output keeps progress bars enabled."""
    original_level = logger.level
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)
    try:
        assert should_disable_progress(stream=_Stream(True)) is False
    finally:
        logger.setLevel(original_level)


def test_progress_disabled_for_non_tty_output(monkeypatch) -> None:
    """Piped or redirected output disables animated progress bars."""
    original_level = logger.level
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)
    try:
        assert should_disable_progress(stream=_Stream(False)) is True
    finally:
        logger.setLevel(original_level)


def test_progress_disabled_for_debug_logging(monkeypatch) -> None:
    """Debug logs stay free of tqdm carriage-return output."""
    original_level = logger.level
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.DEBUG.value)
    try:
        assert should_disable_progress(stream=_Stream(True)) is True
    finally:
        logger.setLevel(original_level)


def test_progress_disabled_in_ci(monkeypatch) -> None:
    """CI logs suppress animated progress even if stdout reports as a TTY."""
    original_level = logger.level
    monkeypatch.setenv('CI', 'true')
    logger.setLevel(Level.INFO.value)
    try:
        assert should_disable_progress(stream=_Stream(True)) is True
    finally:
        logger.setLevel(original_level)
