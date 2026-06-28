# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

"""Exit-code contract tests for the collider "serve" command."""

import argparse
import os

from pathlib import Path
from unittest.mock import MagicMock, patch

from collider.Context import Context
from collider.subcommand.Serve import Serve


def _make_context() -> Context:
    return MagicMock(spec=Context)


def _serve_args(
    path: Path | str = 'repo',
    host: str = '127.0.0.1',
    port: int = 8000,
    push_token: str | None = None,
    push_token_env: str = 'COLLIDER_PUSH_TOKEN',
    publish_url: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=Path(path),
        host=host,
        port=port,
        push_token=push_token,
        push_token_env=push_token_env,
        publish_url=publish_url,
    )


def test_serve_ex_usage_non_directory_repo_path(tmp_path: Path) -> None:
    """serve returns EX_USAGE when the repository path exists but is not a directory."""
    repo_path = tmp_path / 'repo-file'
    repo_path.write_text('not a repo', encoding='utf-8')
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    assert cmd.execute() == os.EX_USAGE


def test_serve_ex_ioerr_new_repo_endpoint_validation_fails(tmp_path: Path) -> None:
    """serve returns EX_IOERR when a newly created repo fails endpoint validation."""
    repo_path = tmp_path / 'repo'  # Does not pre-exist, so repo_was_created is True.
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    with patch.object(
        Serve,
        '_validate_new_repo_endpoints',
        side_effect=RuntimeError('validation smoke test failed'),
    ):
        assert cmd.execute() == os.EX_IOERR


def test_serve_ex_ok_starts_and_stops(tmp_path: Path) -> None:
    """serve returns EX_OK when the server starts and serve_forever exits cleanly."""
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    cmd = Serve(_serve_args(path=repo_path), _make_context())

    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch('collider.subcommand.Serve.ThreadingHTTPServer', return_value=server):
        assert cmd.execute() == os.EX_OK

    server.server_close.assert_called_once()
