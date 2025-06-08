# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

"""Wrap repository with Collider write extension (push/delete)."""

from collider.repository.implementation.Wrap import Wrap


class Collider(Wrap):
    """
    WrapDB-compatible remote repository that exposes the Collider write extension.

    Same read behavior as Wrap (search, install). Push and delete use the
    _collider/v1 HTTP endpoints with bearer token auth.
    """
