# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Override decorator for compatibility."""

from __future__ import annotations

from typing import Any, Callable, TypeVar


_F = TypeVar('_F', bound=Callable[..., Any])

try:
    # Python 3.12+ provides the real decorator.
    from typing import override  # ty: ignore pylint: disable=unused-import
except ImportError:

    def override(func: _F) -> _F:
        """
        No-op compatibility shim for typing.override in Python <3.12.
        :param func: Function to decorate.
        :return: The decorated function.
        """
        return func
