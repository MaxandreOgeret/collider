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

## Dry Run

Use `--dry-run` to validate and package a release without writing anything to
the repository:

```bash
collider publish <repo> --dry-run
```

Collider runs all validation steps, builds the source archive, and prints a
summary of what would be published. No files are written and no network
requests are made. The push token is not required in dry-run mode.

```
[dry-run] Would publish: my-lib 1.0.0
[dry-run] Archive: my-lib-1.0.0.tar.xz (0.1 MB)
[dry-run] Destination: my-repo (file:///path/to/repo)
[dry-run] No files written.
```

## Duplicate Version Error

If the version being published already exists in the repository, Collider exits
with a non-zero status and suggests the available remedies:

```
CRITICAL: Version "1.0.0" of "my-lib" already exists in repository "my-repo".
Unpublish the existing version first, or bump the project version.
```

Use `collider unpublish` to remove the existing version before re-publishing.

## The `[provide]` Section

Collider auto-generates the wrap `[provide]` entry as `<name> = <variable>`,
where the dependency variable is derived from the package name: every character
not in `[A-Za-z0-9_]` is replaced with `_`, and if the result does not begin
with a letter or underscore (for example a leading digit) an underscore is
prepended, then `_dep` is appended. This yields a valid Meson identifier. Ensure
your Meson project exposes that dependency variable so `dependency()` fallbacks
work.

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

| Flag                       | Description                                                                    |
|----------------------------|--------------------------------------------------------------------------------|
| `--builddir PATH`          | Meson build directory used to read project metadata (default: collider-build). |
| `--base REV`               | Git revision to diff against (default: HEAD).                                  |
| `--include-uncommitted`    | Include staged, unstaged, and untracked files (default).                       |
| `--no-include-uncommitted` | Exclude working tree changes; archive only the committed diff from `--base`.   |
| `--output PATH`            | Custom output path for the patch archive.                                      |
| `--list`                   | Dry-run: list files that would be included.                                    |

#### Empty Changeset

If there are no changed files (for example, `--base` points to `HEAD` with no
local changes), `collider patch` logs a warning and exits without creating an
archive. Use `--list` to see which files would be included before running the
full command.

#### Deleted Files

Deleted files cannot be included in a Meson wrap patch because wrap patches
cannot remove files cleanly. Any deletion aborts the patch, including the delete
half of a rename (with `--no-renames`, a rename surfaces as a deletion of the
old path plus an addition of the new one). To proceed, commit the deletion
upstream so it lands as a normal committed change, or remove the deleted path
from the change set before running `collider patch`. There is no per-file
exclude flag; `--no-include-uncommitted` only excludes the entire working tree,
which is a blunt scope change rather than a way to drop a single deletion.

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
