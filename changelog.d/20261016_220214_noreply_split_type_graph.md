### Changed

- **`abicheck/buildsource/type_graph.py` split by responsibility (behavior-preserving)** —
  the 1,947-line module now holds only the edge value type (`TypeEdge`), the
  edge-kind constants, the cross-TU merge (`merge_type_edges`) and the graph
  fold (`augment_graph_with_types`). The rest moved, unchanged, to three
  owners: type-spelling helpers (`_base_type_name`, `_resolve_nested_type_names`,
  …) to `type_graph_names.py`; declaration indexes, name resolution and the
  per-AST scope (`index_declared_type_files`, `index_declared_entity_files`,
  `ast_derived_scope`, `RESOLUTION_*`) to `type_graph_index.py`; and the AST
  walkers that emit the edges (`parse_clang_ast_types`) to
  `type_graph_walk.py`. No compatibility re-export is kept, so import each
  symbol from its new owner. The `RESOLUTION_*` constants and the
  `index_declared_*` helpers come from `type_graph_index`. `CONF_HIGH` and
  `CONF_REDUCED` come from `abicheck.model.graph_facts`. The `type_graph.py`
  debt entry is removed now that the module is under the 800-line cap.
