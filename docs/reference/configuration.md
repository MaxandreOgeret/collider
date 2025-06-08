# Configuration Files

Collider uses three JSON files to manage project state. This page documents
every field in each file.

---

## `collider.json`

Project metadata and dependency declarations. Lives at the root of a Meson
project, next to `meson.build`.

```json
{
  "description": "Example Meson library",
  "dependencies": [
    { "name": "fmt", "source": "system" },
    { "name": "my-lib", "source": "collider", "version": ">=1.2.0" }
  ]
}
```

### Fields

| Field          | Type   | Required | Description                                   |
|----------------|--------|----------|-----------------------------------------------|
| `description`  | string | No       | Short project description.                    |
| `dependencies` | array  | Yes      | List of dependency objects.                   |

### Dependency Object

| Field     | Type     | Required | Description                                       |
|-----------|----------|----------|---------------------------------------------------|
| `name`    | string   | Yes      | Package name as it appears in the repository.     |
| `source`  | string   | Yes      | `"system"` or `"collider"`.                       |
| `version` | string   | No       | PEP 440 version constraint (e.g. `>=1.2,<2.0`).  |
| `exclude` | string[] | No       | Transitive dependency names to skip during resolution. |
| `include` | string[] | No       | Transitive dependency names to force-include (e.g. conditional deps). |
| `include_conditional` | boolean | No | If `true`, resolve dependencies inside Meson `if` blocks. Persisted by `pkg add --include-conditional`. |
| `exclude_optional` | boolean | No | If `true`, skip optional (`required: false`) dependencies. Persisted by `pkg add --exclude-optional`. |

System dependencies are not resolved or installed by Collider. They serve as
documentation that the project expects a system-provided library.

The `exclude`, `include`, `include_conditional`, and `exclude_optional` fields
control transitive dependency resolution. They are persisted when using the
corresponding flags with `collider pkg add` and are respected by
`collider lock` and `collider install`.

`exclude` and `include` are scoped to the dependency they are declared on.
`include_conditional` and `exclude_optional` are broader switches: during
multi-root resolution, Collider enables those behaviors for the overall run
when any declared dependency sets them. See
[Managing Packages](../guide/managing-packages.md) for details.

---

## `config.json`

Application-wide configuration. Located at `~/.config/collider/config.json`
(or `$XDG_CONFIG_HOME/collider/config.json`).

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

### Fields

| Field          | Type  | Required | Description                |
|----------------|-------|----------|----------------------------|
| `repositories` | array | Yes      | List of repository objects.|

### Repository Object

| Field         | Type   | Required     | Description                                          |
|---------------|--------|--------------|------------------------------------------------------|
| `name`        | string | Yes          | Unique name for CLI references.                      |
| `type`        | string | Yes          | `"filesystem"`, `"wrap"`, or `"collider"`.           |
| `url`         | string | Yes          | Repository URL.                                      |
| `publish_url` | string | Conditional  | Base URL for archive rewrites. Required for `filesystem`, ignored for others. |

### Repository Types

| Type         | Read | Publish | Description                                       |
|--------------|------|---------|---------------------------------------------------|
| `filesystem` | Yes  | Yes     | Local directory with WrapDB-compatible layout.    |
| `wrap`       | Yes  | No      | Remote WrapDB-compatible endpoint (read-only).    |
| `collider`   | Yes  | Yes     | Remote with Collider write extension.             |

---

## `collider.lock`

Pinned resolution state for reproducible installs. Written by `collider lock`,
consumed by `collider install`.

```json
{
  "version": 1,
  "dependencies": {
    "my-lib": {
      "version": "1.2.0",
      "wrap_hash": "sha256:abc123..."
    },
    "fmt": {
      "version": "10.2.1",
      "wrap_hash": "sha256:def456..."
    }
  },
  "packages": {
    "abseil-cpp": {
      "version": "20240116.2",
      "wrap_hash": "sha256:..."
    }
  }
}
```

- **`dependencies`**: Direct dependencies (declared in `collider.json`). Keys are package names.
- **`packages`**: Transitive dependencies only. Keys are package names.

### Locked Entry Fields

Each entry under `dependencies` or `packages` contains:

| Field        | Type   | Description                                         |
|--------------|--------|-----------------------------------------------------|
| `version`    | string | Resolved version string.                            |
| `wrap_hash`  | string | SHA-256 hash of the `.wrap` file text (e.g. `sha256:...`). |

The lockfile does not record which repository each package came from. When installing, Collider fetches each package by name and version from any configured repository, then verifies the wrap hash.

The `wrap_hash` transitively pins archive hashes, filenames, and URLs because
those values are embedded in the wrap file. This means a single hash detects
any change to the package's source or patch archives.

System dependencies are not locked because there is no wrap artifact to pin.
