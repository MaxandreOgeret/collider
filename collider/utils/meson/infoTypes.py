# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""TypedDict shapes for Meson introspection JSON data (type checking only)."""

from typing import TypedDict


class PlatformInfo(TypedDict):
    """Typed view of Meson platform JSON for validation."""

    cpu: str
    cpu_family: str
    endian: str
    exe_suffix: str
    is_64_bit: bool
    kernel: str
    object_suffix: str
    subsystem: str
    system: str


class MachinesInfo(TypedDict):
    """Typed view of Meson machine data to keep host/build/target explicit."""

    build: PlatformInfo
    host: PlatformInfo
    target: PlatformInfo


class SubprojectInfo(TypedDict, total=False):
    """Typed view of a subproject entry in intro-projectinfo.json (Meson 1.10+)."""

    name: str
    version: str
    descriptive_name: str


class ProjectInfo(TypedDict):
    """Typed view of Meson project metadata used by validation."""

    descriptive_name: str
    license: list[str]
    license_files: list[str]
    subproject_dir: str
    subprojects: list[str] | list[SubprojectInfo]
    version: str


class DependencyInfo(TypedDict):
    """Typed view of Meson dependency metadata for inspection."""

    compile_args: list[str]
    dependencies: list[str]
    depends: list[str]
    extra_files: list[str]
    include_directories: list[str]
    link_args: list[str]
    meson_variables: list[str]
    name: str
    sources: list[str]
    type: str
    version: str


DependenciesInfo = list[DependencyInfo]


class IntroItem(TypedDict):
    """Typed view of Meson intro file entries."""

    file: str
    updated: bool


class IntroInformation(TypedDict):
    """Typed view of Meson intro metadata for change detection."""

    benchmarks: IntroItem
    buildoptions: IntroItem
    buildsystem_files: IntroItem
    compilers: IntroItem
    dependencies: IntroItem
    install_plan: IntroItem
    installed: IntroItem
    machines: IntroItem
    projectinfo: IntroItem
    targets: IntroItem
    tests: IntroItem


class VersionInfo(TypedDict):
    """Typed view of version parts to avoid string parsing downstream."""

    full: str
    major: int
    minor: int
    patch: int


class IntrospectionInfo(TypedDict):
    """Typed view of Meson introspection metadata blocks."""

    information: IntroInformation
    version: VersionInfo


class MesonVersion(TypedDict):
    """Typed view of Meson version data returned by introspection."""

    full: str
    major: int
    minor: int
    patch: int


class Directories(TypedDict):
    """Typed view of Meson directory paths used by tooling."""

    build: str
    info: str
    source: str


class MesonInfo(TypedDict):
    """Typed view of meson-info.json used during setup checks."""

    build_files_updated: bool
    directories: Directories
    error: bool
    introspection: IntrospectionInfo
    meson_version: MesonVersion


class WrapDbReleasesEntry(TypedDict, total=False):
    """Typed view of WrapDB release metadata."""

    versions: list[str]
    dependency_names: list[str]


WrapDbReleases = dict[str, WrapDbReleasesEntry]
