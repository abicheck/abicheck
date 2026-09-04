### Fixed

- **ADR-063 Phase 3 (D5) slice 12's shared-`SourceGraphSummary` assembly no
  longer populates `compare/surface_graph.py`'s own declaration/type/
  header/symbol facts eagerly on every dump.** `_attach_header_graph()`
  runs unconditionally on essentially every real dump (G31 Phase A), and
  nothing in this phase's own wiring reads those facts back yet --
  `PublicSurfaceQuery.resolve()` reads each declaration's `.entity_id`
  directly and delegates domain resolution to `surface.compute_public_
  surface()` unchanged. Paying `build_public_surface_facts`'s per-
  declaration walk on every dump for a feature with no current reader
  regressed the header-graph attach-cost perf gate by 47-96% at realistic
  sizes (caught by CI's PR-vs-base regression check). The `AbiSnapshot.
  surface_graph`/`AbiSnapshot.build_source.source_graph` shared-instance
  property this slice landed is unaffected -- a caller that does need
  those facts can still populate them onto the same shared graph
  explicitly, which is now the required call shape.
