#!/usr/bin/env bash
# Regenerate the web-root-level files that tie the per-version docs together: versions.json (the
# switcher manifest), index.html (redirect to the latest release), robots.txt, and a root 404.html.
# It is the single source of truth for the version list and the "latest" alias, derived by scanning
# the deployed subdirectories so a dev deploy can never advance "latest". The development version is
# matched by exact name (the deployed branch) so stray or orphaned dirs are never listed as versions.
set -euo pipefail

WEB_ROOT="${1:?Usage: update-site-root.sh WEB_ROOT DEV_SUBDIR}"
DEV_SUBDIR="${2:?Usage: update-site-root.sh WEB_ROOT DEV_SUBDIR}"
test -d "$WEB_ROOT"

# Discover release versions: immediate subdirectories named like semver that carry a built page.
semver_versions=()
for entry in "$WEB_ROOT"/*/; do
  [[ -d "$entry" ]] || continue
  name="$(basename "$entry")"
  [[ -f "$entry/index.html" ]] || continue
  if [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    semver_versions+=("$name")
  fi
done

# Highest semver release is "latest"; empty until the first release is deployed.
latest=""
if [[ ${#semver_versions[@]} -gt 0 ]]; then
  mapfile -t semver_versions < <(printf '%s\n' "${semver_versions[@]}" | sort -rV)
  latest="${semver_versions[0]}"
fi

# Include the development version only if that exact branch dir is actually deployed.
has_dev=false
if [[ -f "$WEB_ROOT/$DEV_SUBDIR/index.html" ]]; then
  has_dev=true
fi

# Ordered version list for the dropdown: development first (newest/unreleased), then releases desc.
ordered=()
if [[ "$has_dev" == true ]]; then
  ordered+=("$DEV_SUBDIR")
fi
ordered+=("${semver_versions[@]:-}")

# Render the versions array as JSON, skipping any empty placeholder.
versions_json=""
for version in "${ordered[@]}"; do
  [[ -n "$version" ]] || continue
  if [[ -n "$versions_json" ]]; then
    versions_json+=", "
  fi
  versions_json+="\"$version\""
done

if [[ -n "$latest" ]]; then
  latest_json="\"$latest\""
else
  latest_json="null"
fi

printf '{ "latest": %s, "versions": [%s] }\n' "$latest_json" "$versions_json" \
  > "$WEB_ROOT/versions.json"

# Root redirect: to the latest release, or to the development version while no release exists yet.
redirect_target="/$DEV_SUBDIR/"
if [[ -n "$latest" ]]; then
  redirect_target="/$latest/"
fi
cat > "$WEB_ROOT/index.html" <<EOF
<!doctype html>
<meta charset="utf-8">
<link rel="canonical" href="https://collider.ee$redirect_target">
<meta http-equiv="refresh" content="0; url=$redirect_target">
<title>Collider documentation</title>
<script>location.replace('$redirect_target')</script>
<a href="$redirect_target">Continue to the documentation</a>
EOF

# Keep the unreleased development docs out of search results.
cat > "$WEB_ROOT/robots.txt" <<EOF
User-agent: *
Disallow: /$DEV_SUBDIR/
EOF

# Serve a styled 404 for unknown root paths by reusing the latest release's error page.
if [[ -n "$latest" && -f "$WEB_ROOT/$latest/404.html" ]]; then
  cp "$WEB_ROOT/$latest/404.html" "$WEB_ROOT/404.html"
fi

# Maintain a stable /latest/ alias pointing at the newest release so external links (README, etc.)
# never go stale across releases. It serves the same pages as /<latest>/, whose canonical URLs point
# back at the versioned path, so search engines consolidate there. The scan above ignores it (not a
# semver dir, not the dev dir), so it never appears as a version.
if [[ -n "$latest" ]]; then
  ln -sfn "$latest" "$WEB_ROOT/latest"
else
  rm -f "$WEB_ROOT/latest"
fi
