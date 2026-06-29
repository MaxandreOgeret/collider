// Version switcher for the documentation site.
//
// The docs deploy as per-version subpaths (/<version>/, /main/) under the domain root, with a
// /versions.json manifest at the root. This script reads the manifest at runtime, injects a
// version dropdown into the header, shows a banner when the visitor is not on the latest release,
// and adds a link to the unversioned blog into the left nav (zensical auto-builds the nav from the
// docs file tree and ignores configured nav, so the blog link is injected here). It is loaded on
// every built version via `extra_javascript` in zensical.toml, and uses only root-absolute URLs so
// it behaves identically regardless of page depth. Every failure is non-fatal: a missing widget is
// always preferable to a broken page.

(function () {
  'use strict';

  function init() {
    try {
      var segments = window.location.pathname.split('/').filter(Boolean);
      var current = segments[0];
      // At the literal root the server-side redirect takes over; nothing to switch.
      if (!current) {
        return;
      }

      // The page path within the current version, kept trailing-slash-accurate so the
      // equivalent page can be reached in another version.
      var subPath = segments.slice(1).join('/');
      if (subPath && window.location.pathname.endsWith('/')) {
        subPath += '/';
      }

      // The blog link is static and does not depend on the manifest, so add it up front.
      renderNavLink();

      fetch('/versions.json', { cache: 'no-store' })
        .then(function (response) {
          return response.ok ? response.json() : null;
        })
        .then(function (manifest) {
          if (!manifest || !Array.isArray(manifest.versions)) {
            return;
          }
          renderSwitcher(manifest, current, subPath);
          renderBanner(manifest, current);
        })
        .catch(function () {
          /* Network or parse failure: leave the page untouched. */
        });
    } catch (error) {
      /* Defensive: never let the switcher break the page. */
    }
  }

  var SEMVER = /^[0-9]+\.[0-9]+\.[0-9]+$/;

  function renderNavLink() {
    // Append a "Blog" entry to the primary (left) nav's top-level list, matching the look of the
    // real nav items. Guarded against double-insertion.
    var list = document.querySelector('.md-nav--primary > .md-nav__list');
    if (!list || list.querySelector('.md-nav__blog-link')) {
      return;
    }
    var span = document.createElement('span');
    span.className = 'md-ellipsis';
    span.textContent = 'Blog';

    var link = document.createElement('a');
    link.className = 'md-nav__link md-nav__blog-link';
    link.href = '/blog/';
    link.appendChild(span);

    var item = document.createElement('li');
    item.className = 'md-nav__item';
    item.appendChild(link);
    list.appendChild(item);
  }

  function renderSwitcher(manifest, current, subPath) {
    var inner = document.querySelector('.md-header__inner');
    if (!inner) {
      return;
    }

    var select = document.createElement('select');
    select.className = 'md-version-select__inner';
    select.setAttribute('aria-label', 'Select documentation version');

    manifest.versions.forEach(function (version) {
      var option = document.createElement('option');
      option.value = version;
      // Versions are shown verbatim: release tags (1.3.0) and the branch name (main).
      option.textContent = version;
      if (version === current) {
        option.selected = true;
      }
      select.appendChild(option);
    });

    select.addEventListener('change', function () {
      var chosen = select.value;
      if (chosen === current) {
        return;
      }
      var versionRoot = '/' + chosen + '/';
      var target = versionRoot + subPath;
      // Try the equivalent page in the chosen version; fall back to its home if absent.
      fetch(target, { method: 'HEAD' })
        .then(function (response) {
          window.location.assign(response.ok ? target : versionRoot);
        })
        .catch(function () {
          window.location.assign(versionRoot);
        });
    });

    var wrapper = document.createElement('div');
    wrapper.className = 'md-version-select';
    wrapper.appendChild(select);

    var source = inner.querySelector('.md-header__source');
    if (source) {
      inner.insertBefore(wrapper, source);
    } else {
      inner.appendChild(wrapper);
    }
  }

  function renderBanner(manifest, current) {
    var latest = manifest.latest;
    var message;
    if (!SEMVER.test(current)) {
      // A non-release version is the development branch (e.g. main).
      message = 'You are viewing the in-development docs (unreleased).';
    } else if (latest && current !== latest) {
      message = 'You are viewing docs for an older release.';
    } else {
      // On the latest release (or no release exists yet): no banner.
      return;
    }

    var banner = document.createElement('div');
    banner.className = 'md-version-banner';

    var text = document.createElement('span');
    text.textContent = message + ' ';
    banner.appendChild(text);

    // Only link to the latest release when one actually exists.
    if (latest) {
      var link = document.createElement('a');
      link.href = '/' + latest + '/';
      link.textContent = 'Go to the latest release (' + latest + ').';
      banner.appendChild(link);
    }

    var main = document.querySelector('.md-main') || document.querySelector('[data-md-component="main"]');
    if (main) {
      main.insertBefore(banner, main.firstChild);
    } else {
      var header = document.querySelector('.md-header');
      if (header && header.parentNode) {
        header.parentNode.insertBefore(banner, header.nextSibling);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
