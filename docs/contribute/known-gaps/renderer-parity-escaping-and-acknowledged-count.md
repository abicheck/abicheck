---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Markdown/review lose symbol characters; three formats omit the acknowledged count

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-28): OPEN.** Found by the renderer-parity matrix; pinned as strict xfail cells there, not fixed.

**Escaping.** Markdown (`markdown`, `md`) and the review digest show a gating symbol containing `<name>` and a backtick as `weird&"*_x`. Two causes, both in prose outside table cells:

- a raw `<name>` in the Breaking Changes description and the review heading is inline HTML, which a CommonMark reader drops. Demangled C++ template names such as `std::vector<int>` hit this.
- a backtick in the symbol closes the code span wrapped around it (the Breaking Changes list, the review Examples line, the bold review heading).

Only table cells are escaped today. Escaping prose and spans changes the rendered bytes for every template-named symbol, so the fix needs its own golden update.

**Acknowledged count.** JUnit, the terminal Audit line and the HTML disposition line omit the acknowledged count that Markdown, the review digest and the one-line summary state. A reader of those three formats cannot see that some gating breaks were acknowledged (ADR-067 D5). The totals they do state agree with the JSON report.

**How it is pinned.** `tests/unit/report/test_renderer_parity_matrix.py` checks every format in the render dispatch table against the JSON report. The cells above are `xfail(strict=True)`, so a fix turns them into failures that ask for the marker to be removed. Registered under `report.finding_entry_builder_parity` in `tests/regressions/manifest_report.py`.
