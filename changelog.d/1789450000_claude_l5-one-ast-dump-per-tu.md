### Performance

- **`--depth source` dumps each translation unit once for every L5 graph
  family.** The call, type, override, template, macro-range and callback
  graphs each ran their own `clang -ast-dump=json` over every compile unit,
  with identical arguments. They now read one shared pass
  (`abicheck.buildsource.l5_ast_pass`) that dumps each unit once and applies
  every family's parser to it. On a 34-unit C++ library this more than
  halved a `dump --depth source` run. See
  `docs/contribute/plans/l4-l2-extraction-convergence.md` for measurements
  and the remaining L2/L4/L5 convergence phases.

### Removed

- **The six `Clang*GraphExtractor` classes** (`ClangCallGraphExtractor`,
  `ClangTypeGraphExtractor`, `ClangOverrideGraphExtractor`,
  `ClangTemplateGraphExtractor`, `ClangMacroGraphExtractor`,
  `ClangCallbackGraphExtractor`), the modules `override_graph_extractor` and
  `template_graph_extractor`, and `call_graph.extract_from_args`. Their work
  is done by `abicheck.buildsource.l5_ast_pass.run_ast_passes`; each graph
  family's cross-TU merge is now a public function in its own module
  (`merge_call_edges`, `merge_type_edges`, `merge_override_facts`,
  `merge_template_instantiations`, `merge_decl_ranges`,
  `merge_callback_edges`). `ClangCallGraphExtractor` is no longer exported
  from `abicheck.buildsource`.
