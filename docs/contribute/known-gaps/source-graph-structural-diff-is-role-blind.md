---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The structural source-graph diff is role-blind: a `param` → `return` change on the same edge reads as no change

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-10-11 at `2a1ccb0c8`): OPEN.** `buildsource/source_graph_compare.py::diff_source_graph` keys edges on `SourceEdge.key()` = `(src, dst, kind)`, not ADR-046 D1's `relation_key()`; its own docstring says "role-level diff granularity is not implemented here".

`false-clean` — When a public declaration keeps its dependency on a type but changes *how* it uses it (`param` → `return`, `field` → `base`), the structural `added`/`removed` edge diff is empty. A two-sided experiment (external review, 2026-10-11) confirmed a zero diff for `param` → `return`.

## Scope of the gap

- Affects only consumers of the structural diff. ADR-046 D3's role coverage (`source_graph_findings._disagreeing_roles`) is a separate path and is not claimed to be missing here.
- Two role-distinct edges sharing one `(src, dst, kind)` collapse onto one representative, so adding a second role is also invisible.

## Why it is not a one-line switch to `relation_key()`

Callers of `diff_source_graph` were promised the coarse key (ADR-046 D1, "existing callers ... are unaffected"). A role-keyed diff turns one role change into a removed+added pair, which every consumer would read as two dependency changes. The fix needs a *changed-role* result shape, and that shape belongs with the exposure-mode model in [ADR-077](../adr/077-exposure-modes-and-change-sensitive-propagation.md) D4.
