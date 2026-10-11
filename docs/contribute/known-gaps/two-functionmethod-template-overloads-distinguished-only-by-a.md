---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Two function/method template overloads distinguished only by a `requires`-clause still collide under ADR-063 Phase 2's `EntityId` discriminator (Codex review, PR #943, fresh evidence)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/buildsource/template_graph.py:102-122 still defers CONSTRAINT_DEPENDS_ON_DECL and says requires-constrained overloads with the same signature collide. abicheck/extract/headers/clang has no ConceptSpecializationExpr handling.


`template<class T> requires C1<T> void f();` and the same declaration
constrained by `C2<T>` instead share scope, leaf name, an identical
ordinary parameter list, and an identical `function_template_param_kinds`
result (`("type",)`), so they collapse onto one `EntityId` even after
the parameter-kind/packness/dependent-rename fixes landed for this
discriminator. Confirmed by direct compilation that clang's own
`ConceptSpecializationExpr` node (a `FunctionTemplateDecl` child
appearing right after the constrained `TemplateTypeParmDecl`) carries no
concept name or resolvable reference to one anywhere in its own JSON
subtree -- every key on the node and its
`ImplicitConceptSpecializationDecl` child was inspected directly, and
neither carries anything but synthetic AST ids and dependent-type
placeholders (`type-parameter-0-0`). **Not fixed**: recovering the
concept's actual name would need either a different clang AST-dump
mode/flag or the raw header source text sliced at the node's own
`range` offsets, and `_ClangAstParser` (`abicheck/dumper_clang.py`)
deliberately consumes only an already-parsed JSON tree with no source
text available to it -- a fragile source-offset hack was rejected rather
than attempted. A correct fix needs either threading the header's raw
source text through to this parser (a larger architectural change
outside this discriminator's own scope) or a clang invocation change
that emits a concept reference here, verified against a real build
before landing either way. See
`docs/contribute/plans/one-semantic-pipeline.md`'s Phase 2 section for
the full investigation.
