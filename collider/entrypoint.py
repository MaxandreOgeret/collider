# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""CLI entry point, argument parsing, and subcommand dispatch."""

import argparse
import importlib
import importlib.metadata
import os
import sys
import traceback

from importlib.metadata import PackageNotFoundError
from typing import cast, no_type_check

import collider
import collider.subcommand

from collider import config
from collider.Context import Context
from collider.errors import ColliderUserError
from collider.log import Level, configure_logging, logger
from collider.utils import core
from collider.utils.meson.meson import MesonUnavailableError


_DIST_NAME = 'collider-wraps'


class _RawDescriptionArgumentDefaultsHelpFormatter(
    argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter
):
    """Argument parser formatter that supports raw descriptions with defaults."""


def _get_installed_version() -> str:
    """Resolve the published distribution version, not the import package name."""
    try:
        return importlib.metadata.version(_DIST_NAME)
    except PackageNotFoundError:
        return '0.0.0'


def _get_project_url() -> str:
    """Read project metadata from the installed distribution when available."""
    try:
        metadata = importlib.metadata.metadata(_DIST_NAME)
        return str(metadata.get('Project-URL')).split(', ')[1]  # ty:ignore[unresolved-attribute]
    except (PackageNotFoundError, AttributeError, ValueError, IndexError):
        return 'https://github.com/MaxandreOgeret/collider'


def error_handler(e: Exception) -> None:
    """
    Handle unhandled exceptions with user-friendly error reporting and bug reporting instructions.
    :param e: Exception to report.
    """
    project_url = _get_project_url()
    logger.critical(
        'Oi oi oi päkapikk! '
        'Collider encountered an unhandled error, this is probably a bug within collider.'
    )
    logger.debug(f'{e}\n{traceback.format_exc()}')

    logger.critical(
        f'Please '
        f'{"Rerun collider with the --verbose flag, and " if logger.level != Level.DEBUG.value else ""}'
        f'report this error to {project_url}/issues, thanks!'
    )


def add_app_args(parser: argparse.ArgumentParser) -> None:
    """
    Add application-level command line arguments to the argument parser.
    :param parser: Argument parser to extend.
    """

    parser.add_argument(
        '-v', '--verbose', help='Enable verbose (DEBUG) output.', action='store_true'
    )
    parser.add_argument(
        '--offline',
        help='Disable network access and rely on local cache where possible.',
        action='store_true',
    )


@no_type_check
def add_subcommand_args(parser: argparse.ArgumentParser, context: Context) -> None:
    """
    Discover and register all available subcommand plugins with their argument parsers.
    :param parser: Argument parser to extend with subcommands.
    :param context: Application context.
    """

    subparsers = parser.add_subparsers(title='Subcommands', required=True)

    # Discover and register subcommand plugins.
    available_subcommands = core.discover_plugins(collider.subcommand)
    for name, subcommand_class in available_subcommands.items():
        name: str
        cast(type[collider.subcommand.Interface], subcommand_class)
        parser_cmd = subparsers.add_parser(
            name,
            help=subcommand_class.help(),
            description=subcommand_class.long_help(),
            epilog=subcommand_class.epilog(),
            formatter_class=_RawDescriptionArgumentDefaultsHelpFormatter,
        )
        subcommand_class.register(parser_cmd)
        parser_cmd.set_defaults(
            func=lambda args, cls=subcommand_class: cls(args, context).execute()
        )


def main() -> int:
    """
    Main entry point that handles argument parsing, context setup, and command execution.
    :return: Exit code.
    """

    parser = argparse.ArgumentParser(
        prog=collider.__name__,
        description='A package and dependency manager for Meson projects.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
    )

    add_app_args(parser)
    app_args = parser.parse_known_args()[0]

    # Create context.
    configure_logging(app_args.verbose)
    version = _get_installed_version()

    logger.info(f'{collider.__name__.capitalize()} {version} - {parser.description}')
    context = config.load(offline=app_args.offline)

    add_subcommand_args(parser, context)

    parser.add_argument(
        '-h',
        '--help',
        action='help',
        default=argparse.SUPPRESS,
        help='show this help message and exit',
    )
    args = parser.parse_args()

    try:
        return args.func(args)
    except MesonUnavailableError:
        # A missing or outdated Meson is a user environment problem, not a Collider bug,
        # so report a clean exit code instead of routing it through error_handler.
        return os.EX_UNAVAILABLE
    except ColliderUserError as e:
        # Usage and user-environment errors are logged at the raise site; exit cleanly
        # with the carried code instead of routing them through error_handler.
        return e.exit_code


def entrypoint() -> None:
    """Entry point for the collider CLI."""
    try:
        sys.exit(main())
    except Exception as e:
        error_handler(e)
        sys.exit(1)


if __name__ == '__main__':
    entrypoint()
