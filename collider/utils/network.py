# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Shared network defaults and safe HTTP access for HTTP(S)."""

import urllib.parse
import urllib.request


DEFAULT_NETWORK_TIMEOUT = 30.0

_DEFAULT_PORTS = {'https': 443, 'http': 80}


def _origin(url: str) -> tuple[str, str, int]:
    """Return the comparable (scheme, host, port) origin of a URL."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or '').lower()
    host = (parts.hostname or '').lower()
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme, 0)
    return scheme, host, port


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when a redirect crosses to a different origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        if _origin(req.full_url) == _origin(newurl):
            return new_req
        # A cross-origin redirect must not carry the bearer token to a new host. urllib keeps
        # request headers in req.headers, so stripping there covers the credential.
        new_req.headers = {k: v for k, v in new_req.headers.items() if k.lower() != 'authorization'}
        return new_req


def build_safe_opener() -> urllib.request.OpenerDirector:
    """Build an opener that strips credentials on cross-origin redirects."""
    return urllib.request.build_opener(_AuthStrippingRedirectHandler)


def safe_urlopen(request, *, timeout: float = DEFAULT_NETWORK_TIMEOUT):
    """Open a request through the cross-origin credential-stripping opener."""
    return build_safe_opener().open(request, timeout=timeout)
