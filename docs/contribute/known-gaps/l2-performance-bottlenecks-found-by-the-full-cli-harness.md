---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Parent section for measurement-only perf findings from scripts/check_l2_cli_perf.py (exists). Its subsections hold the actual status; this one only frames them.


Found while building `scripts/check_l2_cli_perf.py`. Recorded here rather than
acted on: that work was explicitly measurement-only, and each of these is a
production behaviour change needing its own design and its own evidence that the
change is worth it. Every number below is a real local measurement (gcc 13.3.0 /
castxml 0.7.0 / clang 18.1.3, 4 CPUs, Linux), reproducible with
`python scripts/check_l2_cli_perf.py --suite extended`.
