# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

import json

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from unittest.mock import PropertyMock, patch

import pytest

from collider.file_model.FileModelInterface import FileModelInterface


class Color(Enum):
    RED = 'red'
    BLUE = 'blue'


@dataclass(kw_only=True)
class Inner(FileModelInterface):
    tone: Color

    def validate(self) -> bool:
        return True


@dataclass(kw_only=True)
class Outer(FileModelInterface):
    name: str
    inner: Inner
    palette: list[Color]

    def validate(self) -> bool:
        return True


@dataclass(kw_only=True)
class MockFileModel(FileModelInterface):
    name: str
    version: int = 1

    def validate(self) -> bool:
        return True


def test_mock_file_model_instantiation():
    """Test basic instantiation of a concrete FileModelInterface."""
    path = Path('test.json')
    model = MockFileModel(name='test', path=path)

    assert model.name == 'test'
    assert model.version == 1
    assert model.get_path() == path
    assert str(model) == 'mockfilemodel'


def test_mock_file_model_from_dict():
    """Test loading from a dictionary."""
    data = {'name': 'test_dict', 'version': 2}
    model = MockFileModel.from_dict(MockFileModel, data)

    assert isinstance(model, MockFileModel)
    assert model.name == 'test_dict'
    assert model.version == 2


def test_mock_file_model_as_dict():
    """Test converting to a dictionary."""
    model = MockFileModel(name='test_as_dict', version=3)
    data = model.as_dict()

    assert data == {'name': 'test_as_dict', 'version': 3}


def test_mock_file_model_from_path(tmp_path):
    """Test loading from a file path."""
    file_path = tmp_path / 'mock.json'
    data = {'name': 'from_path', 'version': 4}
    file_path.write_text(json.dumps(data), encoding='UTF-8')

    # We need to mock validate() because it tries to load a schema file
    # which doesn't exist for MockFileModel.
    # Alternatively, we can override validate in MockFileModel.
    model = MockFileModel.from_path(file_path)

    assert model is not None
    assert model.name == 'from_path'
    assert model.version == 4
    assert model.get_path() == file_path


def test_mock_file_model_save(tmp_path):
    """Test saving to a file path."""
    file_path = tmp_path / 'saved.json'
    model = MockFileModel(name='saved_model', version=5)

    model.save(file_path)

    assert file_path.exists()
    loaded_data = json.loads(file_path.read_text(encoding='UTF-8'))
    assert loaded_data == {'name': 'saved_model', 'version': 5}
    assert model.get_path() == file_path


def test_mock_file_model_invalid_json(tmp_path):
    """Test loading from an invalid JSON file."""
    file_path = tmp_path / 'invalid.json'
    file_path.write_text('not json', encoding='UTF-8')

    with pytest.raises(json.JSONDecodeError):
        MockFileModel.from_path(file_path)


def test_mock_file_model_missing_file(tmp_path):
    """Test loading from a missing file."""
    file_path = tmp_path / 'missing.json'

    with pytest.raises(FileNotFoundError):
        MockFileModel.from_path(file_path)


def test_enum_serialization_as_dict_and_from_dict():
    """Test Enum serialization to values and deserialization back to instances."""
    o = Outer(name='x', inner=Inner(tone=Color.RED), palette=[Color.BLUE, Color.RED])

    # Serialize
    d = o.as_dict()
    assert d == {'name': 'x', 'inner': {'tone': 'red'}, 'palette': ['blue', 'red']}

    # Deserialize via from_dict (recursive path)
    o2 = Outer.from_dict(Outer, d)
    assert isinstance(o2.inner.tone, Color)
    assert o2.inner.tone is Color.RED
    assert all(isinstance(c, Color) for c in o2.palette)
    assert o2.palette == [Color.BLUE, Color.RED]


def test_mock_file_model_as_json():
    """Test converting to a JSON string."""
    model = MockFileModel(name='test_as_json', version=6)
    json_str = model.as_json()
    assert json.loads(json_str) == {'name': 'test_as_json', 'version': 6}


def test_mock_file_model_as_file():
    """Test converting to a temporary file object."""
    model = MockFileModel(name='test_as_file', version=7)
    with model.as_file() as tmp_file:
        json_str = tmp_file.read()

    assert json.loads(json_str) == {'name': 'test_as_file', 'version': 7}


