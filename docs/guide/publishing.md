# Publishing

## Publishing a Package

```bash
collider publish <repo>
```

Collider reads the package name and version from Meson introspection in the
build directory (default: `collider-build`). It then:

1. Generates a source archive with root directory `<name>-<version>`.
2. Generates a wrap file pointing at the repository's `publish_url`.
3. Stores both in the repository.

### Filesystem Repositories

For filesystem repositories, Collider writes directly to disk. The repository
must have `publish_url` configured so archive URLs in the wrap file resolve
correctly.

### Collider Repositories

For collider-type repositories, Collider sends the package via
`POST /v2/_collider/v1/push`. Authentication uses a bearer token read from the
`COLLIDER_PUSH_TOKEN` environment variable by default:

```bash
export COLLIDER_PUSH_TOKEN=my-secret-token
collider publish my-collider
```

Use `--push-token-env` to read the token from a different variable:

```bash
collider publish my-collider --push-token-env MY_TOKEN
```

## The `[provide]` Section

Collider auto-generates the wrap `[provide]` entry as `<name> = <name>_dep`,
replacing `-` and `.` with `_`. Ensure your Meson project exposes that
dependency variable so `dependency()` fallbacks work.

## Publishing with a Patch

For third-party sources that need modifications, use a two-step workflow:

### 1. Create the Patch Archive

Modify the source tree, then run:

```bash
collider patch
```

This produces `dist/<name>_<version>_patch.tar.xz` from Git changes. By
default, Collider includes the committed diff from `--base` plus staged,
unstaged, and untracked files. Pass `--no-include-uncommitted` to archive only
the committed diff.

Options:

| Flag                         | Description                                      |
|------------------------------|--------------------------------------------------|
| `--base REV`                 | Git revision to diff against (default: HEAD).    |
| `--include-uncommitted`      | Include staged, unstaged, and untracked files (default). |
| `--no-include-uncommitted`   | Exclude working tree changes; archive only the committed diff from `--base`. |
| `--output PATH`              | Custom output path for the patch archive.        |
| `--list`                     | Dry-run: list files that would be included.      |

### 2. Publish with the Patch

Revert the source tree to the unmodified upstream, then publish:

```bash
collider publish local --patch-archive dist/my-lib_1.2.0_patch.tar.xz
```

The repository stores the base source archive plus the patch. Consumers
receive both on install.

## Unpublishing a Package

Remove a package version from a repository:

```bash
collider unpublish <repo> <package> <version>
```

For collider-type repositories, this calls
`DELETE /v2/_collider/v1/packages/<name>/<version>` with the same bearer token
mechanism used by `publish`.
