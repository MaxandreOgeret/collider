# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Shared exception types for user-facing error reporting."""


class ColliderUserError(Exception):
    """
    Raised for usage or user-environment problems, not Collider bugs.
    The entry point reports these cleanly and exits with the carried code instead of
    routing them through the internal-bug handler.
    """

    def __init__(self, message: str, exit_code: int) -> None:
        """
        :param message: Human-readable description, already logged at the raise site.
        :param exit_code: Process exit code (os.EX_*) the CLI should return.
        """
        # A user error that exits successfully is a logic bug, not a recoverable state.
        if exit_code == 0:
            raise ValueError('ColliderUserError must use a non-zero exit code.')
        super().__init__(message)
        self.exit_code = exit_code
