<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`compare` accepts a stored `ProjectSnapshot` package as a release
  operand** — `abicheck compare` now compares a multi-artifact
  `ProjectSnapshot` package directory against a live directory of shared
  libraries, another package, or vice versa (`stored/live`, `live/stored`,
  `stored/stored`), fanning out through the same per-library release engine
  a loose directory of `.so` files already uses. New `--old-variant`/
  `--new-variant` flags select which build variant to compare when a
  package declares more than one (ADR-062 A1.7).
