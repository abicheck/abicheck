# ADR-072: The PR Comment Is a Projection, Not a Second Report

**Date:** 2026-09-15
**Status:** Accepted — implemented. Design record for the reporting-fidelity
decisions behind `abicheck/report/change_summary.py`,
`abicheck/report/evidence_summary.py`, `abicheck/pr_comment*.py`,
`abicheck/cli_pr_comment.py`, `action.yml` and `action/run.sh`. Extends
ADR-061 (responsibility packages), ADR-067 D3 (the `not_evaluated` detector
state) and ADR-070 (the Action layer does not encode CLI semantics). It
changes no compatibility verdict, no gate, and no exit code.

## Context

The sticky PR comment is the surface most maintainers actually read. It was
built as an independent adapter over the JSON report, and had drifted into a
second, lossier report: an audit of one production comparison found that
`confidence`, `evidence_tier`/`evidence_tiers`, `coverage_warnings` and
every per-finding one-sided value present in the JSON reached no comment at
any detail level, `full` included — while 151 focused comment tests passed,
because each asserted a rendering it already produced rather than a fact it
was required to preserve.

The same audit found the default `pr-comment-on: changes` produced *no
comment at all* for a run whose only outcome was a recorded analysis
limitation, and, in sticky mode, deleted the previous comment when it did.

## Decisions

**D1. Renderers project; they never recompute.** A comment renderer reads
compatibility, policy decisions, confidence, gate contributions and exit
codes off the completed report. It does not re-derive them, does not scrape
another format, and does not re-run extraction or comparison
(`tests/test_pr_comment_reporting.py::test_comment_generation_runs_no_
extraction_or_comparison` states this as an executable contract).

**D2. A shared presentation fact gets a `report/`-owned module.** The
entity-by-operation rollup (`report/change_summary.py`) and the evidence
projection (`report/evidence_summary.py`) live in the report package, not in
the comment adapter, so a second renderer that wants either reads one answer
instead of growing its own. Both read canonical classifications
(`ChangeKindMeta` via `report/change_operation.py`); neither classifies by
kind-name prefix or suffix.

**D3. Counting units are carried, not implied.** `ChangeSummary.unit` states
that a row counts *findings* — not declarations, not unique symbols — and
`ChangeSummary.exact` states whether the list it was computed from was
complete. A caller holding a truncated list (a release report's capped
per-library sample) must pass `exact=False` with a reason, and the renderer
discloses it rather than presenting a floor as a total. Totals are computed
before grouping and before any display cap.

**D4. Absence is never defaulted to reassurance.** A report that states no
`confidence` yields no confidence line. This is the one failure a reporting
layer must not have: an invented "high" is indistinguishable from a real
one.

**D5. Detector applicability is three states, not one.** "Did not run"
(`not_evaluated`, ADR-067 D3), "not applicable to these artifacts"
(`enabled: false` — the PE/Mach-O/kABI/SYCL detectors on every ELF run), and
"ran with partial coverage" are reported distinctly, using the existing
status machinery rather than a new vocabulary. Missing PE metadata on an ELF
comparison is never presented as missing *required* coverage.

**D6. Routine inapplicability is split from material limitation
structurally, never by parsing prose.** A real ELF-vs-ELF comparison emits
eight `coverage_warnings`, all of them detector-disablement notes. They are
subtracted by reconstructing each note from the report's own `detectors[]`
ledger through `confidence.detector_disablement_warning` — the single
function that formats it — so producer and consumer share one format string
and no consumer reads the sentence to recover its meaning. A warning the
split cannot account for stays material: the failure direction is "shown
unnecessarily", never "silently dropped".

**D7. `pr-comment-on: changes` posts for a material analysis limitation.**
A clean run whose analysis was narrowed is a reportable outcome, and it is
the one a reviewer is least able to infer from silence. Routine detector
inapplicability (D6) is excluded, so this cannot post noise on every clean
run. This changes *posting eligibility only*; `never` remains
authoritative, and no verdict, gate or exit code moves. Documented in
`docs/use/github-action.md` § "What the default comments on".

**D8. Shortening is section-aware.** The per-section row budget is tightened
before the detail level is downgraded, so a large report keeps per-symbol
rows rather than collapsing to grouped ones. Every shortened body preserves
the headline and gate meaning, head/baseline identity, exact authoritative
counts, evidence and scope limitations, the disposition summary, exact
omitted-row counts, and working navigation. A hard truncation cuts on a line
boundary and closes every open `<details>`.

**D9. A grouped row is a summary, never a dead end.** Every aggregated
family carries a complete member block (itself bounded by the same row
budget, with an exact omitted count), and a family whose findings all concern
one symbol is not aggregated at all — there is nothing to summarise, and the
rollup dropped both findings' values.

**D10. An artifact link is a promise.** "View workflow run" and "Download
full report" are distinct footer links. The Action uploads nothing itself,
so it cannot derive the second; a caller passes its own upload step's
`artifact-url` output via `pr-comment-report-artifact-url`. A link is
therefore never rendered for an upload that failed.

**D11. The compatibility percentage is not propagated.** `report_summary`'s
`binary_compatibility_pct` divides a *finding* count by an *exported symbol*
count. It is not added to the comment in any form, and specifically not as a
confidence score or as "N% of symbols are compatible". Its numerator,
denominator and limitations are documented on `CompatibilityMetrics` itself;
redesigning it belongs in that shared semantic owner, with consistent
cross-format behaviour, never as a comment-only formula. Recorded in
`docs/contribute/known-gaps.md`.

## Consequences

The comment is now information-comparable to the HTML report for the facts a
reviewer acts on, at 2–3 KB for a typical comparison and ≤ 14 KB for a
thousand findings, rendering in well under 30 ms warm. What remains open is
recorded in `known-gaps.md`: a release/bundle report carries no
authoritative entity-by-operation counts, so its rollup is an explicitly
inexact floor until `cli_compare_release.py` emits one.
