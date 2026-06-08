# Checking for Drift

`collider check` scans `meson.build` for `dependency()` calls and compares them
against `collider.json`. It reports two classes of issues:

- **Untracked**: a dependency called in `meson.build` that is not recorded in
  `collider.json`. Fix with `collider pkg add <name>`.
- **Stale**: a collider-managed entry in `collider.json` that no longer appears
  anywhere in `meson.build`. Fix by running `collider pkg remove <name>`.

The command exits `0` when clean and non-zero when drift is detected, making it
suitable for CI gates.

## Basic Usage

Run from the project root:

```bash
collider check
```

Or specify a source directory:

```bash
collider check --sourcedir path/to/project
```

## Conditional Dependencies

By default, `collider check` skips `dependency()` calls inside Meson `if`-blocks
because their resolution depends on the build configuration. Pass
`--include-conditional` to treat them as required:

```bash
collider check --include-conditional
```

## Known Limitation

`collider check` assumes the `dependency()` name in `meson.build` matches the
collider package name. Packages where these differ (for example, `abseil-cpp`
provides `absl_strings`) may produce false positives.

## CI Integration

Add `collider check` as a step in CI to prevent dependency drift from going
unnoticed:

```yaml
- name: Check dependency drift
  run: collider check
```
