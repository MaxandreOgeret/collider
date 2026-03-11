# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Test the pkg subcommand dispatcher."""

import argparse
import os

from unittest.mock import MagicMock

from collider.Context import Context
from collider.subcommand.pkg import Pkg
from test.common.common import Subcommand, run_subcommand


def _make_context() -> Context:
    """Build a minimal context mock for Pkg tests."""
    ctx = MagicMock(spec=Context)
    ctx.offline = False
    return ctx


def test_pkg_register_creates_subparsers() -> None:
    """Pkg.register() adds add/search/remove/prune/upgrade/info subparsers."""
    parser = argparse.ArgumentParser()
    Pkg.register(parser)

    args = parser.parse_args(['add', 'my-package'])
    assert args.pkg_subcommand == 'add'
    assert args.pkg_action == 'add'
    assert args.package == 'my-package'

    args = parser.parse_args(['add', 'my-package', '--version', '>=1.0'])
    assert str(args.version) == '>=1.0'

    args = parser.parse_args(['install', 'my-package'])
    assert args.pkg_subcommand == 'install'
    assert args.pkg_action == 'add'
    assert args.package == 'my-package'

    args = parser.parse_args(['search', '.*'])
    assert args.pkg_subcommand == 'search'
    assert args.pkg_action == 'search'
    assert args.pattern == '.*'

    args = parser.parse_args(['info', 'my-package'])
    assert args.pkg_subcommand == 'info'
    assert args.pkg_action == 'info'
    assert args.package == 'my-package'

    args = parser.parse_args(['policy', 'my-package'])
    assert args.pkg_subcommand == 'policy'
    assert args.pkg_action == 'info'
    assert args.package == 'my-package'

    args = parser.parse_args(['remove', 'my-package'])
    assert args.pkg_subcommand == 'remove'
    assert args.pkg_action == 'remove'
    assert args.package == 'my-package'

    args = parser.parse_args(['rm', 'my-package'])
    assert args.pkg_subcommand == 'rm'
    assert args.pkg_action == 'remove'
    assert args.package == 'my-package'

    args = parser.parse_args(['prune'])
    assert args.pkg_subcommand == 'prune'
    assert args.pkg_action == 'prune'
    assert args.dry_run is False

    args = parser.parse_args(['prune', '--dry-run'])
    assert args.pkg_subcommand == 'prune'
    assert args.pkg_action == 'prune'
    assert args.dry_run is True

    args = parser.parse_args(['upgrade'])
    assert args.pkg_subcommand == 'upgrade'
    assert args.pkg_action == 'upgrade'
    assert args.package is None

    args = parser.parse_args(['upgrade', 'my-package', '--version', '>=1.0'])
    assert args.pkg_subcommand == 'upgrade'
    assert args.pkg_action == 'upgrade'
    assert args.package == 'my-package'
    assert str(args.version) == '>=1.0'


def test_pkg_execute_dispatches_to_add() -> None:
    """Dispatch to add returns EX_NOINPUT when not in a Meson project."""
    args = argparse.Namespace(
        pkg_subcommand='add',
        pkg_action='add',
        package='some-package',
        offline=False,
    )
    cmd = Pkg(args, _make_context())
    assert cmd.execute() == os.EX_NOINPUT


def test_pkg_execute_unknown_action() -> None:
    """Unknown pkg_action yields EX_USAGE."""
    args = argparse.Namespace(pkg_subcommand='nonexistent', pkg_action='nonexistent')
    cmd = Pkg(args, _make_context())
    assert cmd.execute() == os.EX_USAGE


def test_pkg_cli_dispatch() -> None:
    """Full CLI collider pkg add <name> dispatches to Add and returns its exit code."""
    exit_code = run_subcommand(Subcommand.PKG, ['add', 'some-package'])
    assert exit_code == os.EX_NOINPUT


def test_pkg_long_help_mentions_prune() -> None:
    """The pkg help text includes the prune subcommand."""
    assert 'prune' in Pkg.long_help()
