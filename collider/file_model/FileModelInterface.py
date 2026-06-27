# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Abstract file model interface."""

import json
import tempfile

from abc import ABC
from dataclasses import InitVar, dataclass, is_dataclass
from functools import cached_property
from pathlib import Path
from typing import IO, Any, Dict, Optional, Type, TypeVar

import jsonschema

from collider.log import logger
from collider.utils.dataclass import prepare_ctor_kwargs, to_json_dict
from collider.utils.fs import atomic_write_text


T = TypeVar('T', bound='FileModelInterface')


@dataclass(kw_only=True)
class FileModelInterface(ABC):
    """Base for JSON-backed file models with shared schema and path rules."""

    path: InitVar[Optional[Path]] = None

    def __post_init__(self, path: Optional[Path] = None) -> None:
        """Keep path optional so in-memory models can be validated."""
        self._path = path

    def __str__(self) -> str:
        """Lowercase class name aligns with schema file naming."""
        return self.__class__.__name__.lower()

    def __repr__(self) -> str:
        """Match __str__ so logs stay consistent and compact."""
        return self.__str__()

    @cached_property
    def schema(self) -> Dict:
        """Use naming conventions to keep schema wiring frictionless."""
        schema_name = f'{self}.schema.json'

        schema_path = Path(__file__).parent / 'schema' / schema_name
        try:
            with open(schema_path, 'r', encoding='UTF-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f'Schema file not found: {schema_path.as_posix()}')
            raise

    @classmethod
    def get_filename(cls) -> Optional[str]:
        """Override to lock a file name and avoid loading the wrong model."""
        return None

    def get_path(self) -> Optional[Path]:
        """Expose the current file location for save workflows."""
        return self._path

    def _set_path(self, path: Path) -> None:
        """Reject directory paths to avoid clobbering folders."""
        assert isinstance(path, Path)

        if path.is_dir():
            logger.error(msg := f'File save path must not be a directory: {path}')
            raise TypeError(msg)

        self._path = path

    @classmethod
    def from_stream(cls: Type[T], stream: IO[Any]) -> T:
        """Centralize parsing so validation stays consistent across callers."""
        data = json.load(stream)

        loaded_file = cls(path=Path(stream.name), **prepare_ctor_kwargs(data, cls))
        assert isinstance(loaded_file, cls)
        assert loaded_file.get_path() is not None

        if not loaded_file.validate():
            raise TypeError('Failed to validate file before loading.')

        return loaded_file

    @classmethod
    def from_path(cls: Type[T], filepath: Path) -> T:
        """Load JSON from disk and enforce filename constraints."""
        # Prevent loading a different model under a misleading filename.
        if cls.get_filename() and filepath.name != cls.get_filename():
            logger.critical(msg := f'File name mismatch: {filepath} != {cls.get_filename()}')
            raise ValueError(msg)

        try:
            with open(filepath, 'r', encoding='UTF-8') as f:
                loaded_file = cls.from_stream(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error(f'Invalid JSON in "{filepath}".')
            raise
        except FileNotFoundError:
            logger.warning(f'File not found: "{filepath}".')
            raise

        return loaded_file

    @classmethod
    def from_dict(cls, obj: type[Any], data: dict[str, Any]) -> Any:
        """Build dataclass instances while applying standard conversions."""
        if not is_dataclass(obj):
            raise TypeError(f'{obj} is not a dataclass.')
        kwargs = prepare_ctor_kwargs(data, obj)
        return obj(**kwargs)

    def save(self, new_filepath: Optional[Path] = None) -> None:
        """Persist validated models so config stays trustworthy."""
        if isinstance(new_filepath, Path):
            self._set_path(new_filepath)
        else:
            if self._path is None:
                raise TypeError('File path not set.')

        assert isinstance(self._path, Path)

        if not self.validate():
            logger.error(msg := f'Failed to validate {self} before saving.')
            raise TypeError(msg)

        try:
            # Serialize before touching disk so a non-serializable model fails harmlessly.
            text = json.dumps(self.as_dict(), indent=2)
            atomic_write_text(self._path, text)
        except (IOError, TypeError) as e:
            logger.error(f'Failed to save {self} to {self._path}: {e}')
            raise

    def validate(self) -> bool:
        """Schema validation runs before custom rules to keep errors consistent."""
        try:
            jsonschema.validate(self.as_dict(), schema=self.schema)
        except FileNotFoundError as e:
            logger.error(f'Could not load schema for validation: {e.filename} not found.')
            return False
        except jsonschema.ValidationError as e:
            logger.error(f'{self} is invalid.')
            # Debug because validator errors are cryptic.
            logger.debug(str(e))
            return False

        return self.validate_data()

    def as_dict(self) -> Dict:
        """Normalize output so schemas stay clean and diffs remain stable."""
        return to_json_dict(self, exclude_none=True)

    def validate_data(self) -> bool:
        """Hook for domain-specific checks after schema validation."""
        return True

    def as_json(self) -> str:
        """Expose a JSON string for CLIs and temp-file workflows."""
        return json.dumps(self.as_dict())

    def as_file(self) -> IO[str]:
        """
        Provide a rewound temp file handle for use as a context manager.
        The backing file is removed when the handle is closed, so use it within a
        `with` block and read it before the block exits.
        :return: An open, rewound temp file handle.
        """
        tmp = tempfile.NamedTemporaryFile(
            'w+',
            encoding='UTF-8',
            delete=True,
        )
        tmp.write(self.as_json())
        tmp.seek(0)
        return tmp
