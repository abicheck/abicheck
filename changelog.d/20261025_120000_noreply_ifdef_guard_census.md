### Added

- **Exports hidden by any `#ifdef` are counted per guard macro** — a header
  parse sees one preprocessor configuration, so a declaration under
  `#ifdef FOO` with `FOO` undefined left its export undeclared with no stated
  reason (only `sycl::` signatures were recognized). `compare` now reads the
  castxml run's own `-E -dD` output to find the `-H` header branches that
  configuration excluded, attributes each undeclared export to the macro
  whose definition would enable its branch, and adds one coverage line per
  macro: `N exported symbols declared only under #ifdef FOO (not defined;
  pass -D FOO)`. The JSON report carries the same counts, with up to three
  examples per macro, in a new `undeclared_export_census` object (report
  schema 6.7, additive). Informational only: no finding, no exit-code
  change, and nothing emitted when no export is attributed. The scan is
  runtime-only (not stored in snapshots), so a compare of stored snapshots
  keeps the `sycl::` name rule as its fallback. The castxml header-parse
  cache key moved to version 8, so cached parses are re-run once.
