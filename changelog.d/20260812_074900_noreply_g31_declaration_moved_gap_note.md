### Documentation

- **Documented that `declaration_moved` cannot currently be produced by any
  real evidence producer** — every graph-node-id constructor in
  `abicheck/buildsource/` keys a node's id purely off its mangled or
  qualified name, never a declaring file path, so a declaration that keeps
  its exact name but moves to a different header gets the same node id on
  both sides of a real comparison and never reaches
  `graph_reconcile.reconcile_added_removed` as a removed+added pair. Noted
  in `graph_reconcile.py`'s own module docstring and in the G31 plan doc
  (`docs/contribute/plans/g31-header-graph-default-on-followup.md`), so a
  future example-catalog addition for this outcome isn't attempted again
  without a producer-side fix first.
