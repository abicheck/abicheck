### Fixed

- **Internal: corrected the `AbiSnapshot.surface_graph`/`build_source.source_graph`
  read order in the five Phase 10 migrated readers.** A security review found
  that preferring `surface_graph` could silently drop real call-graph/
  dependency edges whenever a `--sources`/`--build-info` embed left
  `build_source.source_graph` a richer, real L3-L5 evidence graph than the
  always-on, header-only-only `surface_graph` — a genuinely-reachable
  internal symbol removal could be misjudged unreachable and suppressed.
  `internal_leak.py`, `buildsource/cross_source_checks.py`,
  `buildsource/evidence_report.py`, `evidence_depth.py`, and `cli_graph.py`
  now prefer `build_source.source_graph`, falling back to `surface_graph`
  only when the former is absent. No change to the intended migration's
  outcome for a pre-Phase-3 snapshot (`surface_graph` absent).
