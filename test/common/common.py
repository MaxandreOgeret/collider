# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

from enum import Enum
from unittest.mock import patch

from collider.entrypoint import main as entrypoint


class Subcommand(str, Enum):
    INIT = 'init'
    LOCK = 'lock'
    SETUP = 'setup'
    PKG = 'pkg'
    STATUS = 'status'
    REPO = 'repo'
    PUBLISH = 'publish'
    UNPUBLISH = 'unpublish'


def run_subcommand(subcommand: Subcommand, args: list[str], verbose: bool = True) -> int:
    """
    Run a subcommand.
    :param subcommand: Subcommand to run.
    :param args: Arguments to pass to the subcommand.
    :param verbose: Enable verbose output.
    :return: Exit code.
    """

    with patch(
        'sys.argv',
        [
            'collider',
            *(['--verbose'] if verbose else []),
            subcommand.value,
            *args,
        ],
    ):
        return entrypoint()
