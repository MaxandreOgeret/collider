# Project Setup

## Initializing a Project

Run `collider init` in a directory that contains a `meson.build` file:

```bash
collider init
```

This creates a `collider.json` file with an empty dependency list. Collider
refuses to initialize outside a Meson project.

If your `meson.build` `project()` call has no `license:` field, `collider init`
emits a non-fatal warning during the init run. The same missing license also
warns on every `collider setup`. Add it now to avoid the noise later:

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

| Field                 | Required | Description                                                |
|-----------------------|----------|------------------------------------------------------------|
| `name`                | Yes      | Package name as it appears in the repository.              |
| `source`              | Yes      | Either `"system"` or `"collider"`.                         |
| `version`             | No       | A PEP 440 version constraint such as `>=1.2,<2.0`.         |
| `include`             | No       | Array of transitive dependency names to force resolve.     |
| `exclude`             | No       | Array of transitive dependency names to skip.              |
| `include_conditional` | No       | Boolean. Also resolve conditional transitive dependencies. |
| `exclude_optional`    | No       | Boolean. Skip optional transitive dependencies.            |

The last four fields apply only to the transitive resolution of the dependency
they are declared on. See
[Including or Excluding Specific Dependencies](managing-packages.md#including-or-excluding-specific-dependencies)
for the matching `collider pkg add` flags and their precedence.

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
