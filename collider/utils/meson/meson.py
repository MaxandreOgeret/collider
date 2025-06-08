# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Meson configure, compile, and test helpers."""

from __future__ import annotations

import logging

from pathlib import Path

from packaging.version import Version

import collider

from collider.log import logger
from collider.utils import command


_MINIMUM_VERSION = Version('1.8.1')
DEFAULT_BUILD_DIR = Path('collider-build')
SUBPROJECTS_DIR = Path('subprojects')


def validate() -> None:
    """
    Fail fast when Meson is missing or too old to avoid cryptic build errors.
    :raises FileNotFoundError: When the meson executable is not found.
    :raises RuntimeError: When the Meson version is below the minimum required.
    """

    try:
        version = command.run(['meson', '--version'], capture=command.StdStream.STDOUT)
    except FileNotFoundError:
        logger.critical('Could not locate "meson" executable.')
        raise

    assert version is not None
    if Version(version) < _MINIMUM_VERSION:
        logger.critical(
            msg := (
                f'{collider.__name__.capitalize()} requires meson version '
                f'"{_MINIMUM_VERSION}", current version is "{version}".'
            )
        )
        raise RuntimeError(msg)

    logger.info(
        f'Using meson version: "{version}".',
    )
    if logger.isEnabledFor(logging.DEBUG):
        meson_path = command.run(['which', 'meson'], capture=command.StdStream.STDOUT)
        logger.debug(f'Meson located in: {meson_path}')


def setup(*, sourcedir=Path('.'), builddir=DEFAULT_BUILD_DIR, args: list[str]) -> None:
    """
    Wrapper around `meson setup` to keep logging consistent.
    :param sourcedir: Source directory (default current directory).
    :param builddir: Build directory (default collider-build).
    :param args: Extra arguments passed to meson setup.
    """
    logger.info(f'Running meson setup with sourcedir="{sourcedir}", builddir="{builddir}"')

    command.run(
        [
            'meson',
            'setup',
            builddir.as_posix(),
            sourcedir.as_posix(),
            *args,
        ]
    )


def kompile(*, builddir=DEFAULT_BUILD_DIR) -> None:
    """
    Wrapper around `meson compile` to keep logging consistent.
    :param builddir: Build directory (default collider-build).
    """
    logger.info(f'Running meson compile with builddir="{builddir}"')

    command.run(
        [
            'meson',
            'compile',
            '-C',
            builddir.as_posix(),
        ]
    )


def test(*, builddir=DEFAULT_BUILD_DIR) -> None:
    """
    Wrapper around `meson test` to keep logging consistent.
    :param builddir: Build directory (default collider-build).
    """
    logger.info(f'Running meson test with builddir="{builddir}"')

    command.run(
        [
            'meson',
            'test',
            '-C',
            builddir.as_posix(),
        ]
    )
