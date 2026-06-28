# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Logging configuration and levels."""

import logging
import os
import sys

from enum import Enum
from typing import Callable, TextIO, cast


first_include = False
logger = logging.getLogger('collider')


class Level(Enum):
    """Shared logging levels used by the CLI."""

    CRITICAL = 50
    ERROR = 40
    WARNING = 30
    WARN = WARNING
    INFO = 20
    DEBUG = 10


def _get_padded_format(padding=0) -> str:
    """Pad level names so log columns stay aligned."""
    return '[%(levelname)s] ' + (' ' * padding) + '%(message)s'


def _get_message_format(_=0) -> str:
    """Keep info logs minimal by omitting level labels."""
    return '%(message)s'


class _CustomFormatter(logging.Formatter):
    """Colored formatter that keeps debug context readable."""

    def __init__(
        self, fmt=None, datefmt=None, style='%', validate=True, *, defaults=None, debug=False
    ):
        self.debug = debug
        super().__init__(fmt, datefmt, style, validate)

    blue = '\x1b[34m'
    grey = '\x1b[38;20m'
    pink = '\x1b[95m'
    yellow = '\x1b[33;20m'
    red = '\x1b[31;20m'
    bold_red = '\x1b[31;1m'
    reset = '\x1b[0m'

    def format(self, record: logging.LogRecord) -> str:
        """Colorize output while keeping severity visible."""
        debug_text = ' (%(funcName)s %(filename)s:%(lineno)d) ' if self.debug else ''

        message_format = _get_padded_format if self.debug else _get_message_format

        formats = {
            # Padding to keep text aligned with logging level.
            logging.DEBUG: self.pink + message_format(3) + debug_text + self.reset,
            logging.INFO: self.blue + message_format(4) + self.reset,
            logging.WARNING: self.yellow + message_format(1) + debug_text + self.reset,
            logging.ERROR: self.red + message_format(3) + debug_text + self.reset,
            logging.CRITICAL: self.bold_red + message_format() + debug_text + self.reset,
        }

        log_fmt = formats.get(record.levelno)
        formatter = logging.Formatter(log_fmt)

        return formatter.format(record)


def init_logger(level: Level) -> None:
    """Reset handlers to avoid duplicate logs across repeated initializations."""
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(_CustomFormatter(debug=level is Level.DEBUG))
    logger.addHandler(handler)
    logger.setLevel(level.value)


def configure_logging(verbose: bool) -> None:
    """Switch between concise and debug logging based on CLI flags."""
    init_logger(Level.DEBUG if verbose else Level.INFO)


def should_disable_progress(*, stream: TextIO | None = None) -> bool:
    """Return whether animated progress output should be suppressed."""
    ci_value = os.environ.get('CI', '').strip().lower()
    ci_enabled = ci_value not in ('', '0', 'false', 'no', 'off')
    output = stream if stream is not None else sys.stdout
    return logger.level <= Level.DEBUG.value or ci_enabled or not output.isatty()


if not first_include:
    init_logger(Level.INFO)
    first_include = True

    # Monkeypatch logger to break on critical.
    if not sys.flags.optimize and 'COLLIDER_BREAK_ON_CRIT' in os.environ:

        def critical_break(*args, **kwargs):
            """Let developers break on critical logs without touching call sites."""
            assert hasattr(logger, 'orig_critical')
            orig_critical = logger.orig_critical
            assert callable(orig_critical)
            cast(Callable[..., object], orig_critical)(*args, **kwargs)
            breakpoint()  # pylint: disable=forgotten-debug-statement

        logger.orig_critical = logger.critical  # ty: ignore[unresolved-attribute]
        logger.critical = critical_break  # ty: ignore[invalid-assignment]
