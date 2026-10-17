### Fixed

- **A malformed clang AST no longer reads as a fully collected header graph** —
  when a cached or freshly dumped AST had an `inner` that was not a list (a
  number, string, mapping or null), or a child that was not an object, the
  header-graph readers treated the wrong container as empty. The call, type,
  override, template, callback and virtual-dispatch passes were then all
  stamped as run with no degraded marker, so the graph claimed a clean,
  complete surface it had never read. The shape is now refused before any
  reader runs, on the whole-tree path, the streamed path and the L5 AST fold
  (`validate_ast_shape`, `MalformedClangAstError`, in
  `buildsource/header_graph_ast_projection.py`). The header-graph attach records
  it as a failed parse, so the call graph and the semantic passes carry the
  same explicit degraded markers a real clang parse failure already gets; the
  L5 fold records it as a per-TU diagnostic. A dump still succeeds: only the
  graph's own call/override/template evidence is lost, never the snapshot.
