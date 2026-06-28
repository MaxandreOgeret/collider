# Collider Design

## Scope and Goals

- Provide a package manager for Meson projects, centered on Meson's wrap system.
- Keep the workflow minimal: publish, search, add, lock, and setup.
- Support offline installs by caching wraps and archives locally.
- Allow hosting of internal repositories with a single filesystem layout that is WrapDB-compatible.
- Verify integrity of archives and wraps at every stage (publish, cache, install, lock).

## Non-Goals

- Supporting build systems other than Meson.
- Providing a general-purpose package manager for arbitrary archive types.
- Managing binary artifacts. Collider manages only sources and patches as defined by wraps.

## Core Concepts

- **Wrap package.** A Meson `.wrap` file plus a source archive and optional patch archive.
- **Repository.** A local filesystem repository, a read-only WrapDB-compatible remote, or a Collider remote that extends WrapDB with write operations.
- **Cache.** A local store for wraps and archives that enables offline builds.

## Repository Types

### Filesystem Repository

Stores wraps and optional archives on disk. Regenerates `releases.json` from the repository layout on every add/remove (independent of whether the repository is served), keeping it WrapDB-compatible.

**Layout**

```
repo/
  releases.json
  <name>_<version>/
    <name>.wrap
  archives/
    <name>_<version>/
      <filename>
```

Patch archives may use any extension; `collider patch` produces `.tar.xz` by default.

**Rationale.** A single layout is straightforward to serve internally and maps directly to WrapDB clients.

**URL Mapping**

| Resource      | URL                                                   |
|---------------|-------------------------------------------------------|
| releases.json | `<repo_url>/v2/releases.json`                         |
| wrap file     | `<repo_url>/v2/<name>_<version>/<name>.wrap`          |
| archive file  | `<publish_url>/archives/<name>_<version>/<filename>`  |

`releases.json` and wrap URLs are derived consumer-side from the repository base URL, with `/v2` appended automatically. Archive URLs are different: they are built directly from `publish_url` with no `/v2` inserted. When archives are served over HTTP by `collider serve`, they are exposed only under `/v2/archives/...`, so in that case `publish_url` must include the `/v2` prefix to resolve there. Local `file://` archive URLs (the `serve` default) are read directly from disk and need no prefix.

### Wrap Repository (Remote)

- WrapDB-compatible endpoint backed by `releases.json` and wraps.
- Read-only: no publish or unpublish support.
- HTTPS preferred; HTTP is allowed with a warning.

**Rationale.** Aligns with Meson expectations and avoids maintaining a custom protocol.

### Collider Repository (Remote with Write Extension)

Extends the WrapDB read API with two write endpoints under a `_collider` namespace. Supports all read operations (search, add, info) like a wrap repository, with additional publish and unpublish operations.

**Write Endpoints**

| Method | Path                                               | Purpose              |
|--------|----------------------------------------------------|----------------------|
| POST   | `<base>/v2/_collider/v1/push`                      | Publish a package.   |
| DELETE | `<base>/v2/_collider/v1/packages/<name>/<version>` | Unpublish a package. |

- Both endpoints require `Authorization: Bearer <token>`.
- The push payload is a single JSON object containing the wrap text, source archive (base64-encoded), and optional patch archive.
- Unpublish returns `204` on success, `404` if the package is not found.

**Authentication**

- Built-in auth is intentionally minimal: a single static bearer token read from `$COLLIDER_PUSH_TOKEN` (or a configurable env var via `--push-token-env`).
- Production deployments should place a reverse proxy with proper authentication in front of the server.
- Collider keeps a pluggable auth seam for future in-process upgrades.

**Rationale**

- The WrapDB protocol is read-only. Without a write extension, publishing to a remote requires out-of-band file transfers.
- Namespacing under `_collider/v1` avoids conflicts with WrapDB `v2` routes and allows independent versioning.
- A single atomic push endpoint is simpler than multi-step upload flows and prevents partial-publish states.
- Static bearer token keeps the implementation minimal while preventing unauthorized writes.

