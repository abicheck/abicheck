### Fixed

- **Internal: the `surface_graph` fallback no longer resurrects excluded L5
  evidence for a depth-limited comparison.** `policy/depth_projection.py`
  deliberately clears `build_source.source_graph` (and stamps an explicit
  "not collected" coverage row) for a `--depth build` (or shallower)
  comparison while retaining `surface_graph` (an L2 fact). The Phase 10
  reader migration's fallback previously read that retained graph anyway,
  which could emit L5-labeled findings and claim source-tier evidence for a
  comparison whose own report said L5 was excluded. `buildsource/
  evidence_report.py` and `evidence_depth.py` now only fall back to
  `surface_graph` when the pack's manifest records no L5 coverage row at
  all. Also switched an `isinstance`-based type narrow to a type-only cast
  in `buildsource/cross_source_checks.py`/`buildsource/evidence_report.py`,
  since `AbiSnapshot.surface_graph`'s declared type is a deliberately
  structural protocol a typed-API caller may implement independently.
