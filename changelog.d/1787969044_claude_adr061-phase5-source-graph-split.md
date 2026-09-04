### Changed

- **`abicheck.buildsource.source_graph` is now a registration facade**
  (ADR-061 Phase 5 item 2, the construction/comparison split): 1352 lines down
  to 140. `build_source_graph` and its private folding helpers (the ADR-029
  `BuildEvidence` fold, phase 2; the ADR-030 `SourceAbiSurface` enrichment and
  the ADR-038 C.9 `source_edges` fold, phases 3-4) moved into two new sibling
  modules, `source_graph_build.py` and `source_graph_build_source_abi.py`
  (split in two purely to stay under the new-file line-count cap).
  `diff_source_graph` and `localize_symbol` moved into a third new sibling,
  `source_graph_compare.py`. The shared node/edge-classification predicates
  neither half owns exclusively (`is_public_dependency_node`,
  `is_internal_dependency_node`, `looks_like_system_name`, and their
  `PUBLIC_VISIBILITIES`/`DECL_NODE_KINDS`-family constants — used by
  `crosscheck.py`, `graph_reconcile.py`, `internal_leak.py`, `impact/*`,
  `surface.py`, and others) moved into a fourth sibling, `source_graph_query.py`.
  Every name `source_graph.py` used to define stays importable from there,
  resolved via explicit re-exports, so no call site changed.
