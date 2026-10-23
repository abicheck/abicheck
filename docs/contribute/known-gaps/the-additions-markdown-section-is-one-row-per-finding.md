---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The Markdown "Additions" section is one row per finding, so it grows linearly with the symbol count (2026-10-22)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-22): OPEN.** The related review-group section was bounded on
this date; the Additions section was not.

The "Related review groups" section is now folded by namespace root and
transition, with counts, capped lines and a capped family list (see
`abicheck/report/render_markdown_document.py`). It stays about 1.2 KB at 10,
1000 and 5000 symbols (`tests/unit/report/test_review_group_bounds.py`).

The "Additions" section still prints one row per compatible addition, each row
carrying the full demangled name. Measured with a NO_CHANGE report of 1000
`std::` additions: about 300 KB of Additions against about 1.2 KB of review
groups. At 5000 symbols it is about 1.5 MB. Row length is not capped.

The per-finding rows are the record of each addition, so this is not a
suppression problem. A fix has to keep every addition visible while bounding
the prose: folding by family as the review section does, with the full list in
the JSON. That is a presentation change to a section shared by every verdict,
so it needs its own review.
