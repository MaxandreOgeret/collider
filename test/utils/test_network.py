# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import logging
import socket
import threading
import urllib.parse
import urllib.request

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collider.utils import network
from collider.utils.network import (
    _AuthStrippingRedirectHandler,
    assert_fetchable_url,
    is_loopback_host,
    may_send_push_token,
    safe_urlopen,
)


def _make_request(url: str) -> urllib.request.Request:
    request = urllib.request.Request(url, method='GET')
    request.add_header('Authorization', 'Bearer secret')
    return request


def test_redirect_keeps_authorization_same_origin():
    handler = _AuthStrippingRedirectHandler()
    request = _make_request('https://example.com/a')

    new_req = handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.com/b')

    assert new_req is not None
    assert new_req.get_header('Authorization') == 'Bearer secret'


@pytest.mark.parametrize(
    'target',
    [
        'https://evil.example.com/b',  # Different host.
        'http://example.com/b',  # Different scheme.
        'https://example.com:8443/b',  # Different port.
    ],
)
def test_redirect_strips_authorization_cross_origin(target: str):
    handler = _AuthStrippingRedirectHandler()
    request = _make_request('https://example.com/a')

    new_req = handler.redirect_request(request, None, 302, 'Found', {}, target)

    assert new_req is not None
    assert new_req.get_header('Authorization') is None


class _RedirectHandler(BaseHTTPRequestHandler):
    """Test server: '/redirect' bounces to a recording server, '/local' stays put."""

    def __init__(self, *args, target_url: str, recorder: dict, **kwargs):
        self.target_url = target_url
        self.recorder = recorder
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', self.target_url)
            self.end_headers()
            return
        if self.path == '/local':
            self.send_response(302)
            self.send_header('Location', '/final')
            self.end_headers()
            return
        self.recorder['authorization'] = self.headers.get('Authorization')
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        # Silence the default stderr access log during tests.
        return


def _serve(handler_factory):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_safe_urlopen_strips_token_on_cross_origin_redirect():
    recorder: dict = {'authorization': 'unset'}
    target_server, target_thread = _serve(
        partial(_RedirectHandler, target_url='', recorder=recorder)
    )
    target_url = f'http://127.0.0.1:{target_server.server_address[1]}/final'

    redirect_server, redirect_thread = _serve(
        partial(_RedirectHandler, target_url=target_url, recorder=recorder)
    )
    redirect_url = f'http://127.0.0.1:{redirect_server.server_address[1]}/redirect'

    try:
        request = _make_request(redirect_url)
        with safe_urlopen(request, timeout=5) as response:
            assert response.status == 200
        # The recording server lives on a different port, so the token must be dropped.
        assert recorder['authorization'] is None
    finally:
        redirect_server.shutdown()
        target_server.shutdown()
        redirect_thread.join()
        target_thread.join()


def test_safe_urlopen_keeps_token_on_same_origin_redirect():
    recorder: dict = {'authorization': 'unset'}
    server, thread = _serve(partial(_RedirectHandler, target_url='', recorder=recorder))
    base_url = f'http://127.0.0.1:{server.server_address[1]}'

    try:
        request = _make_request(f'{base_url}/local')
        with safe_urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert recorder['authorization'] == 'Bearer secret'
    finally:
        server.shutdown()
        thread.join()


def test_assert_fetchable_url_rejects_non_http_scheme():
    with pytest.raises(ValueError, match='scheme'):
        assert_fetchable_url('ftp://example.com/foo.tar.xz')


@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '0.0.0.0', '::1'])
def test_assert_fetchable_url_rejects_blocked_host(monkeypatch, address: str):
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: [address])

    with pytest.raises(ValueError, match='blocked'):
        assert_fetchable_url('https://archives.example.com/foo.tar.xz')


