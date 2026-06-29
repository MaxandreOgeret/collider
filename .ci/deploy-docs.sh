#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/collider-app}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DOCS_DEPLOY_REF="${DOCS_DEPLOY_REF:-origin/$DEPLOY_BRANCH}"
DOCS_DEPLOY_CHECKOUT_MODE="${DOCS_DEPLOY_CHECKOUT_MODE:-branch}"
DOCS_DEPLOY_VERSION="${DOCS_DEPLOY_VERSION:?Set DOCS_DEPLOY_VERSION to the docs version to publish.}"
DOCS_DEPLOY_ALIASES="${DOCS_DEPLOY_ALIASES:-}"
DOCS_STAGE_DIR="${DOCS_STAGE_DIR:-/var/lib/collider/docs-site}"
WEB_ROOT="${WEB_ROOT:?Set WEB_ROOT to the Caddy document root for the built site.}"
WEB_GROUP="${WEB_GROUP:-caddy}"

export UV_CACHE_DIR=/var/cache/collider
export UV_DATA_DIR=/var/lib/collider/uv
export UV_PYTHON_INSTALL_DIR=/var/lib/collider/uv/python
export UV_LINK_MODE=copy
export HOME=/var/lib/collider

cd "$REPO_DIR"
git -c safe.directory="$REPO_DIR" fetch --prune origin
git -c safe.directory="$REPO_DIR" fetch --prune --tags origin
git -C "$REPO_DIR" config user.name "${DOCS_GIT_NAME:-Collider Docs}"
git -C "$REPO_DIR" config user.email "${DOCS_GIT_EMAIL:-docs@collider.ee}"

restore_source_branch() {
    git -c safe.directory="$REPO_DIR" checkout -f -B "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH" >/dev/null 2>&1 || true
    git -c safe.directory="$REPO_DIR" reset --hard "origin/$DEPLOY_BRANCH" >/dev/null 2>&1 || true
}
trap restore_source_branch EXIT

if [ "$DOCS_DEPLOY_CHECKOUT_MODE" = "branch" ]; then
    # Force the checkout so a dirty working tree never blocks the deploy. Without -f,
    # a locally modified file (e.g. uv.lock) aborts checkout before reset --hard can
    # clean it, deadlocking every subsequent deploy.
    git -c safe.directory="$REPO_DIR" checkout -f -B "$DEPLOY_BRANCH" "$DOCS_DEPLOY_REF"
    git -c safe.directory="$REPO_DIR" reset --hard "$DOCS_DEPLOY_REF"
else
    git -c safe.directory="$REPO_DIR" checkout -f "$DOCS_DEPLOY_REF"
    git -c safe.directory="$REPO_DIR" reset --hard "$DOCS_DEPLOY_REF"
fi
mkdir -p "$UV_CACHE_DIR" "$UV_DATA_DIR" "$UV_PYTHON_INSTALL_DIR"
# --frozen keeps the committed lock authoritative: never rewrite uv.lock on the
# server, and fail loudly if it drifts from pyproject instead of silently doing so.
uv sync --frozen --group docs
if [ -n "$DOCS_DEPLOY_ALIASES" ]; then
    # shellcheck disable=SC2086
    set -- $DOCS_DEPLOY_ALIASES
    # mike deploy updates the versions manifest and keeps the other published
    # versions intact while publishing the current build under a new version or
    # alias.
    uv run --group docs mike deploy --update-aliases "$DOCS_DEPLOY_VERSION" "$@"
else
    uv run --group docs mike deploy "$DOCS_DEPLOY_VERSION"
fi

DOCS_SOURCE_DIR="$REPO_DIR"
if [ "$(git -C "$REPO_DIR" branch --show-current)" != "gh-pages" ]; then
    if [ ! -d "$DOCS_STAGE_DIR/.git" ]; then
        git -c safe.directory="$REPO_DIR" worktree add --force "$DOCS_STAGE_DIR" gh-pages
    fi
    git -C "$DOCS_STAGE_DIR" reset --hard gh-pages
    DOCS_SOURCE_DIR="$DOCS_STAGE_DIR"
fi

mkdir -p "$WEB_ROOT"
rsync -a --delete --exclude '.git' "$DOCS_SOURCE_DIR"/ "$WEB_ROOT"/
chgrp -R "$WEB_GROUP" "$WEB_ROOT"
chmod -R g+rX "$WEB_ROOT"
