### Added

- **Callback/function-pointer graph (G29 Phase 5 item 4)**: a new
  `abicheck/buildsource/callback_graph.py` pass closes the plugin/event-loop/
  C-API callback blind spot — a public registration function stashing a
  private handler's address into a slot that is later invoked indirectly.
  A new Clang AST pass (`parse_clang_ast_callbacks`) populates
  `DECL_REGISTERS_CALLBACK` (a function's address as a direct argument at a
  function-pointer-typed parameter position, `CONF_HIGH`) and
  `DECL_TAKES_ADDRESS_OF` (the broader assignment/initializer case,
  `CONF_REDUCED`) on the optional L5 source graph; `CALLBACK_MAY_INVOKE` is a
  pure join (no new clang pass) over `call_graph.py`'s already-folded
  function-pointer-kind `DECL_CALLS_DECL` edges against those two, always
  `resolution: "overapprox"`. Driven by
  `inline_graph_fold.fold_callback_graph`, run right after `fold_macro_graph`.
  `FUNCTION_POINTER_HAS_SIGNATURE` is registered vocabulary with no edge
  producer — a real, investigated gap — populated instead as a
  `function_pointer_signature` node-level fact on the callback slot's own
  `source_decl` node, since a signature is a property of one declaration,
  not a relation between two. See `docs/reference/source-graph-schema.md`
  for the schema detail, including the identity design this module's Part A
  join depends on and a documented, inherited join gap for struct-field-typed
  callback slots invoked through member-call syntax.
