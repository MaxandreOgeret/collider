# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Tests for the transitive dependency resolver."""

import tarfile
import zipfile

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import resolvelib

from collider.repository.entries import (
    RejectedEntry,
    RejectReason,
    RepoPackageEntry,
    add_wrap_entry,
)
from collider.repository.implementation.RepositoryInterface import RepositoryInterface
from collider.utils.meson.scan import ScannedDependency, filter_dependencies
from collider.utils.packaging.resolver import (
    Candidate,
    ColliderProvider,
    MalformedRepositoryMetadata,
    Requirement,
    ResolutionSummary,
    RootSpec,
    _filter_satisfied_skips,
    _group_by_package,
    _ProgressReporter,
    build_dep_name_index,
    build_rejected_name_index,
    resolve_all_dependencies,
    resolve_dependencies,
)


def _make_packages(*specs: tuple[str, str, list[str] | None]) -> dict:
    """Build a packages dict from (name, version, dependency_names) tuples."""
    packages: dict = {}
    for name, version, dep_names in specs:
        add_wrap_entry(packages, name, version, dep_names)
    return packages


def _make_repo(
    packages: dict,
    requires_network: bool = False,
) -> MagicMock:
    repo = MagicMock(spec=RepositoryInterface)
    repo.packages = packages
    repo.requires_network.return_value = requires_network

    def search_side_effect(name_pattern, version_spec=None):
        return {
            k: v
            for k, v in packages.items()
            if name_pattern.match(v.name)
            and (version_spec is None or version_spec.contains(v.version))
        }

    repo.search.side_effect = search_side_effect
    return repo


# -- build_dep_name_index -----------------------------------------------------


