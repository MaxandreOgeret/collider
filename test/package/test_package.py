# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

from pathlib import Path

import pytest

from collider.Package import WrapPackage, get_provide_names, read_wrap_directory


def test_wrap_package_install_writes_wrap_file(tmp_path: Path):
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()

    pkg = WrapPackage(
        name='foo',
        version='1.0.0',
        wrap_text='[wrap-file]\nsource_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\nsource_hash=deadbeef\n',
        source_url='https://example.com/foo.tar.xz',
        source_filename='foo.tar.xz',
        source_hash='deadbeef',
    )

    subproject_path = subprojects / 'foo'
    pkg.install_to_subproject(subproject_path)

    wrap_path = subprojects / 'foo.wrap'
    assert wrap_path.exists()
    assert wrap_path.read_text(encoding='utf-8') == pkg.wrap_text
    assert not subproject_path.exists()


def test_wrap_package_install_errors_on_existing_paths(tmp_path: Path):
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()

    pkg = WrapPackage(
        name='foo',
        version='1.0.0',
        wrap_text='[wrap-file]\nsource_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\nsource_hash=deadbeef\n',
        source_url='https://example.com/foo.tar.xz',
        source_filename='foo.tar.xz',
        source_hash='deadbeef',
    )

    subproject_path = subprojects / 'foo'
    subproject_path.mkdir()

    with pytest.raises(FileExistsError):
        pkg.install_to_subproject(subproject_path)


def test_wrap_package_install_is_idempotent_with_existing_wrap(tmp_path: Path):
    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()

    wrap_text = (
        '[wrap-file]\nsource_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\nsource_hash=deadbeef\n'
    )
    pkg = WrapPackage(
        name='foo',
        version='1.0.0',
        wrap_text=wrap_text,
        source_url='https://example.com/foo.tar.xz',
        source_filename='foo.tar.xz',
        source_hash='deadbeef',
    )

    wrap_path = subprojects / 'foo.wrap'
    wrap_path.write_text(wrap_text, encoding='utf-8')

    pkg.install_to_subproject(subprojects / 'foo')
    assert wrap_path.read_text(encoding='utf-8') == wrap_text


def test_wrap_package_constructor_rejects_traversal_source_filename():
    with pytest.raises(ValueError, match='source_filename must be a safe path segment'):
        WrapPackage(
            name='foo',
            version='1.0.0',
            wrap_text='[wrap-file]\n',
            source_url='https://example.com/foo.tar.xz',
            source_filename='../evil',
            source_hash='deadbeef',
        )


def test_wrap_package_constructor_rejects_traversal_patch_filename():
    with pytest.raises(ValueError, match='patch_filename must be a safe path segment'):
        WrapPackage(
            name='foo',
            version='1.0.0',
            wrap_text='[wrap-file]\n',
            source_url='https://example.com/foo.tar.xz',
            source_filename='foo.tar.xz',
            source_hash='deadbeef',
            patch_url='https://example.com/foo.patch.tar.xz',
            patch_filename='../evil',
            patch_hash='cafe',
        )


def test_wrap_package_rejects_traversal_source_filename():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=../../../../tmp/evil\n'
        'source_hash=deadbeef\n'
    )

    with pytest.raises(ValueError, match='source_filename must be a safe path segment'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_absolute_source_filename():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=/tmp/evil\n'
        'source_hash=deadbeef\n'
    )

    with pytest.raises(ValueError, match='source_filename must be a safe path segment'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_backslash_source_filename():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=..\\evil\n'
        'source_hash=deadbeef\n'
    )

    with pytest.raises(ValueError, match='source_filename must be a safe path segment'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_traversal_patch_filename():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        'patch_url=https://example.com/foo.patch.tar.xz\n'
        'patch_filename=../evil\n'
        'patch_hash=cafe\n'
    )

    with pytest.raises(ValueError, match='patch_filename must be a safe path segment'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_incomplete_patch_metadata():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        'patch_url=https://example.com/foo.patch\n'
    )

    with pytest.raises(ValueError, match='patch metadata is incomplete'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_http_source_url():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=http://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
    )

    with pytest.raises(ValueError, match='insecure HTTP source_url'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_wrap_package_rejects_http_patch_url():
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/foo.tar.xz\n'
        'source_filename=foo.tar.xz\n'
        'source_hash=deadbeef\n'
        'patch_url=http://example.com/foo.patch.tar.xz\n'
        'patch_filename=foo.patch.tar.xz\n'
        'patch_hash=cafe\n'
    )

    with pytest.raises(ValueError, match='insecure HTTP patch_url'):
        WrapPackage.from_wrap_text('foo', '1.0.0', wrap_text)


def test_get_provide_names_returns_named_provide_keys():
    wrap_text = (
        '[wrap-file]\nsource_url=x\n\n[provide]\ncatch2 = catch2_dep\ncatch2-with-main = m\n'
    )
    assert get_provide_names(wrap_text) == ['catch2', 'catch2-with-main']


def test_get_provide_names_expands_reserved_dependency_names():
    wrap_text = (
        '[wrap-file]\nsource_url=x\n\n[provide]\ndependency_names = catch2-with-main, catch2\n'
    )
    assert get_provide_names(wrap_text) == ['catch2', 'catch2-with-main']


def test_get_provide_names_excludes_program_names():
    wrap_text = '[wrap-file]\nsource_url=x\n\n[provide]\nfoo = foo_dep\nprogram_names = cmake\n'
    assert get_provide_names(wrap_text) == ['foo']


def test_get_provide_names_reads_git_wrap_provide():
    wrap_text = '[wrap-git]\nurl=https://example.invalid/foo.git\n\n[provide]\nfoo = foo_dep\n'
    assert get_provide_names(wrap_text) == ['foo']


def test_get_provide_names_without_provide_section_returns_empty():
    assert get_provide_names('[wrap-git]\nurl=https://example.invalid/foo.git\n') == []


def test_read_wrap_directory_from_wrap_file():
    text = '[wrap-file]\ndirectory = fmt-10.0.0\nsource_url = https://x/y.tar.gz\n'
    assert read_wrap_directory(text) == 'fmt-10.0.0'


def test_read_wrap_directory_from_wrap_git():
    text = '[wrap-git]\ndirectory = mydep-src\nurl = https://example.invalid/mydep.git\n'
    assert read_wrap_directory(text) == 'mydep-src'


def test_read_wrap_directory_blank_is_none():
    assert read_wrap_directory('[wrap-file]\ndirectory =   \nsource_url = https://x/y\n') is None


def test_read_wrap_directory_absent_is_none():
    assert read_wrap_directory('[wrap-file]\nsource_url = https://x/y.tar.gz\n') is None


def test_read_wrap_directory_redirect_is_none():
    assert read_wrap_directory('[wrap-redirect]\nfilename = subprojects/other.wrap\n') is None
