# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Tests for logging helpers."""

import sys

import pytest

from collider.log import Level, logger, should_disable_progress


class _Stream:
    """Minimal stream stub reporting a fixed TTY status."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


@pytest.fixture(autouse=True)
def _restore_log_level():
    """Keep a test's logger-level change from leaking into the next test."""
    original = logger.level
    yield
    logger.setLevel(original)


def test_progress_enabled_for_interactive_info_output(monkeypatch) -> None:
    """Interactive INFO output keeps progress bars enabled."""
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)

    assert should_disable_progress(stream=_Stream(is_tty=True)) is False


def test_progress_disabled_for_non_tty_output(monkeypatch) -> None:
    """Piped or redirected output disables animated progress bars."""
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)

    assert should_disable_progress(stream=_Stream(is_tty=False)) is True


def test_progress_disabled_for_debug_logging(monkeypatch) -> None:
    """Debug logs stay free of tqdm carriage-return output, even on a TTY."""
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.DEBUG.value)

    assert should_disable_progress(stream=_Stream(is_tty=True)) is True


def test_progress_disabled_in_ci(monkeypatch) -> None:
    """CI logs suppress animated progress even if the stream reports as a TTY."""
    monkeypatch.setenv('CI', 'true')
    logger.setLevel(Level.INFO.value)

    assert should_disable_progress(stream=_Stream(is_tty=True)) is True


@pytest.mark.parametrize('ci_value', ['', '0', 'false', 'no', 'off', 'False', ' OFF '])
def test_progress_not_forced_by_falsey_ci_values(monkeypatch, ci_value: str) -> None:
    """A falsey CI value must not force-hide an otherwise interactive bar."""
    monkeypatch.setenv('CI', ci_value)
    logger.setLevel(Level.INFO.value)

    assert should_disable_progress(stream=_Stream(is_tty=True)) is False


def test_progress_default_stream_follows_stderr(monkeypatch) -> None:
    """tqdm writes to stderr, so a non-TTY stderr hides the bar even when stdout is a TTY."""
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)
    monkeypatch.setattr(sys, 'stderr', _Stream(is_tty=False))
    monkeypatch.setattr(sys, 'stdout', _Stream(is_tty=True))

    assert should_disable_progress() is True


def test_progress_default_stream_ignores_redirected_stdout(monkeypatch) -> None:
    """A redirected stdout must not hide a bar that still renders on an interactive stderr."""
    monkeypatch.delenv('CI', raising=False)
    logger.setLevel(Level.INFO.value)
    monkeypatch.setattr(sys, 'stderr', _Stream(is_tty=True))
    monkeypatch.setattr(sys, 'stdout', _Stream(is_tty=False))

    assert should_disable_progress() is False