def test_dep_index_basic_mapping() -> None:
    """Each dependency name maps to its parent package name."""
    packages = _make_packages(
        ('abseil-cpp', '20240722.0', ['absl_base', 'absl_strings']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    index = build_dep_name_index({'local': repo})

    assert index['absl_base'] == 'abseil-cpp'
    assert index['absl_strings'] == 'abseil-cpp'
    assert index['zlib'] == 'zlib'


def test_dep_index_missing_dep_name_returns_none() -> None:
    """Unknown dep names are absent from the index."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    index = build_dep_name_index({'local': repo})

    assert index.get('nonexistent') is None


def test_dep_index_no_dependency_names() -> None:
    """Packages with no dependency_names produce no index entries."""
    packages = _make_packages(('mylib', '1.0', None))
    repo = _make_repo(packages)
    index = build_dep_name_index({'local': repo})

    assert len(index) == 0


def test_dep_index_multiple_repos() -> None:
    """Entries from multiple repositories are merged."""
    repo1 = _make_repo(_make_packages(('zlib', '1.3.1', ['zlib'])))
    repo2 = _make_repo(_make_packages(('openssl', '3.0.0', ['openssl', 'libcrypto'])))
    index = build_dep_name_index({'repo1': repo1, 'repo2': repo2})

    assert index['zlib'] == 'zlib'
    assert index['openssl'] == 'openssl'
    assert index['libcrypto'] == 'openssl'


def test_dep_index_deduplicates_across_versions() -> None:
    """Duplicate dep names across versions collapse to one entry."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    index = build_dep_name_index({'local': repo})

    assert index['zlib'] == 'zlib'


def test_dep_index_repo_without_packages_attr() -> None:
    """Repos missing a packages attribute are skipped gracefully."""
    repo = MagicMock()
    del repo.packages
    index = build_dep_name_index({'local': repo})

    assert len(index) == 0


# -- Requirement model ---------------------------------------------------------


def test_requirement_equality() -> None:
    """Requirements with the same name are equal."""
    r1 = Requirement('zlib')
    r2 = Requirement('zlib')
    assert r1 == r2


def test_requirement_inequality() -> None:
    """Requirements with different names are not equal."""
    r1 = Requirement('zlib')
    r2 = Requirement('openssl')
    assert r1 != r2


def test_requirement_with_version_spec() -> None:
    """A version spec string is parsed into a SpecifierSet."""
    r = Requirement('zlib', '>=1.2')
    assert r.name == 'zlib'
    assert r.version_spec is not None


def test_requirement_hash_consistent_with_equality() -> None:
    """Equal requirements have the same hash and collapse in sets."""
    r1 = Requirement('zlib')
    r2 = Requirement('zlib')
    assert hash(r1) == hash(r2)
    assert len({r1, r2}) == 1


def test_requirement_repr_without_version() -> None:
    """Repr omits the version when none is set."""
    r = Requirement('zlib')
    assert repr(r) == "Requirement('zlib')"


def test_requirement_repr_with_version() -> None:
    """Repr includes the version constraint."""
    r = Requirement('zlib', '>=1.2')
    assert 'zlib' in repr(r)
    assert '>=1.2' in repr(r)


def test_requirement_not_equal_to_non_requirement() -> None:
    """Comparison with a non-Requirement returns NotImplemented."""
    r = Requirement('zlib')
    assert r != 'zlib'


# -- Candidate model -----------------------------------------------------------


def test_candidate_attributes() -> None:
    """All constructor arguments are stored as attributes."""
    c = Candidate('zlib', '1.3.1', 'local')
    assert c.name == 'zlib'
    assert c.version == '1.3.1'
    assert c.repo_name == 'local'


def test_candidate_equality() -> None:
    """Candidates with same name and version are equal."""
    c1 = Candidate('zlib', '1.3.1', 'local')
    c2 = Candidate('zlib', '1.3.1', 'local')
    assert c1 == c2


def test_candidate_inequality_different_version() -> None:
    """Candidates with different versions are not equal."""
    c1 = Candidate('zlib', '1.3.1', 'local')
    c2 = Candidate('zlib', '1.2.0', 'local')
    assert c1 != c2


def test_candidate_hash_consistent_with_equality() -> None:
    """Equal candidates (name+version+repo) share a hash and collapse in sets."""
    c1 = Candidate('zlib', '1.3.1', 'local')
    c2 = Candidate('zlib', '1.3.1', 'local')
    assert hash(c1) == hash(c2)
    assert len({c1, c2}) == 1


def test_candidate_distinct_by_repo() -> None:
    """Same name and version from different repos are distinct candidates."""
    c1 = Candidate('zlib', '1.3.1', 'local')
    c2 = Candidate('zlib', '1.3.1', 'remote')
    assert c1 != c2
    assert len({c1, c2}) == 2


def test_candidate_incompatibility_scoped_to_repo() -> None:
    """Marking one repo's candidate incompatible does not drop another repo's identical version."""
    repo1 = _make_repo(_make_packages(('zlib', '1.3.1', ['zlib'])))
    repo2 = _make_repo(_make_packages(('zlib', '1.3.1', ['zlib'])))
    repos = {'repo1': repo1, 'repo2': repo2}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    bad = Candidate('zlib', '1.3.1', 'repo1')
    identifier = provider.identify(req)
    matches = list(
        provider.find_matches(
            identifier=identifier,
            requirements={identifier: [req]},
            incompatibilities={identifier: [bad]},
        )
    )

    repo_names = {m.repo_name for m in matches}
    assert repo_names == {'repo2'}


def test_candidate_repr() -> None:
    """Repr includes all three fields."""
    c = Candidate('zlib', '1.3.1', 'local')
    assert repr(c) == "Candidate('zlib', '1.3.1', 'local')"


def test_candidate_not_equal_to_non_candidate() -> None:
    """Comparison with a non-Candidate returns NotImplemented."""
    c = Candidate('zlib', '1.3.1', 'local')
    assert c != 'zlib'


# -- ColliderProvider ----------------------------------------------------------


def test_provider_identify_requirement() -> None:
    """identify() returns the requirement name."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    assert provider.identify(req) == 'zlib'


def test_provider_identify_candidate() -> None:
    """identify() returns the candidate name."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    cand = Candidate('zlib', '1.3.1', 'local')
    assert provider.identify(cand) == 'zlib'


def test_provider_find_matches_returns_candidates() -> None:
    """find_matches returns candidates sorted newest-first."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    identifier = provider.identify(req)
    matches = list(
        provider.find_matches(
            identifier=identifier,
            requirements={identifier: [req]},
            incompatibilities={identifier: []},
        )
    )

    assert len(matches) == 2
    versions = [m.version for m in matches]
    assert versions[0] == '1.3.1'
    assert versions[1] == '1.2.0'


def test_provider_find_matches_skips_unsafe_version() -> None:
    """find_matches drops entries whose version is not a safe path segment."""
    packages: dict = {}
    add_wrap_entry(packages, 'foo', '../evil', None)
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('foo')
    identifier = provider.identify(req)
    matches = list(
        provider.find_matches(
            identifier=identifier,
            requirements={identifier: [req]},
            incompatibilities={identifier: []},
        )
    )

    assert matches == []


def test_provider_is_satisfied_by_no_constraint() -> None:
    """Any candidate satisfies a requirement with no version spec."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    cand = Candidate('zlib', '1.3.1', 'local')
    assert provider.is_satisfied_by(req, cand) is True


def test_provider_is_satisfied_by_matching_constraint() -> None:
    """A candidate within the version range satisfies the requirement."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=1.0')
    cand = Candidate('zlib', '1.3.1', 'local')
    assert provider.is_satisfied_by(req, cand) is True


def test_provider_is_satisfied_by_failing_constraint() -> None:
    """A candidate outside the version range is rejected."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=2.0')
    cand = Candidate('zlib', '1.3.1', 'local')
    assert provider.is_satisfied_by(req, cand) is False


def test_provider_find_matches_filters_by_version_constraint() -> None:
    """Candidates outside the version spec are excluded."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=1.3')
    identifier = provider.identify(req)
    matches = provider.find_matches(
        identifier=identifier,
        requirements={identifier: [req]},
        incompatibilities={identifier: []},
    )

    assert len(matches) == 1
    assert matches[0].version == '1.3.1'


def test_provider_find_matches_filters_incompatibilities() -> None:
    """Incompatible candidates are excluded from results."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    bad = Candidate('zlib', '1.3.1', 'local')
    identifier = provider.identify(req)
    matches = provider.find_matches(
        identifier=identifier,
        requirements={identifier: [req]},
        incompatibilities={identifier: [bad]},
    )

    assert len(matches) == 1
    assert matches[0].version == '1.2.0'


def test_provider_find_matches_excludes_prerelease_when_stable_exists() -> None:
    """A prerelease is not offered when a stable version satisfies the constraint."""
    packages = _make_packages(
        ('zlib', '1.3.1', ['zlib']),
        ('zlib', '2.0.0rc1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=1.0')
    identifier = provider.identify(req)
    matches = list(
        provider.find_matches(
            identifier=identifier,
            requirements={identifier: [req]},
            incompatibilities={identifier: []},
        )
    )

    assert [m.version for m in matches] == ['1.3.1']


def test_provider_find_matches_falls_back_to_prerelease_when_only_option() -> None:
    """A prerelease is offered when no stable version satisfies the constraint."""
    packages = _make_packages(('zlib', '2.0.0rc1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=1.0')
    identifier = provider.identify(req)
    matches = list(
        provider.find_matches(
            identifier=identifier,
            requirements={identifier: [req]},
            incompatibilities={identifier: []},
        )
    )

    assert [m.version for m in matches] == ['2.0.0rc1']


def test_provider_is_satisfied_by_accepts_prerelease_for_stable_spec() -> None:
    """A prerelease candidate satisfies a stable specifier; the version range still applies."""
    packages = _make_packages(('zlib', '2.0.0rc1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib', '>=1.0')
    assert provider.is_satisfied_by(req, Candidate('zlib', '2.0.0rc1', 'local')) is True
    assert provider.is_satisfied_by(req, Candidate('zlib', '0.9.0rc1', 'local')) is False


def test_provider_find_matches_skips_invalid_versions() -> None:
    """Packages with unparseable versions are silently dropped."""
    packages = _make_packages(('zlib', 'not-a-version', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))

    req = Requirement('zlib')
    identifier = provider.identify(req)
    matches = provider.find_matches(
        identifier=identifier,
        requirements={identifier: [req]},
        incompatibilities={identifier: []},
    )

    assert len(matches) == 0


def test_provider_get_dependencies_maps_deps_to_requirements() -> None:
    """Scanned dep names are mapped to package-level Requirements."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True)]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
        },
    )

    candidate = Candidate('grpc', '1.0.0', 'local')
    deps = provider.get_dependencies(candidate)

    assert len(deps) == 1
    assert deps[0].name == 'zlib'


def test_provider_get_dependencies_skips_self_reference() -> None:
    """A dependency on the candidate's own package is excluded."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True)]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'zlib_1.3.1': scanned,
        },
    )

    candidate = Candidate('zlib', '1.3.1', 'local')
    deps = provider.get_dependencies(candidate)

    assert len(deps) == 0


def test_provider_get_dependencies_deduplicates() -> None:
    """Duplicate dep names in a single scan collapse to one Requirement."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [
        ScannedDependency(name='zlib', required=True),
        ScannedDependency(name='zlib', required=False),
    ]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
        },
    )

    candidate = Candidate('grpc', '1.0.0', 'local')
    deps = provider.get_dependencies(candidate)

    assert len(deps) == 1


def test_provider_get_dependencies_passes_version_constraints() -> None:
    """Version constraints from the scan are forwarded to Requirements."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True, version=['>=1.2'])]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
        },
    )

    candidate = Candidate('grpc', '1.0.0', 'local')
    deps = provider.get_dependencies(candidate)

    assert len(deps) == 1
    assert deps[0].version_spec is not None
    assert deps[0].version_spec.contains('1.3.1')


def test_provider_get_dependencies_unmapped_reported_once() -> None:
    """An unmapped dep is added to the warning set only once."""
    packages = _make_packages(('grpc', '1.0.0', ['grpc']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='unknown_lib', required=True)]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
            'grpc_2.0.0': scanned,
        },
    )

    cand1 = Candidate('grpc', '1.0.0', 'local')
    cand2 = Candidate('grpc', '2.0.0', 'local')
    provider.get_dependencies(cand1)
    provider.get_dependencies(cand2)

    assert 'unknown_lib' in provider._warned_system_deps


