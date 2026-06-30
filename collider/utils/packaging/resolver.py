# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Transitive dependency resolver using resolvelib."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
import tempfile
import zipfile

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Optional, Sequence

import packaging.version
import resolvelib

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion
from tqdm import tqdm

from collider.cache import WrapCache
from collider.log import logger, should_disable_progress
from collider.repository import search_packages
from collider.utils.core import assert_safe_path_segment
from collider.utils.meson.scan import (
    ScannedDependency,
    filter_dependencies,
    scan_dependencies,
)
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key


if TYPE_CHECKING:
    from collider.repository.entries import RejectedEntry, RejectReason, RepoPackageEntry
    from collider.repository.implementation.RepositoryInterface import RepositoryInterface
    from collider.utils.packaging.types import RepoKey


@dataclass(frozen=True)
class Requirement:
    """A named package requirement with an optional version constraint."""

    name: str
    version_spec: Optional[SpecifierSet] = None

    def __init__(self, name: str, version_spec_str: Optional[str] = None):
        object.__setattr__(self, 'name', name)
        spec = SpecifierSet(version_spec_str) if version_spec_str else None
        object.__setattr__(self, 'version_spec', spec)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Requirement):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        if self.version_spec:
            return f'Requirement({self.name!r}, {str(self.version_spec)!r})'
        return f'Requirement({self.name!r})'


@dataclass(frozen=True)
class Candidate:
    """A resolved package at a specific version from a specific repo."""

    name: str
    version: str
    repo_name: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Candidate):
            return NotImplemented
        return (
            self.name == other.name
            and self.version == other.version
            and self.repo_name == other.repo_name
        )

    def __hash__(self) -> int:
        return hash((self.name, self.version, self.repo_name))

    def __repr__(self) -> str:
        return f'Candidate({self.name!r}, {self.version!r}, {self.repo_name!r})'


class MalformedRepositoryMetadata(Exception):
    """
    A package the resolver actually needs has no usable version because its repository metadata was
    rejected. Raised only in strict resolution (lock/install/add) so trust-sensitive operations fail
    closed instead of silently dropping the dependency.
    """

    def __init__(self, name: str, reason: 'RejectReason'):
        self.name = name
        self.reason = reason
        super().__init__(f'Repository metadata for "{name}" is malformed ({reason.value}).')


def build_rejected_name_index(
    repos: dict[str, 'RepositoryInterface'],
) -> dict[str, 'RejectedEntry']:
    """
    Index rejected releases entries by package name.
    :param repos: Configured repositories keyed by name.
    :return: Map from rejected package name to its first reject record.
    """
    index: dict[str, 'RejectedEntry'] = {}
    for repo in repos.values():
        for rejected in getattr(repo, 'rejected_metadata', []):
            if rejected.name:
                index.setdefault(rejected.name, rejected)
    return index


def build_dep_name_index(
    repos: dict[str, 'RepositoryInterface'],
) -> dict[str, str]:
    """
    Build a reverse index: Meson dependency name -> Collider package name.
    :param repos: Configured repositories keyed by name.
    :return: Mapping from dependency name to package name.
    """
    index: dict[str, str] = {}
    for _repo_name, repo in repos.items():
        packages = getattr(repo, 'packages', None)
        if packages is None:
            continue
        for _key, entry in packages.items():
            if entry.dependency_names is None:
                continue
            for dep_name in entry.dependency_names:
                if dep_name not in index:
                    index[dep_name] = entry.name
    return index


