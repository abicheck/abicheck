---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The `clang -M` include-graph pass is per-header and cached only in-process, never across runs

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** abicheck/buildsource/header_include_memo.py now holds a process-wide memo, keyed in header_graph.py:635-650, so release members and sides no longer re-run the same clang -M set. Still open: the memo lives only in-process (never persisted across runs), and nothing batches per-header invocations.


Measured on the header-count axis (one library, N top-level public headers over
one shared `detail/` dependency closure, live/live comparison):

| top-level headers | observed `include_pass` invocations | wall |
|---:|---:|---:|
| 1 | 2 | 1.10 s |
| 8 | 16 | 2.48 s |
| 32 | 64 | 10.40 s |

Two separate things show up here. The invocation count is exactly
`headers × 2 sides` — one `clang -M` subprocess per top-level header per side,
with no batching across headers that share a dependency closure. And the wall
time grows **superlinearly** in header count (8x the headers costs ~9.5x the
time going 1→32), so this is not merely a constant per-header cost.

The separate cache finding: on a warm AST cache the header *extraction* count
drops to zero (the AST cache serves it) while the `include_pass` count stays
unchanged. The include graph is recomputed on every run regardless of cache
state. Combined with the startup cost above, that is why a fully warm small
`dump` measures ~0.93 s against a cold ~0.88 s — within noise, i.e. the cache
hit buys nothing observable at that scale even though it demonstrably happened
(proved by the counters, not the clock).
