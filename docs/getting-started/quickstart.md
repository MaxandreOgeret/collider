# Quick Start

This guide walks through the basic Collider workflow: setting up a project,
adding a dependency, and publishing a package.

## 1. Initialize a Project

Navigate to the root of your Meson project (where `meson.build` lives) and
create a `collider.json`:

```bash
collider init
```

This generates a minimal `collider.json` next to your `meson.build`.

You can also write it by hand:

```json
{
  "description": "Example Meson library",
  "dependencies": [
    { "name": "fmt", "source": "system" },
    { "name": "zlib", "source": "collider", "version": ">=1.2.0" }
  ]
}
```

The two `source` values mean different things. A `source: "system"` dependency
is hand-declared and unmanaged: Collider does not install it, and you provide
it from your system or environment. A `source: "collider"` dependency is
managed by Collider, which installs it and records the entry when you run
`collider pkg add`.

## 2. Configure a Repository

Before adding packages you need at least one repository. Add the official Meson
WrapDB:

```bash
collider repo add wrapdb wrap https://wrapdb.mesonbuild.com/v2/
```

Or add a local filesystem repository:

```bash
mkdir -p /path/to/repo
collider repo add local filesystem file:///path/to/repo \
    --publish-url https://packages.example.com/collider/
```

List configured repositories with:

```bash
collider repo list
```

## 3. Add a Dependency

```bash
collider pkg add zlib
```

Collider resolves the newest available version from a configured repository
such as WrapDB, installs the wrap file into `subprojects/`, and populates
`subprojects/packagecache/` for offline builds. The package is recorded in
`collider.json` as a `source: "collider"` (managed) dependency. If the package
has transitive dependencies, Collider installs them automatically. If no
matching package is found, the command reports an error and exits without
modifying your project.

To pin a version range:

```bash
collider pkg add zlib --version '>=1.2,<2.0'
```

## 4. Configure the Meson Build

```bash
collider setup
```

This runs Meson setup with the collider-managed subprojects in place, forcing
Meson to use Collider's wraps instead of system copies. Until you create
`collider.lock` in the next step, it forces all wraps and prints a warning;
`collider lock` then makes the scope authoritative. Pass extra Meson arguments
after `--`:

```bash
collider setup -- --buildtype=debug
```

## 5. Lock Dependencies

Create a lockfile for reproducible installs:

```bash
collider lock
```

The lockfile (`collider.lock`) records exact versions and wrap hashes so that
`collider install` reproduces the same state on any machine.

## 6. Publish a Package

Publish reads the project name and version from Meson introspection, so it
needs the build directory created by `collider setup` (step 4, default
`collider-build`). You do not need to compile first.

```bash
collider publish local
```

Collider generates the source archive and wrap file and stores them in the
repository. Point publish at a different build with `--builddir`.

## Next Steps

- [Project Setup](../guide/project-setup.md): detailed `collider.json` and `collider setup` usage
- [Managing Packages](../guide/managing-packages.md): add, remove, upgrade, search
- [Repositories](../guide/repositories.md): repository types and configuration
- [Locking and Installing](../guide/locking-and-installing.md): reproducible installs with `collider.lock`
- [Publishing](../guide/publishing.md): packaging and distributing your project
