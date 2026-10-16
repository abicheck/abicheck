---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# The structural source-graph diff is role-blind: a `param` → `return` change on the same edge reads as no change

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-10-12): CLOSED.** `diff_source_graph` now matches edges on `relation_key()` and reports a kept dependency's role change as a `changed_edges` entry (ADR-077 D4), or as `role_unknown_edges` when one side carries no role data. Public entries' role changes surface as `public_dependency_role_changed`. See the [archive entry](../archive/known-gaps-closed.md) for the fix and its tests. Original write-up (recorded 2026-10-11 at `2a1ccb0c8`, status OPEN) follows.

`false-clean` — When a public declaration keeps its dependency on a type but changes *how* it uses it (`param` → `return`, `field` → `base`), the structural `added`/`removed` edge diff is empty. A two-sided experiment (external review, 2026-10-11) confirmed a zero diff for `param` → `return`.

## Scope of the gap

- Affects only consumers of the structural diff. ADR-046 D3's role coverage (`source_graph_findings._disagreeing_roles`) is a separate path and is not claimed to be missing here.
- Two role-distinct edges sharing one `(src, dst, kind)` collapse onto one representative, so adding a second role is also invisible.

## Why it is not a one-line switch to `relation_key()`

Callers of `diff_source_graph` were promised the coarse key (ADR-046 D1, "existing callers ... are unaffected"). A role-keyed diff turns one role change into a removed+added pair, which every consumer would read as two dependency changes. The fix needs a *changed-role* result shape, and that shape belongs with the exposure-mode model in [ADR-077](../adr/077-exposure-modes-and-change-sensitive-propagation.md) D4.
