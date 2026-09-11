### Changed

- **Internal: five `build_source.source_graph` readers now prefer
  `AbiSnapshot.surface_graph`.** `internal_leak.py`,
  `buildsource/cross_source_checks.py`, `buildsource/evidence_report.py`,
  `evidence_depth.py`, and `cli_graph.py` now read the L5 evidence graph via
  the canonical `AbiSnapshot.surface_graph` field, falling back to the
  legacy `build_source.source_graph` nested field only when `surface_graph`
  is absent (a pre-Phase-3 snapshot never populates it). No user-visible
  behavior change (ADR-063 Phase 10).
