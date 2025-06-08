# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Runtime context passed to subcommands (config, cache, offline)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from collider.cache import WrapCache


if TYPE_CHECKING:
    from collider.config import AppConfig


@dataclass(frozen=True)
class Context:
    """Bundled runtime dependencies passed to every sub-command."""

    config: 'AppConfig'
    cache: WrapCache
    offline: bool
