# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import argparse
import hashlib
import os
import urllib.parse

from pathlib import Path
from unittest.mock import MagicMock

from collider.cache import WrapCache
from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.Package import WrapPackage
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.Wrap import Wrap
from collider.subcommand.pkg.Info import Info
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


def _wrap_text(source_hash: str) -> str:
    return (
        '[wrap-file]\n'
        'source_url=https://example.com/demo.tar.xz\n'
        'source_filename=demo.tar.xz\n'
        f'source_hash={source_hash}\n'
    )


def test_policy_reports_versions_and_cache(tmp_path: Path, caplog) -> None:
    cache = WrapCache(tmp_path / 'cache')

    content = b'payload'
    content_hash = hashlib.sha256(content).hexdigest()
    wrap_text = _wrap_text(content_hash)
    package = WrapPackage.from_wrap_text('demo', '1.0.0', wrap_text)

    cache.store_wrap(package)
    cache.archives_dir.mkdir(parents=True, exist_ok=True)
    (cache.archives_dir / f'{content_hash}-demo.tar.xz').write_bytes(content)

    subprojects = tmp_path / 'subprojects'
    subprojects.mkdir()
    (subprojects / 'demo.wrap').write_text(wrap_text, encoding='utf-8')

    colliderfile = Colliderfile(
        dependencies=[Dependency('demo', DependencySource.COLLIDER, '>=1.0.0')]
    )
    colliderfile.save(tmp_path / Colliderfile.get_filename())

    packages = {
        make_repo_key('demo', '1.0.0', PackageType.WRAP): RepoPackageEntry(
            'demo', '1.0.0', PackageType.WRAP, dependency_names=['demo']
        ),
        make_repo_key('demo', '2.0.0', PackageType.WRAP): RepoPackageEntry(
            'demo', '2.0.0', PackageType.WRAP, dependency_names=['demo']
        ),
    }
    repo = Wrap(urllib.parse.urlparse('https://wrapdb.example/v2/'), packages)

    config = MagicMock()
    config.repositories = {'wrapdb': repo}

    context = MagicMock(spec=Context)
    context.config = config
    context.cache = cache

    args = argparse.Namespace(package='demo', repository=None)
    cmd = Info(args, context)

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert cmd.execute() == os.EX_OK
    finally:
        os.chdir(cwd)

    assert 'Installed: 1.0.0' in caplog.text
    assert 'Declared: >=1.0.0' in caplog.text
    assert 'Candidate: 2.0.0 (wrapdb)' in caplog.text
    assert '1.0.0  wrapdb' in caplog.text
    assert '[cached]' in caplog.text
    assert 'provides: demo' in caplog.text


def test_policy_missing_repository_returns_ex_noinput(tmp_path: Path, caplog) -> None:
    """Test Policy returns EX_NOINPUT when a requested repository does not exist."""
    import os

    config = MagicMock()
    config.repositories = {'only_repo': MagicMock()}
    context = MagicMock(spec=Context)
    context.config = config

    args = argparse.Namespace(package='anypkg', repository=['only_repo', 'missing_repo'])
    cmd = Info(args, context)

    exit_code = cmd.execute()
    assert exit_code == os.EX_NOINPUT
    assert 'Missing' in caplog.text


def test_policy_no_package_match_returns_ex_unavailable(tmp_path: Path, caplog) -> None:
    """Test Policy returns EX_UNAVAILABLE when no repo has the package."""
    import os

    repo = MagicMock()
    repo.search.return_value = {}
    config = MagicMock()
    config.repositories = {'repo': repo}
    context = MagicMock(spec=Context)
    context.config = config

    (tmp_path / 'collider.json').write_text('{"dependencies": []}', encoding='utf-8')
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        args = argparse.Namespace(package='nonexistent_package', repository=None)
        cmd = Info(args, context)
        exit_code = cmd.execute()
        assert exit_code == os.EX_UNAVAILABLE
        assert 'No package matching' in caplog.text
    finally:
        os.chdir(cwd)
