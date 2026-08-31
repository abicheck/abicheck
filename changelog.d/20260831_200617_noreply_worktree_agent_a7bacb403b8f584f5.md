### Changed

- **`buildsource/template_graph.py`'s graph-folding half moved to a new
  sibling module.** `augment_graph_with_templates()` (plus its
  `template_decl_node_id()`/`template_instantiation_node_id()` node-id
  helpers and their EDGE_*/NODE_TEMPLATE_*/`TEMPLATE_GRAPH_PROVENANCE`
  vocabulary) now live in `abicheck.buildsource.template_graph_fold`,
  mirroring the earlier `template_graph_value_decls.py`/
  `template_graph_extractor.py` splits out of the same file (its own
  2000-line hard cap). Closes ADR-061 Phase 5 item 2's last
  `buildsource/source_graph.py` facade import in this module: the moved
  functions' `_decl_node_id`/`_type_node_id`/`_symbol_node_id` lookups (and
  a stale `SourceGraphSummary` type-only import) now come directly from
  `abicheck.model.graph_identity`/`abicheck.model.source_graph` instead of
  through the back-compat facade. Internal-only; these were already
  private/internal buildsource names, not part of the tracked Python API
  surface, so no CLI or `abicheck.service` behavior changes.