def test_provider_get_dependencies_uses_scan_cache() -> None:
    """Repeated calls for the same candidate use the cached scan."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True)]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
        },
    )

    candidate = Candidate('grpc', '1.0.0', 'local')
    deps1 = provider.get_dependencies(candidate)
    deps2 = provider.get_dependencies(candidate)

    assert deps1 == deps2


def test_provider_get_dependencies_merges_version_constraints() -> None:
    """Duplicate deps with different version specs have their constraints merged."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [
        ScannedDependency(name='zlib', required=True, version=['>=1.2']),
        ScannedDependency(name='zlib', required=True, version=['>=1.3']),
    ]
    provider = ColliderProvider(
        repos,
        dep_index,
        scan_cache={
            'grpc_1.0.0': scanned,
        },
    )

    candidate = Candidate('grpc', '1.0.0', 'local')
    deps = provider.get_dependencies(candidate)

    assert len(deps) == 1
    assert deps[0].name == 'zlib'
    assert deps[0].version_spec is not None
    assert deps[0].version_spec.contains('1.3.1')
    assert not deps[0].version_spec.contains('1.2.0')


def test_provider_offline_flag_stored() -> None:
    """The offline flag is stored on the provider instance."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    provider = ColliderProvider(repos, dep_index, offline=True)
    assert provider.offline is True

    provider2 = ColliderProvider(repos, dep_index)
    assert provider2.offline is False


def test_resolve_passes_offline_to_provider() -> None:
    """resolve_dependencies forwards the offline flag to ColliderProvider."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}

    captured_provider: list[ColliderProvider] = []
    original_init = ColliderProvider.__init__

    def capturing_init(self_prov, *args, **kwargs):
        original_init(self_prov, *args, **kwargs)
        captured_provider.append(self_prov)

    with (
        patch(
            'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
            return_value=[],
        ),
        patch.object(ColliderProvider, '__init__', capturing_init),
    ):
        resolve_dependencies(
            root_name='zlib',
            root_version_spec=None,
            repos=repos,
            offline=True,
        )

    assert len(captured_provider) == 1
    assert captured_provider[0].offline is True


