# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Dependency and source type models."""

import enum

from dataclasses import dataclass, field
from typing import Optional


class DependencySource(enum.Enum):
    """Dependency source types."""

    SYSTEM = 'system'
    COLLIDER = 'collider'


@dataclass
class Dependency:
    """Type definition for dependency entry in colliderfile."""

    def __eq__(self, other):
        return (
            self.name == other.name
            and self.source == other.source
            and self.version == other.version
        )

    name: str  # Name of the dependency.
    source: DependencySource
    version: Optional[str] = None  # Only when source is collider.
    exclude: Optional[list[str]] = field(default=None)
    include: Optional[list[str]] = field(default=None)
    include_conditional: Optional[bool] = field(default=None)
    exclude_optional: Optional[bool] = field(default=None)
