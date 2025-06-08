# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Repo key encoding and parsing."""

import urllib.parse

from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.types import RepoKey


def _encode_repo_key_part(value: str) -> str:
    return urllib.parse.quote(value, safe='')


def _decode_repo_key_part(value: str) -> str:
    return urllib.parse.unquote(value)


def make_repo_key(name: str, version: str, package_type: PackageType) -> RepoKey:
    """
    Build a stable repository key from package identity.
    Format: "{name}@{version}#{package_type}" with URL-encoded parts.
    :param name: Package name.
    :param version: Package version.
    :param package_type: Package type (e.g. WRAP).
    :return: Encoded repo key string.
    """
    return (
        f'{_encode_repo_key_part(name)}@'
        f'{_encode_repo_key_part(version)}#'
        f'{_encode_repo_key_part(package_type.value)}'
    )


def parse_repo_key(repo_key: RepoKey) -> tuple[str, str, str]:
    """
    Parse a repository key into (name, version, package_type).
    :param repo_key: Encoded repo key string.
    :return: Tuple of (name, version, package_type).
    :raises ValueError: When the repo key format is invalid.
    """
    if '@' not in repo_key or '#' not in repo_key:
        raise ValueError(f'Invalid repo key format: {repo_key}')
    name_part, rest = repo_key.split('@', 1)
    version_part, type_part = rest.split('#', 1)
    return (
        _decode_repo_key_part(name_part),
        _decode_repo_key_part(version_part),
        _decode_repo_key_part(type_part),
    )
