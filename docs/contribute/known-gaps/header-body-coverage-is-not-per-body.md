---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Header-pass body coverage is one blanket `header_inline_bodies` unit, not per body

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-10-11 at `2a1ccb0c8`): OPEN.** `model/source_graph_coverage.py` gives a header-only pass exactly one covered unit, `HEADER_INLINE_BODIES`, for the body-dependent kinds (`DECL_CALLS_DECL`, `DECL_REFERENCES_DECL`). No fact records *which* function bodies were fully seen on each side.

`false-clean` — Because coverage cannot tell "this inline body was fully parsed on both sides" apart from "some body somewhere was header-visible", comparisons that need body-dependent edges conservatively decline them. So a call or reference change inside an inline function that is visible on both sides goes unreported, even though the evidence to prove it exists.

## Direction (not yet designed)

Record body coverage as a per-declaration fact (one ADR-063 `Fact[T]` per inline/template body: `PRESENT` with the body's digest, or a reason it was not seen). Then decide absence per side: a body edge is `proven_absent` on a side only when that declaration's body is `PRESENT` on that side. An observed edge proves presence on its own side, so a removal needs only the new body `PRESENT`, and an addition only the old one. Owner: [ADR-077](../adr/077-exposure-modes-and-change-sensitive-propagation.md) D5.
