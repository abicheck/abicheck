---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# An informational, no-material-change reconciliation outcome gates the build under `--severity-preset strict` (2026-09-12)

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-10-12): CLOSED.** ADR-078 gives these findings a home of their own. `ChangeKindMeta.is_informational` marks a kind whose finding records that nothing a consumer can act on changed. Two shapes qualify: a reconciliation that proves a declaration identical (`declaration_identity_unchanged`, `declaration_coordinates_shifted`), and an aggregate roll-up of findings that are each reported and gated on their own (`public_surface_grew`, `public_surface_shrank`). The flag derives `INFORMATIONAL_KINDS`, a third COMPATIBLE sub-partition beside `ADDITION_KINDS` and `QUALITY_KINDS`. The partition is asserted at import, by `changekind-partition`, and by the tests. Those findings land in `IssueCategory.INFORMATIONAL`, which has no `SeverityConfig` field. Its level is fixed at `info`, so no preset or override gates on it. The findings stay in every report: `summary.informational`, `severity.categories.informational`, a Markdown section, the release count and bucket, and the PR comment, which no longer says "Quality policy violation" for them. `summary.quality_issues` narrows, so the report schema is now 6.0 and the release schema 2.0. Regression test: `tests/test_informational_never_gates.py`. Through the CLI it covers every informational kind under every preset and the legacy scheme, the full `quality_issues`×`addition` level domain, and every COMPATIBLE kind under `strict` as a control. It also covers the whole `SeverityConfig` domain through the typed API, and the catalog validation. Kinds deliberately left in `quality_issues` (hardening improvements, metadata drift, `typedef_version_sentinel`, `abi_surface_explosion`) are listed with the reasons in ADR-078 D2.


Raised as a triage question ("is `quality_issues` the right bucket for a
COMPATIBLE, purely-informational reconciliation outcome, or does the report
shape need an `informational, no action` category?"), it has a prior
question that outranks it: **do any consumers gate or rank on the bucket?**
They do, and that is the real defect.

`declaration_coordinates_shifted` — and now `declaration_identity_unchanged`
— are `COMPATIBLE` but not `ADDITION_KINDS`, so
`report_summary`/`policy.severity.categorize_changes` place them in
`quality_issues`. That is documented, and the partition invariant holds.
But `quality_issues` is one of the four `SeverityConfig` categories, and
`PRESET_STRICT` sets every category to `ERROR`. Measured directly:

```
declaration_coordinates_shifted   bucket=quality  default rc: 0  strict rc: 1
declaration_identity_unchanged    bucket=quality  default rc: 0  strict rc: 1
```

So under `--severity-preset strict` (or a `.abicheck.yml` `severity:` block
setting `quality_issues: error`) a finding whose whole content is "the
reconciliation proves this declaration did not change" produces a non-zero
exit code. `policy.severity.compute_gate_decision` additionally names
`quality_issues` in `blocking_categories`, and
`pr_comment_render.py`'s `_SEVERITY_ICONS` renders that category as
"⛔ Quality policy violation" on the PR comment.

This is not a presentation problem and it is not fixed by a dashboard
ranking differently. Two candidate fixes, neither taken here:

1. **Make the bucket honest.** `quality_issues` is defined as
   `COMPATIBLE_KINDS − ADDITION_KINDS`, i.e. a residue, and the residue now
   holds two populations with opposite meanings: real quality problems
   (`std` symbol leaks) and proofs that nothing happened. A third
   `COMPATIBLE` sub-partition (`INFORMATIONAL_KINDS`, alongside
   `ADDITION_KINDS`/`QUALITY_KINDS`) with no `SeverityConfig` category of
   its own would fix the gate and the report shape together. It is a
   `checker_policy` partition change, a `report/` `compute_*`/`render_*`
   pair change, and a report-schema bump — and `changekind-partition`'s
   AI-readiness gate would need to learn the fourth set.
2. **Exempt these kinds from the gate.** Narrower and worse: it leaves the
   bucket lying about what it holds, and it needs a per-kind exception list
   in exactly the place ADR-049 D8 works to avoid one.

Not attempted in the PR that found this because it changes a public
partition and a report schema — see this file's own precedent that a
partition change is an ADR-scoped decision, not a follow-up patch. The
measurement above is the evidence; the decision is open.