def test_resolve_passes_strict_to_provider() -> None:
    """resolve_dependencies forwards the strict flag to ColliderProvider."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}

    captured_provider: list[ColliderProvider] = []
    original_init = ColliderProvider.__init__

    def capturing_init(self_prov, *args, **kwargs):
        original_init(self_prov, *args, **kwargs)
        captured_provider.append(self_prov)

    with (
        patch(
            'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
            return_value=[],
        ),
        patch.object(ColliderProvider, '__init__', capturing_init),
    ):
        resolve_dependencies(root_name='zlib', root_version_spec=None, repos=repos, strict=True)

    assert captured_provider[0].strict is True


# -- strict-mode metadata rejection -------------------------------------------


def _rejected(name, reason):
    """Build a RejectedEntry for tests."""
    return RejectedEntry(name=name, reason=reason)


def test_rejected_name_index_collects_by_name() -> None:
    """build_rejected_name_index keys rejects by package name."""
    repo = _make_repo({})
    repo.rejected_metadata = [_rejected('foo', RejectReason.UNSAFE_VERSION)]
    index = build_rejected_name_index({'local': repo})
    assert index['foo'].reason is RejectReason.UNSAFE_VERSION


def test_strict_find_matches_raises_when_only_rejected() -> None:
    """A needed root with no valid versions and a rejected entry fails closed in strict mode."""
    repo = _make_repo({})  # No usable packages for 'foo'.
    repo.rejected_metadata = [_rejected('foo', RejectReason.UNSAFE_VERSION)]
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos), strict=True)

    req = Requirement('foo')
    with pytest.raises(MalformedRepositoryMetadata):
        list(
            provider.find_matches(
                identifier='foo',
                requirements={'foo': [req]},
                incompatibilities={'foo': []},
            )
        )


def test_strict_find_matches_tolerates_rejected_sibling() -> None:
    """A valid version alongside a rejected sibling still resolves: no shared-infra DoS."""
    packages = _make_packages(('foo', '1.0.0', ['foo']))
    repo = _make_repo(packages)
    repo.rejected_metadata = [_rejected('foo', RejectReason.UNSAFE_VERSION)]
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos), strict=True)

    req = Requirement('foo')
    matches = list(
        provider.find_matches(
            identifier='foo', requirements={'foo': [req]}, incompatibilities={'foo': []}
        )
    )
    assert [m.version for m in matches] == ['1.0.0']


def test_tolerant_find_matches_never_raises_on_rejected() -> None:
    """Without strict, a rejected-only package just returns no candidates (search stays tolerant)."""
    repo = _make_repo({})
    repo.rejected_metadata = [_rejected('foo', RejectReason.UNSAFE_VERSION)]
    repos = {'local': repo}
    provider = ColliderProvider(repos, build_dep_name_index(repos))  # strict defaults to False.

    matches = list(
        provider.find_matches(
            identifier='foo',
            requirements={'foo': [Requirement('foo')]},
            incompatibilities={'foo': []},
        )
    )
    assert matches == []


def test_strict_get_dependencies_does_not_fail_on_unmapped_dep() -> None:
    """An unmapped dependency() name is indistinguishable from a system dep: strict must NOT fail.

    Keying fail-closed on an unmapped name would let a forged "provides" claim for a common system
    library (e.g. threads/dl/m) brick every project, so strict mode deliberately demotes it to a
    system dependency just like tolerant mode.
    """
    repo = _make_repo({})
    repo.rejected_metadata = [_rejected('bar', RejectReason.UNSAFE_VERSION)]
    repos = {'local': repo}
    scanned = [ScannedDependency(name='libbar', required=True)]
    provider = ColliderProvider(
        repos,
        build_dep_name_index(repos),
        strict=True,
        scan_cache={'foo_1.0.0': scanned},
    )

    assert provider.get_dependencies(Candidate('foo', '1.0.0', 'local')) == []
    assert 'libbar' in provider.all_unmapped


# -- _extract_archive ---------------------------------------------------------


def test_extract_archive_tar(tmp_path: Path) -> None:
    """A tar.gz archive is extracted successfully."""
    archive = tmp_path / 'test.tar.gz'
    content_dir = tmp_path / 'src'
    content_dir.mkdir()
    (content_dir / 'meson.build').write_text("project('test', 'c')")
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(content_dir / 'meson.build', arcname='src/meson.build')

    dest = tmp_path / 'out'
    dest.mkdir()
    assert ColliderProvider._extract_archive(archive, dest) is True
    assert (dest / 'src' / 'meson.build').exists()


def test_extract_archive_zip(tmp_path: Path) -> None:
    """A zip archive is extracted successfully."""
    archive = tmp_path / 'test.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('src/meson.build', "project('test', 'c')")

    dest = tmp_path / 'out'
    dest.mkdir()
    assert ColliderProvider._extract_archive(archive, dest) is True
    assert (dest / 'src' / 'meson.build').exists()


def test_extract_archive_corrupt_returns_false(tmp_path: Path) -> None:
    """A corrupt file returns False instead of raising."""
    archive = tmp_path / 'corrupt.tar.gz'
    archive.write_bytes(b'this is not an archive')

    dest = tmp_path / 'out'
    dest.mkdir()
    assert ColliderProvider._extract_archive(archive, dest) is False


# -- _patch_extract_target -----------------------------------------------------


def test_patch_target_matching_prefix_extracts_to_root(tmp_path: Path) -> None:
    """When the patch prefix matches the source subdir, extract to root."""
    extract_dir = tmp_path / 'extract'
    extract_dir.mkdir()
    source_subdir = extract_dir / 'pkg-1.0'
    source_subdir.mkdir()

    patch = tmp_path / 'patch.zip'
    with zipfile.ZipFile(patch, 'w') as zf:
        zf.writestr('pkg-1.0/meson.build', "project('test', 'c')")

    target = ColliderProvider._patch_extract_target(patch, extract_dir)
    assert target == extract_dir


def test_patch_target_different_prefix_extracts_to_subdir(tmp_path: Path) -> None:
    """When the patch prefix differs, extract into the source subdir."""
    extract_dir = tmp_path / 'extract'
    extract_dir.mkdir()
    source_subdir = extract_dir / 'pkg-1.0'
    source_subdir.mkdir()

    patch = tmp_path / 'patch.zip'
    with zipfile.ZipFile(patch, 'w') as zf:
        zf.writestr('other/meson.build', "project('test', 'c')")

    target = ColliderProvider._patch_extract_target(patch, extract_dir)
    assert target == source_subdir


def test_patch_target_no_source_subdir_extracts_to_root(tmp_path: Path) -> None:
    """When no single source subdir exists, extract to root."""
    extract_dir = tmp_path / 'extract'
    extract_dir.mkdir()
    (extract_dir / 'file.txt').write_text('data')

    patch = tmp_path / 'patch.zip'
    with zipfile.ZipFile(patch, 'w') as zf:
        zf.writestr('meson.build', "project('test', 'c')")

    target = ColliderProvider._patch_extract_target(patch, extract_dir)
    assert target == extract_dir


def test_patch_target_tar_patch_with_matching_prefix(tmp_path: Path) -> None:
    """Tar-format patches with a matching prefix extract to root."""
    extract_dir = tmp_path / 'extract'
    extract_dir.mkdir()
    source_subdir = extract_dir / 'pkg-1.0'
    source_subdir.mkdir()

    patch = tmp_path / 'patch.tar.gz'
    with tarfile.open(patch, 'w:gz') as tar:
        import io

        data = b"project('test', 'c')"
        info = tarfile.TarInfo(name='pkg-1.0/meson.build')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    target = ColliderProvider._patch_extract_target(patch, extract_dir)
    assert target == extract_dir


# -- _find_meson_build ---------------------------------------------------------


def test_find_meson_build_direct(tmp_path: Path) -> None:
    """meson.build at the root of the extract dir is found."""
    (tmp_path / 'meson.build').write_text("project('test', 'c')")
    assert ColliderProvider._find_meson_build(tmp_path) == tmp_path / 'meson.build'


def test_find_meson_build_nested_one_level(tmp_path: Path) -> None:
    """meson.build nested one directory deep is found."""
    subdir = tmp_path / 'pkg-1.0'
    subdir.mkdir()
    (subdir / 'meson.build').write_text("project('test', 'c')")
    assert ColliderProvider._find_meson_build(tmp_path) == subdir / 'meson.build'


def test_find_meson_build_not_found(tmp_path: Path) -> None:
    """None is returned when no meson.build exists."""
    (tmp_path / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.10)')
    assert ColliderProvider._find_meson_build(tmp_path) is None


# -- _find_source_subdir -------------------------------------------------------


def test_find_source_subdir_single(tmp_path: Path) -> None:
    """A single child directory is returned."""
    subdir = tmp_path / 'pkg-1.0'
    subdir.mkdir()
    assert ColliderProvider._find_source_subdir(tmp_path) == subdir


def test_find_source_subdir_multiple_returns_none(tmp_path: Path) -> None:
    """Multiple child directories yield None."""
    (tmp_path / 'dir1').mkdir()
    (tmp_path / 'dir2').mkdir()
    assert ColliderProvider._find_source_subdir(tmp_path) is None


def test_find_source_subdir_no_subdirs_returns_none(tmp_path: Path) -> None:
    """No child directories yields None."""
    (tmp_path / 'file.txt').write_text('data')
    assert ColliderProvider._find_source_subdir(tmp_path) is None


# -- resolve_dependencies -----------------------------------------------------


def test_resolve_single_package_no_transitive() -> None:
    """A package with no transitive deps resolves to just itself."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        return_value=[],
    ):
        result = resolve_dependencies(
            root_name='zlib',
            root_version_spec=None,
            repos=repos,
        )

    assert 'zlib' in result.mapping
    assert result.mapping['zlib'].version == '1.3.1'


