<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`collect-facts` reports whether `phase: auto` actually finished** — a
  new `auto-completed` output (plus an upgraded `::warning::` job
  annotation) tells a caller when `phase: auto` only ran `prepare` for
  `producer: wrapper`/`clang-plugin` (it cannot run your build for you), so
  a workflow can branch on whether the collected pack is really ready
  instead of assuming a single `phase: auto` step always is (G30/ADR-047
  P0.2).
