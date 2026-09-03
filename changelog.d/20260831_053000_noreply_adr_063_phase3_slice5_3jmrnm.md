### Added

- **ADR-063 Phase 3 (D5), slice 9: `surface_graph.py` `public_entity_ids`
  threading.** `build_surface_graph()`/`compute_surface_metrics()` each
  gain one optional `public_entity_ids: frozenset[EntityId] | None = None`
  parameter. When given, `SurfaceGraph.public_roots()` narrows to
  declarations whose own resolved `EntityId` is a member (instead of
  re-deriving `Visibility.PUBLIC` from the snapshot alone), and every
  *public-only* metric tally (`public_functions`/`public_variables`/
  `exported_symbols`/`undocumented_export_ratio`/`exported_counts`/
  `public_types`/`public_enums`) follows the same resolved set —
  `declared_counts` stays unfiltered in every case, since `exported_counts`
  is measured against it as a denominator. `None` (every call site outside
  `compare()`'s own pipeline) preserves the exact pre-Phase-3 behavior,
  pinned by a direct before/after regression test against the module's
  existing non-trivial fixture.
