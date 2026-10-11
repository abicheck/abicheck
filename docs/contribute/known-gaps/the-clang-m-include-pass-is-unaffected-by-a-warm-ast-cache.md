---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The `clang -M` include pass is unaffected by a warm AST cache — confirmed at `--repeat 3`

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** header_include_memo.py is a process-wide memo with no persistent/AST-cache tier, so a new run still recomputes the include graph whatever the warm AST cache holds.


Re-confirmed after the cache-lifecycle fix below, now that a multi-repetition run
actually works: across three repetitions of the cold/warm sequence the header
*extraction* count goes 2 → 0 (the AST cache serves it) while `include_pass`
stays at 1 every time. The include graph is recomputed on every run regardless of
cache state, which is the same finding as the per-header scaling above seen from
the cache side.
