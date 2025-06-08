# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Meson introspection file reading."""

from __future__ import annotations

import json
import re

from enum import Enum
from pathlib import Path
from typing import IO, Any, Literal, cast, overload

from packaging.version import InvalidVersion, Version

from collider.log import logger
from collider.utils.meson.infoTypes import DependenciesInfo, MachinesInfo, MesonInfo, ProjectInfo


INFO_DIR = 'meson-info'


class InfoFile(Enum):
    """Central registry for Meson introspection file names."""

    buildoptions = 'buildoptions'
    compilers = 'compilers'
    dependencies = 'dependencies'
    install_plan = 'install_plan'
    machines = 'machines'
    projectinfo = 'projectinfo'
    targets = 'targets'
    tests = 'tests'
    meson = 'meson'


INFO_FILES = {
    InfoFile.buildoptions: 'intro-buildoptions.json',
    InfoFile.compilers: 'intro-compilers.json',
    InfoFile.dependencies: 'intro-dependencies.json',
    InfoFile.install_plan: 'intro-install_plan.json',
    InfoFile.machines: 'intro-machines.json',
    InfoFile.projectinfo: 'intro-projectinfo.json',
    InfoFile.targets: 'intro-targets.json',
    InfoFile.tests: 'intro-tests.json',
    InfoFile.meson: 'meson-info.json',
}

INFO_FILE_TYPE: dict[InfoFile, type] = {
    InfoFile.buildoptions: dict,
    InfoFile.compilers: dict,
    InfoFile.dependencies: DependenciesInfo,
    InfoFile.install_plan: dict,
    InfoFile.machines: MachinesInfo,
    InfoFile.projectinfo: ProjectInfo,
    InfoFile.targets: dict,
    InfoFile.tests: dict,
    InfoFile.meson: dict,
}

InfoKey = Literal[InfoFile.machines, InfoFile.projectinfo, InfoFile.dependencies]


def get_project_info_dir(builddir: Path) -> Path:
    """
    Fail fast when meson-info is missing to avoid misleading errors.
    :param builddir: Meson build directory.
    :return: Path to the meson-info directory.
    :raises ValueError: When the project info directory does not exist.
    """
    info_dir = builddir / INFO_DIR
    if not info_dir.exists():
        raise ValueError(f'Project info directory does not exist: {info_dir.absolute()}.')
    return info_dir


def read_info_from_stream(stream: IO[Any], info_file: InfoKey):
    """
    Parse Meson introspection JSON from an open stream.
    :param stream: Open file-like object with JSON content.
    :param info_file: Which introspection file type (machines, projectinfo, or dependencies).
    :return: Parsed data as the corresponding typed dict.
    """
    info_type: type = INFO_FILE_TYPE[info_file]
    return cast(info_type, json.load(stream))  # ty:ignore[invalid-type-form]


def read_info_from_path(path: Path, info_file: InfoKey):
    """
    Load Meson introspection data from disk.
    :param path: Path to the introspection JSON file.
    :param info_file: Which introspection file type.
    :return: Parsed data as the corresponding typed dict.
    """
    with open(path, mode='r', encoding='utf8') as f:
        return read_info_from_stream(f, info_file)


@overload
def get_project_info(info_file: Literal[InfoFile.meson], builddir: Path) -> MesonInfo: ...


@overload
def get_project_info(info_file: Literal[InfoFile.machines], builddir: Path) -> MachinesInfo: ...


@overload
def get_project_info(info_file: Literal[InfoFile.projectinfo], builddir: Path) -> ProjectInfo: ...


@overload
def get_project_info(
    info_file: Literal[InfoFile.dependencies], builddir: Path
) -> DependenciesInfo: ...


def get_project_info(info_file: InfoKey, builddir: Path):
    """
    Single entry point for typed Meson introspection data.
    :param info_file: Which introspection file (machines, projectinfo, or dependencies).
    :param builddir: Meson build directory.
    :return: Parsed data as the corresponding typed dict.
    """
    infodir: Path = get_project_info_dir(builddir)
    filename: str = INFO_FILES[info_file]

    return read_info_from_path(infodir / filename, info_file)


def load_project_metadata(
    builddir: Path,
) -> tuple[str | None, str | None, Path | None]:
    """
    Use Meson introspection for name, version, and source directory.
    :param builddir: Meson build directory (must already exist with meson-info).
    :return: (package_name, version, source_dir) or (None, None, None) on failure.
    """
    try:
        project_info = get_project_info(InfoFile.projectinfo, builddir)
        meson_info = get_project_info(InfoFile.meson, builddir)
    except Exception as exc:
        logger.critical(f'Failed to read Meson project info in "{builddir.as_posix()}": {exc}')
        logger.critical('Run "collider setup" or pass --builddir pointing to an existing build.')
        return None, None, None

    package_name = project_info.get('descriptive_name')
    if not package_name:
        logger.critical('Project name missing in Meson project info.')
        return None, None, None
    if not re.match(r'^[A-Za-z0-9._-]+$', package_name):
        logger.critical('Project name must contain only letters, numbers, ".", "_", or "-".')
        return None, None, None

    version = project_info.get('version')
    if not version:
        logger.critical('Project version missing in Meson project info.')
        return None, None, None
    try:
        Version(version)
    except InvalidVersion:
        logger.critical(f'Project version "{version}" is not valid.')
        return None, None, None

    source_dir = Path(meson_info['directories']['source']).resolve()
    if not source_dir.exists():
        logger.critical(f'Source directory "{source_dir.as_posix()}" does not exist.')
        return None, None, None

    return package_name, version, source_dir


def validate_projectinfo(project_info: ProjectInfo):
    """
    Gate setup on sane metadata so downstream tooling stays consistent.
    :param project_info: Parsed projectinfo introspection data.
    """

    try:
        Version(project_info['version'])
    except (InvalidVersion, TypeError) as exc:
        logger.error(msg := f'Project has invalid version: "{project_info["version"]}".')
        raise ValueError(msg) from exc

    if (
        'license' not in project_info
        or not project_info['license']
        or project_info['license'][0] == 'unknown'
    ):
        logger.warning(f'Project has invalid license: "{project_info["license"]}".')