def test_resolve_single_package_with_version_constraint() -> None:
    """Version constraints restrict which candidate is selected."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        return_value=[],
    ):
        result = resolve_dependencies(
            root_name='zlib',
            root_version_spec='>=1.3',
            repos=repos,
        )

    assert 'zlib' in result.mapping
    assert result.mapping['zlib'].version == '1.3.1'


def test_resolve_transitive_resolution() -> None:
    """Transitive deps are pulled in recursively."""
    packages = _make_packages(
        ('grpc', '1.59.1', ['grpc', 'grpc++']),
        ('abseil-cpp', '20240722.0', ['absl_base', 'absl_strings']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    call_count = 0

    def mock_get_deps(self_prov, candidate):
        nonlocal call_count
        call_count += 1
        if candidate.name == 'grpc':
            return [Requirement('abseil-cpp')]
        return []

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        mock_get_deps,
    ):
        result = resolve_dependencies(
            root_name='grpc',
            root_version_spec=None,
            repos=repos,
        )

    assert 'grpc' in result.mapping
    assert 'abseil-cpp' in result.mapping


def test_resolve_summary_collects_metadata() -> None:
    """ResolutionSummary accumulates filtering metadata from the provider."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    scanned = [
        ScannedDependency(name='zlib', required=True),
        ScannedDependency(name='unknown', required=True),
        ScannedDependency(name='cond_dep', required=True, conditional=True),
        ScannedDependency(name='opt_dep', required=False),
    ]

    def mock_get_deps(self_prov, candidate):
        if candidate.name == 'grpc':
            filter_result = filter_dependencies(scanned)
            self_prov.all_skipped_conditional.update(filter_result.skipped_conditional)
            self_prov.all_included_optional.update(filter_result.included_optional)
            for dep in filter_result.included:
                pkg = self_prov.dep_name_index.get(dep.name)
                if pkg is None:
                    self_prov.all_unmapped.add(dep.name)
            return [Requirement('zlib')]
        return []

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        mock_get_deps,
    ):
        result = resolve_dependencies(
            root_name='grpc',
            root_version_spec=None,
            repos=repos,
        )

    assert 'cond_dep' in result.summary.skipped_conditional
    assert 'opt_dep' in result.summary.included_optional
    assert 'unknown' in result.summary.unmapped_system


# -- _scan_candidate offline behavior -----------------------------------------


def test_scan_candidate_offline_uses_cache_when_repo_requires_network(
    tmp_path: Path,
) -> None:
    """In offline mode, _scan_candidate loads from wrap_cache instead of repo."""
    import hashlib

    content = b'source-payload'
    content_hash = hashlib.sha256(content).hexdigest()

    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=True)
    repos = {'remote': repo}
    dep_index = build_dep_name_index(repos)

    from collider.cache import WrapCache
    from collider.Package import WrapPackage

    cached_pkg = WrapPackage.from_wrap_text(
        'zlib',
        '1.3.1',
        f'[wrap-file]\n'
        f'source_url=https://example.com/zlib-1.3.1.tar.xz\n'
        f'source_filename=zlib-1.3.1.tar.xz\n'
        f'source_hash={content_hash}\n',
    )

    mock_cache = MagicMock(spec=WrapCache)
    mock_cache.load_wrap.return_value = cached_pkg

    provider = ColliderProvider(repos, dep_index, offline=True, wrap_cache=mock_cache)
    candidate = Candidate('zlib', '1.3.1', 'remote')

    # _scan_candidate will try prepare_packagecache which will fail in the
    # temp dir, but that's fine - we only need to verify load_wrap was used.
    provider._scan_candidate(candidate)

    mock_cache.load_wrap.assert_called_once_with('zlib', '1.3.1')
    repo.get_package.assert_not_called()


def test_scan_candidate_offline_warns_when_no_cache_hit() -> None:
    """In offline mode, a missing cache entry yields a warning and empty list."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=True)
    repos = {'remote': repo}
    dep_index = build_dep_name_index(repos)

    mock_cache = MagicMock()
    mock_cache.load_wrap.return_value = None

    provider = ColliderProvider(repos, dep_index, offline=True, wrap_cache=mock_cache)
    candidate = Candidate('zlib', '1.3.1', 'remote')

    result = provider._scan_candidate(candidate)

    assert result == []
    mock_cache.load_wrap.assert_called_once_with('zlib', '1.3.1')
    repo.get_package.assert_not_called()


def test_scan_candidate_online_uses_repo_get_package() -> None:
    """When not offline and no wrap cache hit, _scan_candidate uses repo.get_package()."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=True)
    repo.get_package.return_value = None
    repos = {'remote': repo}
    dep_index = build_dep_name_index(repos)

    provider = ColliderProvider(repos, dep_index, offline=False)
    candidate = Candidate('zlib', '1.3.1', 'remote')

    result = provider._scan_candidate(candidate)

    assert result == []
    repo.get_package.assert_called_once()


