# Repositories

Collider uses repositories to store and retrieve wrap packages. All repository
types serve a WrapDB-compatible layout so Meson can consume them directly.

## Repository Types

### Filesystem

A directory on disk containing wraps, archives, and a `releases.json` index.
Use filesystem repositories for local development or to build a repository
that you serve over HTTP(S) yourself.

```bash
collider repo add local filesystem file:///path/to/repo \
    --publish-url https://packages.example.com/collider/
```

`--publish-url` is required for filesystem repositories. It tells Collider
which base URL to write into wrap files when publishing, so that consumers can
download archives over the network.

### Wrap

A read-only remote that speaks the WrapDB protocol. The official Meson WrapDB
is a wrap repository:

```bash
collider repo add wrapdb wrap https://wrapdb.mesonbuild.com/v2/
```

Wrap repositories support search, add, and info commands but cannot be
published to.

### Collider

A remote repository that extends the WrapDB protocol with write endpoints
for publishing and unpublishing packages:

```bash
collider repo add my-collider collider https://packages.example.com/collider/v2/
```

Publish and unpublish operations require a bearer token. See
[Publishing](publishing.md) for authentication details.

## Configuration File

Repository entries are stored in `config.json`, located at
`~/.config/collider/config.json` (or `$XDG_CONFIG_HOME/collider/config.json`).

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

## Managing Repositories from the CLI

### Add a Repository

```bash
collider repo add <name> <type> <url> [--publish-url URL]
```

If another repository already uses the same URL, Collider logs a warning but
still adds the entry.

### Remove a Repository

```bash
collider repo remove <name>
```

The alias `repo rm` also works.

### List Repositories

```bash
collider repo list
```

The alias `repo ls` also works.

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

`releases.json` is regenerated on every publish or unpublish to keep the
repository index up to date. When wraps define a `[provide]` section,
`releases.json` includes `dependency_names` - the mapping that enables
transitive dependency resolution. Repositories without `dependency_names`
still work for direct installs but do not participate in transitive
resolution. See [Wrap API](../reference/wrap-api.md) for the full
specification.
