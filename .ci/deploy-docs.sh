#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/collider-app}"
WEB_ROOT="${WEB_ROOT:?Set WEB_ROOT to the Caddy document root for the built site.}"
WEB_GROUP="${WEB_GROUP:-caddy}"

# Versioned deploy inputs. A release builds a tag into /<version>/; a branch build goes into
# /<branch>/ (the development version). DEV_SUBDIR names that branch dir so the root updater can
# tell it apart from stray dirs. DEPLOY_SUBDIR is the only thing standing between an empty value
# and an rsync that would wipe the whole web root, so it is validated hard below.
DEPLOY_REF="${DEPLOY_REF:-main}"
DEPLOY_SUBDIR="${DEPLOY_SUBDIR:-main}"
DEV_SUBDIR="${DEV_SUBDIR:-main}"
IS_RELEASE="${IS_RELEASE:-false}"

# Refuse to continue unless the target subdir is a branch name or a semver release. An empty or
# unexpected value (slash, "..", spaces) would collapse the rsync destination to "$WEB_ROOT//" and
# --delete the whole site.
if [[ ! "$DEPLOY_SUBDIR" =~ ^([A-Za-z0-9_-]+|[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "Refusing to deploy: invalid DEPLOY_SUBDIR='$DEPLOY_SUBDIR'." >&2
  exit 1
fi

export UV_CACHE_DIR=/var/cache/collider
export UV_DATA_DIR=/var/lib/collider/uv
export UV_PYTHON_INSTALL_DIR=/var/lib/collider/uv/python
export UV_LINK_MODE=copy
export HOME=/var/lib/collider

cd "$REPO_DIR"
git -c safe.directory="$REPO_DIR" fetch --prune --tags origin
# Force the checkout so a dirty working tree never blocks the deploy. Without -f,
# a locally modified file (e.g. uv.lock) aborts checkout before reset --hard can
# clean it, deadlocking every subsequent deploy.
if [[ "$IS_RELEASE" == "true" ]]; then
  # Build the immutable tag on a throwaway branch; the next dev deploy resets it back to main.
  git -c safe.directory="$REPO_DIR" checkout -f -B __deploy "refs/tags/$DEPLOY_REF"
  git -c safe.directory="$REPO_DIR" reset --hard "refs/tags/$DEPLOY_REF"
else
  git -c safe.directory="$REPO_DIR" checkout -f -B "$DEPLOY_REF" "origin/$DEPLOY_REF"
  git -c safe.directory="$REPO_DIR" reset --hard "origin/$DEPLOY_REF"
fi
mkdir -p "$UV_CACHE_DIR" "$UV_DATA_DIR" "$UV_PYTHON_INSTALL_DIR"
# --frozen keeps the committed lock authoritative: never rewrite uv.lock on the
# server, and fail loudly if it drifts from pyproject instead of silently doing so.
uv sync --frozen --group docs

# Temp configs (site_url/docs_dir overrides) must live inside REPO_DIR because zensical resolves
# docs_dir/custom_dir/extra_* relative to the config file's directory. The trap removes both on exit
# (an untracked stray would never block the next forced checkout, but leaks would accumulate). The
# .toml suffix matters: zensical infers the config format from it.
tmp_config=""
blog_config=""
trap 'rm -f "$tmp_config" "$blog_config"' EXIT

# The blog was relocated out of docs/ to keep it unversioned. Tags up to 1.3.0 still carry
# docs/blog; remove it before every docs build so no versioned /<version>/blog/ is ever produced,
# regardless of which ref we build. Harmless no-op for refs that already lack it.
rm -rf docs/blog

# Build the versioned docs first with a per-version site_url so each version canonicalizes to its
# own subpath, then publish into that subdir only: --delete is scoped there, so sibling versions and
# the web-root-level files (versions.json, index.html, robots.txt) are untouched.
tmp_config="$(mktemp -p "$REPO_DIR" --suffix=.toml)"
sed 's#^site_url = .*#site_url = "https://collider.ee/'"$DEPLOY_SUBDIR"'/"#' zensical.toml > "$tmp_config"
uv run --group docs zensical build --clean -f "$tmp_config"
test -f site/index.html   # Never publish an empty build over a live version.
mkdir -p "$WEB_ROOT/$DEPLOY_SUBDIR"
rsync -a --delete site/ "$WEB_ROOT/$DEPLOY_SUBDIR/"

# The blog is unversioned: built from the development branch and served at /blog/ as its own
# self-contained site (docs_dir=blog, its own /blog/assets, no version switcher), so it never
# freezes per release. Only a dev (branch) deploy rebuilds it. The whole block is non-fatal: a blog
# failure must never abort the already-published docs deploy, the root-file refresh, or the perms
# fix below -- it just leaves the previous /blog/ in place.
if [[ "$IS_RELEASE" != "true" && "$DEPLOY_SUBDIR" == "$DEV_SUBDIR" ]]; then
  # Created in the parent scope so the EXIT trap still cleans it up after the subshell.
  blog_config="$(mktemp -p "$REPO_DIR" --suffix=.toml)"
  (
    set -e
    rm -rf blog/assets
    cp -r docs/assets blog/assets
    rm -f blog/assets/version-select.js   # The blog carries no version switcher.
    # docs_dir=blog (own source), no version switcher, homepage="/" so the blog logo returns to the
    # docs root (the root redirect lands on the latest version) instead of looping to the blog, and
    # blog_satellite drops the redundant sidebar title.
    sed -e 's#^site_url = .*#site_url = "https://collider.ee/blog/"#' \
        -e '/^extra_javascript = /d' \
        -e 's#^\[project\]#[project]\ndocs_dir = "blog"#' \
        -e 's#^\[project.extra\]#[project.extra]\nhomepage = "/"\nblog_satellite = true#' \
        zensical.toml > "$blog_config"
    uv run --group docs zensical build --clean -f "$blog_config"
    test -f site/index.html
    mkdir -p "$WEB_ROOT/blog"
    rsync -a --delete site/ "$WEB_ROOT/blog/"
  ) || echo "WARN: blog build failed; keeping the previous /blog/. Docs deploy unaffected." >&2
fi

# Regenerate the manifest, redirect, robots.txt and root 404 from what is now deployed. Runs even if
# the blog block above failed, so the version list and root redirect always reflect the new docs.
bash "$REPO_DIR/.ci/update-site-root.sh" "$WEB_ROOT" "$DEV_SUBDIR"

chgrp -R "$WEB_GROUP" "$WEB_ROOT"
chmod -R g+rX "$WEB_ROOT"
