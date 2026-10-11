---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The compatibility percentage counts findings against symbols

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The formula is unchanged at report_summary.py:235 `(old_symbol_count - breaking_count) / old_symbol_count * 100`. The entry records a deliberate 'not fixed' decision (ABICC heuristic, public contract), documented in the CompatibilityMetrics docstring.


`report_summary.compatibility_metrics` computes
`binary_compatibility_pct = (old_symbol_count - breaking_count) / old_symbol_count * 100`,
where `breaking_count` is a count of **findings** whose effective verdict is
`BREAKING` and `old_symbol_count` is a count of the old side's **exported
symbols**. The two quantities are not the same unit, so the percentage is
not what it reads as. A library with ten exports and two breaking findings
about one symbol reports 80% binary compatibility while nine of its ten
symbols are untouched and one is broken — verified with a synthetic probe.

When `old_symbol_count` is unknown the numerator is divided by
`len(changes)` instead — a third unit — and `affected_pct` degrades to
`0.0`, which is *missing denominator information* rather than a claim that
nothing is affected. A consumer must not reconstruct a symbol count from
either percentage: they are lossy in both the rounding and the unit.

**Not fixed, deliberately.** It is an ABICC-compatible heuristic, it is part
of the JSON report's public data contract, and redesigning it is a change to
the shared semantic owner (`report_summary.py`) with consistent behaviour
required across JSON, HTML and Markdown — not something a reporting-surface
change may do for one format. What the PR-comment work *did* do is refuse to
propagate it: the comment states explicit counts (breaking / needs review /
safe, plus the entity-by-operation rollup in `report/change_summary.py`) and
carries no percentage at all, so it cannot be read as a confidence score or
as "N% of symbols are compatible". The semantics, the limitation and this
prohibition are recorded in `CompatibilityMetrics`' own docstring, next to
the formula.
