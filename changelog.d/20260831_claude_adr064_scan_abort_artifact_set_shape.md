### Fixed

- **A `scan --artifact-set` abort report's preserved decision was invisible
  to `aggregate`** (PR review finding, immediately following the two
  preserved-contribution fixes above). `workflows/aggregate/load.py`'s
  scan-abort helpers only looked for a preserved decision at `diff.exit` —
  the single-binary `scan`/`ScanOutcome` abort envelope's own shape. A
  `scan --artifact-set` abort report (`ScanSetResult.to_dict()`) has no
  `diff` key at all: its own top-level `verdict` can equally read
  `"BUDGET_OVERFLOW"` (`_aggregate_scan_set_verdict`: any member overflowing
  makes the whole set report one), but each member's own preserved decision
  nests instead at `per_artifact[i].report.exit` (`ScanArtifactResult.
  to_dict()` wrapping the typed API's `ScanResult.report` envelope). Reading
  only the single-binary shape silently downgraded a real member break
  (exit 4) to the generic abort floor (1) and omitted the preserved
  contract-coverage/analysis-assurance axes entirely. `_scan_abort_exit_
  block` is now `_scan_abort_exit_blocks`, returning every exit-decision
  block a report may carry (the single `diff.exit`, plus every
  `per_artifact[i].report.exit`); `_scan_abort_prior_exit`/`_scan_abort_
  exit_axis` fold `max()` across all of them instead of reading one.
