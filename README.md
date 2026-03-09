<p align="center">
<img src="https://collider.ee/assets/logo.svg" width="300">
</p>

<p align="center">
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://pypi.org/project/collider-wraps/"><img src="https://img.shields.io/pypi/v/collider-wraps?label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/MaxandreOgeret/collider/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://codecov.io/gh/MaxandreOgeret/collider"><img src="https://codecov.io/gh/MaxandreOgeret/collider/graph/badge.svg" alt="codecov"></a>
  <a href="https://matrix.to/#/#collider:matrix.org"><img src="https://img.shields.io/badge/Join%20chat-%23collider%3Amatrix.org-000000?logo=matrix" alt="Matrix"></a>
</p>

Collider is a dependency manager for Meson projects.

It also provides the infrastructure to run private WrapDB-compatible
package repositories, enabling teams to build their own package ecosystem.

## Features

- Dependency manager for Meson wrap projects.
- Publishable wrap repositories.
- Host your own WrapDB-compatible package repository.
- Lockfiles for reproducible builds.
- Offline dependency cache.
- Compatible with WrapDB.

## Why Collider?

Meson provides WrapDB and subprojects, but dependency management across
multiple repositories can become difficult:

- Wrap files must be maintained manually.
- Offline builds require prefetching sources.
- Publishing reusable wraps is cumbersome.

Collider adds a lightweight package workflow on top of Meson's wrap system:

- Publishable wrap repositories.
- Self-hosted WrapDB-compatible registries.
- Reproducible dependency lockfiles.
- Offline dependency caching.

## Requirements

- Python 3.10+
- Meson 1.8.5+ (and Ninja)
- See [`pyproject.toml`](https://github.com/MaxandreOgeret/collider/blob/main/pyproject.toml) for Python dependency versions.

## Installation

```bash
pip install collider-wraps
```

### From source (development)

```bash
git clone git@github.com:MaxandreOgeret/collider.git
cd collider
uv venv
source .venv/bin/activate
uv sync
```

## Quick start

1) Add a `collider.json` at the root of your Meson project:

```json
{
  "description": "Example Meson library",
  "dependencies": [
    { "name": "fmt", "source": "system" },
    { "name": "my-lib", "source": "collider", "version": ">=1.2.0" }
  ]
}
```

Collider expects `collider.json` next to `meson.build`.

2) Configure the project:

```bash
collider setup
# or
collider setup --builddir build
# pass args through to meson
collider setup -- --buildtype=debug
```

3) Publish the project to a repository:

```bash
collider publish local
```

The package name and version come from Meson introspection in the build directory
(default: `collider-build`). Collider generates the source archive and wrap file
and stores them in the repository. Filesystem repositories must have `publish_url`
configured so Collider can generate archive URLs. To attach a patch archive for
an external source, pass `--patch-archive`. Create the patch with `collider patch` from a modified source tree, then run `collider publish` from the **unmodified** tree and pass the patch archive (tar.xz) with `--patch-archive` so the repository stores base source plus patch.

Collider also generates the wrap `[provide]` entry as `<name> = <name>_dep`
(with `-` and `.` replaced by `_`). Ensure your Meson project exposes that
dependency variable for `dependency()` fallbacks.

4) Add a dependency (online or offline):

```bash
collider pkg add my-lib
collider pkg add my-lib --version '>=1.2,<2.0'
collider pkg add my-lib --offline
collider pkg upgrade my-lib
collider pkg remove my-lib
```

5) Create or refresh the lockfile when you want reproducible installs:

```bash
collider lock
```

## Configuration

Collider reads `config.json` from `~/.config/collider/` or `$XDG_CONFIG_HOME/collider/`.

```json
{
  "repositories": [
    {
      "name": "local",
      "type": "filesystem",
      "url": "file:///path/to/repo",
      "publish_url": "https://packages.example.com/collider/"
    },
    {
      "name": "wrapdb",
      "type": "wrap",
      "url": "https://wrapdb.mesonbuild.com/v2/"
    },
    {
      "name": "my-collider",
      "type": "collider",
      "url": "https://packages.example.com/collider/v2/"
    }
  ]
}
```

Repository `type` can be `filesystem`, `wrap`, or `collider`. Use `wrap` for read-only WrapDB-compatible remotes (e.g. official Meson WrapDB). Use `collider` for a remote that exposes the Collider write extension (publish and unpublish); publish and unpublish require a bearer token (e.g. `$COLLIDER_PUSH_TOKEN`).

Filesystem repositories are directories containing a wrap layout and `releases.json` at the root.
Wraps are stored under `<name>_<version>/<name>.wrap` and `releases.json` is generated at the root.
Collider stores the generated source archive (and optional patch archive) under
`archives/<name>_<version>/`.
Filesystem repositories require `publish_url`. Set it to the HTTPS, HTTP, or `file://` base where the repository is served; `collider publish`
uses it to rewrite archive URLs without requiring per-command flags.

When served at `publish_url`, the expected URLs are:
- `releases.json`: `<publish_url>/v2/releases.json`
- wrap file: `<publish_url>/v2/<name>_<version>/<name>.wrap`
- archive file: `<publish_url>/v2/archives/<name>_<version>/<filename>`
Create one manually:

```bash
mkdir -p /path/to/repo
```

