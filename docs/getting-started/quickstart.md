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
    { "name": "my-lib", "source": "collider", "version": ">=1.2.0" }
  ]
}
```

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
collider pkg add my-lib
```

Collider resolves the newest available version, installs the wrap file into
`subprojects/`, and populates `subprojects/packagecache/` for offline builds.
If the package has transitive dependencies, Collider installs them
automatically. (To add a package from WrapDB such as `fmt`, use
`collider pkg add fmt` and it will be added as a collider dependency.)

To pin a version range:

```bash
collider pkg add my-lib --version '>=1.2,<2.0'
```

## 4. Configure the Meson Build

```bash
collider setup
```

This runs Meson setup with the collider-managed subprojects in place. Pass
extra Meson arguments after `--`:

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

After building your project, publish it to a repository:

```bash
collider publish local
```

The package name and version are read from Meson introspection. Collider
generates the source archive and wrap file and stores them in the repository.

## Next Steps

- [Project Setup](../guide/project-setup.md): detailed `collider.json` and `collider setup` usage
- [Managing Packages](../guide/managing-packages.md): add, remove, upgrade, search
- [Repositories](../guide/repositories.md): repository types and configuration
