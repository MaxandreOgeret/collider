---
hide_from_nav: true
hide:
 - toc
---

# Building a Package Manager on Top of Meson's Wrap System

<p style="display: flex; align-items: center; justify-content: center; gap: 1rem;">
  <img src="https://collider.ee/assets/logo.svg" alt="Collider logo" style="height: 121px; width: auto;" />
  <span style="font-size: 3rem; line-height: 1;">&amp;</span>
  <img src="https://mesonbuild.com/assets/images/meson_logo.png" alt="Meson logo" style="height: 121px; width: auto;" />
</p>

Meson has a built-in dependency mechanism called wraps: a `.wrap` file points at a source archive (with a hash), and Meson downloads, extracts, and builds it as a subproject. WrapDB is the public index of wraps. It works, but if you want to host your own wrap packages, manage versions with a lockfile, or resolve transitive dependencies, you are on your own.

I built Collider to fill that gap. The core insight is that Meson already gives you most of what a package manager needs: a wrap format with integrity hashes, a public package index, `subdir()`-based subproject inclusion, and an introspection API that can discover every dependency in a project without running a configure step or needing compilers. Collider adds the missing pieces (lockfile, resolution, private hosting) while relying on Meson for the heavy lifting. That leverage is what keeps the tool small and the feature set broad.

This post explains how it works under the hood and what design decisions it makes. If you use Meson or are interested in how dependency resolution can be layered on top of an existing build system without replacing it, read on.

## The wrap format: what we start with

A Meson `.wrap` file is an TOML config that tells Meson where to get a source archive and (optionally) a patch archive:

```ini
[wrap-file]
directory = libfoo-1.2.0
source_url = https://example.com/libfoo-1.2.0.tar.xz
source_filename = libfoo-1.2.0.tar.xz
source_hash = sha256:abc123...

patch_url = https://example.com/libfoo-1.2.0-meson.zip
patch_filename = libfoo-1.2.0-meson.zip
patch_hash = sha256:def456...

[provide]
libfoo = libfoo_dep
```

The `[provide]` section is important: it declares which Meson `dependency()` names this wrap satisfies. So `dependency('libfoo')` in a consumer's `meson.build` can fall back to this subproject.

WrapDB serves these files plus a `releases.json` index over HTTP. The format is simple, URL-based, and already understood by Meson. Collider reuses all of this as-is.

## Publishing: from `meson.build` to a wrap in your repo

`collider publish <repo>` reads your project's name and version from Meson introspection (specifically from `intro-projectinfo.json` in the build directory), builds a source archive of the source tree, generates a `.wrap` file pointing at the repo's `publish_url`, and writes both to the repository. If the wrap has a `[provide]` section, the package's `dependency_names` are recorded in the repo's `releases.json` index (this matters for transitive resolution, explained below).

`collider setup` is a thin wrapper around `meson setup` that creates the build directory Collider reads from. The build system is still Meson; Collider only needs the introspection output.

A repository can be a filesystem directory (Collider reads/writes directly), or an HTTP server exposing the same WrapDB-style layout. `collider serve` is a built-in server for that. For filesystem repos, the config has two paths:

```json
{
  "name": "local",
  "type": "filesystem",
  "url": "file:///srv/my-wraps",
  "publish_url": "https://packages.example.com/wraps/"
}
```

`url` is where Collider reads and writes on disk. `publish_url` is the HTTP base that gets embedded in generated wrap files so consumers can fetch archives over the network.

## Version constraints and `collider.json`

Dependencies are declared in `collider.json`. Each entry has a name, a source type, and an optional version constraint:

```json
{
  "dependencies": [
    { "name": "libfoo", "source": "collider", "version": ">=1.0,<2.0" },
    { "name": "libbar", "source": "collider" }
  ]
}
```

