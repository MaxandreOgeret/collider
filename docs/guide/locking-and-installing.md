# Locking and Installing

Collider separates **intent** (`collider.json`) from **resolution**
(`collider.lock`) to give you both flexibility during development and
reproducibility in CI.

## Creating a Lockfile

```bash
collider lock
```

This resolves all dependencies declared in `collider.json` - including
their transitive dependencies - and writes `collider.lock`. All declared
dependencies are resolved in a single pass so that cross-root conflicts
(e.g. two packages requiring incompatible versions of the same transitive
dependency) are detected and reported.

Transitive resolution requires repositories to advertise the dependency names
each package provides: the `dependency_names` field in `releases.json`,
populated from the `[provide]` section keys of each wrap. Without that index
Collider cannot map a scanned Meson dependency back to a package, so it
resolves only the dependencies declared directly in `collider.json` (each to
the newest version matching its constraint) and treats unmapped dependencies
as system-provided.

The lockfile has two sections:

- **`dependencies`**: Direct dependencies (from `collider.json`). Each entry has `version`, `wrap_hash`, and `origin`.
- **`packages`**: Transitive dependencies only. Same entry shape.

Each locked entry contains:

| Field        | Description                                                   |
|--------------|---------------------------------------------------------------|
| `version`    | The resolved version string.                                  |
| `wrap_hash`  | SHA-256 of the `.wrap` file text (e.g. `sha256:...`).         |
| `origin`     | Normalized URL of the repository this package was resolved from. |

The `wrap_hash` transitively pins archive hashes, filenames, and URLs because those values are embedded in the wrap file itself. The `origin` URL is normalized at write time (lowercased scheme/host, trailing slash stripped) so lockfile diffs are stable across trivial URL variations.

Use `--offline` to resolve only from the local cache:

```bash
collider lock --offline
```

## Installing from a Lockfile

```bash
collider install
```

When `collider.lock` exists, `install` restores all packages from it:

1. For each locked package, finds the configured repository whose URL matches the recorded `origin` (URL normalization is applied when comparing). If no configured repository matches, install fails with `EX_CONFIG`.
2. Fetches the package from the origin repository. If the origin repository does not provide the package, install fails with `EX_UNAVAILABLE`. There is no fallback to other repositories.
3. Verifies the fetched wrap hash against the recorded `wrap_hash`.
4. Skips packages whose installed wrap already matches the lock.

If no lockfile exists, Collider falls back to resolving from `collider.json`
(including transitive dependencies) without writing a lockfile. Like `lock`,
this uses unified multi-root resolution to detect cross-root conflicts, and
the same transitive-resolution caveat applies: without the repository
`dependency_names` index, only declared direct dependencies are resolved.

### Hash Verification

Wrap hash mismatches between the fetched package and the lockfile are always
a hard failure, regardless of `--frozen`. This is a security boundary: a
mismatch indicates tampering, republishing, or corruption.

### Frozen Installs

For CI pipelines, use `--frozen` to refuse any lockfile modifications:

```bash
collider install --frozen
```

This fails if the lockfile is missing or stale (collider.json vs collider.lock
drift), ensuring builds are fully reproducible. Note that hash verification
is unconditional and does not require `--frozen`.

### Offline Installs

```bash
collider install --offline
```

Network access is disabled. Only cached wraps and archives are used. When
installing from a lockfile with `--offline`, if the origin repository requires
network access, Collider falls back to the local cache and emits a warning that
origin provenance cannot be verified. The `wrap_hash` check still protects
content integrity.

## Lock Drift Detection

When both `collider.json` and `collider.lock` exist, Collider warns on
incompatibilities during install:

- A locked package is not declared in `collider.json`.
- A declared dependency has no lock entry.
- A locked version does not satisfy the constraint in `collider.json`.

Run `collider lock` to re-resolve and clear these warnings.

## Relationship Between Intent and Resolution

| File             | Purpose                                                  |
|------------------|----------------------------------------------------------|
| `collider.json`  | What you want: declared dependencies and constraints.    |
| `collider.lock`  | What you got: exact versions and integrity hashes.       |

Commands like `pkg add`, `pkg remove`, and `pkg upgrade` modify
`collider.json` and the installed state but never touch `collider.lock`.
The lockfile is only written by `collider lock`.

The lockfile also scopes which wraps `collider setup` forces Meson to use: with
a lockfile, only its managed packages (direct and transitive) are forced;
without one, `setup` forces every present wrap as a best effort and warns. See
[Wrap Fallback Enforcement](project-setup.md#wrap-fallback-enforcement).

Per-dependency `include`, `exclude`, `include_conditional`, and
`exclude_optional` in `collider.json` control transitive resolution:
`include` and `exclude` are scoped to each root; `include_conditional` and
`exclude_optional` apply to the whole run when any dependency sets them. See
[Managing Packages](managing-packages.md) and
[Configuration Files](../reference/configuration.md) for details.
