# Project Setup

## Initializing a Project

Run `collider init` in a directory that contains a `meson.build` file:

```bash
collider init
```

This creates a `collider.json` file with an empty dependency list. Collider
refuses to initialize outside a Meson project.

If your `meson.build` `project()` call has no `license:` field, `collider init`
emits a warning at setup time, because a missing license causes warnings on
every `collider publish`. Add it now to avoid the noise later:

```meson
project('mylib', 'cpp', version: '1.0.0', license: 'MIT')
```

## The `collider.json` File

`collider.json` lives at the root of your Meson project, next to `meson.build`.
It declares project metadata and dependencies:

```json
{
  "description": "My Meson library",
  "dependencies": [
    { "name": "fmt", "source": "system" },
    { "name": "my-lib", "source": "collider", "version": ">=1.2.0" }
  ]
}
```

Each dependency has:

| Field       | Required | Description                                                  |
|-------------|----------|--------------------------------------------------------------|
| `name`      | Yes      | Package name as it appears in the repository.                |
| `source`    | Yes      | Either `"system"` or `"collider"`.                           |
| `version`   | No       | A PEP 440 version constraint such as `>=1.2,<2.0`.          |

System dependencies are not managed by Collider. They document that the
project expects a system-installed library. Only `"collider"` dependencies are
resolved and installed.

## Running Meson Setup

`collider setup` configures the Meson build:

```bash
collider setup
```

By default, the build directory is `collider-build`. Override it with
`--builddir`:

```bash
collider setup --builddir build
```

To pass arguments through to Meson, place them after `--`:

```bash
collider setup -- --buildtype=debug -Dfoo=bar
```

You can also specify a different source directory:

```bash
collider setup --sourcedir path/to/project
```

## Build Directory Conventions

Collider defaults to `collider-build` as the build directory to avoid conflicts
with a project's own `build/` directory. Many Collider commands read Meson
introspection data from this directory, so keep it consistent across your
workflow.