## Wrap File Requirements

- `source_url`, `source_filename`, `source_hash` are required.
- Patch fields are optional, but if any patch field is present, all must be present.
- HTTP URLs are allowed with a warning. HTTPS or local file paths are preferred.

**Rationale**

- A complete source tuple is required for reproducible builds.
- Mixed patch metadata creates ambiguous cache behavior.
- Warning on HTTP helps avoid accidental downgrade to insecure transports.

## CLI Workflows

### `init`

Creates `collider.json` if missing. Refuses to run outside a Meson project.

**Rationale.** Establishes a single source of truth before dependencies are added.

### `setup`

Runs Meson setup and validates `collider.json` against Meson introspection.

### `lock`

Resolves dependencies from `collider.json` (including transitive dependencies) and writes `collider.lock`. Does not modify `subprojects/`; only refreshes pinned resolution state.

### `publish`

- Reads Meson introspection (`intro-projectinfo.json` and `meson-info.json`) to obtain project name, version, and source directory.
- Requires a Meson build directory (default: `collider-build`).
- Generates a source archive with root directory `<name>-<version>`.
- Generates a wrap file pointing at the repository `publish_url`.
- Optional: stage a patch archive with `--patch-archive`.
- Filesystem repos use `publish_url` from `config.json` to build archive URLs.
- Collider repos are published via `POST /v2/_collider/v1/push`; the bearer token is read from env (`COLLIDER_PUSH_TOKEN` by default).
- Auto-generates `[provide]` using `<name>` and a sanitized `<name>_dep` variable.

**Rationale**

- Staging archives gives a single place to publish and consume dependencies.
- Rewriting URLs makes wraps self-contained against the repository base.
- Auto-generating the wrap removes manual wrap editing for internal projects.

### `patch`

- Creates a patch archive (tar.xz) from Git changes in the project source directory.
- Uses Meson introspection (build directory, default `collider-build`) for package name and version.
- Output default: `dist/<name>_<version>_patch.tar.xz`. Requires Git and an existing Meson build.
- By default, includes the committed diff from `--base` plus staged, unstaged, and untracked files. Use `--no-include-uncommitted` to archive only the committed diff.
- Options: `--base` (revision to diff against), `--include-uncommitted` / `--no-include-uncommitted`, `--output`, `--list` (dry-run list of files).
- **Typical workflow for patched upstreams**: (1) modify the source tree, (2) run `collider patch` to produce the patch archive, (3) revert the tree to unmodified upstream, (4) run `collider publish <repo> --patch-archive <path>` so the repository stores the base source archive plus the patch. Consumers receive base + patch on install.

### `repo add`

- Adds a repository entry to `config.json`.
- Supports `filesystem`, `wrap`, and `collider` repository types.
- Requires `--publish-url` for filesystem repositories.
- Warns (non-blocking) when another entry already points to the same repository URL.

### `repo remove`

Removes a repository entry from `config.json` by name. Fails with `EX_NOINPUT` when the repository is not found.

### `serve`

- Serves a filesystem repository over HTTP.
- Takes a filesystem path directly (not a config repository name).
- Creates repository layout (`releases.json`, `archives/`) when the path does not exist.
- Runs endpoint smoke checks when a repository path is created.
- Uses `--publish-url` when provided; otherwise defaults to a local `file://` publish URL.
- Intended for lightweight local testing and internal sharing.
- Exposes only Wrap API routes under `/v2/`; other paths return 404.
- Optional write extension at `POST /v2/_collider/v1/push` when push auth is configured.
- Push auth is intentionally minimal (static bearer token). Strong auth should be handled externally.

### `unpublish`