class ColliderProvider(resolvelib.AbstractProvider):  # pylint: disable=too-many-instance-attributes
    """resolvelib provider that connects the Meson/wrap ecosystem."""

    def __init__(
        self,
        repos: dict[str, 'RepositoryInterface'],
        dep_name_index: dict[str, str],
        *,
        offline: bool = False,
        strict: bool = False,
        wrap_cache: Optional['WrapCache'] = None,
        include_conditional: bool = False,
        exclude_optional: bool = False,
        include_names: Optional[set[str]] = None,
        exclude_names: Optional[set[str]] = None,
        root_overrides: Optional[dict[str, tuple[Optional[set[str]], Optional[set[str]]]]] = None,
        scan_cache: Optional[dict[str, list[ScannedDependency]]] = None,
    ):
        self.repos = repos
        self.dep_name_index = dep_name_index
        self.offline = offline
        self.strict = strict
        # Strict resolution maps a needed package name back to metadata rejected at the index
        # boundary, so trust-sensitive ops fail closed instead of silently dropping the dependency.
        # Scoped to package names only: an unmapped dependency() name is indistinguishable from a
        # system dependency, so keying fail-closed on attacker-declared provides would be a DoS.
        self.rejected_by_name = build_rejected_name_index(repos) if strict else {}
        self.wrap_cache = wrap_cache
        self.include_conditional = include_conditional
        self.exclude_optional = exclude_optional
        self.include_names = include_names or set()
        self.exclude_names = exclude_names or set()
        self.root_overrides = root_overrides or {}
        self.scan_cache: dict[str, list[ScannedDependency]] = scan_cache or {}
        self._warned_system_deps: set[str] = set()
        self.all_skipped_conditional: set[str] = set()
        self.all_skipped_optional: set[str] = set()
        self.all_included_optional: set[str] = set()
        self.all_unmapped: set[str] = set()

    def identify(self, requirement_or_candidate: Requirement | Candidate) -> str:
        return requirement_or_candidate.name

    def get_preference(
        self,
        identifier: str,
        resolutions: Mapping[str, Candidate],
        candidates: Mapping[str, Iterator[Candidate]],
        information: Mapping[str, Iterator[Any]],
        backtrack_causes: Sequence[Any],
    ) -> int:
        return sum(1 for _ in candidates[identifier])

    def find_matches(
        self,
        identifier: str,
        requirements: Mapping[str, Iterator[Requirement]],
        incompatibilities: Mapping[str, Iterator[Candidate]],
    ) -> Iterable[Candidate]:
        reqs = list(requirements.get(identifier, iter(())))
        bad = set(incompatibilities.get(identifier, iter(())))

        all_matches = search_packages(
            self.repos,
            re.compile(f'^{re.escape(identifier)}$'),
        )

        # Prefer stable releases; only consider prereleases when nothing stable
        # satisfies the constraints, so packages that ship only prereleases stay
        # installable without surprising stable resolutions.
        candidates = self._collect_matches(all_matches, reqs, bad, allow_prereleases=False)
        if not candidates:
            candidates = self._collect_matches(all_matches, reqs, bad, allow_prereleases=True)

        # Fail closed when a needed package has no usable version anywhere AND its metadata was
        # rejected: the rejection is why it is unresolvable, so surface that instead of a generic
        # "no matching version". A package that still has valid versions is left to normal
        # resolution (and was already warned about), so one bad sibling never breaks the build.
        if self.strict and identifier in self.rejected_by_name:
            total_indexed = sum(len(pkgs) for pkgs in all_matches.values())
            if total_indexed == 0:
                rejected = self.rejected_by_name[identifier]
                raise MalformedRepositoryMetadata(identifier, rejected.reason)

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in candidates]

    @staticmethod
    def _matches_requirements(
        version: str, reqs: list[Requirement], allow_prereleases: bool
    ) -> bool:
        """
        Check a version against every requirement's specifier.
        :param version: Candidate version string.
        :param reqs: Requirements that must all be satisfied.
        :param allow_prereleases: Allow prereleases even when a specifier targets stable releases.
        :return: True when the version satisfies all requirements.
        """
        # prereleases=None lets each specifier apply its own default, so explicit
        # prerelease constraints keep working; True forces the prerelease fallback.
        prereleases = True if allow_prereleases else None
        return all(
            req.version_spec is None or req.version_spec.contains(version, prereleases=prereleases)
            for req in reqs
        )

    def _collect_matches(
        self,
        all_matches: Mapping[str, Mapping[RepoKey, RepoPackageEntry]],
        reqs: list[Requirement],
        bad: set[Candidate],
        allow_prereleases: bool,
    ) -> list[tuple[packaging.version.Version, Candidate]]:
        """
        Build the list of candidates that satisfy the requirements.
        :param all_matches: Packages found per repository for the identifier.
        :param reqs: Requirements every candidate must satisfy.
        :param bad: Candidates marked incompatible by the resolver.
        :param allow_prereleases: Whether prereleases may satisfy stable specifiers.
        :return: List of (parsed version, candidate) tuples.
        """
        candidates: list[tuple[packaging.version.Version, Candidate]] = []
        for repo_name, pkgs in all_matches.items():
            for _key, entry in pkgs.items():
                try:
                    # Names and versions from repository metadata become cache and
                    # subproject paths downstream; never resolve unsafe segments.
                    assert_safe_path_segment(entry.name)
                    assert_safe_path_segment(entry.version, 'version')
                except ValueError:
                    logger.debug(
                        f'Skipping package with unsafe name or version: '
                        f'"{entry.name}" "{entry.version}".'
                    )
                    continue

                try:
                    parsed = packaging.version.parse(entry.version)
                except InvalidVersion:
                    continue

                if not self._matches_requirements(entry.version, reqs, allow_prereleases):
                    continue

                cand = Candidate(entry.name, entry.version, repo_name)
                if cand in bad:
                    continue
                candidates.append((parsed, cand))
        return candidates

    def is_satisfied_by(self, requirement: Requirement, candidate: Candidate) -> bool:
        if requirement.version_spec is None:
            return True
        # Allow prereleases here so a prerelease pin chosen by find_matches still
        # satisfies stable specifiers; the version range itself is still enforced.
        return requirement.version_spec.contains(candidate.version, prereleases=True)

    def get_dependencies(self, candidate: Candidate) -> list[Requirement]:
        """
        Scan the candidate's meson.build for dependency() calls and return
        requirements for each dependency that maps to a known package.
        """
        # Key the scan cache by repo too: two repos serving the same name+version are
        # distinct packages, so reusing one's scan for the other would bake the wrong
        # dependency graph into collider.lock.
        cache_key = f'{candidate.name}_{candidate.version}_{candidate.repo_name}'
        if cache_key in self.scan_cache:
            scanned = self.scan_cache[cache_key]
        else:
            scanned = None
            # The disk scan cache is keyed by name+version only and cannot tell two repos'
            # same-versioned packages apart. Trust it only offline (where there is no repo to
            # re-fetch from); online, always rescan the resolver-selected candidate.
            if self.offline and self.wrap_cache is not None:
                scanned = self.wrap_cache.load_scan(candidate.name, candidate.version)
            if scanned is None:
                scanned = self._scan_candidate(candidate)
                if self.wrap_cache is not None:
                    self.wrap_cache.store_scan(candidate.name, candidate.version, scanned)
            self.scan_cache[cache_key] = scanned

        inc = self.include_names
        exc = self.exclude_names
        if candidate.name in self.root_overrides:
            root_inc, root_exc = self.root_overrides[candidate.name]
            inc = root_inc or set()
            exc = root_exc or set()

        filter_result = filter_dependencies(
            scanned,
            include_conditional=self.include_conditional,
            exclude_optional=self.exclude_optional,
            include_names=inc,
            exclude_names=exc,
        )

        self.all_skipped_conditional.update(filter_result.skipped_conditional)
        self.all_skipped_optional.update(filter_result.skipped_optional)
        self.all_included_optional.update(filter_result.included_optional)

        requirements: list[Requirement] = []
        for dep in filter_result.included:
            pkg_name = self.dep_name_index.get(dep.name)
            if pkg_name is None:
                if dep.name not in self._warned_system_deps:
                    self.all_unmapped.add(dep.name)
                    self._warned_system_deps.add(dep.name)
                continue

            if pkg_name == candidate.name:
                continue

            version_spec_str = None
            if dep.version:
                version_spec_str = ','.join(dep.version)

            requirements.append(Requirement(pkg_name, version_spec_str))

        merged: dict[str, Requirement] = {}
        for req in requirements:
            if req.name in merged:
                existing = merged[req.name]
                if req.version_spec and existing.version_spec:
                    combined = str(existing.version_spec) + ',' + str(req.version_spec)
                    merged[req.name] = Requirement(req.name, combined)
                elif req.version_spec:
                    merged[req.name] = req
            else:
                merged[req.name] = req

        return list(merged.values())

    def _scan_candidate(self, candidate: Candidate) -> list[ScannedDependency]:
        """Download, extract source + patch, and scan the resulting meson.build."""
        repo_key = make_repo_key(candidate.name, candidate.version, PackageType.WRAP)

        repo = self.repos.get(candidate.repo_name)
        if repo is None:
            logger.warning(f'Repository "{candidate.repo_name}" not available for scan.')
            return []

        package = None
        if self.offline and repo.requires_network() and self.wrap_cache is not None:
            package = self.wrap_cache.load_wrap(candidate.name, candidate.version)
            if package is None:
                logger.warning(
                    f'Offline: no cached wrap for "{candidate.name}" {candidate.version}.'
                )
                return []
        else:
            # Online, fetch the wrap from the resolver-selected repo rather than the
            # name+version-keyed cache, which may hold a same-versioned wrap from another repo.
            package = repo.get_package(repo_key)

        if package is None:
            logger.warning(f'Could not fetch "{candidate.name}" for dependency scan.')
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            cache = self.wrap_cache
            if cache is None:
                cache = WrapCache(tmp / '.cache')
            try:
                cache.prepare_packagecache(package, tmp, offline=self.offline)
            except (FileNotFoundError, ValueError) as e:
                logger.warning(f'Could not prepare source for scan: {e}')
                return []

            source_archive = tmp / 'packagecache' / package.source_filename
            if not source_archive.exists():
                logger.warning(f'Source archive not found for "{candidate.name}".')
                return []

            extract_dir = tmp / 'extract'
            extract_dir.mkdir()
            if not self._extract_archive(source_archive, extract_dir):
                logger.warning(f'Could not extract source archive for "{candidate.name}".')
                return []

            if package.patch_filename:
                patch_archive = tmp / 'packagecache' / package.patch_filename
                if patch_archive.exists():
                    patch_target = self._patch_extract_target(patch_archive, extract_dir)
                    if not self._extract_archive(patch_archive, patch_target):
                        logger.warning(f'Could not extract patch archive for "{candidate.name}".')

            meson_build = self._find_meson_build(extract_dir)
            if meson_build is None:
                logger.warning(f'No meson.build found in "{candidate.name}" source.')
                return []

            try:
                return scan_dependencies(meson_build)
            except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
                logger.warning(f'Dependency scan failed for "{candidate.name}": {e}')
                return []

    @staticmethod
    def _extract_archive(archive_path: Path, dest: Path) -> bool:
        """Extract a tar or zip archive into *dest*. Returns False on failure."""
        if zipfile.is_zipfile(archive_path):
            try:
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(path=dest)
                return True
            except (zipfile.BadZipFile, OSError) as e:
                logger.warning(f'Zip extraction failed for "{archive_path.name}": {e}')
                return False

        try:
            with tarfile.open(archive_path) as tar:
                tar.extractall(path=dest, filter='data')
            return True
        except (tarfile.TarError, OSError) as e:
            logger.warning(f'Tar extraction failed for "{archive_path.name}": {e}')
            return False

    @staticmethod
    def _patch_extract_target(patch_archive: Path, extract_dir: Path) -> Path:
        """Decide where to extract the patch so its files overlay the source tree.

        If the patch already contains a subdirectory prefix that matches
        the source subdir (e.g. both use ``grpc-1.59.1/``), extract into
        ``extract_dir`` so they merge.  Otherwise extract into the source
        subdirectory itself.
        """
        source_subdir = ColliderProvider._find_source_subdir(extract_dir)
        if source_subdir is None:
            return extract_dir

        patch_prefix: Optional[str] = None
        if zipfile.is_zipfile(patch_archive):
            with zipfile.ZipFile(patch_archive) as zf:
                names = zf.namelist()
                if names:
                    patch_prefix = names[0].split('/')[0]
        else:
            try:
                with tarfile.open(patch_archive) as tar:
                    members = tar.getnames()
                    if members:
                        patch_prefix = members[0].split('/')[0]
            except tarfile.TarError:
                pass

        if patch_prefix and patch_prefix == source_subdir.name:
            return extract_dir

        return source_subdir

    @staticmethod
    def _find_source_subdir(extract_dir: Path) -> Optional[Path]:
        """Find the single subdirectory that tarballs typically extract into."""
        children = [c for c in extract_dir.iterdir() if c.is_dir()]
        if len(children) == 1:
            return children[0]
        return None

    @staticmethod
    def _find_meson_build(extract_dir: Path) -> Optional[Path]:
        """Locate meson.build in an extracted archive (may be nested one level)."""
        direct = extract_dir / 'meson.build'
        if direct.exists():
            return direct

        for child in extract_dir.iterdir():
            if child.is_dir():
                nested = child / 'meson.build'
                if nested.exists():
                    return nested

        return None


