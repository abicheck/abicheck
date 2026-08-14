<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`dump --help` and `scan --help` now show a curated common subset**,
  matching the progressive-disclosure pattern `compare --help` already used
  (G21.8 M2): the everyday options lead, and the long tail (toolchain
  overrides, debug-info resolution, per-category severity overrides,
  release-only knobs, …) folds behind the new `dump --help-all` /
  `scan --help-all`. Purely presentational — no option was added, removed,
  or renamed; every option keeps working unqualified either way.
