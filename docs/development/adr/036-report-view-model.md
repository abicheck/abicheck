# ADR-036: Report view-model and canonical report severity

## Status

Accepted (implemented incrementally — see "Rollout").

## Context

abicheck renders a comparison result in many formats (JSON, Markdown, text,
HTML, SARIF, JUnit, PR comment). Historically each renderer independently:

1. re-applied the `--show-only` display filter
   (`apply_show_only(list(result.changes), …)` repeated in `reporter`, `sarif`,
   `junit`, `html_report`), and
2. re-derived how to **bucket** the change set for display.

The bucketing is where the real hazard lay. There are in fact **three different
classification axes** in the codebase, which had been conflated:

| Axis | Question it answers | Home |
|------|---------------------|------|
| **Verdict** — BREAKING / API_BREAK / RISK / COMPATIBLE | "does this break the ABI, and does it gate CI?" | `checker_policy`, `severity`, `DiffResult._effective_verdict_for_change` |
| **Display severity** — HIGH / MEDIUM / LOW | ABICC-style report colouring | `report_classifications` |
| **Origin** — rtti / internal / public | "is a big breaking count just RTTI/internal churn?" | `report_summary` |

Because every renderer re-computed the verdict axis on its own (and the PR
comment used yet another string-keyed bucket dict), the formats could *disagree*
with each other and, worse, with the gate/exit code.

## Decision

1. **Introduce a `ReportModel`** (`abicheck/report_model.py`) — a render-ready
   value object built once from a `DiffResult`: the (optionally `show_only`-
   filtered) change set, the four verdict-axis buckets, and the headline
   summary. Renderers become thin projections over it.

2. **Canonical report severity = the verdict axis**, specifically each finding's
   `result._effective_verdict_for_change(c)` (which already honours PolicyFile
   overrides and ADR-027 A4 per-finding modulation). This is the *same*
   partition that produces the overall verdict and the process exit code, so a
   report can never contradict the gate.

3. **The other two axes are kept as deliberate, separate projections, not
   collapsed.** Display severity (HIGH/MEDIUM/LOW) is an ABICC-compatibility
   presentation; origin (rtti/internal/public) explains breaking-count
   composition. They answer different questions from the verdict axis, so
   merging them would lose information rather than de-duplicate. `ReportModel`
   exposes the verdict axis; the display/origin projections remain available via
   `report_classifications` / `report_summary`.

4. **Cycle-safety:** `report_model` imports only `checker_policy` and
   `report_summary`. The `show_only` filter (`apply_show_only`) stays in
   `reporter`; callers apply it and pass the filtered list into
   `ReportModel.from_result(result, changes=…)`, so `report_model` never imports
   `reporter` and `reporter` depends on it one-directionally.

## Consequences

- Single canonical verdict-axis bucketer: `reporter._classify_changes_by_kind`
  is now a thin wrapper over `ReportModel.classify`. New output formats classify
  via the model instead of re-deriving buckets.
- No behaviour change in this first increment — the Markdown/text path was routed
  through the model and the golden snapshots are byte-identical.
- The verdict axis is documented as canonical, settling the "which severity is
  authoritative?" question for future renderer work.

## Cross-channel invariant (what "unified" actually means)

Investigating the renderers showed they are *not* meant to emit identical
vocabulary, and forcing that would be wrong:

- **Native channels** (JSON, text/Markdown, JUnit) classify on the verdict axis.
- **SARIF** keeps a finer **per-kind** level (`policy_for(kind).severity`) — e.g.
  additions are SARIF `warning`, not `note` (there is a long-standing test for
  this). On the A4/PolicyFile *override* path it maps the overridden verdict via
  `VERDICT_TO_SARIF_LEVEL`.
- **ABICC-compat HTML** uses ABICC's own kind-based HIGH/MEDIUM/LOW so ABICC
  report parsers/diffs keep working — deliberately *not* the verdict axis.

So "unified" means two concrete, testable guarantees, not identical buckets:

1. **Breaking-boundary consistency.** A finding on the breaking side of the gate
   (BREAKING/API_BREAK) reads as error/failure/breaking in *every* native
   channel; one off it never does. (`ReportModel.is_breaking_boundary`.)
2. **Override propagation.** A PolicyFile/A4 effective-verdict override is
   honoured by every native channel (the demoted-change case).

`tests/test_report_integrity.py` asserts both across JSON/SARIF/JUnit and pins
the ABICC-HTML exception as a conscious decision.

## Rollout

- **Increment 1 (done):** add `ReportModel`; route the Markdown/text reporter
  and the shared classifier through it; golden output unchanged.
- **Increment 2 (done):** consolidate the previously-duplicated verdict→vocabulary
  maps (`reporter._VERDICT_TO_SEVERITY_LABEL`, `sarif._VERDICT_TO_SARIF_LEVEL`)
  into `report_model` so they can no longer drift; add the cross-channel
  integrity tests above. No behaviour change (the maps were identical); the tests
  are the new guard.
- **Increment 3 (follow-up):** optionally route `html_report` (native, non-compat
  path) and `pr_comment` model construction through `ReportModel` to delete their
  remaining local bucketing. Pure cleanup; the integrity invariant already holds.

## Alternatives considered

- **Collapse all three axes into one severity enum.** Rejected: display severity
  and origin are genuinely different questions; collapsing loses the
  ABICC-compat colouring and the RTTI/internal-churn explanation.
- **Put `apply_show_only` in `report_model`.** Rejected: it would force a
  `report_model ↔ reporter` import cycle (the readiness gate flags it). Keeping
  the filter in `reporter` and passing filtered changes in keeps the dependency
  one-directional.
