# Glossary

Collider
:   A package and dependency manager for Meson projects, centered on Meson's
    wrap system.

Collider Home
:   User-specific configuration directory. Defaults to
    `$XDG_CONFIG_HOME/collider` or `~/.config/collider/`.

Subcommand
:   A specific action that Collider performs, given as a positional argument
    (e.g. `setup` in `collider setup`).

Colliderfile (`collider.json`)
:   Project metadata and dependency declarations. Located alongside
    `meson.build` at the root of a project.

Configfile (`config.json`)
:   Application-wide configuration containing repository entries. Lives in
    `~/.config/collider/config.json`.

Lockfile (`collider.lock`)
:   Pinned resolution state written by `collider lock`. Contains `dependencies`
    (direct, from `collider.json`) and `packages` (transitive), each with
    `version`, `wrap_hash`, and `origin` (the normalized repository URL the
    package was resolved from). Consumed by `collider install` for reproducible
    installs.

Origin
:   The normalized repository URL a package was resolved from, recorded for
    each locked entry in `collider.lock`. Normalization lowercases the scheme
    and host and strips a trailing slash so URLs compare equal.

Transitive dependency
:   A dependency pulled in by another dependency rather than declared directly
    in `collider.json`. Recorded under `packages` in `collider.lock`.

Resolver
:   The component that resolves all declared roots and their transitive
    dependencies in a single pass, surfacing cross-root version conflicts.

Frozen
:   `collider install --frozen` mode: install strictly from `collider.lock`
    and fail if the lockfile is missing or stale (does not match
    `collider.json`).

Drift
:   A mismatch between `meson.build` `dependency()` calls and `collider.json`,
    detected by `collider check`. Untracked dependencies appear in
    `meson.build` but not in `collider.json`; stale entries are tracked in
    `collider.json` but absent from `meson.build`. `collider check` exits
    non-zero (`EX_DATAERR`) when drift is found.

Repository
:   A storage location for wrap packages. Filesystem repositories are local
    directories, wrap repositories are remote WrapDB-compatible endpoints,
    and collider repositories extend the protocol with write operations.

WrapDB
:   Meson's wrap package database. Collider's wrap repositories speak the
    WrapDB-compatible protocol, exposing a `releases.json` index over a base
    URL such as `wrapdb.mesonbuild.com/v2/`.

Publish URL
:   Required HTTPS, HTTP, or `file://` base URL for filesystem repositories.
    Used to rewrite archive URLs when publishing packages.

Push token
:   Bearer token authorizing writes to a collider repository, read from an
    environment variable (default `COLLIDER_PUSH_TOKEN`, overridable with
    `--push-token-env`). Sent as a bearer token to the repository write
    endpoints: `_collider/v1/push` by `collider publish`, and
    `_collider/v1/packages/<name>/<version>` by `collider unpublish`.

`releases.json`
:   WrapDB-compatible index of packages and versions, generated for
    filesystem repositories.

Dependency Names
:   Provided dependency names derived from a wrap `[provide]` section. Emitted
    in `releases.json` when present.

System dependency
:   A dependency satisfied by the host system rather than fetched as a wrap.
    Marked with source `system` in `collider.json` and listed separately by
    `collider status`.

Version Constraint
:   A PEP 440 specifier string such as `>=1.2,<2.0`, stored in
    `collider.json` and enforced during package resolution.

Cache
:   Local store for wrap files and archives, located at
    `<collider home>/cache/`. Enables offline builds.

Wrap File
:   A Meson `.wrap` file describing a package, its source archive, and
    optional patch archive. Stored in repositories and installed to
    `subprojects/`.

Package Cache
:   Meson's `subprojects/packagecache/` directory containing cached archives.
    Collider populates this to enable offline builds.

Archive
:   A source or patch archive referenced by a wrap. May be hosted upstream or
    stored alongside wraps in a filesystem repository.

Patch Archive
:   A `tar.xz` archive containing changes for a wrap `patch_url`. Produced
    by `collider patch`.

Build Directory
:   The Meson build directory. Collider defaults to `collider-build`.

Source Directory
:   The Meson source directory.

Infofile
:   Meson introspection files under `<builddir>/meson-info/`
    (e.g. `intro-projectinfo.json`).

Wrap Package
:   A package represented by a `.wrap` file plus its source archive and
    optional patch archive.
