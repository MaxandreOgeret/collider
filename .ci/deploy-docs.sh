#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/collider-app}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
WEB_ROOT="${WEB_ROOT:?Set WEB_ROOT to the Caddy document root for the built site.}"
WEB_GROUP="${WEB_GROUP:-caddy}"

export UV_CACHE_DIR=/var/cache/collider
export UV_DATA_DIR=/var/lib/collider/uv
export UV_PYTHON_INSTALL_DIR=/var/lib/collider/uv/python
export UV_LINK_MODE=copy
export HOME=/var/lib/collider

cd "$REPO_DIR"
git -c safe.directory="$REPO_DIR" fetch --prune origin
git -c safe.directory="$REPO_DIR" checkout -B "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH"
git -c safe.directory="$REPO_DIR" reset --hard "origin/$DEPLOY_BRANCH"
mkdir -p "$UV_CACHE_DIR" "$UV_DATA_DIR" "$UV_PYTHON_INSTALL_DIR"
uv sync --group docs
uv run --group docs zensical build --clean

mkdir -p "$WEB_ROOT"
rsync -a --delete site/ "$WEB_ROOT"/
chgrp -R "$WEB_GROUP" "$WEB_ROOT"
chmod -R g+rX "$WEB_ROOT"
