# Docs deployment runbook

The documentation site at https://collider.ee serves **versioned** docs plus an unversioned blog.
The CI workflow (`.github/workflows/deploy-docs.yml`) and the server scripts (`deploy-docs.sh`,
`update-site-root.sh`) handle the steady state automatically; this file documents the parts they
cannot encode -- the Caddy config and the one-time cutover.

## Web-root layout (served by Caddy from `WEB_ROOT`, default `/var/www/collider`)

```
/<version>/      e.g. /1.3.0/   one dir per release, built from its git tag
/main/                          the development version (built from the default branch)
/blog/                          unversioned blog, its own self-contained site
/versions.json                  switcher manifest: { "latest": "1.3.0", "versions": ["main","1.3.0"] }
/index.html                     redirect to /<latest>/ (or /main/ until a release exists)
/robots.txt                     Disallow: /main/
/404.html                       copy of the latest release's styled 404
```

`latest` = highest semver subdir. It is recomputed from the filesystem on every deploy, so a `main`
deploy can never advance it.

## How the automation works

- **Push to the default branch** -> builds the dev docs into `/main/` and rebuilds `/blog/`.
- **Push a `X.Y.Z` tag** -> builds that release into `/<X.Y.Z>/` and updates `latest` + the root
  redirect. (Non-semver tags are skipped.) Release deploys do **not** touch `/blog/`.
- Each deploy regenerates `versions.json`, `index.html`, `robots.txt`, and `404.html`.

The version switcher, the dev banner, and the docs sidebar "Blog" link are injected client-side by
`docs/assets/version-select.js`; the blog is built as a separate site (`docs_dir=blog`,
`blog_satellite=true`, `homepage="/"`).

## Caddy configuration (server-managed -- confirm these)

A default `file_server` works, with two requirements:

```caddy
collider.ee {
  root * /var/www/collider
  file_server          # serves directory index.html and ADDS the trailing slash (must not strip it)
  handle_errors {      # required for the styled root 404.html the deploy writes
    rewrite * /404.html
    file_server
  }
}
```

- Trailing slashes must be **added, not stripped** -- clean URLs (`/<ver>/<page>/`) and the
  switcher's `HEAD` probe rely on directory+index serving.
- `versions.json` is fetched with `cache: no-store`; no special cache header is required, but do not
  set a long `max-age` on it or new releases will not appear in the dropdown.

## One-time cutover (do this once, in order)

The existing site is a flat build at the web-root top level. Migrating to the versioned layout needs
a manual cutover because (a) the old flat files are never deleted by the new subdir-scoped rsync, and
(b) the existing `1.3.0` tag predates this system, so pushing it does **not** trigger the new
workflow.

**Order matters: purge -> seed 1.3.0 -> first dev deploy.** Doing a dev deploy first leaves the
public root redirecting to `/main/` (which `robots.txt` disallows) until a release is seeded.

### 1. Back up and purge the old flat site

```bash
cp -a /var/www/collider /var/www/collider.bak
# Remove the old flat top-level content; the new deploy recreates index.html/404.html/robots.txt,
# versions.json, and the per-version + /blog/ dirs.
cd /var/www/collider
rm -rf assets development getting-started guide reference search blog \
       index.html 404.html sitemap.xml objects.inv search.json
```

### 2. Seed `/1.3.0/` from the tag, with the switcher overlaid

The tag lacks the switcher assets and the current `nav.html`, so overlay them before building. Run on
a checkout that has this branch available as `feat/versioned-docs` (or `main` once merged):

```bash
cd /collider-app   # the server clone
git fetch --tags origin
git checkout -f -B __seed refs/tags/1.3.0
# Overlay the current presentation onto the released content:
git checkout origin/main -- docs/assets/version-select.js docs/assets/extra.css overrides/partials/nav.html
rm -rf docs/blog                                   # the blog is unversioned
tmp=$(mktemp -p "$PWD" --suffix=.toml)
sed -e 's#^site_url = .*#site_url = "https://collider.ee/1.3.0/"#' \
    -e 's#^extra_css = .*#&\nextra_javascript = ["assets/version-select.js"]#' \
    zensical.toml > "$tmp"
uv sync --frozen --group docs
uv run --group docs zensical build --clean -f "$tmp"
rm -f "$tmp"
test -f site/index.html
rsync -a --delete site/ /var/www/collider/1.3.0/
bash .ci/update-site-root.sh /var/www/collider main   # promotes 1.3.0 to latest, fixes the root redirect
chgrp -R caddy /var/www/collider && chmod -R g+rX /var/www/collider
```

### 3. Trigger the first dev deploy

Push to the default branch (or run the workflow manually). This creates `/main/` and `/blog/` and
refreshes the root files. After this, `collider.ee/` redirects to `/1.3.0/`, `/main/` carries the
"in-development" banner, and `/blog/` is live.

## Notes

- Future releases (`1.4.0`+) carry this machinery in their tag, so they deploy automatically -- no
  manual seed. Only the pre-existing `1.3.0` tag needs the step above.
- `workflow_dispatch` from an arbitrary branch would create an unlisted `/<branch>/` dir; only push
  the default branch and semver tags.