The `source` field indicates the package type; `collider` means a wrap package resolved from configured repositories. Version constraints follow [PEP 440](https://peps.python.org/pep-0440/#version-specifiers) syntax (the same format pip uses): `>=1.0`, `==1.2.0`, `~=1.4`, `>=1.0,<2.0`, etc. Collider uses Python's `packaging.specifiers.SpecifierSet` to parse and evaluate them. If no constraint is specified, the resolver picks the newest available version.

Per-dependency overrides for the scanner are also declared here: `exclude`, `include`, `include_conditional`, and `exclude_optional` control how `--scan-dependencies` results are filtered for that specific package during transitive resolution.

## Lockfile: integrity and provenance

`collider lock` resolves all dependencies from `collider.json` and writes `collider.lock`. Each entry records the resolved version and a SHA-256 of the entire `.wrap` file text:

```json
{
  "dependencies": {
    "my-lib": {
      "version": "1.2.0",
      "wrap_hash": "sha256:abc123...",
      "origin": "https://packages.example.com/wraps"
    }
  },
  "packages": {
    "libfoo": {
      "version": "1.0.0",
      "wrap_hash": "sha256:def456...",
      "origin": "https://wrapdb.mesonbuild.com/v2"
    }
  }
}
```

`dependencies` holds direct deps (from `collider.json`); `packages` holds transitive deps. Integrity is enforced at three points:

1. **Lock time:** `collider lock` downloads and verifies source and patch archive hashes before writing the lockfile. If any archive hash mismatches, the lockfile is not written. This catches bad publishes, corruption, and tampering early, before a lockfile is committed. It also guarantees that every archive referenced by the lockfile was available and correct at lock time, rather than deferring that discovery to the build.
2. **Lockfile to wrap (install):** Collider checks the fetched wrap file SHA-256 against `wrap_hash`. A mismatch is always a hard failure.
3. **Wrap to archive (build):** Meson checks the archive SHA-256 when extracting.

Each locked entry also records the `origin`: the normalized URL (lowercase scheme and host, trailing slash stripped) of the repository the package was resolved from. On install, Collider fetches each package from the configured repository matching the locked origin (the same normalization is applied when comparing). If no configured repository matches, install fails. There is no fallback to other repositories (except in offline mode, described below), so provenance is pinned per package.

This directly addresses dependency confusion. If an attacker publishes `libfoo 1.0.0` on a public repo, but the lockfile says `origin` is your private repo, install will not accept the public copy. The lockfile diff also makes it visible when a package migrates between repos across lock runs.

The one exception is offline installs: when the origin repo requires network access and is unreachable, Collider falls back to the local cache with a warning. The cache does not track which repository a wrap came from, so provenance is unverifiable in this case. Content integrity is still enforced via `wrap_hash`, but origin provenance is not.

## Dependency resolution: resolvelib, not custom code

Collider does not implement its own resolution algorithm. It uses [resolvelib](https://github.com/sarugaku/resolvelib), the same library that pip uses for Python package resolution. resolvelib is a generic dependency resolver: it handles graph walking, backtracking when a candidate leads to a conflict, cycle detection, and producing a consistent set of versions. It does not know about Meson, wraps, or C++ libraries.

Collider connects resolvelib to the wrap ecosystem by implementing resolvelib's `AbstractProvider` interface. The key methods:

| Method | What Collider does |
|--------|--------------------|
| `identify()` | Returns the package name. |
| `get_preference()` | Prefers packages with fewer candidates (fail-fast on conflicts). |
| `find_matches()` | Searches configured repositories for wrap versions matching the constraint; returns newest-first. |
| `is_satisfied_by()` | Checks if a candidate version satisfies a version specifier. |
| `get_dependencies()` | Downloads source and patch archives, extracts and overlays them, runs `meson introspect --scan-dependencies`, maps discovered dependency names to wrap packages via the reverse index, and returns the requirements. |

The interesting method is `get_dependencies()`. This is where Meson introspection enters the resolution loop. It is also the expensive one: for every candidate resolvelib considers (including ones it backtracks away from), Collider downloads and extracts the source archive to scan it. To keep this tractable, scan results are cached by a `name_version` key, both in memory (a dict on the provider) during a single resolution run and on disk as JSON files in the local wrap cache across runs. A candidate that was scanned once is never scanned again, even if the resolver revisits it after backtracking. The cache key does not include a content hash: it assumes the same name+version always produces the same scan output, which holds as long as the repository does not mutate published versions. If someone force-publishes over an existing version, the cache serves stale scan results; the lockfile hash check at install time will catch the mismatch, but the error message will point at the wrong layer. For typical dependency trees (a handful of direct deps, each with a few transitive deps), resolution completes in seconds. For deep trees with many version candidates, the cache prevents redundant work, though the first resolution of a large graph will still require downloading every candidate at least once.

## Dependency discovery: `meson introspect --scan-dependencies`

**Requires Meson >= 1.8.5.** This subcommand is relatively new and is the minimum Meson version Collider supports for transitive resolution.

This is where Meson's design makes Collider powerful. `--scan-dependencies` parses a project's `meson.build` using Meson's own AST parser and returns every `dependency()` call as structured JSON, **without requiring a configured build directory, compilers, or system libraries**. That last part is key: Collider can discover what a package depends on by extracting the source, pointing Meson at it, and reading the output. No configure step, no toolchain, no host environment. A CI job with nothing but Python and Meson installed can resolve a full transitive dependency tree.

Meson's scanner also follows `subdir()` calls recursively, walking into all included `meson.build` files and extracting `dependency()` calls from the entire project tree. Even conditional `subdir()` blocks (inside `if` statements) are walked, because the scanner operates on the AST without evaluating conditions. So you get a complete picture of every dependency the project could possibly need, regardless of how the project splits its build files across directories. This is what makes the approach robust: Collider does not need its own parser, and Meson's parser does not cut corners.

The output looks like this:

```json
[
  {"name": "libfoo", "required": true, "version": [], "has_fallback": false, "conditional": false},
  {"name": "libbar", "required": false, "version": [], "has_fallback": false, "conditional": false}
]
```

For each candidate package during resolution, Collider:

1. Fetches the wrap and archives from the repo.
2. Extracts the source archive. If the wrap has a patch archive (common for WrapDB packages where upstream does not ship `meson.build`), the patch is overlaid on top.
3. Runs `--scan-dependencies` on the extracted `meson.build`.
4. Classifies each result.

Classification uses these rules:

| Condition | Action |
|-----------|--------|
| `required: true, conditional: false` | Include: hard dependency. |
| `required: false` | Include (inform user): optional but available. |
| `required: false, has_fallback: true` | Include: project expects a subproject fallback. |
| `conditional: true` | Exclude (summarized): may not execute depending on build options. |
| Well-known system dep (`threads`, `openmp`, `dl`, etc.) | Skip: compiler/OS intrinsics, cannot be provided by wraps. |
| Not in any configured repo | Skip (inform): assumed system dependency. |

`required: get_option(...)` is treated as `required: true` because the option value is unknown at scan time. This is the conservative interpretation.

The default of including `required: false` deps deserves explanation. In Meson, `dependency('foo', required: false)` means "use it if the system has it." In a wrap-based world, "the system has it" translates to "a configured repo has a wrap for it." Including these by default matches what Meson would do if the subproject were available: use it. The `--exclude-optional` CLI flag (equivalent to setting `exclude_optional` per root in `collider.json`) and per-root `exclude` overrides exist for cases where this default pulls in unwanted packages.

Because the scanner walks the full AST without evaluating conditions, it over-approximates: you see every dependency the project could ever need, not just the ones your build configuration will use. For small-to-medium projects this is harmless (a few extra packages that get built but never linked). For large projects with many platform-specific or feature-gated backends, the extra deps can cause version conflicts or pull in unwanted packages. The per-root overrides (`exclude`, `include_conditional`, `exclude_optional`) exist for this reason: they let you prune the scan results for specific packages without changing the scan itself.

## Mapping dependency names to packages

Meson dependency names (e.g. `absl_algorithm_container`) do not necessarily match wrap package names (e.g. `abseil-cpp`). The connection is the wrap's `[provide]` section, which declares what Meson dependency names a package satisfies.

Collider reads `dependency_names` from each repository's `releases.json` (populated when you `collider publish`) and builds a reverse index once per resolution run: Meson dependency name to (package name, versions). When `--scan-dependencies` reports that a package needs `absl_algorithm_container`, the resolver looks up which wrap provides it and adds that as a requirement for resolvelib.

## Single-pass, multi-root resolution

`collider lock` and `collider install` feed **all** direct dependencies from `collider.json` into one resolvelib session. This means if package A needs `libfoo>=1.0` and package B needs `libfoo<1.0`, the resolver detects the conflict and fails with a clear error instead of silently picking one version.

Per-root overrides (`include`, `exclude`, `include_conditional`, `exclude_optional`) are scoped: they only apply when scanning that root's `meson.build`. The resolver stores them in a `root_overrides` map and looks them up by candidate name in `get_dependencies()`.

## Self-hosting: what `collider serve` does and does not do

`collider serve /path/to/repo` exposes WrapDB-compatible GET routes under `/v2/` (`releases.json`, wrap files, archives). Write routes (publish/unpublish) are disabled by default and enabled with a static bearer token (`--push-token` or `--push-token-env`).

What it does not do: TLS, signature verification, or read authentication. Reads are unauthenticated. The push token is a static bearer token that travels in cleartext unless you terminate TLS upstream. The lockfile's hash verification catches tampered wraps on install, but the publish path is protected only by this token.

This is intentional: `collider serve` is a development and testing server. For production use, put a reverse proxy (nginx, Caddy) in front for TLS and access control. Do not expose `collider serve` directly on a network you do not trust, especially with `--push-token` enabled: a leaked or sniffed token gives full write access to the repository.

## Extending the WrapDB HTTP API

WrapDB's v2 API is read-only: `GET /v2/releases.json` for the package index, `GET /v2/<name>_<version>/<name>.wrap` for wrap files, and `GET /v2/archives/...` for archives. Collider serves these same endpoints unchanged, so any tool that reads from WrapDB can also read from a Collider repository.

The addition is a write API namespaced under `/v2/_collider/v1/`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v2/_collider/v1/push` | Publish a package |
| `DELETE` | `/v2/_collider/v1/packages/<name>/<version>` | Unpublish a package |

The `_collider` prefix avoids any conflict with WrapDB's own routes, and the `v1` allows the write API to evolve independently of the read API.

**Atomic publish.** `collider publish` sends the wrap text, source archive, and optional patch archive in a single JSON request (archives base64-encoded). Either the entire package is published or nothing is. There is no multi-step upload where a partial failure leaves the repository in an inconsistent state.

**Pluggable auth.** Write endpoints are gated by a `PushAuthProvider` interface. The built-in implementation is a static bearer token compared with `hmac.compare_digest`. The interface exists so stronger mechanisms (OIDC, mTLS) can be added without changing the server. Read endpoints are unauthenticated by design: the lockfile's hash verification is the integrity boundary, not read access control.

**Path allowlisting.** The server only serves paths matching the WrapDB URL patterns. Everything else returns 404. Directory listing is disabled. This reduces the attack surface when serving from a filesystem repository.

## Trade-offs vs. Conan and vcpkg

Collider removes one kind of friction: adopting a full package-manager ecosystem (recipes, ports, manifests, overlays) and changing how you run or integrate builds. It stays inside Meson and the wrap format.

What you give up:

- **Binary caching:** Conan caches compiled binaries per configuration. Collider has no binary cache; every consumer builds from source. For projects where dependency build time dominates (large C++ trees with heavy template libraries), this is a real cost. It limits Collider's practical fit to projects where the dependency graph is small enough that building from source on every CI run is acceptable.
- **Option propagation:** Conan propagates settings and options across the dependency graph. Collider does not manage build configuration; it only manages wraps.
- **Overlay / registry model:** vcpkg manifest mode with custom overlays and registries. Collider has repos and a lockfile.

What you already get from Meson and WrapDB (not from Collider, but Collider does not need to replicate them):

- **Cross-compilation:** Meson handles it natively via cross files. Collider deals with wraps, not build configuration.
- **Curated catalog:** WrapDB is Meson's curated package index. Collider works with WrapDB as one of its repository types.

So: if you need binary caching or option propagation across the graph, use Conan. If you need a large curated port collection independent of Meson, use vcpkg. If your need is "Meson-centric, private wrap hosting, versioned deps with a lockfile, and transitive resolution," Collider fills that gap with fewer moving parts. Because Meson's introspection is powerful enough to discover the full dependency tree without a build directory or compilers, and resolvelib handles conflict detection and backtracking, Collider gets a lot of capability from very little code.

## Roadmap

### Planned

- **Package and repository signing:** Cryptographic signatures on wraps and archives so consumers can verify who published a package, not just that the content matches a hash. This defends against supply chain attacks: impersonation, tampering, and compromised repos.

---

Any questions? Comments? Feedback?
Join the discussion on [Github](https://github.com/MaxandreOgeret/collider/discussions).

\- MOG
