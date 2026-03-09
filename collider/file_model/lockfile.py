# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Lockfile model that pins resolved dependency state."""

import hashlib

from dataclasses import dataclass, field

from collider.file_model.FileModelInterface import FileModelInterface
from collider.utils.compat import override


@dataclass
class LockedPackage:
    """Single resolved dependency entry in the lockfile."""

    version: str
    wrap_hash: str
    origin: str

    @classmethod
    def from_wrap_text(cls, version: str, wrap_text: str, origin: str) -> 'LockedPackage':
        """
        Build a locked entry by hashing wrap file text.
        :param version: Resolved package version.
        :param wrap_text: Raw text content of the .wrap file.
        :param origin: Repository URL the package was resolved from.
        """
        return cls(version=version, wrap_hash=compute_wrap_hash(wrap_text), origin=origin)


def compute_wrap_hash(wrap_text: str) -> str:
    """
    Compute a SHA-256 hash of wrap file text.
    :param wrap_text: Raw text content of a .wrap file.
    :return: Hash string prefixed with ``sha256:``.
    """
    digest = hashlib.sha256(wrap_text.encode('utf-8')).hexdigest()
    return f'sha256:{digest}'


@dataclass
class Lockfile(FileModelInterface):
    """Pinned resolution state for reproducible installs."""

    version: int = 1
    dependencies: dict[str, LockedPackage] = field(default_factory=dict)
    packages: dict[str, LockedPackage] = field(default_factory=dict)

    @property
    def all_packages(self) -> dict[str, LockedPackage]:
        """Return a merged view of direct dependencies and transitive packages."""
        return {**self.dependencies, **self.packages}

    @classmethod
    @override
    def get_filename(cls) -> str:
        """Fixed file name prevents loading unrelated JSON."""
        return 'collider.lock'
