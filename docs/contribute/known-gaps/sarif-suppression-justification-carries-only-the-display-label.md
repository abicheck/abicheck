---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# SARIF suppression justification carries only the display label, not the full disposition record

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The cli_scan_baseline.py half is moot because the file was deleted. The sarif.py half is still open: abicheck/sarif.py:963 still interpolates only change.suppression_rule ('suppressed by --suppress rule: ...') and never reads DispositionLedger.rule_for.


ADR-067 D3 says a disposition keeps the rule that made it — rule id, source
file, reason, label, expiry. `Change.suppression_rule` is not that record: it
is `SuppressionOutcome.rule_label()`'s deliberate `label or reason` collapse,
so a waiver stating both publishes one and silently drops the other, along
with the file it lives in and when it lapses — exactly what a reviewer needs
to decide whether the waiver still applies.

`compare --no-baseline` had this in all four of its projections and was fixed
in PR #1188: each suppressed entry now carries the run's own
`DispositionLedger.rule_for` record, and the regression test is parametrized
over `NO_BASELINE_SUPPORTED_FORMATS` so a format added later is held to it.
Two-sided `compare`'s **JSON** was already correct before that
(`reporter.py`'s `_suppressed_change_entry` emits `rule.to_dict()`).

Two readers are still on the display label alone. Both were found by grepping
every `suppression_rule` reader once the audit's own were fixed, and both are
outside the scope PR #1188 was opened for, so they are recorded here rather
than folded into it:

1. **`sarif.py`** (two-sided `compare`) — the suppression's `justification`
   interpolates `change.suppression_rule` only. Since the same run's JSON
   already publishes the full record, this is a *between-formats* split of one
   report: a SARIF consumer sees strictly less than a JSON consumer of the
   identical run.
2. **`cli_scan_baseline.py`** (`scan --against`) — each suppressed
   `findings[]` entry sets `entry["suppression_rule"]` and carries no
   provenance field at all.

Fixing either is the same shape as the audit's fix: route the run's
`DispositionLedger` to that entry builder and read `rule_for(change)`, rather
than re-evaluating the rule set (which can name a different rule than the one
that actually fired, since a finding's fields may have been enriched after the
match). Neither needs a new mechanism — only the existing one wired to one
more builder.

Registered as open residuals on the `report.finding_entry_builder_parity`
bug class in `tests/regressions/manifest.py`'s report sibling
(`tests/regressions/manifest_report.py`), so the next person to touch that
class sees them without re-deriving the grep.
