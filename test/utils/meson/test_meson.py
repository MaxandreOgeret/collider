# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Tests for meson utilities."""

import subprocess

from pathlib import Path

import pytest

from collider.utils import meson


def test_compile_and_test_fail_on_missing_builddir(tmp_path: Path) -> None:
    """Ensure compile and test helpers are invoked and error out when builddir is missing."""
    # Using a non-existent build directory should cause meson to fail
    with pytest.raises(subprocess.CalledProcessError):
        meson.kompile(builddir=tmp_path / 'no_such_build')

    with pytest.raises(subprocess.CalledProcessError):
        meson.test(builddir=tmp_path / 'no_such_build')


def test_validate_no_meson(monkeypatch, caplog):
    """Test meson.validate when meson executable is missing."""

    def fake_run(args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(meson.command, 'run', fake_run)

    with pytest.raises(meson.MesonUnavailableError):
        meson.validate()

    assert 'Could not locate "meson" executable.' in caplog.text


def test_validate_version_too_low(monkeypatch, caplog):
    """Test meson.validate when meson version is below requirement."""

    def fake_run(args, **kwargs):
        return '0.0.1'

    monkeypatch.setattr(meson.command, 'run', fake_run)

    with pytest.raises(meson.MesonUnavailableError):
        meson.validate()

    assert 'requires meson version' in caplog.text
