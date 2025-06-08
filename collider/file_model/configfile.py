# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""User config file model used to resolve repositories."""

from dataclasses import dataclass, field
from typing import Optional

from collider.file_model.FileModelInterface import FileModelInterface
from collider.repository import RepoImplRegistry


@dataclass
class RepoEntry:
    """Persisted repo entry aligned with the implementation registry."""

    name: str
    type: RepoImplRegistry
    url: str
    publish_url: Optional[str] = None  # Required for filesystem repos to keep wraps deterministic.


@dataclass
class ConfigFile(FileModelInterface):
    """Holds the repository list loaded at startup."""

    repositories: list[RepoEntry] = field(default_factory=list)
