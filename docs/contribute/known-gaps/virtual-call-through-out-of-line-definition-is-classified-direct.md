---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A virtual call through an out-of-line definition is classified as a direct call

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-25): FIXED.** Originally reported 2026-10-21: `call_graph.py`'s `_ref_is_virtual` decides whether a member call is virtual from the *resolved* callee declaration node. Clang's `referencedMemberDecl` for a call to a method defined out of line (`double Shape::area() const { ... }` in a `.cpp`) points at the definition, which carries no `virtual` flag. The call is then recorded as `call_kind: direct, resolution: exact`, so `virtual_dispatch_graph.py` never materializes its `VIRTUAL_CALL_MAY_DISPATCH_TO` edges and `PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED` cannot fire for that base.

Reproduced with the real producer (`fold_semantic_graphs` over clang 18): the same call on a method defined in-class yields `call_kind: virtual` and a `VIRTUAL_CALL_MAY_DISPATCH_TO` edge; the out-of-line variant yields `direct` and none.

`false-clean` — The call reads as a direct, exact call to a method whose targets can in fact change. The dispatch-set detector stays silent rather than reporting a change it cannot see.

**Fix (2026-10-25):** `_ref_is_virtual` walks the `previousDecl` chain through `member_index` (cycle-guarded; `previousDecl` added to `call_decl_record._MEMBER_INDEX_FIELDS`). Every out-of-line virtual call in a stored call graph changes `call_kind`, so `EXTRACTOR_GENERATION` advanced to 24 (header graph) and `SOURCE_GENERATION` to 10 (L5); an older graph is reported as generation drift. Pinned by `tests/test_call_graph_out_of_line_virtual.py` (hand-built shapes plus six real-clang sibling shapes and an end-to-end L5 dispatch join). Residual: an override redeclared with neither `virtual` nor `override`/`final` is still read as direct (the pre-existing, documented `_ref_is_virtual` false negative).

**Original fix direction:** the definition node carries `previousDecl` back to the in-class declaration, which does carry `virtual`. `_ref_is_virtual` should follow `previousDecl` before deciding. This changes the `call_kind` of every out-of-line virtual call in every call graph, so it has its own compatibility analysis and tests and was not folded into the detector PR. Owner: `buildsource/call_graph.py`.
