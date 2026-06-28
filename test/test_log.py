# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

from collider.log import Level, init_logger, progress_disable


def test_progress_disable_true_when_debug(monkeypatch):
    """Debug logging force-hides bars so they don't interleave with verbose logs."""
    monkeypatch.delenv('CI', raising=False)
    init_logger(Level.DEBUG)
    try:
        assert progress_disable() is True
    finally:
        init_logger(Level.INFO)


def test_progress_disable_true_under_ci(monkeypatch):
    """A CI environment is non-interactive, so bars are force-hidden."""
    monkeypatch.setenv('CI', 'true')
    init_logger(Level.INFO)
    assert progress_disable() is True


def test_progress_disable_none_when_interactive(monkeypatch):
    """Otherwise defer to tqdm: None auto-detects the TTY at the output stream."""
    monkeypatch.delenv('CI', raising=False)
    init_logger(Level.INFO)
    assert progress_disable() is None
