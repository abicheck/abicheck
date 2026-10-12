---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The default L2 header-graph projection drops template, override and callback facts its own AST already contains

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): CLOSED.** One owner, `buildsource/semantic_fact_folds.py`, now parses (`parse_semantic_facts`, and `SemanticFactStream` for the streamed reader), merges and folds the override, template and callback families for both producers. `HeaderGraphAstProjection.semantic_facts` carries them from the same parse (whole-tree and streamed, and through the projection cache, schema `/4`); `seed_ast_graph` folds them after the type/call edges in the L5 order, and stamps `header_override_graph`/`header_template_graph`/`header_callback_graph`/`header_virtual_dispatch_graph` (degraded with the call pass on a partial parse). The L5 folds in `inline_graph_fold` call the same owner. A graph that never ran a family (one stored before this) is a coverage gap in `diff_source_graph` (`GraphSummaryDiff.coverage_gaps`), not a removal or addition. `tests/test_header_graph_semantic_facts.py` asserts headers-only == L5 family edges on four C++ fixtures, whole-tree and streamed, and that changes in each family surface in the graph diff.

`false-clean` — With headers only (the default L2 path), the graph has no `template_instantiation`/`DECL_INSTANTIATES_TEMPLATE`/`TEMPLATE_USES_TYPE` facts, no override and no callback edges. This holds even though the same `clang -ast-dump=json` document contains them. An external review (2026-10-11) ran `template_graph`'s parser on a fixture and got an instantiation that the L2 projection did not keep.

## How the fix met the constraints

- No second clang run and no resident tree: the families are read from the same parse, inside the projection's existing `ast_derived_scope` (the override facts reuse its type edges). The streamed reader runs the template parse's third walk as one extra read of the document that decodes only elements naming a `FunctionTemplateDecl`; complete specializations are kept as compact records (`template_graph._compact_full_node`), not whole subtrees. Measured on a 114 MB `<functional>` AST: streamed peak 110 -> 128 MiB, whole-tree template parse 0.44 -> 0.28 s.
- Same AST, same facts: the L2 header tree has explicit (materialized) locations while the streamed document and each L5 TU had clang's sticky ones, and the template parser's file attribution differs between the two. The streamed reader now materializes each element with the writer state carried across elements (`LocationMaterializer`), and `clang_ast_run` materializes each L5 tree, so all three read one representation. This also removed a latent stream-vs-whole divergence in the existing type/entity-file projection on STL-heavy headers.
- Coverage stays honest: a hand-built projection (`semantic_facts=None`) stamps nothing, and a graph without the flags is `unknown` for those families.

## Constraints on a fix (as recorded)

- Keep the projection's reason for existing: the AST must not stay resident while the graph is built (measured peak on oneDAL, `docs/contribute/measurements/header-graph-attach-memory.md`). Any new family must be another compact, streaming projection of the *same* parse, not a second clang run.
- Coverage must stay honest. A family the header pass does not project must stay `unknown` for header-only graphs, never `proven_absent`.
- Owner for "one parse per TU": [`l4-l2-extraction-convergence.md`](../plans/l4-l2-extraction-convergence.md). Owner for the families: [G29 Phase 5](../plans/g29-impact-analysis-layer.md).
