<p align="center">
<img src="https://collider.ee/latest/assets/logo.svg" width="300">
</p>

<p align="center">
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://pypi.org/project/collider-wraps/"><img src="https://img.shields.io/pypi/v/collider-wraps?label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/MaxandreOgeret/collider/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://codecov.io/gh/MaxandreOgeret/collider"><img src="https://codecov.io/gh/MaxandreOgeret/collider/graph/badge.svg" alt="codecov"></a>
  <a href="https://app.codacy.com/gh/MaxandreOgeret/collider/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade"><img src="https://app.codacy.com/project/badge/Grade/3d3482248f0449d38843da4a93969b8a" alt="Codacy Badge"></a>
  <a href="https://matrix.to/#/#collider:matrix.org"><img src="https://img.shields.io/badge/Join%20chat-%23collider%3Amatrix.org-000000?logo=matrix" alt="Matrix"></a>
</p>

Collider is a dependency manager for Meson projects.

It also provides the infrastructure to run private WrapDB-compatible
package repositories, enabling teams to build their own package ecosystem.

**Documentation: [collider.ee](https://collider.ee)**

## Features

- Dependency manager for Meson wrap projects.
- Publishable wrap repositories.
- Host your own WrapDB-compatible package repository.
- Lockfiles for reproducible builds.
- Offline dependency cache.
- Compatible with WrapDB.

## Why Collider?

Meson provides WrapDB and subprojects, but dependency management across
multiple repositories can become difficult:

- Wrap files must be maintained manually.
- Offline builds require prefetching sources.
- Publishing reusable wraps is cumbersome.

Collider adds a lightweight package workflow on top of Meson's wrap system:

- Publishable wrap repositories.
- Self-hosted WrapDB-compatible registries.
- Reproducible dependency lockfiles.
- Offline dependency caching.

## Requirements

- Python 3.10+
- Meson 1.8.5+ (and Ninja)
- See [`pyproject.toml`](https://github.com/MaxandreOgeret/collider/blob/main/pyproject.toml) for Python dependency versions.

## Installation

```bash
pip install collider-wraps
```

### From source (development)

```bash
git clone git@github.com:MaxandreOgeret/collider.git
cd collider
uv venv
source .venv/bin/activate
uv sync
```

## Quick start

From the root of a Meson project (next to `meson.build`):

```bash
collider init                                                   # create collider.json
collider repo add wrapdb wrap https://wrapdb.mesonbuild.com/v2/ # configure a repository
collider pkg add zlib                                           # add a dependency
collider setup                                                  # configure the Meson build
collider lock                                                   # write collider.lock for reproducible installs
```

See the [Quick Start guide](https://collider.ee/latest/getting-started/quickstart/) for
a full walkthrough.

## Documentation

Full documentation lives at [collider.ee](https://collider.ee):

- [Getting started](https://collider.ee/latest/getting-started/quickstart/): install and first steps.
- [User guide](https://collider.ee/latest/guide/project-setup/): managing packages, repositories, publishing, locking, offline mode, and serving.
- [Configuration](https://collider.ee/latest/reference/configuration/): `config.json`, `collider.json`, and `collider.lock`.
- [CLI reference](https://collider.ee/latest/reference/cli/): every command, flag, and exit code.
- [Contributing](https://collider.ee/latest/development/contributing/): development setup and pull request checks.

## License

Apache-2.0  
Copyright 2026 MOG Robotics OÜ
