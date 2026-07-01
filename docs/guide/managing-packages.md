# Managing Packages

## Adding a Package

```bash
collider pkg add <name>
```

Collider resolves the newest version across all configured repositories,
installs the wrap file into `subprojects/`, and populates
`subprojects/packagecache/` so Meson can build offline.

If the package is already installed and already declared directly, Collider
exits with an error rather than redoing work. Pass `--force` to reinstall:

```bash
collider pkg add <name> --force
```

If the wrap is already present only because it was installed transitively for
another dependency, `pkg add` promotes it into `collider.json` as a direct
dependency without reinstalling it.

Collider also resolves **transitive dependencies** automatically. When the
added package depends on other packages (detected via
`meson introspect --scan-dependencies`), Collider installs their wraps too.
Only the direct dependency is added to `collider.json`; transitive deps are
recorded in `collider.lock` when you run `collider lock`.

### Transitive Dependencies

Transitive resolution requires at least one configured repository whose index
includes `dependency_names` entries (derived from wrap `[provide]` sections).
When no repository provides this mapping, only the direct package is installed.

By default, Collider applies the following rules to scanned dependencies:

| Condition                                                | Behavior                                                                                                                |
|----------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Required dependency                                      | Included automatically.                                                                                                 |
| Optional dependency (`required: false`)                  | Included with an info message.                                                                                          |
| Conditional dependency (inside an `if` block)            | Excluded. A summary of skipped conditional deps is shown (entries already satisfied by a resolved package are omitted). |
| Well-known system dependency (`threads`, `openmp`, etc.) | Silently skipped.                                                                                                       |
| Not found in any configured repository                   | Skipped (assumed system dependency). Each unknown name is reported once.                                                |

If transitive resolution fails after the direct package has been installed,
Collider rolls back the direct package (removes its `.wrap` and cached
archives) to avoid a partially installed state.

#### Including or Excluding Specific Dependencies

Fine-grained control over which transitive deps are resolved:

```bash
collider pkg add grpc --exclude protobuf
collider pkg add grpc --include libbaz
```

These overrides are persisted in `collider.json` and apply only to the
dependency they are declared on. When multiple dependencies are resolved
together (e.g. during `collider lock`), each dependency's overrides are
scoped to its own dependency scan and do not affect other roots.

#### Broad Flags

```bash
collider pkg add grpc --include-conditional    # also resolve conditional deps
collider pkg add grpc --exclude-optional       # skip optional deps
```

Precedence: `--include`/`--exclude` by name > broad flags > defaults.

### Version Constraints

Pin a version range with `--version`:

```bash
collider pkg add my-lib --version '>=1.2,<2.0'
```

A bare version is treated as an exact pin: `--version 1.2.13` means `==1.2.13`.
The constraint is stored in `collider.json` and enforced on future operations.
If `collider.json` already declares a constraint for the package, it is used
automatically.

!!! warning "Wrap revision suffixes"
    Repository versions often carry a wrap-revision suffix such as `1.2.13-1`,
    which PEP 440 reads as `1.2.13.post1`, so an exact pin like `==1.2.13` does
    not match `1.2.13-1`. Pin the full tag (`==1.2.13-1`) or use a range
    (`>=1.2.13,<1.2.14`). `collider pkg search my-lib --version 1.2.13` lists the
    matching revisions, since search widens a bare version to `==1.2.13.*`.

### Offline Mode

Add a package using only the local cache:

```bash
collider pkg add my-lib --offline
```

This disables network access and relies on previously cached wraps and
archives. See [Offline Mode](offline-mode.md) for details.

!!! note
    `pkg add` does not update `collider.lock`. Run `collider lock` explicitly
    after adding packages to refresh the lockfile.

## Removing a Package

```bash
collider pkg remove <name>
```

This removes the dependency from `collider.json`. The alias `pkg rm` also
works.

`pkg remove` is conservative about installed artifacts. If another dependency
still requires the package transitively, Collider keeps the wrap in place. If
Collider cannot determine safely whether the package is still needed, it also
leaves the wrap in place and warns instead of deleting it. When it does remove a
package, it deletes both the `.wrap` descriptor and the extracted source tree
under `subprojects/`, so an obsolete source tree cannot keep satisfying a Meson
`dependency()` after removal.

If the package is still present in `collider.lock`, Collider warns you to run
`collider lock` to update the lockfile.

To also clean up orphaned transitive dependencies in one step, use:

```bash
collider pkg remove --prune <name>
```

## Pruning Orphaned Dependencies

```bash
collider pkg prune
```

`pkg prune` scans `subprojects/` for transitive wraps that are no longer
needed by any declared Collider dependency and removes them. Only wraps proven
to be Collider-managed (listed in `collider.lock`) are eligible; manually
placed wraps are never touched.

If no lockfile exists, `prune` warns and does nothing. Running `collider lock`
creates ownership metadata for future operations, but existing leftover wraps
may still need to be removed manually.

Pruning does not update `collider.lock`; after wraps are removed, Collider
warns you to run `collider lock` to refresh the lockfile.

Use `--dry-run` to preview what would be removed without actually deleting
anything:

```bash
collider pkg prune --dry-run
```

## Upgrading Packages

Upgrade a single package:

```bash
collider pkg upgrade my-lib
```

Upgrade all collider-managed dependencies:

```bash
collider pkg upgrade
```

Collider resolves the newest version allowed by the constraints in
`collider.json`. A package is only reinstalled when the fetched wrap differs
from the currently installed one.

To change the version constraint during upgrade:

```bash
collider pkg upgrade my-lib --version '>=2.0'
```

The `--offline` flag is also supported for upgrades.

!!! note
    `pkg upgrade` does not update `collider.lock`. If an upgrade changes
    the installed version, Collider warns you to run `collider lock`.

## Searching for Packages

```bash
collider pkg search <pattern>
```

The pattern is a regular expression matched against package names. Restrict
the search to a specific repository:

```bash
collider pkg search '^fmt$' --repository wrapdb
```

Filter by version constraint:

```bash
collider pkg search my-lib --version '>=1.0.0'
```

Search only the local wrap cache:

```bash
collider pkg search '.*' --cache
```

## Package Information

```bash
collider pkg info <name>
```

Shows available versions across repositories, which versions are cached
locally, the currently installed version, and the declared version constraint
from `collider.json`.

Restrict to specific repositories:

```bash
collider pkg info my-lib --repository local --repository wrapdb
```

## Dependency Status

```bash
collider status
```

Lists tracked Collider dependencies from `collider.json` and also reports:

- Whether the corresponding wrap file exists in `subprojects/`.
- Dependencies declared with `"source": "system"` in `collider.json`, shown in
  a separate **system** section.
- Wraps that are resolved but not declared as direct dependencies, shown as
  **transitive**. When `collider.lock` exists, Collider uses it. Without a
  lockfile, Collider re-resolves from `collider.json` when repository metadata
  is available.
- Wraps in `subprojects/` that are neither direct nor resolved transitive
  dependencies, shown as **untracked**.
- When `collider.lock` exists, lock drift per dependency: `ok`, `modified`, or
  `missing`.
