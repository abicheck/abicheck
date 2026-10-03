### Performance

- **One type-graph walk per clang AST.** The L5 `type_graph` pass and
  `override_graph`'s override and virtual-method parsers each derived the
  same translation unit's type edges by walking its whole clang AST again,
  and the header-only projection walked its tree three times for the
  type-file index, the edges and the entity-file index. Those derivations
  are now shared per AST within one unit of work
  (`buildsource.type_graph.ast_derived_scope`, a `ScopedCache` pinned to
  the tree), so each tree is indexed once and its edges built once. Answers
  are unchanged, including under `ABICHECK_REFERENCE_MODE`, which disables
  the sharing.
