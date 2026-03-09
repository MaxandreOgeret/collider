# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""URL normalization utilities."""

from __future__ import annotations

import urllib.parse


def normalize_url(url: str) -> str:
    """
    Normalize a URL for comparison: lowercase scheme/host, strip trailing slash.
    :param url: URL to normalize.
    """
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=parsed.path.rstrip('/') or '/',
        )
    )
