---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The legacy gate counts a redundant finding the review groups omit (2026-10-23)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-26): OPEN.** Found by the cross-format gating oracle
(`tests/_report_gating_oracle.py`); the renderer half of the class is fixed,
and `result_counts` now states the populations consistently:
`gating_retained` (the review groups' sum) plus `non_gating` equals
`retained`, and the summary names the folded, still-scored finding instead
of reading "5 retained (6 gating, 0 non-gating)". What remains is the
gate-semantics decision below.

Every report's review-group gating count now comes from one rule,
`policy.gate_contribution.resolved_gate_contribution`, the same one the JSON
per-finding `gate_contribution` uses. Two populations still differ on one
shape: a finding the redundancy filter folds out of `changes` but keeps for
the verdict (ledger `application_point: redundancy_filter_scored`).

- Under the legacy verdict scheme the ledger records it `gating`, because the
  verdict really is computed over `kept + verdict_redundant`. `result_counts`
  then read, before `gating_retained`, e.g. on `case45_multi_dim_array_change`,
  "5 retained (6 gating, 0 non-gating)", and the review groups (built from the
  5 retained findings) sum to 5.
- Under any severity configuration, `DispositionLedger.with_gate` relabels the
  same record `non_gating`, because `compute_exit_code` scores
  `result.changes` only.

So the two gate schemes disagree on whether a verdict-redundant finding gates,
and the "retained" and "gating" populations are not nested under the legacy
scheme. Settling it is a gate-semantics decision (should the legacy verdict
stop scoring folded redundants, or should the severity gate start), not a
renderer fix. The oracle bounds the difference by the JSON `redundant_count`
until then.
