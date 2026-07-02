# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the ``collider pkg search`` command."""

import os

from pathlib import Path

from collider.cache import WrapCache
from collider.Package import WrapPackage
from collider.utils.packaging import compute_file_hash
from test.common.common import Subcommand, run_subcommand


def _populate_cache(tmp_path: Path) -> None:
    """Store one wrap plus its archive in the isolated on-disk cache.
    :param tmp_path: Isolated HOME provided by the autouse ``mock_home`` fixture.
    """
    cache = WrapCache(tmp_path / '.config' / 'collider' / 'cache')

    content = b'payload'
    archive = tmp_path / 'demo.tar.xz'
    archive.write_bytes(content)
    wrap_text = (
        '[wrap-file]\n'
        'source_url=https://example.com/demo.tar.xz\n'
        'source_filename=demo.tar.xz\n'
        f'source_hash={compute_file_hash(archive)}\n'
    )
    package = WrapPackage.from_wrap_text('demo', '1.0.0', wrap_text)
    cache.store_wrap(package)
    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (cache.archives_dir / f'{package.source_hash}-demo.tar.xz').write_bytes(content)


def test_pkg_search_ex_ok_cache_hit(tmp_path: Path):
    """`pkg search --cache` returns EX_OK when a cached wrap matches the pattern."""
    _populate_cache(tmp_path)
    assert run_subcommand(Subcommand.PKG, ['search', '--cache', 'demo']) == os.EX_OK


def test_pkg_search_ex_unavailable_cache_empty():
    """`pkg search --cache` returns EX_UNAVAILABLE when no cached wrap matches."""
    assert run_subcommand(Subcommand.PKG, ['search', '--cache', 'nomatch']) == os.EX_UNAVAILABLE


def test_pkg_search_ex_noinput_unknown_repository():
    """`pkg search -r <name>` returns EX_NOINPUT when the repository is unknown."""
    assert run_subcommand(Subcommand.PKG, ['search', '-r', 'nonexistent', '.*']) == os.EX_NOINPUT


def test_pkg_search_ex_usage_invalid_regex():
    """`pkg search` returns EX_USAGE when the pattern is not a valid regex."""
    assert run_subcommand(Subcommand.PKG, ['search', '[']) == os.EX_USAGE
