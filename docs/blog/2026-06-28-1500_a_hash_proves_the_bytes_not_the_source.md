---
hide_from_nav: true
hide:
 - toc
---

# A hash proves the bytes, not the source

The [first post](2026-03-09-2328_building_a_package_manager.md) covered how Collider verifies dependencies: `collider lock` records a SHA-256 of each wrap, Collider checks that hash on install, Meson checks the archive hash on extraction, and every locked package is pinned to the `origin` it resolved from. That covers content. The bytes Collider builds match the bytes the lockfile recorded.

It does not cover how Collider decides which bytes to fetch, or how it fetches them. The name that selects a package, the URL that locates it, and the redirects that deliver it all run before there is anything to hash. Collider 1.3.0 fixed two places where untrusted input reached past that line.

## Treating the repository index as untrusted input

A WrapDB-compatible repository serves a `releases.json` index: package names mapped to versions. Collider reads it to know what a repository offers. The names and versions are not only labels; they become paths:

```
subprojects/<name>.wrap
~/.config/collider/cache/wraps/<name>_<version>.wrap
~/.config/collider/cache/archives/<hash>-<filename>
```

A `releases.json` entry named `../../../../etc/cron.d/x` is a relative path that escapes the cache directory, and Collider would use it as a filename. The version string has the same problem. These values come from whatever server the repository URL points at, which for a public mirror or a colleague's host is not a server Collider controls.

Sanitizing at each point of use (before the cache file, before the wrap, before the archive) is whack-a-mole: every new place that turns a name into a path is a new place to forget. `packages_from_releases` is the one function that parses an index into package entries, so the check goes there. It rejects any name or version that is not a safe single path segment before the entry exists. `is_safe_path_segment` rejects empty strings, `.` and `..`, anything containing a path separator or a null byte, and anything that `Path(value).name` does not return unchanged.

Reads and writes react differently to a rejected segment. A write raises: caching or publishing a traversal name is a corruption to stop. A read skips and logs at debug: a malformed entry in someone else's `releases.json` should not crash `collider pkg search`. The sink-level checks at each path-building call stay in place as defense in depth, and they are also where the `source_hash` is validated, since that value arrives through the wrap file rather than the index.

Archive contents get the same treatment. The archive a wrap points at is untrusted input too, and a member named `../../x` is another way out of a directory. When Collider extracts an archive to scan its `meson.build`, it uses Python's tar data filter and the zip module's path sanitization, so a crafted member either lands inside the extraction directory or the scan fails. Build-time extraction into your project is Meson's, guarded by the archive hash.

## Stripping the bearer token on cross-origin redirects

`collider publish` and `collider unpublish` send a bearer token to the repository's write API, namespaced under `_collider/v1/`. Python's default `urllib` opener replays a request's headers across a redirect, and that includes `Authorization`. A repository that answers a push with `302 Location: https://another-host/...` receives the token on the next request. So does a man-in-the-middle on a plaintext hop, since the redirect target is attacker-chosen.

Authenticated calls go through `safe_urlopen`, which installs a redirect handler that drops the header when the redirect changes origin:

```python
def redirect_request(self, req, fp, code, msg, headers, newurl):
    new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
    if new_req is None or _origin(req.full_url) == _origin(newurl):
        return new_req
    new_req.headers = {k: v for k, v in new_req.headers.items()
                       if k.lower() != 'authorization'}
    return new_req
```

`_origin` compares scheme, host, and port, with default ports normalized so `https://h` and `https://h:443` are one origin. A same-origin redirect keeps the token; any other drops it, so the redirected request arrives without credentials and the write endpoint rejects it. That rejection is the intended result.

This does not make the token safe in transit. `collider serve` has no TLS, and a static bearer token is only as private as the channel under it; production deployments must put a TLS-terminating reverse proxy in front. The redirect fix is narrower: it stops Collider from sending the token to a host that was not the one it authenticated to.

## Provenance and signing

A verified hash proves the bytes match what the lockfile recorded. It does not prove who produced them. Origin pinning narrows this: a locked package installs only from the repository URL it was locked against, which defeats the basic dependency-confusion swap of a public package for a private name. But the origin is a URL. It records where a package was fetched from, not who published it. If the repository at that URL is compromised, or the lockfile was generated against a poisoned index, the hashes still agree. When an offline install falls back to the cache, origin is lost: as the first post noted, the cache cannot tell which repository a wrap came from, so only the content hash holds there.

Signing is the next item on the roadmap: a publisher signs the wrap and archive, and a consumer verifies the signature against a key it already trusts. That ties a package to the holder of a trusted signing key, which a hash cannot.

The rest of 1.3.0 (drift detection with `collider check`, prerelease resolution, a stricter publish archive) is in the [release notes](https://github.com/MaxandreOgeret/collider/releases/tag/1.3.0).

---

Any questions? Comments? Feedback?
Join the discussion on [Github](https://github.com/MaxandreOgeret/collider/discussions).

\- MOG