@dataclass
class ResolutionSummary:
    """Accumulated filtering metadata from the entire resolution run."""

    skipped_conditional: set[str]
    skipped_optional: set[str]
    included_optional: set[str]
    unmapped_system: set[str]
    skipped_conditional_by_pkg: dict[str, list[str]]
    skipped_optional_by_pkg: dict[str, list[str]]


def _group_by_package(
    dep_names: set[str],
    dep_name_index: dict[str, str],
) -> dict[str, list[str]]:
    """Group Meson dependency names by their providing Collider package."""
    groups: dict[str, list[str]] = {}
    for name in dep_names:
        pkg = dep_name_index.get(name, 'system')
        groups.setdefault(pkg, []).append(name)
    for deps in groups.values():
        deps.sort()
    return dict(sorted(groups.items()))


def _filter_satisfied_skips(
    summary: ResolutionSummary,
    dep_name_index: dict[str, str],
    resolved_names: set[str],
) -> None:
    """Remove auto-skipped conditional deps whose package is resolved, then group the rest.

    Only conditional deps are filtered: they are auto-skipped by default and
    showing them when the providing package is already resolved is noise.
    Optional deps are kept unconditionally because they reflect an explicit
    user choice (--exclude-optional) that should always be visible.
    """
    summary.skipped_conditional = {
        name
        for name in summary.skipped_conditional
        if dep_name_index.get(name) not in resolved_names
    }
    summary.skipped_conditional_by_pkg = _group_by_package(
        summary.skipped_conditional, dep_name_index
    )
    summary.skipped_optional_by_pkg = _group_by_package(summary.skipped_optional, dep_name_index)


