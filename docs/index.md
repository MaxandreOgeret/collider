---
hide:
  - toc
---

<p align="center">
<img src="assets/logo.svg" width="300" alt="Collider logo">
</p>

<p align="center">
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml"><img src="https://github.com/MaxandreOgeret/collider/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://pypi.org/project/collider-wraps/"><img src="https://img.shields.io/pypi/v/collider-wraps?label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/MaxandreOgeret/collider/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://codecov.io/gh/MaxandreOgeret/collider"><img src="https://codecov.io/gh/MaxandreOgeret/collider/graph/badge.svg" alt="codecov"></a>
  <a href="https://matrix.to/#/#collider:matrix.org"><img src="https://img.shields.io/badge/Join%20chat-%23collider%3Amatrix.org-000000?logo=matrix" alt="Matrix"></a>
</p>

<p align="center"><strong>A package and dependency manager for Meson projects, centered on Meson's wrap system.</strong></p>

Collider streamlines how you publish, share, and consume Meson wrap packages.
It works with WrapDB-compatible repositories so your existing Meson workflows stay
intact while gaining proper dependency management.

## Key Features

- **Publish and share** Meson projects as wrap packages to local or remote repositories.
- **Add, upgrade, and remove** dependencies with version constraints.
- **Resolve transitive dependencies** automatically by scanning `meson.build` files.
- **Lock dependencies** for reproducible builds across machines and CI.
- **Offline builds** through a local cache of wraps and archives.
- **Host your own repository** with a built-in HTTP server or a static filesystem layout.
- **WrapDB-compatible**: works with the official Meson WrapDB out of the box.

## Next Steps

- [Install Collider](getting-started/installation.md)
- [Follow the Quick Start guide](getting-started/quickstart.md)
- [Read the CLI reference](reference/cli.md)