def test_scan_candidate_online_tries_wrap_cache_before_http() -> None:
    """In online mode, _scan_candidate checks wrap_cache before calling repo.get_package()."""
    import hashlib

    from collider.cache import WrapCache
    from collider.Package import WrapPackage

    content = b'source-payload'
    content_hash = hashlib.sha256(content).hexdigest()

    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=True)
    repos = {'remote': repo}
    dep_index = build_dep_name_index(repos)

    cached_pkg = WrapPackage.from_wrap_text(
        'zlib',
        '1.3.1',
        f'[wrap-file]\n'
        f'source_url=https://example.com/zlib-1.3.1.tar.xz\n'
        f'source_filename=zlib-1.3.1.tar.xz\n'
        f'source_hash={content_hash}\n',
    )

    mock_cache = MagicMock(spec=WrapCache)
    mock_cache.load_wrap.return_value = cached_pkg
    mock_cache.load_scan.return_value = None

    provider = ColliderProvider(repos, dep_index, offline=False, wrap_cache=mock_cache)
    candidate = Candidate('zlib', '1.3.1', 'remote')

    provider._scan_candidate(candidate)

    mock_cache.load_wrap.assert_called_once_with('zlib', '1.3.1')
    repo.get_package.assert_not_called()


def test_scan_candidate_offline_local_repo_uses_get_package() -> None:
    """Offline mode with a local repo (no network needed) still uses get_package."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=False)
    repo.get_package.return_value = None
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    provider = ColliderProvider(repos, dep_index, offline=True)
    candidate = Candidate('zlib', '1.3.1', 'local')

    result = provider._scan_candidate(candidate)

    assert result == []
    repo.get_package.assert_called_once()


# -- resolve_all_dependencies -------------------------------------------------


def test_resolve_all_single_root_matches_resolve_dependencies() -> None:
    """A single-root resolve_all produces the same mapping as resolve_dependencies."""
    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages)
    repos = {'local': repo}

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        return_value=[],
    ):
        single = resolve_dependencies(
            root_name='zlib',
            root_version_spec=None,
            repos=repos,
        )
        multi = resolve_all_dependencies(
            roots=[RootSpec(name='zlib')],
            repos=repos,
        )

    assert single.mapping.keys() == multi.mapping.keys()
    assert single.mapping['zlib'].version == multi.mapping['zlib'].version


def test_resolve_all_multiple_roots() -> None:
    """Multiple independent roots are resolved in a single pass."""
    packages = _make_packages(
        ('zlib', '1.3.1', ['zlib']),
        ('openssl', '3.0.0', ['openssl']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        return_value=[],
    ):
        result = resolve_all_dependencies(
            roots=[RootSpec(name='zlib'), RootSpec(name='openssl')],
            repos=repos,
        )

    assert 'zlib' in result.mapping
    assert 'openssl' in result.mapping
    assert result.mapping['zlib'].version == '1.3.1'
    assert result.mapping['openssl'].version == '3.0.0'


def test_resolve_all_compatible_constraints_picks_tightest() -> None:
    """When two roots constrain a shared dep compatibly, the tightest wins."""
    packages = _make_packages(
        ('libfoo', '1.0.0', ['libfoo']),
        ('libbar', '1.0.0', ['libbar']),
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    def mock_get_deps(self_prov, candidate):
        if candidate.name == 'libfoo':
            return [Requirement('zlib', '>=1.2')]
        if candidate.name == 'libbar':
            return [Requirement('zlib', '>=1.3')]
        return []

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        mock_get_deps,
    ):
        result = resolve_all_dependencies(
            roots=[RootSpec(name='libfoo'), RootSpec(name='libbar')],
            repos=repos,
        )

    assert 'zlib' in result.mapping
    assert result.mapping['zlib'].version == '1.3.1'


def test_resolve_all_incompatible_constraints_raises() -> None:
    """Incompatible version constraints across roots raise ResolutionImpossible."""
    packages = _make_packages(
        ('libfoo', '1.0.0', ['libfoo']),
        ('libbar', '1.0.0', ['libbar']),
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    def mock_get_deps(self_prov, candidate):
        if candidate.name == 'libfoo':
            return [Requirement('zlib', '>=1.3')]
        if candidate.name == 'libbar':
            return [Requirement('zlib', '<1.3')]
        return []

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        mock_get_deps,
    ):
        with pytest.raises(
            (resolvelib.RequirementsConflicted, resolvelib.ResolutionImpossible),
        ):
            resolve_all_dependencies(
                roots=[RootSpec(name='libfoo'), RootSpec(name='libbar')],
                repos=repos,
            )


def test_resolve_all_keeps_per_root_include_exclude() -> None:
    """Per-root include/exclude overrides are stored separately, not merged globally."""
    packages = _make_packages(
        ('libfoo', '1.0.0', ['libfoo']),
        ('libbar', '1.0.0', ['libbar']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    captured_provider: list[ColliderProvider] = []
    original_init = ColliderProvider.__init__

    def capturing_init(self_prov, *args, **kwargs):
        original_init(self_prov, *args, **kwargs)
        captured_provider.append(self_prov)

    with (
        patch(
            'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
            return_value=[],
        ),
        patch.object(ColliderProvider, '__init__', capturing_init),
    ):
        resolve_all_dependencies(
            roots=[
                RootSpec(name='libfoo', include_names={'dep_a'}),
                RootSpec(name='libbar', include_names={'dep_b'}, exclude_names={'dep_c'}),
            ],
            repos=repos,
        )

    assert len(captured_provider) == 1
    prov = captured_provider[0]
    assert prov.include_names == set()
    assert prov.exclude_names == set()
    assert prov.root_overrides == {
        'libfoo': ({'dep_a'}, None),
        'libbar': ({'dep_b'}, {'dep_c'}),
    }


def test_resolve_all_per_root_exclude_does_not_leak() -> None:
    """Root A excluding a dep does not prevent Root B from pulling it in."""
    packages = _make_packages(
        ('libfoo', '1.0.0', ['libfoo']),
        ('libbar', '1.0.0', ['libbar']),
        ('libcommon', '1.0.0', ['libcommon']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    dep_name_index = build_dep_name_index(repos)

    provider = ColliderProvider(
        repos,
        dep_name_index,
        root_overrides={
            'libfoo': (None, {'libcommon'}),
        },
    )

    scan_results = {
        'libfoo_1.0.0': [
            ScannedDependency(name='libcommon', required=True),
        ],
        'libbar_1.0.0': [
            ScannedDependency(name='libcommon', required=True),
        ],
    }
    provider.scan_cache = scan_results

    foo_deps = provider.get_dependencies(Candidate('libfoo', '1.0.0', 'local'))
    assert len(foo_deps) == 0, 'libfoo should have libcommon excluded'

    bar_deps = provider.get_dependencies(Candidate('libbar', '1.0.0', 'local'))
    assert len(bar_deps) == 1
    assert bar_deps[0].name == 'libcommon'


def test_resolve_all_per_root_include_scopes_to_root() -> None:
    """Root A force-including a dep does not force-include it for Root B."""
    packages = _make_packages(
        ('libfoo', '1.0.0', ['libfoo']),
        ('libbar', '1.0.0', ['libbar']),
        ('libcond', '1.0.0', ['libcond']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    dep_name_index = build_dep_name_index(repos)

    provider = ColliderProvider(
        repos,
        dep_name_index,
        root_overrides={
            'libfoo': ({'libcond'}, None),
        },
    )

    scan_results = {
        'libfoo_1.0.0': [
            ScannedDependency(name='libcond', required=True, conditional=True),
        ],
        'libbar_1.0.0': [
            ScannedDependency(name='libcond', required=True, conditional=True),
        ],
    }
    provider.scan_cache = scan_results

    foo_deps = provider.get_dependencies(Candidate('libfoo', '1.0.0', 'local'))
    assert len(foo_deps) == 1, 'libfoo force-includes libcond'

    bar_deps = provider.get_dependencies(Candidate('libbar', '1.0.0', 'local'))
    assert len(bar_deps) == 0, 'libbar should skip conditional libcond (no override)'


def test_get_dependencies_uses_persistent_scan_cache() -> None:
    """get_dependencies loads from persistent scan cache, skipping _scan_candidate."""
    from collider.cache import WrapCache

    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    cached_scan = [ScannedDependency(name='zlib', required=True)]

    mock_cache = MagicMock(spec=WrapCache)
    mock_cache.load_scan.return_value = cached_scan

    provider = ColliderProvider(repos, dep_index, wrap_cache=mock_cache)
    candidate = Candidate('grpc', '1.0.0', 'local')

    with patch.object(provider, '_scan_candidate') as mock_scan:
        deps = provider.get_dependencies(candidate)

    mock_cache.load_scan.assert_called_once_with('grpc', '1.0.0')
    mock_scan.assert_not_called()
    assert len(deps) == 1
    assert deps[0].name == 'zlib'


def test_get_dependencies_stores_scan_result_on_miss() -> None:
    """On scan cache miss, get_dependencies stores the result in persistent cache."""
    from collider.cache import WrapCache

    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True)]

    mock_cache = MagicMock(spec=WrapCache)
    mock_cache.load_scan.return_value = None

    provider = ColliderProvider(repos, dep_index, wrap_cache=mock_cache)
    candidate = Candidate('grpc', '1.0.0', 'local')

    with patch.object(provider, '_scan_candidate', return_value=scanned):
        deps = provider.get_dependencies(candidate)

    mock_cache.store_scan.assert_called_once_with('grpc', '1.0.0', scanned)
    assert len(deps) == 1
    assert deps[0].name == 'zlib'


def test_get_dependencies_no_persistent_cache_still_works() -> None:
    """Without a wrap_cache, get_dependencies falls back to _scan_candidate."""
    packages = _make_packages(
        ('grpc', '1.0.0', ['grpc']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}
    dep_index = build_dep_name_index(repos)

    scanned = [ScannedDependency(name='zlib', required=True)]
    provider = ColliderProvider(repos, dep_index)

    with patch.object(provider, '_scan_candidate', return_value=scanned):
        deps = provider.get_dependencies(Candidate('grpc', '1.0.0', 'local'))

    assert len(deps) == 1
    assert deps[0].name == 'zlib'


def test_scan_candidate_uses_persistent_archive_cache(tmp_path: Path) -> None:
    """_scan_candidate uses self.wrap_cache for archives instead of ephemeral cache."""
    import hashlib

    from collider.cache import WrapCache
    from collider.Package import WrapPackage

    content = b'source-payload'
    content_hash = hashlib.sha256(content).hexdigest()

    packages = _make_packages(('zlib', '1.3.1', ['zlib']))
    repo = _make_repo(packages, requires_network=True)
    repos = {'remote': repo}
    dep_index = build_dep_name_index(repos)

    cached_pkg = WrapPackage.from_wrap_text(
        'zlib',
        '1.3.1',
        f'[wrap-file]\n'
        f'source_url=https://example.com/zlib-1.3.1.tar.xz\n'
        f'source_filename=zlib-1.3.1.tar.xz\n'
        f'source_hash={content_hash}\n',
    )

    mock_cache = MagicMock(spec=WrapCache)
    mock_cache.load_wrap.return_value = cached_pkg
    mock_cache.load_scan.return_value = None

    provider = ColliderProvider(repos, dep_index, offline=False, wrap_cache=mock_cache)
    candidate = Candidate('zlib', '1.3.1', 'remote')

    provider._scan_candidate(candidate)

    mock_cache.prepare_packagecache.assert_called_once()
    args = mock_cache.prepare_packagecache.call_args
    assert args[0][0] == cached_pkg


def test_resolve_all_root_version_constraints_applied() -> None:
    """Version constraints on root packages themselves are respected."""
    packages = _make_packages(
        ('zlib', '1.2.0', ['zlib']),
        ('zlib', '1.3.1', ['zlib']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    with patch(
        'collider.utils.packaging.resolver.ColliderProvider.get_dependencies',
        return_value=[],
    ):
        result = resolve_all_dependencies(
            roots=[RootSpec(name='zlib', version_spec='<1.3')],
            repos=repos,
        )

    assert result.mapping['zlib'].version == '1.2.0'


# ---------------------------------------------------------------------------
# _group_by_package / _filter_satisfied_skips
# ---------------------------------------------------------------------------


def test_group_by_package_groups_and_sorts() -> None:
    """Dep names are grouped by providing package with sorted values."""
    dep_name_index = {
        'absl_cord': 'abseil-cpp',
        'absl_base': 'abseil-cpp',
        'gmock': 'gtest',
        'unknown_dep': 'gtest',
    }
    names = {'absl_cord', 'absl_base', 'gmock', 'unknown_dep'}
    result = _group_by_package(names, dep_name_index)

    assert result == {
        'abseil-cpp': ['absl_base', 'absl_cord'],
        'gtest': ['gmock', 'unknown_dep'],
    }


def test_group_by_package_unmapped_goes_to_system() -> None:
    """Deps not in the index are grouped under 'system'."""
    dep_name_index = {'absl_base': 'abseil-cpp'}
    names = {'absl_base', 'libfoo'}
    result = _group_by_package(names, dep_name_index)

    assert result == {
        'abseil-cpp': ['absl_base'],
        'system': ['libfoo'],
    }


def test_group_by_package_empty() -> None:
    """Empty input produces empty output."""
    assert _group_by_package(set(), {}) == {}


def test_filter_satisfied_skips_removes_resolved_conditional() -> None:
    """Conditional deps whose providing package is resolved are removed."""
    summary = ResolutionSummary(
        skipped_conditional={'absl_base', 'absl_cord', 'gmock'},
        skipped_optional={'protoc'},
        included_optional=set(),
        unmapped_system=set(),
        skipped_conditional_by_pkg={},
        skipped_optional_by_pkg={},
    )
    dep_name_index = {
        'absl_base': 'abseil-cpp',
        'absl_cord': 'abseil-cpp',
        'gmock': 'gtest',
        'protoc': 'protobuf',
    }
    resolved_names = {'abseil-cpp', 'protobuf'}

    _filter_satisfied_skips(summary, dep_name_index, resolved_names)

    assert summary.skipped_conditional == {'gmock'}
    assert summary.skipped_conditional_by_pkg == {'gtest': ['gmock']}
    # Optional deps are never filtered -- they reflect explicit user choice
    assert summary.skipped_optional == {'protoc'}
    assert summary.skipped_optional_by_pkg == {'protobuf': ['protoc']}


def test_filter_satisfied_skips_keeps_unresolved() -> None:
    """Skipped deps whose package is NOT resolved remain."""
    summary = ResolutionSummary(
        skipped_conditional={'absl_base', 'gmock'},
        skipped_optional={'protoc'},
        included_optional=set(),
        unmapped_system=set(),
        skipped_conditional_by_pkg={},
        skipped_optional_by_pkg={},
    )
    dep_name_index = {
        'absl_base': 'abseil-cpp',
        'gmock': 'gtest',
        'protoc': 'protobuf',
    }
    resolved_names = set()

    _filter_satisfied_skips(summary, dep_name_index, resolved_names)

    assert summary.skipped_conditional == {'absl_base', 'gmock'}
    assert summary.skipped_optional == {'protoc'}
    assert summary.skipped_conditional_by_pkg == {
        'abseil-cpp': ['absl_base'],
        'gtest': ['gmock'],
    }
    assert summary.skipped_optional_by_pkg == {'protobuf': ['protoc']}


def test_filter_satisfied_skips_unmapped_deps_kept() -> None:
    """Deps not in dep_name_index are never filtered (no providing package)."""
    summary = ResolutionSummary(
        skipped_conditional={'unknown_dep'},
        skipped_optional=set(),
        included_optional=set(),
        unmapped_system=set(),
        skipped_conditional_by_pkg={},
        skipped_optional_by_pkg={},
    )

    _filter_satisfied_skips(summary, {}, {'some-package'})

    assert summary.skipped_conditional == {'unknown_dep'}
    assert summary.skipped_conditional_by_pkg == {'system': ['unknown_dep']}


def test_resolve_dependencies_filters_satisfied_skips() -> None:
    """resolve_dependencies() filters skipped deps whose package is resolved."""
    packages = _make_packages(
        ('grpc', '1.59.1', ['grpc']),
        ('abseil-cpp', '20240722.0', ['absl_base', 'absl_cord']),
    )
    repo = _make_repo(packages)
    repos = {'local': repo}

    def fake_get_deps(self, candidate):
        if candidate.name == 'grpc':
            self.all_skipped_conditional.update({'absl_base', 'absl_cord'})
            return [Requirement('abseil-cpp')]
        return []

    with patch.object(ColliderProvider, 'get_dependencies', fake_get_deps):
        result = resolve_dependencies('grpc', None, repos)

    assert 'abseil-cpp' in result.mapping
    assert result.summary.skipped_conditional == set()
    assert result.summary.skipped_conditional_by_pkg == {}


@pytest.mark.parametrize('disable', [True, False])
def test_progress_reporter_forwards_disable_to_tqdm(disable: bool) -> None:
    """The resolution progress bar is created with the disable flag it was given."""
    with patch('collider.utils.packaging.resolver.tqdm') as mock_tqdm:
        _ProgressReporter(disable=disable).starting()

    assert mock_tqdm.call_args.kwargs['disable'] is disable


def test_resolve_dependencies_disables_progress_via_helper() -> None:
    """resolve_dependencies wires should_disable_progress into the progress bar."""
    repos: dict = {}
    with (
        patch('collider.utils.packaging.resolver.should_disable_progress', return_value=True),
        patch('collider.utils.packaging.resolver._ProgressReporter') as mock_reporter,
        patch.object(resolvelib.Resolver, 'resolve', side_effect=RuntimeError('stop')),
    ):
        with pytest.raises(RuntimeError, match='stop'):
            resolve_dependencies('grpc', None, repos)

    assert mock_reporter.call_args.kwargs['disable'] is True
