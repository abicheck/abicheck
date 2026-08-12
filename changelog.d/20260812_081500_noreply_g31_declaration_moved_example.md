### Added

- **`declaration_moved` (G31 Phase B, ADR-048) gained example-catalog
  coverage** — `examples/case196_header_graph_move_reconciled/` demonstrates
  a real, production-reachable path to this reconciliation outcome (a
  declaration whose signature changes, moving its mangled name, in the same
  release its header moves — the qualified-name alias tier still pairs the
  two nodes, and the reconciler correctly classifies the pair as
  `declaration_moved`). The fixture is built by running real
  `SourceEntity`/`BuildEvidence` facts through the actual production fold
  (`source_graph.build_source_graph()`), not hand-assembled graph node ids.
  `graph_reconcile.py`'s own module docstring now documents which move
  shapes are reachable through a real evidence producer today (a compound
  move-plus-identity-changing edit) and which are not yet (a pure move with
  an unchanged signature).
