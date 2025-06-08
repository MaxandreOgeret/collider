# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

# Test layout mirrors the collider package structure for easier navigation:
#
#   test/
#   ├── common/           Shared fixtures and helpers (fixture.py, common.py)
#   ├── assets/           Meson projects and plugin fixtures for tests
#   ├── config/           Tests for collider.config
#   ├── cache/            Tests for collider.cache (WrapCache)
#   ├── entrypoint/       Tests for collider.entrypoint
#   ├── file_model/       Tests for collider.file_model (Colliderfile, ConfigFile, etc.)
#   ├── package/          Tests for collider.Package (WrapPackage)
#   ├── repository/       Tests for collider.repository (registry, implementation)
#   ├── subcommand/       Tests for each CLI subcommand
#   └── utils/            Tests for collider.utils (core, command, dataclass, meson, compat, repo_key, types)
#
# Pytest discovers all test_*.py files under test/ (see pyproject.toml).
