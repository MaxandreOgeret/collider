# Offline Mode

Collider maintains a local cache so you can build without network access.

## Cache Location

The cache lives at `~/.config/collider/cache/` (or
`$XDG_CONFIG_HOME/collider/cache/`) and includes:

| Directory   | Contents                                                     |
|-------------|--------------------------------------------------------------|
| `wraps/`    | Wrap files, stored by package name and version.              |
| `archives/` | Source and patch archives, keyed by content hash.            |
| `scans/`    | Cached Meson introspect scans (resolved dependency results). |
| `wrapdb/`   | Cached `releases.json` per HTTP repository, with a 300s TTL. |

Hash-keyed archive storage deduplicates identical content across packages.
Each `wrapdb/` entry is keyed by the full repository URL (its host plus a hash
of the URL), so repositories that share a host but differ by path do not
collide.

## How the Cache is Populated

When Collider downloads a wrap or archive during `pkg add`, `pkg upgrade`,
`install`, or `lock`, it stores a copy in the cache automatically. No extra
command is needed.

## Using Offline Mode

Pass `--offline` to any command that resolves packages:

```bash
collider pkg add my-lib --offline
collider pkg upgrade --offline
collider lock --offline
collider install --offline
```

!!! note "Releases cache prerequisite"
    Offline use of an HTTP-backed repository (`wrap` or `collider` type) requires
    that repository's `releases.json` to have been cached by a prior online
    command. Without a cached copy, loading the repository fails with
    `Offline mode requires cached wrap releases.`. This is not a clean hard-fail:
    config loading catches the error, logs
    `Failed to load repository "<name>": ...`, and skips
    the repository. The failure surfaces downstream instead, for example as
    `No package matching query` with exit code `EX_UNAVAILABLE` when no other
    repository can satisfy the request. Filesystem repositories are unaffected.

In offline mode, Collider:

- Refuses to make network requests.
- Resolves packages only from cached wraps.
- Allows `file://` archive URLs (local paths).
- During transitive resolution, checks the local wrap cache for each
  dependency before attempting network access. If a cached wrap exists, it
  is used for scanning without going online. If no cache entry is found, the
  dependency is skipped with a warning.
- When installing from a lockfile, falls back to the local cache if the
  origin repository requires network access and emits a warning that origin
  provenance cannot be verified. The `wrap_hash` check still ensures content
  integrity.

## Meson Package Cache

On install, Collider populates `subprojects/packagecache/` with the source
and patch archives referenced by each wrap. This is the directory Meson checks
for cached downloads, so subsequent `meson setup` calls work without network
access.

## Wrap File Requirements

For offline mode to work, wrap files must include:

- `source_url`, `source_filename`, and `source_hash` (always required).
- `patch_url`, `patch_filename`, and `patch_hash`: optional, but if one is
  present all three must be present.

!!! warning
    HTTP URLs are allowed with a warning. Prefer HTTPS or local file paths for
    security.
