# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Base implementation for subcommands."""

from __future__ import annotations

import argparse
import os

from abc import ABC, abstractmethod
from typing import Optional

from collider.Context import Context
from collider.log import logger


class SubcommandInterface(ABC):
    """Shared shape for CLI subcommands to keep plugin discovery uniform."""

    @staticmethod
    @abstractmethod
    def help() -> str:
        """Short help line shown in parent command help listings."""

    @classmethod
    def long_help(cls) -> str:
        """Longer command description shown in command-specific help."""
        return cls.help()

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    @abstractmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command implementation."""

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store parsed arguments and shared context for the subcommand.
        :param args: Parsed CLI arguments for this subcommand.
        :param context: Application context (config, cache, offline).
        """
        self.args = args
        self.context = context

    @abstractmethod
    def execute(self) -> int:
        """Process exit code for the CLI to propagate."""

    @staticmethod
    def _reject_malformed_metadata(exc: Exception, action: str) -> int:
        """
        Log a refusal to act on malformed repository metadata and return the data-error code.
        :param exc: The raised malformed-metadata error.
        :param action: Verb describing the refused action, e.g. "lock" or "install".
        :return: The data-error exit code.
        """
        logger.critical(f'Refusing to {action} against malformed repository metadata: {exc}')
        return os.EX_DATAERR