@dataclass
class ResolutionResult:
    """Output of resolve_dependencies."""

    mapping: dict[str, Candidate]
    summary: ResolutionSummary


class _ProgressReporter(resolvelib.BaseReporter):
    """Reporter that displays a tqdm progress bar during resolution."""

    def __init__(self, *, disable: bool = False):
        self._disable = disable
        self._bar: Optional[tqdm] = None

    def starting(self) -> None:
        self._bar = tqdm(
            desc='Resolving dependencies',
            unit='pkg',
            leave=False,
            disable=self._disable,
        )

    def pinning(self, candidate: Any) -> None:
        if self._bar is not None:
            self._bar.set_postfix_str(candidate.name)
            self._bar.update(1)

    def ending(self, state: Any) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None


def resolve_dependencies(
    root_name: str,
    root_version_spec: Optional[str],
    repos: dict[str, 'RepositoryInterface'],
    *,
    offline: bool = False,
    strict: bool = False,
    wrap_cache: Optional['WrapCache'] = None,
    include_conditional: bool = False,
    exclude_optional: bool = False,
    include_names: Optional[set[str]] = None,
    exclude_names: Optional[set[str]] = None,
) -> ResolutionResult:
    """
    Resolve a package and all its transitive dependencies.
    :param root_name: Package name to resolve.
    :param root_version_spec: Optional version constraint string.
    :param repos: Configured repositories.
    :param offline: Disable network access during scanning.
    :param strict: Fail closed on malformed metadata for a needed dependency.
    :param wrap_cache: Local wrap cache for offline resolution.
    :param include_conditional: Include conditional deps.
    :param exclude_optional: Exclude optional deps.
    :param include_names: Force-include specific dep names.
    :param exclude_names: Force-exclude specific dep names.
    :return: ResolutionResult with mapping and summary.
    :raises MalformedRepositoryMetadata: In strict mode, when a needed dependency is unresolvable
        because its repository metadata was rejected.
    """
    dep_name_index = build_dep_name_index(repos)

    provider = ColliderProvider(
        repos,
        dep_name_index,
        offline=offline,
        strict=strict,
        wrap_cache=wrap_cache,
        include_conditional=include_conditional,
        exclude_optional=exclude_optional,
        include_names=include_names,
        exclude_names=exclude_names,
    )
    reporter = _ProgressReporter(disable=should_disable_progress())
    resolver = resolvelib.Resolver(provider, reporter)

    root_req = Requirement(root_name, root_version_spec)
    result = resolver.resolve([root_req])

    mapping = dict(result.mapping)
    summary = ResolutionSummary(
        skipped_conditional=provider.all_skipped_conditional,
        skipped_optional=provider.all_skipped_optional,
        included_optional=provider.all_included_optional,
        unmapped_system=provider.all_unmapped,
        skipped_conditional_by_pkg={},
        skipped_optional_by_pkg={},
    )
    _filter_satisfied_skips(summary, dep_name_index, set(mapping.keys()))

    return ResolutionResult(mapping=mapping, summary=summary)


