# Contributing

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Git

## Setup

```bash
git clone git@github.com:MaxandreOgeret/collider.git
cd collider
uv sync
```

`uv sync` installs the project and all development dependencies (including docs)
into an isolated virtual environment.

## Running Tests

```bash
uv run pytest
```

## Code Style

Collider uses [ruff](https://docs.astral.sh/ruff/) for formatting and linting,
[ty](https://docs.astral.sh/ty/) for type checking, and
[pylint](https://pylint.readthedocs.io/) for static analysis.

```bash
uv run ruff format          # auto-format
uv run ruff check           # lint
uv run ty check collider    # type check
uv run pylint collider      # static analysis (target: 10.00/10)
```

## Running the Docs Locally

```bash
uv run zensical serve
```

The site is served at `http://127.0.0.1:8000` with live reload on file changes.
Pages are rebuilt automatically as you edit files under `docs/`.

## Commit Convention

Collider uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`, `ci`.

Examples:

```
feat(publish): add --dry-run flag
fix(serve): log warning on non-loopback push endpoint
docs(contributing): add local dev setup guide
```

## Pull Requests

Open pull requests against `main`. CI runs pytest on Python 3.10 through 3.13,
and ruff, ty, and pylint on Python 3.13. All checks must pass before merge.
