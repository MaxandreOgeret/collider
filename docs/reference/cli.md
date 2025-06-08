# CLI Reference

Global options must be placed before the subcommand. Use `collider -v <command>`
for debug logging; some `pkg` subcommands use `-v` for `--version`.

```bash
collider [-v] [--offline] <command> [options]
```

Use `collider <command> --help` for command-specific help.

---

## `init`

Create `collider.json` in the current project.

```bash
collider init
```

Refuses to run outside a Meson project (no `meson.build` found).

---

## `setup`

Configure a Meson project.

```bash
collider setup [--sourcedir PATH] [--builddir PATH] -- [meson args]
```

| Option            | Description                                      |
|-------------------|--------------------------------------------------|
| `--sourcedir PATH` | Meson source directory.                         |
| `--builddir PATH`  | Meson build directory (default: `collider-build`). |
| `-- [args]`        | Arguments passed through to Meson.              |

**Examples:**

```bash
collider setup
collider setup --builddir build
collider setup -- --buildtype=debug
```

---

## `lock`

Resolve dependencies from `collider.json` and write `collider.lock`.

```bash
collider lock [--offline]
```

| Option      | Description                               |
|-------------|-------------------------------------------|
| `--offline` | Resolve only from the local cache.        |

---

## `install`

Install dependencies. Uses `collider.lock` when present, falls back to
`collider.json` otherwise.

```bash
collider install [--frozen] [--offline]
```

| Option      | Description                                                  |
|-------------|--------------------------------------------------------------|
| `--frozen`  | Fail if `collider.lock` is missing or stale. For CI use.     |
| `--offline` | Disable network access; use cached wraps and archives only.  |

---

## `patch`

Create a patch archive from Git changes.

```bash
collider patch [--builddir PATH] [--base REV] [--output PATH] [--list]
               [--include-uncommitted / --no-include-uncommitted]
```

| Option                      | Description                                          |
|-----------------------------|------------------------------------------------------|
| `--builddir PATH`           | Meson build directory (default: `collider-build`).   |
| `--base REV`                | Git revision to diff against.                        |
| `--output PATH`             | Output path (default: `dist/<name>_<version>_patch.tar.xz`). |
| `--list`                    | Dry-run: list files without creating the archive.    |
| `--include-uncommitted`     | Include staged, unstaged, and untracked files (default). |
| `--no-include-uncommitted`  | Exclude working tree changes; archive only the committed diff from `--base`. |

---

## `publish`

Generate a wrap and source archive, then publish to a repository.

```bash
collider publish <repo> [--builddir PATH] [--patch-archive PATH]
                        [--push-token-env VAR]
```

| Option                | Description                                              |
|-----------------------|----------------------------------------------------------|
| `<repo>`              | Repository name from `config.json`.                      |
| `--builddir PATH`     | Meson build directory (default: `collider-build`).       |
| `--patch-archive PATH`| Attach a patch archive to the published package.         |
| `--push-token-env VAR`| Environment variable holding the bearer token (default: `COLLIDER_PUSH_TOKEN`). |

---

## `unpublish`

Remove a package version from a repository.

```bash
collider unpublish <repo> <package> <version> [--push-token-env VAR]
```

| Option                | Description                                              |
|-----------------------|----------------------------------------------------------|
| `<repo>`              | Repository name from `config.json`.                      |
| `<package>`           | Package name.                                            |
| `<version>`           | Version to remove.                                       |
| `--push-token-env VAR`| Environment variable holding the bearer token.           |

---

## `repo add`

Add a repository entry to `config.json`.

```bash
collider repo add <name> <type> <url> [--publish-url URL]
```

| Option            | Description                                              |
|-------------------|----------------------------------------------------------|
| `<name>`          | Repository name.                                         |
| `<type>`          | Repository type: `filesystem`, `wrap`, or `collider`.    |
| `<url>`           | Repository URL.                                          |
| `--publish-url URL` | Required for `filesystem` repositories. Ignored for others. |

---

## `repo remove`

Remove a repository entry from `config.json`.

```bash
collider repo remove <name>
```

Alias: `collider repo rm <name>`

---

## `repo list`

List configured repositories.

```bash
collider repo list
```

