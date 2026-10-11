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

**Status (recorded 2026-10-11 at `2a1ccb0c8`): OPEN.** `buildsource/header_graph_ast_projection.py` reads exactly four projections (declared type files, `parse_clang_ast_types`, `parse_clang_ast_calls`, declared entity files). `fold_template_graph` (and the callback/override folds) run only from the L5 pass (`buildsource/l5_ast_pass.py:266`), never from the header-only attach path.

`false-clean` — With headers only (the default L2 path), the graph has no `template_instantiation`/`DECL_INSTANTIATES_TEMPLATE`/`TEMPLATE_USES_TYPE` facts, no override and no callback edges. This holds even though the same `clang -ast-dump=json` document contains them. An external review (2026-10-11) ran `template_graph`'s parser on a fixture and got an instantiation that the L2 projection did not keep.

## Constraints on a fix

- Keep the projection's reason for existing: the AST must not stay resident while the graph is built (measured peak on oneDAL, `docs/contribute/measurements/header-graph-attach-memory.md`). Any new family must be another compact, streaming projection of the *same* parse, not a second clang run.
- Coverage must stay honest. A family the header pass does not project must stay `unknown` for header-only graphs, never `proven_absent`.
- Owner for "one parse per TU": [`l4-l2-extraction-convergence.md`](../plans/l4-l2-extraction-convergence.md). Owner for the families: [G29 Phase 5](../plans/g29-impact-analysis-layer.md).
