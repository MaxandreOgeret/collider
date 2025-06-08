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
| `--push-token-env VAR`     | Environment variable holding the push token.      |

## Exposed Endpoints

The server exposes only WrapDB-compatible routes under `/v2/`:

| Method | Path                                             | Description                |
|--------|--------------------------------------------------|----------------------------|
| GET    | `/v2/releases.json`                              | Package index.             |
| GET    | `/v2/<name>_<version>/<name>.wrap`               | Wrap file.                 |
| GET    | `/v2/archives/<name>_<version>/<filename>`       | Source or patch archive.   |

Any other path returns `404 Not Found`.

## Push Authentication

By default, the push endpoint is disabled. Enable it by configuring a token:

```bash
collider serve /path/to/repo --push-token my-secret
```

Or read the token from an environment variable:

```bash
export COLLIDER_PUSH_TOKEN=my-secret
collider serve /path/to/repo --push-token-env COLLIDER_PUSH_TOKEN
```

When enabled, the server exposes:

| Method | Path                                             | Description                |
|--------|--------------------------------------------------|----------------------------|
| POST   | `/v2/_collider/v1/push`                          | Publish a package.         |
| DELETE | `/v2/_collider/v1/packages/<name>/<version>`     | Unpublish a package.       |

Both require `Authorization: Bearer <token>`.

!!! note
    Built-in push auth is intentionally minimal (static bearer token). For
    production use, place a reverse proxy with proper authentication in front
    of the server.

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
