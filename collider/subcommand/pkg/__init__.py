# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Project dependency operations discovered from this package."""

from __future__ import annotations

import argparse
import os
import sys

from collider.log import logger
from collider.subcommand.SubcommandInterface import SubcommandInterface
from collider.utils import core
from collider.utils.compat import override


Interface = SubcommandInterface

# Mutable holder so we can cache without a global statement (Pylint W0603).
_PKG_SUBCOMMANDS_HOLDER: list[dict[str, type[SubcommandInterface]] | None] = [None]
_PROJECT_PACKAGE_ACTIONS = frozenset({'add', 'info', 'prune', 'remove', 'search', 'upgrade'})
_PKG_SUBCOMMAND_ALIASES = {'add': ['install'], 'info': ['policy'], 'remove': ['rm']}


def _get_pkg_subcommands() -> dict[str, type[SubcommandInterface]]:
    """
    Discover project-focused pkg subcommands once; reused in register() and execute().
    :return: Map of subcommand name to implementation class.
    """
    if _PKG_SUBCOMMANDS_HOLDER[0] is None:
        pkg_module = sys.modules[__name__]
        discovered = core.discover_plugins(pkg_module)
        _PKG_SUBCOMMANDS_HOLDER[0] = {
            name: subcommand
            for name, subcommand in discovered.items()
            if name in _PROJECT_PACKAGE_ACTIONS
        }
    return _PKG_SUBCOMMANDS_HOLDER[0]


def _pkg_help_summary() -> str:
    """Build the help line from discovered subcommand names."""
    names = sorted(_get_pkg_subcommands().keys())
    return 'Project dependency operations: ' + ', '.join(names) + '.'


class Pkg(SubcommandInterface):
    """Project dependency operations."""

    @staticmethod
    def help() -> str:
        """Short help string surfaced by the CLI."""
        return _pkg_help_summary()

    @classmethod
    def long_help(cls) -> str:
        """Longer help text shown for `collider pkg --help`."""
        del cls
        return (
            'Manage project dependencies (add/remove/prune/search/upgrade/info) '
            'for the current Meson project.'
        )

    @staticmethod
    def register(parser: argparse.ArgumentParser) -> None:
        """Keep argparse wiring co-located with the command."""
        subparsers = parser.add_subparsers(dest='pkg_subcommand', required=True)
        for name, subcommand_class in _get_pkg_subcommands().items():
            subparser = subparsers.add_parser(
                name,
                aliases=_PKG_SUBCOMMAND_ALIASES.get(name, []),
                help=subcommand_class.help(),
                description=subcommand_class.long_help(),
                epilog=subcommand_class.epilog(),
            )
            subcommand_class.register(subparser)
            subparser.set_defaults(pkg_action=name)

    @override
    def execute(self) -> int:
        """Run the pkg command.
        :return: Exit code.
        """
        action = getattr(self.args, 'pkg_action', None)
        subcommands = _get_pkg_subcommands()
        if action is None or action not in subcommands:
            logger.critical(f'Unknown pkg subcommand: {action}')
            return os.EX_USAGE
        subcommand_class = subcommands[action]
        return subcommand_class(self.args, self.context).execute()


__all__ = ['Interface', 'Pkg']
