# Serving Repositories

Collider includes a built-in HTTP server for hosting filesystem repositories
locally. This is useful for testing, team sharing, and lightweight internal
package hosting.

## Starting the Server

```bash
collider serve /path/to/repo
```

If the directory does not exist, Collider creates the repository layout
(`releases.json`, `archives/`) and runs endpoint smoke checks before starting.

### Options

| Flag                | Description                                              |
|---------------------|----------------------------------------------------------|
| `--host HOST`       | Bind address (default: `127.0.0.1`).                     |
| `--port PORT`       | Bind port (default: `8000`).                             |
| `--publish-url URL` | Base URL for archive URLs in wraps. Defaults to the repository path as a local `file://` URL. |
| `--push-token TOKEN`       | Static bearer token for push auth.                |
| `--push-token-env VAR`     | Environment variable holding the push token (default: `COLLIDER_PUSH_TOKEN`). |

## Exposed Endpoints

The server exposes only WrapDB-compatible routes under `/v2/`:

| Method | Path                                             | Description                |
|--------|--------------------------------------------------|----------------------------|
| GET    | `/v2/releases.json`                              | Package index.             |
| GET    | `/v2/<name>_<version>/<name>.wrap`               | Wrap file.                 |
| GET    | `/v2/archives/<name>_<version>/<filename>`       | Source or patch archive.   |

Any other path returns `404 Not Found`.

## Push Authentication

The push and delete endpoints auto-enable whenever a token is available.
`--push-token-env` defaults to `COLLIDER_PUSH_TOKEN`, so an exported
`COLLIDER_PUSH_TOKEN` enables them with no extra flags:

```bash
export COLLIDER_PUSH_TOKEN=my-secret
collider serve /path/to/repo
```

You can also pass the token inline instead:

```bash
collider serve /path/to/repo --push-token my-secret
```

The endpoints are disabled only when neither `--push-token` nor the
`COLLIDER_PUSH_TOKEN` environment variable is present.

When enabled, the server exposes:

| Method | Path                                             | Description                |
|--------|--------------------------------------------------|----------------------------|
| POST   | `/v2/_collider/v1/push`                          | Publish a package.         |
| DELETE | `/v2/_collider/v1/packages/<name>/<version>`     | Unpublish a package.       |

Both require `Authorization: Bearer <token>`.

!!! warning
    When the push endpoint is enabled and `--host` is bound to a non-loopback
    address, the server logs a security warning. Expose the push endpoint only
    through a trusted reverse proxy.

!!! note
    Built-in push auth is intentionally minimal (static bearer token). For
    production use, place a reverse proxy with proper authentication in front
    of the server.

### `--publish-url` and pushed wraps

When publishing via `collider publish`, the server rewrites archive URLs inside
the generated wrap files using `--publish-url` as the base. If `--publish-url`
is not set and push is enabled, Collider logs a warning and wraps will reference
`file://` archive URLs instead. This works for local testing but is almost
certainly wrong for any server that clients reach over the network -- always
pass `--publish-url` in that case.

## Example Workflow

Start a local server and publish to it:

```bash
# Terminal 1: start the server
collider serve /srv/repo --publish-url http://localhost:8000/ --push-token dev

# Terminal 2: configure and publish
collider repo add dev collider http://localhost:8000/v2/
export COLLIDER_PUSH_TOKEN=dev
collider publish dev
```
