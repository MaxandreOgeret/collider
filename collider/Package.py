# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Wrap package model and install helpers."""

from __future__ import annotations

import configparser
import urllib.parse

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from collider.log import logger
from collider.utils.fs import atomic_write_text


def _load_wrap_section(
    wrap_text: str,
) -> tuple[configparser.ConfigParser, configparser.SectionProxy]:
    """Parse wrap text once so validation rules stay consistent."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # ty: ignore[invalid-assignment]
    parser.read_string(wrap_text)

    if 'wrap-file' not in parser:
        raise ValueError('Unsupported wrap file format. Expected [wrap-file] section.')

    return parser, parser['wrap-file']


def get_provide_names(wrap_text: str) -> list[str]:
    """Extract provided dependency names from a wrap file."""
    parser, _ = _load_wrap_section(wrap_text)
    if 'provide' not in parser:
        return []
    provide_section = parser['provide']
    provide_names: set[str] = set()
    for key, value in provide_section.items():
        if key in {'dependency_names', 'program_names'}:
            provide_names.update(name.strip() for name in value.split(',') if name.strip())
        else:
            provide_names.add(key)
    return sorted(provide_names)


def get_wrap_directory(wrap_text: str) -> Optional[str]:
    """Extract the extracted-tree directory name from a wrap file, if present."""
    parser, section = _load_wrap_section(wrap_text)
    if 'wrap-file' not in parser:
        return None
    return section.get('directory')


@dataclass(frozen=True)
class WrapPackage:  # pylint: disable=too-many-instance-attributes
    """Wrap metadata required to resolve and cache Meson dependencies."""

    name: str
    version: str
    wrap_text: str
    source_url: str
    source_filename: str
    source_hash: str
    patch_url: Optional[str] = None
    patch_filename: Optional[str] = None
    patch_hash: Optional[str] = None

    @classmethod
    def from_wrap_text(cls, name: str, version: str, wrap_text: str) -> 'WrapPackage':
        """Parse wrap text while enforcing required metadata and HTTPS."""
        _, section = _load_wrap_section(wrap_text)
        source_url = section.get('source_url')
        source_filename = section.get('source_filename')
        source_hash = section.get('source_hash')

        if not source_url or not source_filename or not source_hash:
            # Meson needs a complete source tuple to resolve the wrap.
            raise ValueError('Wrap file missing required source fields.')

        if urllib.parse.urlparse(source_url).scheme == 'http':
            logger.warning('HTTP source URLs are allowed but insecure; prefer HTTPS.')

        patch_url = section.get('patch_url')
        patch_filename = section.get('patch_filename')
        patch_hash = section.get('patch_hash')

        if patch_url or patch_filename or patch_hash:
            # Partial patch metadata would make caching and verification ambiguous.
            if not (patch_url and patch_filename and patch_hash):
                raise ValueError('Wrap file patch metadata is incomplete.')
            if urllib.parse.urlparse(patch_url).scheme == 'http':
                logger.warning('HTTP patch URLs are allowed but insecure; prefer HTTPS.')

        return cls(
            name=name,
            version=version,
            wrap_text=wrap_text,
            source_url=source_url,
            source_filename=source_filename,
            source_hash=source_hash,
            patch_url=patch_url,
            patch_filename=patch_filename,
            patch_hash=patch_hash,
        )

    def install_to_subproject(self, path: Path) -> None:
        """Persist a wrap file so Meson can resolve the dependency."""
        if path.exists():
            # Refuse to overwrite user-managed subprojects.
            raise FileExistsError(f'Subproject directory "{path}" already exists.')

        path.parent.mkdir(parents=True, exist_ok=True)

        wrap_path = path.parent / f'{path.name}.wrap'
        if wrap_path.exists():
            # Avoid clobbering an existing wrap file with a different source.
            existing_text = wrap_path.read_text(encoding='utf-8')
            if existing_text == self.wrap_text:
                return
            raise FileExistsError(f'Wrap file "{wrap_path}" already exists.')

        # Meson resolves dependencies from the wrap file, not from a populated directory.
        atomic_write_text(wrap_path, self.wrap_text, encoding='utf-8')
