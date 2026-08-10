### Fixed

- **`collect --source-abi --source-graph summary` was missing three L5
  graph-fold passes**: this out-of-band collection path had fallen behind
  `inline_graph_fold.fold_semantic_graphs`'s own pass list a third time
  (after two earlier fixes for `fold_template_graph`/`fold_archive_graph`)
  — `fold_override_graph`, `fold_virtual_dispatch_graph`, and
  `fold_macro_graph` were still not called here, so an otherwise-equivalent
  collected pack silently carried no `METHOD_POSSIBLE_OVERRIDE`/
  `VIRTUAL_CALL_MAY_DISPATCH_TO`/`TYPE_HAS_VTABLE`/`MACRO_CONTROLS_DECL`/
  `DECL_USES_MACRO` edges (or their coverage stamps) regardless of what
  `--source-abi` collected. Both paths now fold the same seven passes.
