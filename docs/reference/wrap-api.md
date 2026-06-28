# Wrap API

Collider consumes and produces a WrapDB-compatible API. The API is a static
layout plus a `releases.json` index, optionally extended with write endpoints.

## Base URL and Versioning

- Remote repositories should use HTTPS. HTTP is allowed with a warning.
- If a configured URL does not end with `/v2/`, Collider appends it
  automatically and logs a warning.
- Filesystem repositories use `publish_url` from `config.json` to generate
  wrap and archive URLs.

## Endpoints

### Read Endpoints

| Method | Path                                          | Description                |
|--------|-----------------------------------------------|----------------------------|
| GET    | `<base>/v2/releases.json`                     | Package index.             |
| GET    | `<base>/v2/<name>_<version>/<name>.wrap`      | Wrap file for a version.   |
| GET    | `<base>/v2/archives/<name>_<version>/<file>`  | Source or patch archive.   |

`<base>` is the server base URL. Configured repository URLs are normalized to
include `/v2/`.

Remote WrapDB repositories may point wrap URLs to upstream archives instead of
hosting them under `/v2/archives/`.

### Write Endpoints (Collider Extension)

These endpoints are optional and only available when push authentication is
configured.

#### Publish

```
POST <base>/v2/_collider/v1/push
Authorization: Bearer <token>
```

JSON payload:

```json
{
  "name": "demo",
  "version": "1.0.0",
  "wrap_text": "[wrap-file]\n...",
  "source_filename": "demo-1.0.0.tar.xz",
  "source_archive_base64": "<base64>",
  "patch_filename": "demo.patch",
  "patch_archive_base64": "<base64>"
}
```

- `patch_filename` and `patch_archive_base64` are optional but must be
  provided together.
- `source_filename` and `patch_filename` must match the values in `wrap_text`.

Response codes:

- Returns `201 Created` on success.
- Returns `409 Conflict` if the version already exists.
- Returns `400 Bad Request` for an invalid or incomplete payload.
- Returns `413 Payload Too Large` if the request body exceeds 128 MiB.
- Returns `401 Unauthorized` for a missing or invalid bearer token.

#### Unpublish

```
DELETE <base>/v2/_collider/v1/packages/<name>/<version>
Authorization: Bearer <token>
```

- Returns `204 No Content` on success.
- Returns `404` if the package is not found or the path is invalid.
- Returns `400` for other request errors.
- `<name>` and `<version>` are single path segments. The literal segments `.`
  and `..` are rejected.

## `releases.json` Schema

A JSON object keyed by package name:

```json
{
  "fmt": {
    "versions": ["10.2.1", "11.0.0"],
    "dependency_names": ["fmt", "fmt-header"]
  },
  "my-lib": {
    "versions": ["1.2.0"]
  }
}
```

| Field              | Type     | Required | Description                                    |
|--------------------|----------|----------|------------------------------------------------|
| `versions`         | string[] | Yes      | Available version strings.                     |
| `dependency_names` | string[] | No       | Names from the wrap `[provide]` section. Deduplicated and sorted. Required for transitive dependency resolution. |

## Wrap File Requirements

Collider enforces these rules when parsing wraps:

- A `[wrap-file]` section is required.
- Required fields: `source_url`, `source_filename`, `source_hash`.
- Patch metadata is all-or-nothing: if any of `patch_url`, `patch_filename`,
  or `patch_hash` is present, all three must be present.
- HTTP URLs are allowed with a warning.

When Collider stages archives into a filesystem repository:

- `source_filename` and `patch_filename` must not contain path components.
- Filenames must match the wrap metadata exactly.
- Archive hashes are verified before publishing.

### Example Wrap File

```ini
[wrap-file]
directory = my-lib-1.2.0
source_url = https://packages.example.com/collider/archives/my-lib_1.2.0/my-lib-1.2.0.tar.xz
source_filename = my-lib-1.2.0.tar.xz
source_hash = <sha256>
patch_url = https://packages.example.com/collider/archives/my-lib_1.2.0/my-lib-1.2.0.patch
patch_filename = my-lib-1.2.0.patch
patch_hash = <sha256>

[provide]
my-lib = my_lib_dep
```

## Filesystem Repository Layout

```
repo/
  releases.json
  my-lib_1.2.0/
    my-lib.wrap
  archives/
    my-lib_1.2.0/
      my-lib-1.2.0.tar.xz
      my-lib-1.2.0.patch
```

Collider regenerates `releases.json` on every publish or unpublish.

### URL Mapping

When served at `publish_url`:

| Resource       | URL                                                       |
|----------------|-----------------------------------------------------------|
| Package index  | `<publish_url>/v2/releases.json`                          |
| Wrap file      | `<publish_url>/v2/<name>_<version>/<name>.wrap`           |
| Archive        | `<publish_url>/archives/<name>_<version>/<filename>`      |

The Archive row is the URL embedded in published wraps as `source_url` and
`patch_url`, so it has no `/v2/` prefix and matches the example wrap above. The
`releases.json` and wrap file rows are HTTP serve routes, which Collider serves
under `/v2/`, as is the `/v2/archives/` read endpoint listed above. The embedded
archive URL is separate from that read route and resolves against `publish_url`.