Alias: `collider repo ls`

---

## `pkg add`

Add a package dependency and install it into `subprojects/`.

```bash
collider pkg add <name> [--version SPEC] [--offline] [--force]
                 [--include PKG] ... [--exclude PKG] ...
                 [--include-conditional] [--exclude-optional]
```

| Option               | Description                                              |
|----------------------|----------------------------------------------------------|
| `<name>`             | Package name.                                            |
| `--version SPEC`     | Version constraint (e.g. `>=1.2,<2.0`).                  |
| `--offline`          | Resolve from cache only.                                 |
| `--force`            | Reinstall even if the package is already installed.      |
| `--include PKG`      | Force-include a transitive dependency by name (repeatable). |
| `--exclude PKG`      | Skip a transitive dependency by name (repeatable).       |
| `--include-conditional` | Also resolve dependencies inside Meson `if` blocks.  |
| `--exclude-optional` | Skip optional (`required: false`) dependencies.          |

Fails if the package is already installed unless `--force` is given. Does not update `collider.lock`.

---

## `pkg remove`

Remove a dependency from `collider.json` and delete its wrap from
`subprojects/`. Orphaned transitive dependencies (no longer needed by any remaining direct dependency) are removed only when a lockfile exists; only wraps listed in the lockfile are considered for cleanup (manual wraps are never removed).

```bash
collider pkg remove <name>
```

Alias: `collider pkg rm <name>`

Does not update `collider.lock`; you are warned to run `collider lock` if the removed package is still in the lockfile.

---

## `pkg search`

Search repositories or the local cache by package name.

```bash
collider pkg search <pattern> [--cache] [--repository NAME] ... [--version SPEC]
```

| Option              | Description                                          |
|---------------------|------------------------------------------------------|
| `<pattern>`         | Regular expression matched against package names.    |
| `--cache`           | Search only cached wraps without accessing repositories. |
| `--repository NAME` | Restrict search to specific repositories (repeatable). |
| `--version SPEC`    | Filter by version constraint.                        |

---

## `pkg info`

Show versions, origins, and cache status for a package.

```bash
collider pkg info <name> [--repository NAME] ...
```

| Option              | Description                                          |
|---------------------|------------------------------------------------------|
| `<name>`            | Package name.                                        |
| `--repository NAME` | Restrict to specific repositories (repeatable).      |

---

## `pkg upgrade`

Upgrade one or all collider-managed dependencies.

```bash
collider pkg upgrade [<name>] [--version SPEC] [--offline]
```

| Option          | Description                                              |
|-----------------|----------------------------------------------------------|
| `<name>`        | Package to upgrade. Omit to upgrade all.                 |
| `--version SPEC`| New version constraint (only valid with a package name). |
| `--offline`     | Resolve from cache only.                                 |

Does not update `collider.lock`.

---

## `status`

Show collider-managed dependencies and local wrap status.

```bash
collider status
```

Reports **tracked** Collider dependencies from `collider.json`, **system**
dependencies declared with `"source": "system"`, **transitive** dependencies,
and **untracked** wraps found in `subprojects/`. For tracked and transitive
entries, Collider shows version information and whether the wrap is installed or
missing. When `collider.lock` exists, transitive classification uses the lock.
Otherwise, Collider re-resolves from `collider.json` when repository metadata
is available. Lock drift (`ok` / `modified` / `missing`) is reported when a
lockfile exists.

---

## `serve`

Serve a filesystem repository over HTTP.

```bash
collider serve <path> [--host HOST] [--port PORT]
               [--push-token TOKEN] [--push-token-env VAR]
               [--publish-url URL]
```

| Option                | Description                                              |
|-----------------------|----------------------------------------------------------|
| `<path>`              | Filesystem path to serve.                                |
| `--host HOST`         | Bind address (default: `127.0.0.1`).                     |
| `--port PORT`         | Bind port (default: `8000`).                             |
| `--publish-url URL`   | Base URL for archive URLs in wraps. Defaults to the repository path as a local `file://` URL. |
| `--push-token TOKEN`  | Static bearer token for push auth.                       |
| `--push-token-env VAR`| Environment variable holding the push token.             |

Push routes are disabled unless a push token is configured.
