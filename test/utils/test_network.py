# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import threading
import urllib.request

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collider.utils.network import _AuthStrippingRedirectHandler, safe_urlopen


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
