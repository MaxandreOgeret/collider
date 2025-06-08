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
dependency) are detected and reported. The lockfile has two sections:

- **`dependencies`**: Direct dependencies (from `collider.json`). Each entry has `version` and `wrap_hash`.
- **`packages`**: Transitive dependencies only. Same entry shape.

Each locked entry contains:

| Field        | Description                                           |
|--------------|-------------------------------------------------------|
| `version`    | The resolved version string.                          |
| `wrap_hash`  | SHA-256 of the `.wrap` file text (e.g. `sha256:...`). |

The lockfile does not store which repository each package came from. The `wrap_hash` transitively pins archive hashes, filenames, and URLs because those values are embedded in the wrap file itself.

Use `--offline` to resolve only from the local cache:

```bash
collider lock --offline
```

## Installing from a Lockfile

```bash
collider install
```

When `collider.lock` exists, `install` restores all packages from it:

1. Fetches each package by name and version from any configured repository (searching until one provides it).
2. Verifies the fetched wrap hash against the recorded `wrap_hash`.
3. Skips packages whose installed wrap already matches the lock.

If no lockfile exists, Collider falls back to resolving from `collider.json`
(including transitive dependencies) without writing a lockfile. Like `lock`,
this uses unified multi-root resolution to detect cross-root conflicts.

### Frozen Installs

For CI pipelines, use `--frozen` to refuse any lockfile modifications:

```bash
collider install --frozen
```

This fails if the lockfile is missing or stale, ensuring builds are fully
reproducible.

### Offline Installs

```bash
collider install --offline
```

Network access is disabled. Only cached wraps and archives are used.

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

Per-dependency `include`, `exclude`, `include_conditional`, and
`exclude_optional` in `collider.json` control transitive resolution:
`include` and `exclude` are scoped to each root; `include_conditional` and
`exclude_optional` apply to the whole run when any dependency sets them. See
[Managing Packages](managing-packages.md) and
[Configuration Files](../reference/configuration.md) for details.