- Removes a package version from a repository: `collider unpublish <repo> <name> <version>`.
- Supported for `filesystem` and `collider` repositories only; `wrap` (read-only) repositories return `EX_USAGE`.
- Filesystem: deletes the wrap and staged archives from disk and regenerates `releases.json`.
- Collider remote: issues `DELETE /v2/_collider/v1/packages/<name>/<version>`; the bearer token is read from the env var named by `--push-token-env`.
- Returns `EX_NOINPUT` when the repository or package is not found.

### `pkg search`

Searches configured repositories by name and optional version constraint.

### `pkg info`

- Shows available versions for a package across repositories.
- Marks versions that are fully cached for offline installs.
- Reports installed (subprojects wrap), declared (`collider.json`), and candidate versions.

### `status`

- Lists collider-managed dependencies from `collider.json`.
- Shows system dependencies declared in `collider.json` separately.
- Reports whether the corresponding wrap file exists in `subprojects/`.
- Shows transitive dependencies from `collider.lock`, or re-resolves them from `collider.json` when no lockfile is present and repository metadata is available.
- Highlights wrap files present in `subprojects/` but not tracked by Collider.
- When `collider.lock` exists, reports lock drift per dependency: `ok`, `modified`, or `missing`.

### `check`

- Detects drift between the `dependency()` calls scanned from `meson.build` and the dependencies declared in `collider.json`.
- Reports untracked dependencies (present in `meson.build` but absent from `collider.json`) and stale entries (declared in `collider.json` but absent from `meson.build`).
- Scans the source directory (`--sourcedir`, default: current directory); `--include-conditional` also considers dependencies inside Meson `if` blocks.
- Exits `EX_DATAERR` when any drift is found, `EX_OK` when clean. Missing `meson.build` returns `EX_NOINPUT`.

**Rationale.** Provides a non-mutating CI gate that flags a `collider.json` out of sync with the actual build.

### `pkg add`

