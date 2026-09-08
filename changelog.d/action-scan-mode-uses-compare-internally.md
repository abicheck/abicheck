<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **The GitHub Action's `mode: scan` now runs `abicheck compare` internally
  for a common subset of invocations** (ADR-068 Phase 4 item 1) — a
  single-artifact `scan --against` run in `format: json` that uses none of
  `new-library-set`/`budget`/`crosscheck`/`risk-rules`/`build-target`.
  Every documented `mode: scan` input keeps working exactly as before;
  this is an internal implementation change with no Action input/output
  change. Invocations using any of the capabilities named above, a
  directory/package `against`, a non-`json` `format`, or scan's one-build
  audit mode (no `against` resolved) still invoke `abicheck scan` directly,
  since `compare` has no equivalent for those yet.