@pytest.mark.parametrize('address', ['93.184.216.34', '192.168.1.10', '10.0.0.5'])
def test_assert_fetchable_url_allows_public_and_private_hosts(monkeypatch, address: str):
    """Private LAN addresses stay allowed because self-hosted repositories are supported."""
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: [address])

    assert_fetchable_url('https://archives.example.com/foo.tar.xz')


def test_assert_fetchable_url_rejects_unresolvable_host(monkeypatch):
    def _fail(_host):
        raise socket.gaierror('no such host')

    monkeypatch.setattr(network, '_resolve_host_addresses', _fail)

    with pytest.raises(ValueError, match='resolve'):
        assert_fetchable_url('https://archives.example.com/foo.tar.xz')


def test_redirect_rejects_non_http_target():
    handler = _AuthStrippingRedirectHandler()
    request = _make_request('https://example.com/a')

    with pytest.raises(ValueError, match='non-http'):
        handler.redirect_request(request, None, 302, 'Found', {}, 'ftp://example.com/b')


def test_redirect_rejects_blocked_host_when_checking(monkeypatch):
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: ['169.254.169.254'])
    handler = _AuthStrippingRedirectHandler(check_redirect_hosts=True)
    request = _make_request('https://example.com/a')

    with pytest.raises(ValueError, match='blocked'):
        handler.redirect_request(request, None, 302, 'Found', {}, 'https://metadata.internal/x')


def test_redirect_rechecks_same_host_when_checking(monkeypatch):
    """A same-host redirect re-resolves: attacker DNS can rebind the name between hops."""
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: ['127.0.0.1'])
    handler = _AuthStrippingRedirectHandler(check_redirect_hosts=True)
    request = _make_request('https://evil.example.com/a')

    with pytest.raises(ValueError, match='blocked'):
        handler.redirect_request(request, None, 302, 'Found', {}, 'https://evil.example.com/b')


def test_redirect_allows_blocked_host_without_checking(monkeypatch):
    """Trusted callers (user-configured repos) may legitimately redirect to loopback."""
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: ['127.0.0.1'])
    handler = _AuthStrippingRedirectHandler()
    request = _make_request('https://repo.example.com/a')

    new_req = handler.redirect_request(request, None, 302, 'Found', {}, 'https://repo.local/b')

    assert new_req is not None


def test_assert_fetchable_url_rejects_ipv4_mapped_ipv6(monkeypatch):
    """An IPv4-mapped IPv6 address must be judged by its embedded IPv4 address."""
    monkeypatch.setattr(network, '_resolve_host_addresses', lambda _host: ['::ffff:127.0.0.1'])

    with pytest.raises(ValueError, match='blocked'):
        assert_fetchable_url('https://archives.example.com/foo.tar.xz')


def test_may_send_push_token_allows_https():
    url = urllib.parse.urlparse('https://packages.example.com/v2/')

    assert may_send_push_token(url, insecure=False) is True


def test_may_send_push_token_refuses_http_without_insecure(caplog):
    url = urllib.parse.urlparse('http://packages.example.com/v2/')

    with caplog.at_level(logging.CRITICAL, logger='collider'):
        assert may_send_push_token(url, insecure=False) is False

    assert 'Refusing to send the push token' in caplog.text


def test_may_send_push_token_allows_http_with_insecure(caplog):
    url = urllib.parse.urlparse('http://packages.example.com/v2/')

    with caplog.at_level(logging.WARNING, logger='collider'):
        assert may_send_push_token(url, insecure=True) is True

    assert 'insecure' in caplog.text.lower()


@pytest.mark.parametrize(
    'host',
    ['localhost', 'LOCALHOST', ' localhost ', '127.0.0.1', '::1', '[::1]', '::ffff:127.0.0.1'],
)
def test_is_loopback_host_true(host: str):
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    'host',
    ['example.com', '192.168.1.1', 'localhost.', '', '127.1', '0x7f000001'],
)
def test_is_loopback_host_false(host: str):
    assert not is_loopback_host(host)