@dataclass(frozen=True)
class RootSpec:
    """A single root dependency with its per-package filter overrides."""

    name: str
    version_spec: Optional[str] = None
    include_names: Optional[set[str]] = None
    exclude_names: Optional[set[str]] = None


def resolve_all_dependencies(
    roots: Sequence[RootSpec],
    repos: dict[str, 'RepositoryInterface'],
    *,
    offline: bool = False,
    strict: bool = False,
    wrap_cache: Optional['WrapCache'] = None,
    include_conditional: bool = False,
    exclude_optional: bool = False,
) -> ResolutionResult:
    """
    Resolve multiple root packages and all their transitive dependencies
    in a single pass so that cross-root conflicts are detected.
    :param roots: Root packages to resolve together.
    :param repos: Configured repositories.
    :param offline: Disable network access during scanning.
    :param strict: Fail closed on malformed metadata for a needed dependency.
    :param wrap_cache: Local wrap cache for offline resolution.
    :param include_conditional: Include conditional deps.
    :param exclude_optional: Exclude optional deps.
    :return: ResolutionResult with a unified mapping and summary.
    :raises MalformedRepositoryMetadata: In strict mode, when a needed dependency is unresolvable
        because its repository metadata was rejected.
    """
    root_overrides: dict[str, tuple[Optional[set[str]], Optional[set[str]]]] = {}
    for root in roots:
        if root.include_names or root.exclude_names:
            root_overrides[root.name] = (root.include_names, root.exclude_names)

    dep_name_index = build_dep_name_index(repos)

    provider = ColliderProvider(
        repos,
        dep_name_index,
        offline=offline,
        strict=strict,
        wrap_cache=wrap_cache,
        include_conditional=include_conditional,
        exclude_optional=exclude_optional,
        root_overrides=root_overrides,
    )
    reporter = _ProgressReporter(disable=should_disable_progress())
    resolver = resolvelib.Resolver(provider, reporter)

    root_reqs = [Requirement(r.name, r.version_spec) for r in roots]
    result = resolver.resolve(root_reqs)

    mapping = dict(result.mapping)
    summary = ResolutionSummary(
        skipped_conditional=provider.all_skipped_conditional,
        skipped_optional=provider.all_skipped_optional,
        included_optional=provider.all_included_optional,
        unmapped_system=provider.all_unmapped,
        skipped_conditional_by_pkg={},
        skipped_optional_by_pkg={},
    )
    _filter_satisfied_skips(summary, dep_name_index, set(mapping.keys()))

    return ResolutionResult(mapping=mapping, summary=summary)
