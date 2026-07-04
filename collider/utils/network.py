# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Shared network defaults and safe HTTP access for HTTP(S)."""

import ipaddress
import socket
import urllib.parse
import urllib.request

from collider.log import logger


DEFAULT_NETWORK_TIMEOUT = 30.0

_DEFAULT_PORTS = {'https': 443, 'http': 80}


def _origin(url: str) -> tuple[str, str, int]:
    """Return the comparable (scheme, host, port) origin of a URL."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or '').lower()
    host = (parts.hostname or '').lower()
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme, 0)
    return scheme, host, port


def _resolve_host_addresses(host: str) -> list[str]:
    """
    Resolve a host to its IP addresses. Module-level so tests can stub resolution.
    :param host: Hostname or IP literal.
    :return: All resolved address strings.
    """
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_blocked_address(address: str) -> bool:
    """
    Whether an address is in a range that must never be fetched (SSRF guard).
    Private LAN ranges stay allowed because self-hosted repositories are supported.
    :param address: IP address string.
    """
    ip = ipaddress.ip_address(address)
    # Judge an IPv4-mapped IPv6 address by its embedded IPv4 address; the mapped form does
    # not reliably report loopback/link-local on all supported Python versions.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    """
    Whether any resolved address of the host is in a blocked range.
    :param host: Hostname or IP literal.
    :raises ValueError: When the host cannot be resolved (fail closed).
    """
    try:
        addresses = _resolve_host_addresses(host)
    except OSError as exc:
        raise ValueError(f'Cannot resolve host "{host}": {exc}') from exc
    return any(_is_blocked_address(address) for address in addresses)


def assert_fetchable_url(url: str) -> None:
    """
    Reject a URL that untrusted input must never make us fetch (SSRF guard).
    Only http(s) URLs are allowed, and the host must not resolve to a loopback, link-local,
    reserved, multicast or unspecified address. Private LAN ranges stay allowed.
    This validates the resolved host, but the socket layer resolves again on connect, so a
    low-TTL rebinding record remains a residual gap; closing it requires pinning the checked
    address into the connection.
    :param url: URL to validate.
    :raises ValueError: When the scheme is not http(s) or the host is blocked.
    """
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or '').lower()
    if scheme not in ('http', 'https'):
        raise ValueError(f'Unsupported URL scheme "{scheme}" in "{url}".')
    host = parts.hostname
    if not host:
        raise ValueError(f'URL "{url}" has no host.')
    if _host_is_blocked(host):
        raise ValueError(f'Refusing to fetch "{url}": the host resolves to a blocked address.')


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Drop the Authorization header when a redirect crosses to a different origin, and pin
    redirect targets to http(s). With check_redirect_hosts, every redirect hop is also
    re-validated against the blocked address ranges.
    """

    def __init__(self, *, check_redirect_hosts: bool = False):
        """
        :param check_redirect_hosts: Reject redirects whose host resolves to a blocked
            address. Enable when the initial URL comes from untrusted input.
        """
        self.check_redirect_hosts = check_redirect_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        new_scheme, new_host, _ = _origin(newurl)
        # CPython's default handler follows ftp:// and file:// Location targets, which
        # would bypass any scheme allowlist applied to the initial URL.
        if new_scheme not in ('http', 'https'):
            raise ValueError(f'Refusing redirect to non-http(s) URL "{newurl}".')
        # Re-check every hop, including same-host targets: with attacker-controlled DNS a
        # redirect re-resolves the name and can rebind it into a blocked range (SSRF).
        if self.check_redirect_hosts and (not new_host or _host_is_blocked(new_host)):
            raise ValueError(f'Refusing redirect to blocked host in "{newurl}".')
        if _origin(req.full_url) == _origin(newurl):
            return new_req
        # A cross-origin redirect must not carry the bearer token to a new host. urllib keeps
        # request headers in req.headers, so stripping there covers the credential.
        new_req.headers = {k: v for k, v in new_req.headers.items() if k.lower() != 'authorization'}
        return new_req


def build_safe_opener(*, check_redirect_hosts: bool = False) -> urllib.request.OpenerDirector:
    """
    Build an opener that strips credentials on cross-origin redirects and pins redirect
    targets to http(s).
    :param check_redirect_hosts: Also reject redirect hosts resolving to blocked addresses.
    """
    return urllib.request.build_opener(
        _AuthStrippingRedirectHandler(check_redirect_hosts=check_redirect_hosts)
    )


def safe_urlopen(
    request, *, timeout: float = DEFAULT_NETWORK_TIMEOUT, check_redirect_hosts: bool = False
):
    """
    Open a request through the cross-origin credential-stripping opener.
    :param request: URL string or Request to open.
    :param timeout: Socket timeout in seconds.
    :param check_redirect_hosts: Reject redirect hosts resolving to blocked addresses.
        Enable when the URL comes from untrusted input.
    """
    return build_safe_opener(check_redirect_hosts=check_redirect_hosts).open(
        request, timeout=timeout
    )


def may_send_push_token(url: urllib.parse.ParseResult, *, insecure: bool) -> bool:
    """
    Decide whether a bearer-token push may be sent to the given URL.
    Sending the credential over a non-HTTPS transport exposes it in cleartext, so a non-HTTPS push
    is refused unless the caller explicitly opts in with insecure.
    :param url: Parsed push or delete endpoint URL.
    :param insecure: Allow sending the token over a non-HTTPS connection.
    :return: True when the push may proceed, False when it must be refused.
    """
    scheme = url.scheme.lower()
    if scheme == 'https':
        return True
    if insecure:
        logger.warning(
            f'Sending the push token over an insecure "{scheme}" connection because --insecure '
            'was passed.'
        )
        return True
    logger.critical(
        f'Refusing to send the push token over an insecure "{scheme}" connection. Use an https '
        'repository URL, or pass --insecure to send the token in cleartext.'
    )
    return False
