# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Add a package dependency to the project."""

import argparse
import logging
import os
import re
import shutil

from pathlib import Path
from typing import Optional

import packaging.version
import resolvelib

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion
from tqdm import tqdm

from collider.Context import Context
from collider.file_model.colliderfile import Colliderfile
from collider.file_model.lockfile import Lockfile, compute_wrap_hash
from collider.log import logger
from collider.Package import WrapPackage
from collider.repository import search_packages
from collider.repository.entries import RepoPackageEntry
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils.compat import override
from collider.utils.meson import SUBPROJECTS_DIR
from collider.utils.packaging.Dependency import Dependency, DependencySource
from collider.utils.packaging.PackageType import PackageType
from collider.utils.packaging.repo_key import make_repo_key
from collider.utils.packaging.resolver import (
    ResolutionSummary,
    RootSpec,
    build_dep_name_index,
    resolve_all_dependencies,
    resolve_dependencies,
)
from collider.utils.packaging.types import RepoKey
from collider.utils.project_state import remove_installed_artifacts


class Add(SubcommandInterface):  # pylint: disable=too-many-instance-attributes
    """Add a package dependency to the project."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return 'Add a package dependency to the project.'

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg add --help`."""
        del cls
        return (
            'Resolve the requested package across configured repositories and install '
            'the newest version.\nUpdates collider.json to keep dependency intent '
            'consistent; use `collider lock` to refresh collider.lock explicitly.'
        )

    @staticmethod
    def epilog() -> Optional[str]:
        """Optional examples appended to the help output."""
        return None

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        parser.add_argument('package', type=str, help='Name of the package to add.')
        parser.add_argument(
            '--version',
            '-v',
            type=SpecifierSet,
            required=False,
            help='Version constraint to resolve and persist in collider.json.',
        )
        parser.add_argument(
            '--offline',
            action='store_true',
            help='Disable network access and rely on local cache.',
        )
        parser.add_argument(
            '--include',
            nargs='+',
            action='extend',
            default=None,
            help='Force-include specific transitive dependency names.',
        )
        parser.add_argument(
            '--exclude',
            nargs='+',
            action='extend',
            default=None,
            help='Force-exclude specific transitive dependency names.',
        )
        parser.add_argument(
            '--include-conditional',
            action='store_true',
            default=False,
            help='Include conditional dependencies (inside if-blocks).',
        )
        parser.add_argument(
            '--exclude-optional',
            action='store_true',
            default=False,
            help='Exclude optional dependencies (required: false).',
        )
        parser.add_argument(
            '--force',
            '-f',
            action='store_true',
            default=False,
            help='Reinstall even if the package is already present.',
        )

    def __init__(self, args: argparse.Namespace, context: Context):
        """
        Store add arguments and context.
        :param args: Parsed CLI arguments (package name, offline, etc.).
        :param context: Application context.
        """
        super().__init__(args, context)
        self.package_name: str = args.package
        self.version_spec: Optional[SpecifierSet] = getattr(args, 'version', None)
        self.offline: bool = bool(args.offline or context.offline)
        self.include_names: Optional[list[str]] = getattr(args, 'include', None)
        self.exclude_names: Optional[list[str]] = getattr(args, 'exclude', None)
        self.include_conditional: bool = bool(getattr(args, 'include_conditional', False))
        self.exclude_optional: bool = bool(getattr(args, 'exclude_optional', False))
        self.force: bool = bool(getattr(args, 'force', False))

    @override
    def execute(self) -> int:
        """Run the add command.
        :return: Exit code.
        """
        logger.debug(f'Installing package "{self.package_name}" in "{Path.cwd().as_posix()}".')
        if not Path.cwd().joinpath('meson.build').exists():
            logger.critical('No meson.build file found in current directory.')
            return os.EX_NOINPUT

        colliderfile_path = Path.cwd().joinpath(Colliderfile.get_filename())
        if colliderfile_path.exists():
            if colliderfile_path.is_dir():
                logger.critical('collider.json exists but is a directory.')
                return os.EX_DATAERR
            colliderfile = Colliderfile.from_path(colliderfile_path)
        else:
            colliderfile = Colliderfile()
            colliderfile.save(colliderfile_path)
            logger.info('Created collider.json.')
        version_spec_str = self._resolve_version_specifier_text(colliderfile)

        wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{self.package_name}.wrap'
        if wrap_path.exists() and not self.force:
            already_declared = any(
                dep.name == self.package_name and dep.source == DependencySource.COLLIDER
                for dep in colliderfile.dependencies
            )
            if not already_declared and self._is_existing_wrap_transitive(colliderfile):
                logger.info(
                    f'Package "{self.package_name}" is already installed; '
                    'adding it to collider.json as a direct dependency.'
                )
                self._add_dependency(
                    colliderfile,
                    RepoPackageEntry(self.package_name, '', PackageType.WRAP),
                    version_spec_str,
                )
                if Path.cwd().joinpath(Lockfile.get_filename()).exists():
                    logger.warning(
                        f'collider.lock was not updated for "{self.package_name}"; '
                        'run "collider lock" to refresh it.'
                    )
                return os.EX_OK
            logger.error(f'Package "{self.package_name}" is already installed.')
            logger.info('Use --force to reinstall.')
            return os.EX_DATAERR

        if self.force:
            remove_installed_artifacts(self.package_name)

        try:
            version_spec = self.version_spec or (
                SpecifierSet(version_spec_str) if version_spec_str is not None else None
            )
        except InvalidSpecifier:
            logger.critical(
                f'Invalid version constraint "{version_spec_str}" for "{self.package_name}".'
            )
            return os.EX_DATAERR

        repos = dict(self.context.config.repositories.items())
        logger.debug(f'Searching across {len(repos)} repositories for "{self.package_name}".')
        newest = self._find_newest_package(repos, version_spec)
        if newest is None:
            constraint_suffix = (
                f' matching version constraint "{version_spec_str}".'
                if version_spec_str is not None
                else '.'
            )
            logger.critical(f'No package matching query{constraint_suffix}')
            return os.EX_UNAVAILABLE

        newest_entry, newest_repo_name, newest_repo, newest_key = newest
        logger.info(f'Package "{self.package_name}" found in repository "{newest_repo_name}".')
        logger.debug(
            f'Selected package "{newest_entry.name}" version "{newest_entry.version}" '
            f'with key "{newest_key}".'
        )

        package = self._install_package(newest_entry, newest_repo, newest_key)
        if package is None:
            return os.EX_IOERR

        transitive_result = self._resolve_and_install_transitive(repos, version_spec_str)
        if transitive_result != os.EX_OK:
            self._rollback_package(newest_entry.name, package)
            return transitive_result

        self._add_dependency(colliderfile, newest_entry, version_spec_str)
        self._warn_if_lockfile_stale(newest_entry, package)
        return os.EX_OK

    def _is_existing_wrap_transitive(self, colliderfile: Colliderfile) -> bool:
        """
        Determine whether an installed wrap is already required transitively.

        :param colliderfile: Current project dependency declarations.
        :return: True if the package is known to be transitive, False otherwise.
        """
        lockfile_path = Path.cwd() / Lockfile.get_filename()
        if lockfile_path.exists():
            try:
                lockfile = Lockfile.from_path(lockfile_path)
                if self.package_name in lockfile.packages:
                    return True
            except Exception:
                pass

        direct_deps = [
            dep
            for dep in colliderfile.dependencies
            if dep.source == DependencySource.COLLIDER and dep.name != self.package_name
        ]
        if not direct_deps:
            return False

        repos = dict(self.context.config.repositories.items())
        dep_name_index = build_dep_name_index(repos)
        if not dep_name_index:
            return False

        root_specs = [
            RootSpec(
                name=dep.name,
                version_spec=dep.version,
                include_names=set(dep.include) if dep.include else None,
                exclude_names=set(dep.exclude) if dep.exclude else None,
            )
            for dep in direct_deps
        ]

        try:
            resolution = resolve_all_dependencies(
                roots=root_specs,
                repos=repos,
                offline=bool(self.offline),
                wrap_cache=self.context.cache,
                include_conditional=True,
                exclude_optional=False,
            )
        except (
            resolvelib.RequirementsConflicted,
            resolvelib.ResolutionImpossible,
            resolvelib.ResolutionTooDeep,
            Exception,
        ):
            return False

        return self.package_name in resolution.mapping

    def _find_newest_package(
        self,
        repos: dict[str, RepositoryInterface],
        version_spec: Optional[SpecifierSet] = None,
    ) -> Optional[tuple[RepoPackageEntry, str, RepositoryInterface, RepoKey]]:
        all_matches = search_packages(
            repos,
            re.compile(f'^{re.escape(self.package_name)}$'),
            version_spec,
        )
        if not all_matches:
            return None
        logger.debug(f'Found matches in {len(all_matches)} repositories for "{self.package_name}".')

        # Prefer the highest version across all repositories to avoid surprising downgrades.
        newest_entry: Optional[RepoPackageEntry] = None
        newest_repo_name: Optional[str] = None
        newest_repo: Optional[RepositoryInterface] = None
        newest_key: Optional[RepoKey] = None

        for repo_name, packages in all_matches.items():
            for repo_key, package in packages.items():
                try:
                    candidate_version = packaging.version.parse(package.version)
                except InvalidVersion:
                    logger.warning(
                        f'Skipping package "{package.name}" with invalid version '
                        f'"{package.version}".'
                    )
                    continue

                if newest_entry is None:
                    newest_entry = package
                    newest_repo_name = repo_name
                    newest_repo = repos[repo_name]
                    newest_key = repo_key
                    continue

                try:
                    newest_version = packaging.version.parse(newest_entry.version)
                except InvalidVersion:
                    logger.warning(
                        f'Skipping package "{newest_entry.name}" with invalid version '
                        f'"{newest_entry.version}".'
                    )
                    newest_entry = package
                    newest_repo_name = repo_name
                    newest_repo = repos[repo_name]
                    newest_key = repo_key
                    continue

                if candidate_version > newest_version:
                    newest_entry = package
                    newest_repo_name = repo_name
                    newest_repo = repos[repo_name]
                    newest_key = repo_key

        assert newest_entry is not None
        assert newest_repo_name is not None
        assert newest_repo is not None
        assert newest_key is not None

        return newest_entry, newest_repo_name, newest_repo, newest_key

    def _install_package(
        self,
        entry: RepoPackageEntry,
        repo: RepositoryInterface,
        repo_key: RepoKey,
        *,
        quiet: bool = False,
    ) -> Optional[WrapPackage]:
        """
        Fetch, cache, and install a package into subprojects.
        :param entry: Package metadata from the repository index.
        :param repo: Repository that owns the package.
        :param repo_key: Encoded repository key for the package.
        :param quiet: Log install progress at DEBUG instead of INFO.
        :return: Installed WrapPackage on success, None on failure.
        """
        log = logger.debug if quiet else logger.info
        log(f'Installing package "{entry.name}" (version {entry.version})...')
        logger.debug(f'Fetching package "{repo_key}".')

        package: WrapPackage | None = None
        if self.offline and repo.requires_network():
            package = self.context.cache.load_wrap(entry.name, entry.version)
            if package is None:
                logger.critical('Package not found in cache for offline install.')
                return None
            logger.info('Using cached wrap (offline).')
        else:
            fetched = repo.get_package(repo_key)
            if fetched is None:
                logger.critical('Failed to fetch package from repository.')
                return None
            if not isinstance(fetched, WrapPackage):
                logger.critical('Repository returned an unsupported package type.')
                return None
            package = fetched
            self.context.cache.store_wrap(package)
            logger.debug('Wrap fetched from repository and cached.')

        if package is None:
            logger.critical('Package not available for installation.')
            return None

        subproject_path = Path.cwd() / SUBPROJECTS_DIR / entry.name
        logger.debug(f'Installing package to "{subproject_path.as_posix()}".')
        if subproject_path.exists():
            logger.critical(
                f'Subproject directory "{subproject_path}" already exists. '
                'Remove or rename it (or uninstall the existing dependency) before installing.'
            )
            return None

        try:
            logger.debug('Preparing Meson package cache for offline resolution.')
            self.context.cache.prepare_packagecache(
                package,
                Path.cwd() / SUBPROJECTS_DIR,
                offline=self.offline,
            )
            logger.debug('Package cache updated.')
        except (FileNotFoundError, ValueError) as e:
            logger.critical(str(e))
            return None

        try:
            logger.debug('Writing wrap file into subprojects.')
            package.install_to_subproject(subproject_path)
            logger.debug('Wrap file installed.')
        except FileExistsError as exc:
            logger.critical(
                f'{exc} Remove or rename the existing file/directory before installing this package.'
            )
            return None
        return package

    @staticmethod
    def _rollback_package(name: str, package: WrapPackage) -> None:
        """
        Remove a direct package's artifacts after a transitive resolution failure.
        :param name: Package name.
        :param package: Installed WrapPackage whose files should be cleaned up.
        """
        wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{name}.wrap'
        if wrap_path.exists():
            wrap_path.unlink()
            logger.debug(f'Rolled back wrap file "{wrap_path}".')

        packagecache = Path.cwd() / SUBPROJECTS_DIR / 'packagecache'
        for filename in (package.source_filename, package.patch_filename):
            if filename:
                cached = packagecache / filename
                if cached.exists():
                    cached.unlink()
                    logger.debug(f'Rolled back cached archive "{cached}".')

        logger.warning(f'Rolled back package "{name}" because transitive resolution failed.')

    def _resolve_and_install_transitive(
        self,
        repos: dict[str, RepositoryInterface],
        version_spec_str: Optional[str] = None,
    ) -> int:
        """
        Resolve and install transitive dependencies via scan + resolvelib.
        :param repos: All configured repositories.
        :param version_spec_str: Version constraint for the root package.
        :return: Exit code.
        """
        include_set = set(self.include_names) if self.include_names else set()
        exclude_set = set(self.exclude_names) if self.exclude_names else set()

        dep_name_index = build_dep_name_index(repos)
        if not dep_name_index:
            logger.debug('No dependency name index available; skipping transitive resolution.')
            return os.EX_OK

        try:
            result = resolve_dependencies(
                root_name=self.package_name,
                root_version_spec=version_spec_str,
                repos=repos,
                offline=self.offline,
                wrap_cache=self.context.cache,
                include_conditional=self.include_conditional,
                exclude_optional=self.exclude_optional,
                include_names=include_set,
                exclude_names=exclude_set,
            )
        except (
            resolvelib.RequirementsConflicted,
            resolvelib.ResolutionImpossible,
            resolvelib.ResolutionTooDeep,
        ) as e:
            logger.critical(f'Dependency resolution failed: {e}')
            return os.EX_UNAVAILABLE

        transitive_candidates = [
            (name, cand) for name, cand in result.mapping.items() if name != self.package_name
        ]

        installed: list[tuple[str, str]] = []
        already_present: list[tuple[str, str]] = []
        failed: list[str] = []
        progress = tqdm(
            transitive_candidates,
            desc='Installing dependencies',
            unit='pkg',
            leave=False,
            disable=not transitive_candidates or logger.level <= logging.DEBUG,
        )
        for pkg_name, candidate in progress:
            progress.set_postfix_str(pkg_name)

            wrap_path = Path.cwd() / SUBPROJECTS_DIR / f'{pkg_name}.wrap'
            if wrap_path.exists():
                logger.debug(f'Transitive dependency "{pkg_name}" already installed, skipping.')
                already_present.append((pkg_name, candidate.version))
                continue

            subproject_path = Path.cwd() / SUBPROJECTS_DIR / pkg_name
            if subproject_path.exists():
                logger.debug(f'Subproject directory for "{pkg_name}" already exists, skipping.')
                already_present.append((pkg_name, candidate.version))
                continue

            repo = repos.get(candidate.repo_name)
            if repo is None:
                logger.critical(f'Repository "{candidate.repo_name}" unavailable for "{pkg_name}".')
                failed.append(pkg_name)
                continue

            repo_key = make_repo_key(pkg_name, candidate.version, PackageType.WRAP)
            trans_entry = RepoPackageEntry(pkg_name, candidate.version, PackageType.WRAP)

            trans_package = self._install_package(trans_entry, repo, repo_key, quiet=True)
            if trans_package is None:
                failed.append(pkg_name)
                continue

            installed.append((pkg_name, candidate.version))
        progress.close()

        self._log_resolution_summary(result.summary, installed, already_present)

        if failed:
            names = ', '.join(failed)
            logger.critical(f'Failed to install transitive dependencies: {names}.')
            return os.EX_IOERR
        return os.EX_OK

    @staticmethod
    def _format_name_list(names: set[str], indent: int = 2) -> list[str]:
        """Wrap sorted names into indented lines that fit the terminal width."""
        width = shutil.get_terminal_size().columns
        sorted_names = sorted(names)
        lines: list[str] = []
        current = ' ' * indent

        for name in sorted_names:
            if current.strip() and len(current) + len(name) + 2 > width:
                lines.append(current.rstrip())
                current = ' ' * indent
            current += name + '  '

        if current.strip():
            lines.append(current.rstrip())
        return lines

    @staticmethod
    def _log_resolution_summary(
        summary: ResolutionSummary,
        installed: list[tuple[str, str]],
        already_present: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        already_present = already_present or []
        n_installed = len(installed)
        n_present = len(already_present)
        n_optional = len(summary.included_optional)
        n_conditional = len(summary.skipped_conditional)
        n_optional_skipped = len(summary.skipped_optional)

        if installed:
            logger.info('')
            logger.info('Transitive dependencies:')
            Add._log_name_version_list(installed)

        if already_present:
            logger.info('')
            logger.info('Already installed:')
            Add._log_name_version_list(already_present)

        if summary.included_optional:
            logger.info('')
            logger.info('Included optional:')
            for line in Add._format_name_list(summary.included_optional):
                logger.info(line)
            logger.info('  Use --exclude-optional or --exclude to skip.')

        if summary.skipped_conditional_by_pkg:
            logger.info('')
            logger.info('Skipped conditional:')
            Add._log_grouped_skips(summary.skipped_conditional_by_pkg)
            logger.info('  Use --include-conditional or --include to override.')

        if summary.skipped_optional_by_pkg:
            logger.info('')
            logger.info('Skipped optional:')
            Add._log_grouped_skips(summary.skipped_optional_by_pkg)
            logger.info('  Use --include or --exclude-optional=false to override.')

        if summary.unmapped_system:
            names = ', '.join(sorted(summary.unmapped_system))
            logger.debug(f'Assumed system: {names}.')

        parts: list[str] = []
        if n_installed:
            parts.append(f'{n_installed} installed')
        if n_present:
            parts.append(f'{n_present} already present')
        if n_optional:
            parts.append(f'{n_optional} optional included')
        if n_conditional:
            parts.append(f'{n_conditional} conditional skipped')
        if n_optional_skipped:
            parts.append(f'{n_optional_skipped} optional skipped')
        if parts:
            logger.info('')
            logger.info(f'Summary: {", ".join(parts)}.')

    @staticmethod
    def _log_name_version_list(items: list[tuple[str, str]]) -> None:
        """Log a list of (name, version) pairs wrapped to terminal width."""
        width = shutil.get_terminal_size().columns
        formatted = [f'{name} ({version})' for name, version in sorted(items)]
        current = '  '
        for item in formatted:
            if current.strip() and len(current) + len(item) + 2 > width:
                logger.info(current.rstrip())
                current = '  '
            current += item + '  '
        if current.strip():
            logger.info(current.rstrip())

    @staticmethod
    def _log_grouped_skips(groups: dict[str, list[str]]) -> None:
        """Log skipped dependency names grouped by providing package."""
        width = shutil.get_terminal_size().columns
        for pkg, deps in groups.items():
            prefix = f'  {pkg}: '
            line = prefix
            for i, dep in enumerate(deps):
                suffix = dep if i == len(deps) - 1 else f'{dep}, '
                if len(line) + len(suffix) > width and line != prefix:
                    logger.info(line.rstrip(', '))
                    line = ' ' * len(prefix)
                line += suffix
            if line.strip():
                logger.info(line)

    def _add_dependency(
        self,
        colliderfile: Colliderfile,
        entry: RepoPackageEntry,
        version_spec: Optional[str],
    ) -> None:
        """
        Record the dependency in collider.json if not already present.
        :param colliderfile: Project colliderfile to update.
        :param entry: Resolved package entry.
        :param version_spec: Optional version constraint to persist.
        """
        include_list = sorted(self.include_names) if self.include_names else None
        exclude_list = sorted(self.exclude_names) if self.exclude_names else None
        inc_cond = True if self.include_conditional else None
        exc_opt = True if self.exclude_optional else None

        for existing_dep in colliderfile.dependencies:
            if existing_dep.name == entry.name and existing_dep.source == DependencySource.COLLIDER:
                changed = False
                if existing_dep.version != version_spec:
                    existing_dep.version = version_spec
                    changed = True
                if include_list and existing_dep.include != include_list:
                    existing_dep.include = include_list
                    changed = True
                if exclude_list and existing_dep.exclude != exclude_list:
                    existing_dep.exclude = exclude_list
                    changed = True
                if inc_cond != existing_dep.include_conditional:
                    existing_dep.include_conditional = inc_cond
                    changed = True
                if exc_opt != existing_dep.exclude_optional:
                    existing_dep.exclude_optional = exc_opt
                    changed = True
                if changed:
                    colliderfile.save()
                logger.debug(f'Dependency "{entry.name}" already declared in colliderfile.')
                return

        logger.info('Adding dependency to colliderfile.')
        new_dep = Dependency(
            entry.name,
            DependencySource.COLLIDER,
            version=version_spec,
            exclude=exclude_list,
            include=include_list,
            include_conditional=inc_cond,
            exclude_optional=exc_opt,
        )
        colliderfile.dependencies.append(new_dep)
        colliderfile.save()

    def _warn_if_lockfile_stale(self, entry: RepoPackageEntry, package: WrapPackage) -> None:
        """
        Warn when an existing lockfile no longer matches the installed package.
        :param entry: Resolved package entry.
        :param package: Installed wrap package.
        """
        lockfile_path = Path.cwd() / Lockfile.get_filename()
        if not lockfile_path.exists():
            return

        lockfile = Lockfile.from_path(lockfile_path)
        locked = lockfile.all_packages.get(entry.name)
        actual_hash = compute_wrap_hash(package.wrap_text)

        if locked is None or locked.version != entry.version or locked.wrap_hash != actual_hash:
            logger.warning(
                f'collider.lock was not updated for "{entry.name}"; '
                'run "collider lock" to refresh it.'
            )

    def _resolve_version_specifier_text(self, colliderfile: Colliderfile) -> Optional[str]:
        """
        Determine which version constraint should be used for resolution.
        :param colliderfile: Project colliderfile to inspect for existing declarations.
        :return: Version specifier string, or None if unconstrained.
        """
        if self.version_spec is not None:
            return str(self.version_spec)

        for dep in colliderfile.dependencies:
            if dep.name != self.package_name or dep.source != DependencySource.COLLIDER:
                continue
            return dep.version

        return None
