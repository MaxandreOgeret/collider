# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Project-level collider.json model."""

from dataclasses import dataclass, field
from typing import Optional

from collider.file_model.FileModelInterface import FileModelInterface
from collider.utils.compat import override
from collider.utils.packaging.Dependency import Dependency


@dataclass
class Colliderfile(FileModelInterface):
    """Declared dependencies used to keep Meson wraps in sync."""

    description: Optional[str] = None
    dependencies: list[Dependency] = field(default_factory=list)

    @classmethod
    @override
    def get_filename(cls) -> str:
        """Fixed file name prevents loading unrelated JSON."""
        return 'collider.json'