- Resolves the newest version across repositories satisfying the requested constraint, if any.
- Uses `--version <spec>` when provided; otherwise reuses the constraint already declared in `collider.json`, if present.
- Writes the `.wrap` into `subprojects/`.
- Populates `subprojects/packagecache/` to support offline builds.
- Resolves and installs transitive dependencies (see [Transitive Dependency Resolution](#transitive-dependency-resolution)).
- Adds the direct dependency to `collider.json` if not already present.
- If the wrap is already installed only as a transitive dependency, promotes it into `collider.json` without reinstalling it.
- Persists the requested version constraint in `collider.json` when `--version` is used.
- Leaves `collider.lock` unchanged; lockfile writes are explicit via `collider lock`.

### `pkg remove`

- Removes a collider-managed dependency from `collider.json`.
- Deletes the package's installed wrap from `subprojects/` only when Collider can prove it is no longer needed.
- If the package is still needed transitively, or Collider cannot determine that safely, leaves the installed wrap in place.
- Leaves `collider.lock` unchanged; warns if the package is still locked.

### `pkg upgrade`

- Upgrades one or all collider-managed dependencies using the constraints declared in `collider.json`.
- `--version <spec>` is only valid when a package name is provided, and updates that package's stored constraint before resolving.
- Reinstalls only when the fetched wrap differs from the currently installed wrap.
- Leaves `collider.lock` unchanged; warns if the installed version no longer matches the lock entry.

### `install`

- If `collider.lock` exists, restores all packages from it.
    - For each locked package, finds the configured repository whose URL matches the recorded `origin` (URL normalization applied). Fails with `EX_CONFIG` if no configured repository matches.
    - Fetches the package from the origin repository. Fails with `EX_UNAVAILABLE` if the origin repository does not provide the package. No fallback to other repositories.
    - Verifies the fetched wrap hash against the locked `wrap_hash`.
    - Skips packages whose installed wrap already matches the locked hash.
    - With `--offline`, if the origin repository requires network access, falls back to the local cache and emits a provenance warning. The `wrap_hash` check still protects content integrity.
- If no lockfile exists, falls back to resolving from `collider.json` (including transitive dependencies) without writing `collider.lock`.
- When both files exist, warns on incompatibilities:
    - Locked package not declared in `collider.json`.
    - Declared dependency with no lock entry.
    - Locked version that does not satisfy a version specifier in `collider.json`.
- Wrap hash verification is unconditional: a mismatch between the fetched package and the lockfile always fails with `EX_DATAERR`, regardless of `--frozen`.
- `--frozen`: refuses to modify `collider.lock`; fails if lock is missing or stale (lockfile drift). Intended for CI.
- `--offline`: disables network access and relies on local cache.

## Cache and Offline Mode

- Cache root: `~/.config/collider/cache/`.
- `wraps/` caches wrap files by name+version.
- `archives/` caches archives by hash.
- `scans/` caches dependency scan results by name+version.
- `wrapdb/` caches fetched `releases.json` per remote repository.
- Offline mode forbids network access but allows local `file://` archives.

**Rationale**

- Meson resolves offline wraps via `subprojects/packagecache/`.
- Hash-keyed archives deduplicate identical content across wraps.

## Data Models

### `collider.json`

- Project description and dependencies (intent).
- Only explicitly listed dependencies are managed by Collider.
- Version field is optional; versionless dependencies and version ranges are both supported.
- Per-dependency `include` and `exclude` lists control transitive dependency overrides.

### `collider.lock`

- Pinned resolution state for reproducible installs.
- **`dependencies`**: Direct dependencies (from `collider.json`). Each entry has `version` (str), `wrap_hash` (str), and `origin` (str, normalized repository URL).
- **`packages`**: Transitive dependencies only. Same entry shape. Install fetches from the configured repository matching the locked `origin` and verifies `wrap_hash`.
- `wrap_hash` is a SHA-256 of the `.wrap` file text, which transitively pins archive hashes, filenames, and URLs.
- System dependencies are not locked (no wrap artifact to pin).
- `collider.json` represents intent (requested dependencies and optional constraints); `collider.lock` represents resolution (exact versions and integrity hashes).
- Install precedence:
    - `collider lock` writes `collider.lock` from `collider.json`.
    - `collider install` uses `collider.lock` when present.
    - If no lockfile exists, `collider install` resolves from `collider.json` without writing a new lockfile.
    - If both exist, Collider uses the lockfile and warns on mismatches with `collider.json`.
    - Both `collider install` and `collider lock` enforce declared version constraints.

**Rationale**

- The `.wrap` file already contains `source_hash`, `patch_hash`, filenames, and URLs. A single hash of the wrap text avoids redundancy.
- The hash is a verification gate, not a lookup key. Packages are fetched by name+version. The hash catches republished or tampered content.
- Existing validation in `WrapPackage.from_wrap_text()` already rejects wraps with missing hashes, so no additional validation is needed in the lockfile path.

### `config.json`

- List of repositories (`filesystem`, `wrap`, `collider`).
- Filesystem repositories require `publish_url` for hosted archive rewrites.

### `releases.json`

- WrapDB-compatible list of packages and versions.
- Includes `dependency_names` when wraps define `[provide]`.
- Generated from the repository layout on every add/remove.

## Transitive Dependency Resolution

### Problem

`pkg add` installs only the directly requested package. However, a package's `meson.build` may call `dependency()` on other packages that are not yet available in `subprojects/`. Without those transitive dependencies, `meson setup` fails at configure time.

### Discovery

Collider uses `meson introspect --scan-dependencies <meson.build>` to discover dependencies declared in a build file. This command uses Meson's own AST parser and returns all `dependency()` calls as structured JSON, without requiring a build directory or a successful configure step.

```json
[
  {"name": "libfoo", "required": true, "version": [], "has_fallback": false, "conditional": false},
  {"name": "libbar", "required": false, "version": [], "has_fallback": false, "conditional": false}
]
```

The same code path is used for all repository types, with no protocol changes or additional API endpoints.

### Source and Patch Extraction

Many WrapDB packages ship upstream source tarballs that do not contain a `meson.build`. The Meson build files are provided separately in a patch archive (often a zip file). During scanning, Collider extracts both the source and patch archives and overlays the patch on top of the source tree before running introspection. The overlay correctly handles the common case where both archives share the same top-level directory prefix.

### Dependency Classification

`--scan-dependencies` returns four flags per dependency:

| Flag | Meaning |
|------|---------|
| `required: true` | Build fails if not found. |
| `required: false` | Build continues without it. |
| `has_fallback: true` | A named subproject fallback is declared. |
| `conditional: true` | Inside an `if` block; may not execute depending on build options. |

Default resolution behavior:

| Condition | Action | Rationale |
|-----------|--------|-----------|
| `required: true, conditional: false` | Include | Hard dependency; always needed. |
| `required: false` | Include (info) | Optional but available. The user is informed. |
| `required: false, has_fallback: true` | Include | The project expects a subproject fallback. |
| `conditional: true` | Exclude (summarized) | May not be needed; depends on build options. All excluded conditional dependencies are listed in a single message. |
| Well-known system dependency | Skip silently | Compiler/OS intrinsics (`threads`, `appleframeworks`, `openmp`, `blocks`, `cuda`, `mpi`, `coarray`, `dl`, `iconv`, `intl`, `atomic`) cannot be provided by wraps. |
| Not in any configured repository | Skip (info) | Assumed system dependency. Each unknown name is reported once across the entire resolution run. |
| Empty name | Discard | `dependency('', required: false)` is a valid Meson idiom for null placeholder dependencies. |

`required: get_option(...)` is reported as `required: true` because the option value is unknown at scan time. This is the conservative (safe) interpretation.

### User Controls

**CLI flags**

```bash
collider pkg add <name> --include-conditional    # include conditional deps
collider pkg add <name> --exclude-optional       # exclude optional deps
collider pkg add <name> --include <dep>          # force-include a specific dep
collider pkg add <name> --exclude <dep>          # force-exclude a specific dep
```

Precedence: `--include`/`--exclude` by name > `--include-conditional`/`--exclude-optional` > defaults.

**Persistent overrides in `collider.json`**

`--include`, `--exclude`, `--include-conditional`, and `--exclude-optional` are persisted in `collider.json` so that `collider lock` and `collider install` produce consistent results without repeating CLI flags:

```json
{
  "dependencies": [
    {
      "name": "<package>",
      "source": "collider",
      "version": ">=1.0",
      "exclude": ["<dep-a>"],
      "include": ["<dep-b>"],
      "include_conditional": true,
      "exclude_optional": false
    }
  ]
}
```

### System vs. Wrap Classification

No publisher-side classification is required. At resolution time, Collider infers the dependency type:

- If `dependency_names` across configured repositories contain the name, it is a wrap dependency and is installed.
- If no repository provides it, it is treated as a system dependency and skipped.

A set of Meson dependency names known to be intrinsic to the compiler or OS is silently discarded before lookup: `threads`, `appleframeworks`, `openmp`, `blocks`, `cuda`, `mpi`, `coarray`, `dl`, `iconv`, `intl`, `atomic`. Dependencies with an empty name are also discarded during scanning.

This consumer-side classification is more flexible than publisher-side classification because different consumers may have different repositories configured.

### Resolution Engine

Collider uses [resolvelib](https://github.com/sarugaku/resolvelib) for dependency resolution. resolvelib handles graph walking, backtracking on conflicts, cycle detection, and producing a consistent resolution. Collider implements the `Provider` interface to connect resolvelib to the Meson/wrap ecosystem.

`ColliderProvider` implements:

| Method | Behavior |
|--------|----------|
| `identify()` | Returns the package name. |
| `get_preference()` | Prefers packages with fewer candidates (fail fast on conflicts). |
| `find_matches()` | Searches repositories for versions matching constraints; returns newest-first. |
| `is_satisfied_by()` | Checks whether a candidate satisfies a version constraint. |
| `get_dependencies()` | Downloads source and patch archives, extracts and overlays them, runs `--scan-dependencies`, applies filters, maps dependency names to packages via `dependency_names`, and returns requirements for providing packages. |

### Dependency Name Mapping

Meson dependency names (e.g., `absl_algorithm_container`) do not necessarily match Collider package names (e.g., `abseil-cpp`). The mapping is derived from `dependency_names` in `releases.json`, which reflects the `[provide]` section of each wrap. A reverse index (dependency name to package name) is built once per resolution run from all configured repositories.

### Resolution Flow

1. Resolve the best version of the requested package across repositories.
2. Download the source archive and patch archive (if present).
3. Extract both to a temporary directory, overlaying the patch on top of the source.
4. Run `meson introspect --scan-dependencies` on the extracted `meson.build`.
5. Discard empty names and well-known system dependencies.
6. Apply filtering rules (default behavior + user overrides).
7. For each included dependency, map to a providing package via the reverse index and recursively resolve.
8. Install all resolved wraps into `subprojects/`.
9. Add only the direct (user-requested) dependency to `collider.json`.
10. Persist any `--include`/`--exclude` overrides into `collider.json`.

### Prerequisite: `dependency_names`

Transitive resolution requires at least one configured repository whose `releases.json` includes `dependency_names` entries. This field maps Meson dependency names to packages and is used to build the reverse index. When no repository provides `dependency_names`, transitive resolution is skipped and only the direct package is installed.

### Multi-Root Resolution

`collider lock` and `collider install` resolve all declared dependencies in a single pass rather than resolving each dependency in isolation. This is implemented by `resolve_all_dependencies`, which feeds all root packages into one resolvelib session.

Single-pass resolution detects cross-root conflicts: if two declared dependencies require incompatible versions of the same transitive dependency, the resolver reports the conflict instead of silently installing one version and breaking the other.

### Per-Root Include/Exclude

`include` and `exclude` overrides declared in `collider.json` are scoped to the root dependency they are declared on. When resolving multiple roots, each root's overrides apply only when scanning that root's `meson.build` - they do not affect scans of other roots or their transitive dependencies.

Internally, `ColliderProvider` stores per-root overrides in a `root_overrides` map. In `get_dependencies()`, overrides are looked up by candidate name. Candidates that are not roots use the provider-level defaults (no include/exclude).

### Global Conditional and Optional Filters

`include_conditional` and `exclude_optional` are persisted per dependency in `collider.json`, but multi-root commands apply them as run-wide switches. If any declared dependency enables one of these flags, the combined `collider lock` / `collider install` / status resolution run uses that behavior for all roots.

### Rollback

If transitive resolution fails after the direct package has been installed, `pkg add` rolls back the direct package: the `.wrap` file is removed from `subprojects/` and any cached archives (source and patch) are deleted. This prevents a partially installed state where the direct package is present but unusable due to missing transitive dependencies.

### Reinstall Guard

`pkg add` checks whether a `.wrap` file already exists for the requested package before performing any repository or network operations. If the wrap is present and the package is not already declared directly, Collider first checks whether it is an installed transitive dependency. In that case, the command adds it to `collider.json` without reinstalling it. Otherwise, the command exits immediately with an error and suggests `--force`. When `--force` is passed, existing artifacts (wrap file and subproject directory) are removed before proceeding with a fresh install.

### Transitive Cleanup: Remove vs Prune

`pkg remove` is conservative: it always deletes the direct dependency from `collider.json`, but it only removes the installed wrap/subproject directory when Collider can prove the package is no longer needed. If another direct dependency still requires it transitively, or if re-resolution fails and Collider cannot determine safety, the installed wrap is left in place. This guarantees that manually placed wraps and potentially shared dependencies are never accidentally deleted.

Transitive cleanup is handled by a separate `pkg prune` command (also invoked via `pkg remove --prune`). Prune requires a lockfile for provenance: `collider.lock` defines which wraps are Collider-managed. Without a lockfile, prune warns and does nothing. When a lockfile is present, prune loads the managed package set, re-resolves all remaining `collider.json` dependencies to compute the "needed" set, and removes wraps that are managed but no longer needed. If a transitive dep is shared with another direct dependency, it is kept. Wraps not listed in the lockfile (manually placed) are never removed. A `--dry-run` flag allows previewing removals without deleting. Prune resolves conservatively: unlike install, lock, and status, it always includes conditional dependencies and never excludes optional ones, because the safety requirement is to never delete a wrap that might still be needed. Successful prune operations do not rewrite `collider.lock`; instead they warn that the lockfile should be refreshed with `collider lock`.

### Offline Transitive Resolution

When `--offline` is set and a repository requires network access, the resolver checks the local wrap cache before giving up. If a cached wrap exists for the package and version, it is used for scanning without network access. If no cache entry is found, a warning is logged and the candidate is skipped.

### Caching

- Scan results are cached per package+version in the local cache.
- The reverse index (dependency name to package name) is built in memory once per resolution run.

### Impact on `collider.json` and `collider.lock`

- `collider.json` contains only direct (user-declared) dependencies, with optional `include`/`exclude` and `include_conditional`/`exclude_optional` for transitive dependency control.
- `collider.lock` contains all dependencies (direct in `dependencies`, transitive in `packages`) with resolved versions, wrap hashes, and `origin` (normalized repository URL) per entry.
- `pkg add` installs direct and transitive wraps into `subprojects/` but only adds the direct dependency to `collider.json`.
- `install` from lockfile restores everything (direct + transitive).
- `install` without a lockfile resolves from `collider.json` using multi-root resolution.
- `lock` re-resolves transitively and writes the full dependency graph.

### Limitations

- `--scan-dependencies` runs static AST analysis with no build directory or configure step. It follows `subdir()` recursively from the project root `meson.build`, so multi-file projects that split `dependency()` calls across subdirectories are scanned fully. The residual limitation is that values resolved only at configure time (for example a `subdir()` path computed from a build option) are not evaluated.
- Rollback on transitive failure only removes the direct package. If some transitive dependencies were already installed before the failure, they are left in place. This is a known limitation that avoids accidentally removing wraps that may be shared with other packages.

**Rationale**

- Using Meson's own introspection avoids fragile regex parsing and leverages the exact parser Meson uses.
- A single code path for all repository types keeps the implementation simple and avoids protocol changes.
- resolvelib is battle-tested (used by pip) and handles the hard parts of resolution: backtracking, conflict detection, and cycle breaking.
- Consumer-side classification (system vs. wrap) adapts to each user's repository configuration.
- Single-pass multi-root resolution is more correct than per-root isolation because it detects shared transitive conflicts early.

## Security and Integrity

- HTTP URLs are allowed but warned to avoid silent downgrade risks.
- Archives are validated against hashes before caching or publishing.
- Path traversal is prevented by using basename-only filenames.
- The lockfile's `wrap_hash` detects tampered or republished wrap files. Without the lockfile, an attacker controlling the repository could replace a wrap file (with matching archive hashes for malicious archives) undetected. The lockfile closes this gap by recording the expected wrap content hash at lock time.

## Error Handling

- CLI commands return non-zero exit codes on failure.
- Failure conditions are logged at critical level for visibility.

## Design Principles

- Three repository types: filesystem, WrapDB-compatible remote (read-only), and Collider remote (read-write).
- Single layout reused for both internal and hosted usage.
- No binary packaging or non-Meson integrations.

## Testing Strategy

- Unit tests for wrap parsing, cache, repositories, and CLI workflows.
- Repository layout and URL rewrite behavior are verified.
- Offline cases are explicitly tested.

## Open Questions

- Should `releases.json` be sorted strictly by version semantics rather than string?
