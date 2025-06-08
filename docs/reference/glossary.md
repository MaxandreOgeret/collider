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
    `version` and `wrap_hash`. Consumed by `collider install` for reproducible
    installs.

Repository
:   A storage location for wrap packages. Filesystem repositories are local
    directories, wrap repositories are remote WrapDB-compatible endpoints,
    and collider repositories extend the protocol with write operations.

Publish URL
:   Required HTTPS, HTTP, or `file://` base URL for filesystem repositories.
    Used to rewrite archive URLs when publishing packages.

`releases.json`
:   WrapDB-compatible index of packages and versions, generated for
    filesystem repositories.

Dependency Names
:   Provided dependency names derived from a wrap `[provide]` section. Emitted
    in `releases.json` when present.

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
