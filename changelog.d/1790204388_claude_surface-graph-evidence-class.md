### Changed

- The public-surface graph builder (`abicheck/compare/surface_graph.py`) now
  declares an evidence class (`observed` / `resolved_join` / `derived`,
  `abicheck/model/graph_evidence_class.py`) for every edge kind it emits. Its
  symbol → declaration edge, projected from a declaration's own mangled name
  and never matched against the export table, is renamed from `exports` to
  `declares_linker_name` and classed `derived`, leaving `exports` for a future
  observed export-table join. These edges are not persisted, so no snapshot
  schema change.
