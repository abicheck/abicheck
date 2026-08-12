<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`** now validates its input before
  packaging, closing three gaps (Codex review):
  - Requires `baseline-path/manifest.json` to exist — an existing-but-wrong
    directory (empty, or simply not a real baseline-set) previously
    packaged and reported success anyway, with the mistake only surfacing
    much later, opaquely, when `resolve-baseline` couldn't find
    `manifest.json` inside the extracted archive.
  - Rejects any symlink found under `baseline-path` before packaging —
    both `actions/resolve-baseline/run.sh` and the root Action's
    baseline-set fallback reject any symlink found after extraction, so a
    source directory that already contained one previously packaged and
    reported success here anyway, producing an asset neither canonical
    consumer could actually use.
  - The staging directory's location is now verified (via `realpath`) to
    be genuinely outside `baseline-path`, falling back to a plain
    `/tmp`-derived location when the default (`mktemp -d`, which honors
    `$TMPDIR`) collides — `$TMPDIR` set to a path under `baseline-path`
    (or `baseline-path` itself being `$TMPDIR`) was not actually
    guaranteed disjoint, contrary to an earlier version of this fix's own
    claim.