To publish a repository, serve the same directory over HTTP(S) and configure a `collider` repository
in `config.json` that points to the server base URL so you can publish and unpublish packages with a token.

## Repository workflow

```bash
collider publish local
collider pkg search '^my-lib$' --repository local --version '>=1.0.0'
collider pkg add my-lib
```

## Offline caching

Collider maintains a local cache under `~/.config/collider/cache/`:

- `wraps/` stores wrap files.
- `archives/` stores source and patch archives keyed by hash.

On install, Collider populates `subprojects/packagecache/` so Meson can build offline.
Use `--offline` to disable network access and rely on the local cache.
When installing from a lockfile with `--offline`, if the origin repository requires network access,
Collider falls back to the local cache and emits a warning that origin provenance cannot be verified.

Wrap files must include `source_url`, `source_filename`, and `source_hash`; patch fields are optional
but must be complete when present. Patch archives are typically used for third-party sources.
When publishing, Collider stores the generated source archive (and optional patch archive) in the
repository and rewrites the wrap URLs to the configured base.
HTTP URLs are allowed with a warning; prefer HTTPS or local file paths.

## Commands

Use `collider <command> --help` for command-specific options and examples.
Use `collider -v <command>` for debug logging. Place `-v` before the subcommand;
some `pkg` commands use `-v` for version constraints.

- `collider init`  
  Create `collider.json` in the current project.

- `collider setup [--sourcedir PATH] [--builddir PATH] -- [meson args]`  
  Configure a Meson project (default builddir: `collider-build`).

- `collider lock [--offline]`  
  Resolve dependencies from `collider.json` and write `collider.lock`.

- `collider patch [--builddir PATH] [--base REV] [--output PATH] [--list] [--include-uncommitted / --no-include-uncommitted]`  
  Create a patch archive (tar.xz) from Git changes for use with Meson wrap `patch_url`. Reads project name and version from Meson introspection. Default output: `dist/<name>_<version>_patch.tar.xz`. Requires Git and an existing Meson build directory.

- `collider publish <repo> [--builddir PATH] [--patch-archive PATH] [--push-token-env VAR]`  
  Generate a wrap and source archive, then publish it.
  For filesystem repositories, Collider writes directly to disk.
  For collider repositories, Collider calls `POST /v2/_collider/v1/push` and reads bearer token from
  `$COLLIDER_PUSH_TOKEN` (or `--push-token-env`).

- `collider unpublish <repo> <package> <version> [--push-token-env VAR]`  
  Remove a package version from a filesystem or collider repository.

- `collider repo add <name> <type> <url> [--publish-url URL]`  
  Add a repository entry to `config.json`.
  For `filesystem` repositories, `--publish-url` is required.
  For non-filesystem repositories, `--publish-url` is ignored.
  If another repository already uses the same URL, Collider logs a warning but still adds the entry.

- `collider repo remove <name>` or `collider repo rm <name>`  
  Remove a repository entry from `config.json`.

- `collider repo list` or `collider repo ls`  
  List the repositories configured in `config.json`.

- `collider pkg add <name> [--version SPEC] [--offline]`  
  Add a package dependency to the project and install it into `subprojects/`.
  When `--version` is provided, Collider resolves only matching versions and persists that constraint in `collider.json`.
  If the package is already declared in `collider.json` with a version constraint, that constraint is enforced automatically.
  This command does not create or update `collider.lock`; use `collider lock` explicitly.

- `collider pkg remove <name>` or `collider pkg rm <name>`  
  Remove a collider-managed dependency from `collider.json` and delete its installed wrap state from `subprojects/`.
  This command does not create or update `collider.lock`; if the package is still present in the lockfile, Collider warns to run `collider lock`.

- `collider pkg search <pattern> [--cache] [--repository NAME] ... [--version SPEC]`  
  Search repositories or the local cache.

- `collider pkg info <name> [--repository NAME] ...`  
  Show versions, origins, and cache status.

- `collider pkg upgrade [<name>] [--version SPEC] [--offline]`  
  Upgrade one dependency or all collider-managed dependencies to the newest versions allowed by `collider.json`.
  When `--version` is provided, it is only valid with a package name and replaces that package's declared constraint before upgrading.
  This command does not create or update `collider.lock`; if the installed package no longer matches the lockfile, Collider warns to run `collider lock`.

- `collider status`  
  Show collider-managed dependencies and local wrap status.

- `collider serve <path> [--host HOST] [--port PORT] [--push-token TOKEN] [--push-token-env VAR] [--publish-url URL]`  
  Serve a filesystem repository over HTTP.
  Collider serves the path directly and creates repository layout if it does not exist.
  When creating a new repository path, Collider runs endpoint smoke checks before starting the main server.
  The server exposes read routes only under `/v2/` (`/v2/releases.json`, package `.wrap`, and `/v2/archives/*`);
  other GET paths return `404`.
  The push route `POST /v2/_collider/v1/push` is disabled by default and enabled only when a push token is
  configured (`--push-token`, `$COLLIDER_PUSH_TOKEN`, or `--push-token-env` to specify the environment variable name).
  Built-in push auth is intentionally minimal (static bearer token).

## License

Apache-2.0  
Copyright 2026 MOG Robotics OÜ
