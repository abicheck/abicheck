---
doc_type: contributor
audience:
  - contributor
summarizes:
  - impact-analysis
depends_on:
  - abicheck/impact/model.py
  - abicheck/impact/engine.py
  - abicheck/buildsource/graph_impact.py
  - abicheck/internal_leak.py
lifecycle: active
generated: false
---

# Detector Impact Contract

A detector that reads the optional [L5 source graph](../reference/source-graph-schema.md)
— today's `source_graph_findings.py`/`internal_leak.py`/`crosscheck.py`, and
every future [G29](plans/g29-impact-analysis-layer.md) Phase 5/6 family
(template instantiation, macro/config, virtual dispatch, callback/
function-pointer, object/link provenance) — has more evidence available to
it than a plain artifact diff does, and more ways to get the *honesty* of
that evidence wrong. This page is the checklist such a detector must satisfy
before its findings are trustworthy, on top of the ordinary new-`ChangeKind`
procedure (`/CLAUDE.md` "Adding a new ChangeKind", and the
[G24 shared checklist](plans/g24-linux-abi-gap-closure.md#shared-checklist-every-new-changekind-in-this-plan)).

## 1. Classification: never fabricate `BREAKING` from graph-only evidence

**The one rule that governs everything in `abicheck/buildsource/`** (see
that directory's `CLAUDE.md`) applies to every impact-layer consumer too:
artifact-backed L0/L1/L2 evidence stays authoritative for a shipped `BREAKING`
verdict. Graph evidence (L5) may *explain, localize, scope, add confidence/
provenance, or correlate* an artifact-proven break — it must never itself
promote a finding to `BREAKING_KINDS`. A graph-only detector's `ChangeKind`
defaults to `API_BREAK_KINDS` (source-level) or `RISK_KINDS` (deployment/
context risk); it only escalates to `BREAKING` when an artifact diff also
proves the break (see `evidence_policy.apply_evidence_policy`'s modulation
model for the mechanism).

This matters most for an over-approximating walk: `VIRTUAL_CALL_MAY_DISPATCH_TO`
(Phase 5 item 3) must stay `overapprox`/`RISK`, never `exact`/`BREAKING`,
however confident the walk otherwise looks — see
[Proof-path preference order](../reference/source-graph-schema.md#proof-path-preference-order-adr-046-d6)
for the `effect_transitions` mechanism that marks a proof as
over-approximated in the first place, and propagate that marking into your
`ChangeKind`'s classification, not just the displayed path text.

## 2. Coverage honesty: report what you didn't check, not just what you found

A detector built on the graph must be able to say "I checked and found
nothing" apart from "I never checked" — the same distinction
[`ReachabilityState`](../learn/graph-coverage.md) draws for reachability.
Concretely:

- Stamp `extractor_passes`/`narrowed_passes`/`degraded_passes` (family grain)
  and, where your detector's precision genuinely varies by edge role, the
  `(kind, role)`-grain [coverage matrix](../reference/source-graph-schema.md#coverage-matrix)
  (`ROLE_COVERAGE_MATRIX`) — an absent edge is never proof of an absent
  dependency when the relevant pass didn't run or ran narrowed.
- An absent edge from a *confirmed-complete* pass is real negative evidence;
  the same absence from a narrowed/degraded pass is not — don't let your
  detector's own confidence label imply otherwise.
- If your evidence source is itself opt-in and typically empty today (like
  `GraphEdge.occurrences` — see [`occurrence_id`](../reference/source-graph-schema.md#relation_key-and-occurrence_id)),
  document that plainly rather than let a reader assume "empty" means
  "checked, found none."

## 3. Suppression safety: never silently withhold a public-reachable break

If your detector's finding can be suppressed, it must go through the same
reachability-aware gate every other graph-derived finding does
([ADR-044](adr/044-reachability-aware-suppression.md)):
`Change.reachability_state`/`public_reachable` set honestly (never
`PROVEN_UNREACHABLE` on `UNKNOWN` evidence), so `suppression.py`'s
`reachability: proven-unreachable-only` gate can do its job. A new walk that
skips this — e.g. by hand-rolling its own reachability check instead of
routing through `post_processing.MarkReachability` or the shared
`TraversalPolicy` machinery — risks a suppression rule hiding a break it was
never actually proven safe to hide.

## 4. Populate the impact shape, don't hand-roll a parallel one

`ImpactAssessment`/`GraphProofPath`/`FindingDecision`
([Unified Impact Assessment](../learn/impact-analysis.md)) are the shared
shape every graph-derived finding's reachability/impact fields flow through.
A new detector should:

- Set the existing `Change` fields (`reachability_state`, `reachability_kind`,
  `reachability_proof_path`, `public_reachable`) the same way today's
  producers do — `impact.engine.assess_change` derives `ImpactAssessment`
  from them automatically; there is no separate object to populate by hand.
- When you have a structured `list[GraphEdge]` path (not just a formatted
  string), call `buildsource.graph_impact.attach_impact_metadata` — never
  hand-build the `impact_proof_path` node/edge-dict shape inline. If you
  have more than one candidate path, run them through
  `select_preferred_graph_path` (or extend it — see
  [Proof-path preference order](../reference/source-graph-schema.md#proof-path-preference-order-adr-046-d6)
  for which tiers it already covers) and pass the runner-ups as
  `alternative_paths` rather than silently dropping them.
- Reuse `TraversalPolicy` for a new graph walk instead of re-deriving an
  inline edge-kind/stop-condition/confidence-floor combination — a new
  walk-specific policy instance is fine; a new ad hoc walk that ignores the
  shape entirely is what this contract exists to prevent.

## 5. What this contract does *not* require (yet)

- **Consumer-proven evidence** (`select_preferred_graph_path`'s tier 1,
  a real `--used-by` consumer graph): doesn't exist until
  [G29 Phase 4](plans/g29-impact-analysis-layer.md#phase-4-consumer-use-case-join).
  A detector can't claim this tier — don't invent a proxy for it.
- **`root_cause_id`/`impact_group_id`** on `ImpactAssessment`: correctly
  computing either needs whole-`DiffResult` context (which findings
  elsewhere reference this one) a single `Change`'s read view can't see —
  see [ADR-052](adr/052-unified-impact-assessment-model.md)'s "Deliberately
  not implemented" section and
  [Phase 6](plans/g29-impact-analysis-layer.md#phase-6-new-detectors-examples-fp-gates)'s
  `RootCauseCorrelator`. Group findings via the existing `caused_by_type`
  field and `--report-mode root-cause` instead of adding a parallel
  correlation mechanism.
- **A new report format restructuring.** Every new field this contract asks
  for is additive to the existing JSON/SARIF/JUnit shapes (mirrors how
  [`--report-mode root-cause` reached JUnit](../learn/impact-analysis.md)
  without restructuring its per-symbol `<testcase>` tree) — don't propose a
  breaking schema change to accommodate a new detector's output.

## Checklist summary

Before merging a new graph-derived detector, alongside the ordinary
new-`ChangeKind` procedure:

- [ ] Classification never escalates to `BREAKING` from graph-only evidence.
- [ ] `extractor_passes`/`narrowed_passes`/`degraded_passes` (and the
      per-role matrix, if applicable) are stamped honestly.
- [ ] `reachability_state`/`public_reachable` are set so suppression can't
      silently hide a real break.
- [ ] Structured proof paths go through `attach_impact_metadata`/
      `select_preferred_graph_path`, not a hand-rolled equivalent.
- [ ] A new FP-rate-gate corpus case if the detector is heuristic
      (mirrors the G24 shared checklist's item 7).