def test_mock_file_model_set_path_invalid():
    """Test _set_path with a directory."""
    model = MockFileModel(name='test')
    with pytest.raises(TypeError, match='File save path must not be a directory:'):
        model._set_path(Path('.'))


def test_mock_file_model_save_no_path():
    """Test save without path when no path is set."""
    model = MockFileModel(name='test')
    with pytest.raises(TypeError, match='File path not set.'):
        model.save()


def test_mock_file_model_save_validation_failure():
    """Test save when validation fails."""

    @dataclass(kw_only=True)
    class InvalidModel(FileModelInterface):
        def validate(self) -> bool:
            return False

    model = InvalidModel(path=Path('test.json'))
    with pytest.raises(TypeError, match='Failed to validate .* before saving.'):
        model.save()


def test_file_model_from_dict_non_dataclass():
    """Test from_dict with a non-dataclass."""
    with pytest.raises(TypeError, match='is not a dataclass.'):
        FileModelInterface.from_dict(dict, {})


@dataclass(kw_only=True)
class OptionalModel(FileModelInterface):
    name: str
    opt_val: Optional[int] = None

    def validate(self) -> bool:
        return True


def test_file_model_optional_type():
    """Test _convert_value with Optional types."""
    data = {'name': 'test', 'opt_val': 42}
    model = OptionalModel.from_dict(OptionalModel, data)
    assert model.opt_val == 42

    data_none = {'name': 'test', 'opt_val': None}
    model_none = OptionalModel.from_dict(OptionalModel, data_none)
    assert model_none.opt_val is None


def test_file_model_get_filename_enforcement(tmp_path):
    """Test that from_path enforces get_filename() if it's set."""

    @dataclass(kw_only=True)
    class RestrictedModel(MockFileModel):
        @classmethod
        def get_filename(cls) -> str:
            return 'restricted.json'

    file_path = tmp_path / 'wrong_name.json'
    file_path.write_text('{"name": "test"}', encoding='UTF-8')

    with pytest.raises(ValueError):
        RestrictedModel.from_path(file_path)

    correct_path = tmp_path / 'restricted.json'
    correct_path.write_text('{"name": "test"}', encoding='UTF-8')
    model = RestrictedModel.from_path(correct_path)
    assert model is not None
    assert model.get_path() == correct_path


@dataclass(kw_only=True)
class ListModel(FileModelInterface):
    tags: list[str]

    def validate(self) -> bool:
        return True


def test_file_model_list_type():
    """Test _convert_value with list types."""
    data = {'tags': ['a', 'b']}
    model = ListModel.from_dict(ListModel, data)
    assert model.tags == ['a', 'b']


def test_mock_file_model_save_io_error(tmp_path):
    """Test save when I/O error occurs."""
    file_path = tmp_path / 'io_error.json'
    model = MockFileModel(name='test', path=file_path)

    # Use patch to make open raise IOError
    with patch('builtins.open', side_effect=IOError('Simulated I/O error')):
        with pytest.raises(IOError, match='Simulated I/O error'):
            model.save()


def test_file_model_as_dict_cleaning():
    """Test as_dict recursive cleaning of None values."""

    @dataclass(kw_only=True)
    class CleanModel(FileModelInterface):
        name: str
        optional_field: Optional[str] = None
        nested_list: list[Optional[str]] = field(default_factory=list)
        nested_dict: dict[str, Optional[str]] = field(default_factory=dict)

        def validate(self) -> bool:
            return True

    model = CleanModel(
        name='test',
        optional_field=None,
        nested_list=['a', None, 'b'],
        nested_dict={'k1': 'v1', 'k2': None},
    )

    d = model.as_dict()
    assert d == {'name': 'test', 'nested_list': ['a', 'b'], 'nested_dict': {'k1': 'v1'}}
    assert 'optional_field' not in d

    # Test when value is not None
    model.optional_field = 'value'
    d_with_val = model.as_dict()
    assert d_with_val['optional_field'] == 'value'


def test_file_model_validate_schema_not_found():
    """Test validate() when schema file is missing."""

    @dataclass(kw_only=True)
    class SchemaModel(FileModelInterface):
        name: str

    model = SchemaModel(name='test')
    # Mock schema property to raise FileNotFoundError
    with patch.object(SchemaModel, 'schema', new_callable=PropertyMock) as mock_schema:
        mock_schema.side_effect = FileNotFoundError
        assert model.validate() is False
