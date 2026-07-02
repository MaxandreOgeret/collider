# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Application config paths and loading."""

import os

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from collider.cache import WrapCache
from collider.Context import Context
from collider.errors import ColliderUserError
from collider.file_model.configfile import ConfigFile
from collider.log import logger
from collider.repository.implementation.RepositoryInterface import RepositoryInterface


if TYPE_CHECKING:
    from collider.utils.Registry import _RegistryValue

_COLLIDER_HOME_NAME: Final = 'collider'
_DEFAULT_CONFIGFILE_NAME: Final = 'config.json'


def get_default_collider_home() -> Path:
    """
    Centralize config roots so CLI and tests resolve paths consistently.
    :return: Path to the collider config directory (e.g. ~/.config/collider).
    """
    user_config_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')).resolve()
    return user_config_dir / _COLLIDER_HOME_NAME


def get_config_path() -> Path:
    """
    Keep config path derivation in one place for consistency.
    :return: Path to the config file (e.g. ~/.config/collider/config.json).
    """
    return get_default_collider_home() / _DEFAULT_CONFIGFILE_NAME


@dataclass(frozen=True)
class AppConfig:
    """Resolved runtime configuration used by the CLI."""

    collider_home_path: Path
    # Repos as { name: RepoInterface }
    repositories: dict[str, RepositoryInterface]
    offline: bool


def load(*, offline: bool = False) -> Context:
    """
    Load config and cache into a runtime context with sane defaults.
    :param offline: If True, disable network access for repository loading.
    :return: Context with config, cache, and offline flag.
    """
    config_path = get_config_path()

    if not config_path.exists():
        config_file = ConfigFile()
        # Bootstrap a minimal config on first run so CLI commands can proceed.
        get_default_collider_home().mkdir(parents=True, exist_ok=True)
        config_file.save(config_path)
        logger.debug(f'Created new config file at "{config_path.as_posix()}".')
    else:
        try:
            config_file = ConfigFile.from_path(config_path)
        except ColliderUserError as exc:
            # SystemExit bypasses the entrypoint's ColliderUserError reporting,
            # so surface the message here before terminating.
            logger.critical(str(exc))
            logger.critical('Fix or delete the config file to regenerate defaults.')
            raise SystemExit(exc.exit_code) from exc
        logger.debug(f'Loaded config file from "{config_path.as_posix()}".')

    # Shared cache keeps wrap and archive data reusable across projects.
    cache_root = get_default_collider_home() / 'cache'
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = WrapCache(cache_root)
    logger.debug(f'Initialized cache at "{cache_root.as_posix()}".')

    # Construct the list of repositories from the config file
    repositories: dict[str, RepositoryInterface] = {}
    logger.debug(f'Loading {len(config_file.repositories)} configured repositories.')
    for entry in config_file.repositories:
        try:
            repo_impl_registry_value = cast('_RegistryValue', entry.type)
            repo_cls = cast(RepositoryInterface, repo_impl_registry_value.cls)
            logger.debug(f'Initializing repository "{entry.name}" from "{entry.url}".')
            repositories[entry.name] = repo_cls.from_url(
                entry.url,
                cache_path=cache_root,
                offline=offline,
                publish_url=entry.publish_url,
            )
        except Exception as e:
            logger.warning(f'Failed to load repository "{entry.name}": {e}')
            continue

    appconfig = AppConfig(
        collider_home_path=get_default_collider_home(),
        repositories=repositories,
        offline=offline,
    )
    return Context(config=appconfig, cache=cache, offline=offline)
