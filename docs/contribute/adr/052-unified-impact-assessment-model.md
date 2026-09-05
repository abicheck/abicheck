# ADR-052: Unified Impact Assessment Model (G29 Phase 3, slices 1-11)

**Date:** 2026-07-22
**Status:** Accepted — slices 1-11 implemented. Slice 6 (G29 Phase 3
follow-up) closed two items this ADR originally left open: `--format junit`
now renders `--report-mode root-cause` (additive `rootCauseId`/`rootCause`
attributes on each `<failure>`, not a restructured `<testcase>` tree — see
"JUnit root-cause rendering" below), and a stable, `description`-independent
`occurrence_id` now exists on `GraphProofPath` (built on
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md) D1's
`occurrence_id` half, itself implemented after this ADR was first accepted).
Slice 7 (G29 Phase 3 follow-up) closed the remaining item: `root_cause_id`/
`root_cause_display`/`impact_group_id` now exist on `ImpactAssessment`,
computed report-wide and passed into `assess_change` as a plain parameter —
see "Slice 7" below. Slices 8-9 (G29 Phase 3 follow-up) deliver the D2
direction flip as a deliberately *scoped* subset — two producers
(`internal_leak.py`'s two leak-finding builders, Slice 8; `appcompat.py`'s
one consumer-overlay builder, Slice 9) construct `ImpactAssessment` directly
and `assess_change` reuses their evidence fields, each verified safe by its
own pipeline-ordering/purity audit. Slice 10 (G29 Phase 3 follow-up) closes
the `post_processing.MarkReachability` half of D2's remaining scope: a
real measurement (not an assumption) confirmed `assess_change` is called
more than once for the same `Change` within a single `compare` invocation
(`--secondary-format`, e.g. `--format json --secondary-format sarif`,
renders the identical `DiffResult` twice in one process), so
`MarkReachability` now caches `impact_assessment` right after it finalizes
each change's reachability/evidence fields — the *only* place in the
codebase that mutates those fields on an existing `Change`, verified by the
same repo-wide-grep discipline Slice 8 used.
`abicheck/buildsource/source_graph_findings.py`'s ten construction sites
(across its nine per-family helpers) were re-audited in the same slice and
found *not* individually cacheable at construction time — unlike
`internal_leak.py`'s builders, they run **before**
`post_processing.DEFAULT_PIPELINE` (their output is merged into
`checker.compare`'s `changes` as `extra_changes`, ahead of the whole
pipeline), so `MarkReachability` still runs downstream of them and would
make an eagerly-cached assessment stale. They are not left uncovered,
though: `MarkReachability`'s own new caching (above) reaches every one of
these findings too, once each is tagged, closing the practical gap without
needing nine/ten independent construction-site edits. Slice 11 (G29 Phase 3
follow-up) then closed the one remaining question the D2 decision text left
open: `suppression.py`, its third named module, turns out to construct no
`Change` of its own — the actual mutation site is `checker.py`'s
`_filter_suppressed_changes`/`post_processing.ApplySuppression` (already
migrated, Slice 2), so there was never a fourth producer to migrate, only an
imprecise module name in the original decision text — see "Slice 11" below.
See "Slice 8"/"Slice 9"/"Slice 10"/"Slice 11" below for the full scoping
rationale. See
[ADR-049's 2026-09 amendment](049-contract-relevance-and-compatibility-configuration.md)
for a proposed (not yet implemented) migration of `--used-by`-scoped
evidence from a gate-affecting override toward enrichment beside the
whole-comparison result — this ADR's report-level impact-attribution model
is the natural home for that "beside the global result" enrichment block,
should it be picked up.
**Verified:** main@7c59880 on 2026-08-10
**Decision maker:** (pending — recorded per repository convention;
implemented under [G29](../plans/g29-impact-analysis-layer.md) Phase 3's own
"needs its own ADR" gate — [ADR-046](046-source-graph-identity-v2-and-evidence-merge.md)'s
Non-goals section names this explicitly: "A later G29 Phase 3 ADR is where
`--report-mode root-cause` and structured proof-path JSON output land.")

---

## Context

[G29](../plans/g29-impact-analysis-layer.md) Phase 1 (PR #607) added
`Change.reachability_state`, a tri-state refinement of the boolean
`Change.public_reachable`. [ADR-046](046-source-graph-identity-v2-and-evidence-merge.md)
(G29 Phase 2) split graph edge identity and replaced first-writer-wins node/edge
merge, without touching the reporting surface. [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
(G31 Phase B) added structured, machine-walkable proof-path data
(`Change.affected_public_roots`/`impact_proof_path`/`impact_is_direct`) for one
producer (`source_graph_findings._internal_dependency_findings`).

None of that is unified. Per Phase 3's problem statement, `source_graph_findings.py`,
`internal_leak.py`, `post_processing.py`, `suppression.py`, and `appcompat.py`
each independently set overlapping fields on `Change`
(`public_reachable`/`reachability_kind`/`reachability_proof_path`/
`reachability_state`/`affected_public_roots`/`impact_proof_path`/
`impact_is_direct`/`effective_verdict`/`modulation_reason`/`modulation_rule`/
`correlated_change_kind`/`evidence_category`) with no shared object a consumer
can query as one thing.

A second, independently discovered gap made this concrete rather than
aspirational: **`Change.reachability_state` has existed in memory since PR
`#607`, but `reporter.py`/`sarif.py` never serialize it.** A JSON/SARIF
consumer today can see `public_reachable: false` for two changes — one the
graph walk *proved* unreachable, one it never examined at all (`UNKNOWN`,
e.g. because the relevant `extractor_passes` family was narrowed/degraded) —
with no way to tell them apart. That is exactly the "no evidence ≠ proof of
absence" distinction `docs/learn/graph-coverage.md` already documents for
suppression's own `reachability: proven-unreachable-only` gate; it was never
extended to the report output.

## The one rule that does not change

Same authority rule every L3-L5 evidence decision in this codebase already
follows (ADR-028 D3, ADR-044, ADR-046, ADR-048): this ADR adds a read view
and a reporting surface. It does not change which findings are produced,
which findings are suppressed, any `ChangeKind`'s default verdict, or any
existing field's value. `ImpactAssessment` is assembled *from* fields
producers already set; no producer's own logic changes in this slice.

## Decision

### D1. `abicheck/impact/` package — `ImpactAssessment`, `GraphProofPath`,
### `FindingDecision`

New `abicheck/impact/model.py`:

- `ProofStep` — one typed node/edge reference (`step_type`, `label`, `kind`,
  `role`, `confidence`, `node_id`), the dataclass counterpart of one entry in
  `graph_impact.structured_proof_path`'s `list[dict]` shape. `node_id`
  carries a node entry's stable `id` separately from its (possibly
  colliding across nodes) human-readable `label` — see "Follow-up fixes"
  below.
- `GraphProofPath` — `root` (the public entry label, when known), `target`
  (the finding's actually-affected subject — the last node of the
  structured path when one is attached, falling back to `Change.symbol`
  only for a prose-only or absent path; see "Follow-up fixes" below for why
  `symbol` alone is not always correct), `is_direct`, `steps` (a
  `tuple[ProofStep, ...]`, empty when only the human-readable rendering is
  available), `prose` (the existing `reachability_proof_path` string, kept
  verbatim rather than re-derived — there is exactly one producer of that
  string today and duplicating its logic here would be a second, driftable
  implementation).
- `FindingDecision` — `state` (`"kept"` / `"suppressed"`), `reason_code` (from
  `Change.modulation_reason` when a pattern-aware rule fired),
  `verdict_override` (from `Change.effective_verdict` when set —
  deliberately not named "demotion": an override can raise a finding's
  category too, not just lower it; see "Follow-up fixes" below),
  `suppression_rule` (left `None` in this slice — see "Deliberately not
  implemented" below).
- `ImpactAssessment` — `reachability_state`, `public_reachable`,
  `reachability_kind`, `confidence`, `proof_path: GraphProofPath | None`,
  `decision: FindingDecision`, `evidence_category`, `correlated_change_kind`.
  Every field is read from a `Change` attribute that already exists and is
  already independently populated by one of the five producer modules named
  above — this dataclass adds no new signal, only a shared shape to query it
  through.

`abicheck/impact/engine.py`: `assess_change(change, *, suppressed=False) ->
ImpactAssessment` — a pure function, no I/O, no graph traversal of its own.
It only reads attributes already on the `Change` object passed in.
`suppressed` is a caller-supplied flag (the caller already knows whether it
is rendering `DiffResult.changes` or `DiffResult.suppressed_changes`).
`FindingDecision.suppression_rule` is read from `Change.suppression_rule`
unconditionally (not gated on `suppressed`, since the field is never set on
a kept change) — see "Slice 2" below for how that field gets populated.

### D2. Direction: `ImpactAssessment` derives from `Change`, not the reverse

The Phase 3 plan text describes the target end state as the existing
`Change` fields becoming *derived views over* `ImpactAssessment` (producers
populate the unified object; the flat fields become computed from it for
backward compatibility). This slice does **not** do that flip. `Change`'s
own fields remain the source of truth, set by the same five producers
exactly as before; `assess_change` only reads them after the fact. Flipping
the direction — making `post_processing.MarkReachability`,
`source_graph_findings.py`, `internal_leak.py`, `suppression.py`, and
`appcompat.py` all construct one `ImpactAssessment` and derive the flat
fields from it — touches five modules' core control flow (several
performance-sensitive graph walks) for a benefit (avoiding field
duplication) that does not change behavior or output. Given the "shipping
each phase independently, keeping every new signal additive" mitigation this
initiative committed to, that flip is deferred to a later slice under this
same ADR, the same way ADR-046 deferred D4 and its own D1 `occurrence_id`
half: a real, scoped follow-up, not an oversight.

### D3. Reporting surface — `reachability_state` and `impact_assessment`

`reporter.py`'s `_change_to_dict` (used by every `changes[]` entry in the
full JSON report) gains:

- `reachability_state` — always present (the enum's own default is
  `UNKNOWN`, an honest "not evidenced" answer, not an absent key). This is
  the fix for the gap this ADR's Context section describes.
- `impact_assessment` — present only when it carries information beyond
  the all-defaults case (a proof path exists, `reachability_state` is not
  `UNKNOWN`, `public_reachable` is true, `confidence` is not `HIGH`, the
  decision `state` is not `"kept"`, a modulation/verdict-override fired, or
  `correlated_change_kind`/`evidence_category` is set) — matching this
  function's existing convention of only emitting a key when there is
  something to say, rather than padding every one of the (typically
  hundreds of) plain findings with a mostly-empty object.

`impact_assessment` intentionally **duplicates** several already-published
top-level fields (`public_reachable`, `reachability_kind`, the proof path's
prose rendering) inside its own shape. Removing the top-level fields would
be a breaking JSON-schema change (Non-goals, below, rules that out); keeping
both is the accepted cost of offering one object a consumer can query
without stitching six separate keys together — the entire point of
"unified" in this initiative's name.

`_to_json_leaf` (`--report-mode leaf`)'s own `_leaf_entry()` helper builds its
dict independently of `_change_to_dict` rather than routing through it — the
same "smaller summary" reasoning ADR-048 used for excluding JUnit initially
looked like it applied here too. It does not: `_leaf_entry()` already
duplicates the ADR-044 P1 reachability fields (`public_reachable`/
`reachability_kind`/`reachability_proof_path`) for exactly this reason —
root `TYPE_*` changes are the category the layout-reachability walk tags
most often, and leaf mode's `changes[]` union is documented as
backward-compatible with full mode. Omitting `reachability_state`/
`impact_assessment` there would have silently dropped these two fields for
every `TYPE_*` finding under `--report-mode leaf` alone (caught by Codex
review — see "Follow-up fixes" below); `_leaf_entry()` now adds both,
following the same existing duplication pattern. **`junit_report.py` remains
untouched** — that exclusion's rationale (a structured node/edge object is a
poor fit for JUnit's `<properties>` text-value model) is a genuine format
difference, not a "smaller summary" argument, and still holds.

### D4. SARIF surface

`sarif.py` gains `properties.reachabilityState` (always present, same
rationale as D3) and `properties.impactAssessment` (same gating condition).
Kept as a `properties` value, not `codeFlows`/`relatedLocations` — the exact
same reasoning ADR-048 D4 already recorded for `impactProofPath` applies
unchanged here: SARIF's flow/location model is source-file-anchored, and
most L2 header-only graph nodes have no file/line of their own to synthesize
one from.

### D5. Schema version bump

`REPORT_SCHEMA_VERSION` 2.14 → 2.15 (additive: two new optional keys, no
existing key removed or reshaped). `abicheck/schemas/compare_report.schema.json`
gains `reachability_state` (enum, matching `ReachabilityState`'s three
values) and `impact_assessment` (object, matching `ImpactAssessment.to_dict()`'s
shape) on each `changes[]` entry; `scripts/publish_schemas.py` republishes
the synced copy under `docs/reference/schemas/v1/`.

## Follow-up fixes (Codex review)

Seven gaps in the initial slice-1 landing, each caught by automated review on
the same PR and fixed before merge:

- **`has_signal()` missed three of `ImpactAssessment`'s own non-default
  states.** The initial gate checked `proof_path`/`reachability_state`/
  `public_reachable`/`decision.reason_code`/`decision.verdict_override`
  (then still named `demotion`) /`correlated_change_kind`/
  `evidence_category`, but not `confidence != HIGH` or `decision.state !=
  "kept"`. A finding whose *only* non-default field was a reduced
  confidence (e.g. the vtable/RTTI layout findings in
  `diff_elf_layout.py`, which set `Confidence.MEDIUM` with no
  reachability/proof metadata) or a plain suppressed decision with no other
  metadata would silently never get an `impact_assessment` at all — the one
  object meant to carry exactly that signal. Fixed by adding both checks;
  `tests/test_impact_model.py`'s `test_non_high_confidence_has_signal`/
  `test_suppressed_state_has_signal` are the regression tests.
- **`ProofStep.from_dict` dropped the node `id`.** `graph_impact.structured_proof_path`
  emits a stable `id` per node distinct from its human-readable `label` (two
  different internal declarations can share a label). The initial
  conversion used `id` only as a `label` fallback and discarded it
  otherwise, so `impact_assessment.proof_path.steps` could not disambiguate
  two same-label nodes or let a consumer walk back to the graph without
  also reading the old top-level `impact_proof_path` field — defeating the
  "single object" point of this slice. Fixed by adding `ProofStep.node_id`,
  populated from the raw `id` and re-emitted in `to_dict()` as `"id"`.
- **`GraphProofPath.target` used `Change.symbol` even when a structured
  path pointed elsewhere.** `source_graph_findings._internal_dependency_findings`
  (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`) sets `Change.symbol` to the
  *public entry* label the walk started from — identical to
  `affected_public_roots[0]` — not the internal declaration/type it
  reached. Using `symbol` as `target` made `target == root` for every such
  finding, pointing a JSON/SARIF consumer at the API entry instead of the
  actually-affected internal entity. Fixed by deriving `target` from the
  last node of the structured path when one is present, falling back to
  `symbol` only for a prose-only or absent path (`engine._proof_path_target`).
- **`FindingDecision.demotion` mislabeled escalations.** `Change.effective_verdict`
  (ADR-025 A4/D4.1) can *raise* a finding's category, not just lower it —
  e.g. `STDLIB_IMPLEMENTATION_CHANGED` promoted to `BREAKING` once layout
  evidence proves public `std::` embedding. Serializing that as
  `"demotion": "BREAKING"` contradicts the finding's own severity and misleads
  a consumer keying off `decision`. Renamed the field (and JSON/SARIF key) to
  `verdict_override` — a neutral name that carries `effective_verdict`'s
  value regardless of direction — before this slice reached any release, so
  no compatibility shim was needed.
- **`_leaf_entry()` (`--report-mode leaf`) omitted both new fields for root
  `TYPE_*` changes.** D3 above only updated `_change_to_dict`; `_leaf_entry()`
  builds its own dict for root type changes rather than routing through it,
  so leaf mode's `leaf_changes[]` (and the backward-compatible `changes[]`
  union) silently dropped `reachability_state`/`impact_assessment` for
  exactly the finding category (`TYPE_SIZE_CHANGED` et al.) the
  layout-reachability walk tags most often — the same category `_leaf_entry()`
  already special-cases to keep the *older* ADR-044 P1 reachability fields in
  sync with full mode. Fixed by adding the same two fields there, following
  that existing precedent — see the D3 update above for why this reverses
  the original "not touched" framing.
- **`_add_suppression()`'s `suppressed_changes` list never called
  `assess_change(suppressed=True)` at all.** The `suppressed` parameter
  existed and was tested directly, but no production call site ever passed
  it — `_add_suppression` still emitted `kind`/`symbol`/`description` only,
  so `decision.state: "suppressed"` was advertised (in this ADR's own D1
  text and in `docs/learn/impact-analysis.md`) but unreachable from any
  real report. Fixed by routing each suppressed change through
  `assess_change(c, suppressed=True)` (new `reporter._suppressed_change_entry`)
  and adding `reachability_state`/`impact_assessment` to each
  `suppressed_changes[]` entry — `impact_assessment` is now unconditionally
  present there (a suppressed decision is never the default `"kept"` state,
  so `has_signal()` always fires), which is the intended outcome, not a
  regression of D3's "only when it carries signal" gate for the main
  `changes[]` list.
- **Missing-contract synthetic entries had no `reachability_state` at all.**
  A `--used-by`/`--required-symbol(s)` run whose only gated issue is a
  required symbol/version absent from the new library has no backing
  `Change` — `cli_compare_fold._fold_scoped_compat_into_text`'s
  `missing_labels` loop (JSON) and `sarif._missing_contract_result` (SARIF)
  each hand-build a synthetic entry instead of routing through
  `_change_to_dict`/`assess_change`. (The neighboring `scoped_only` loop in
  the same JSON function already routes real, graph-backed `Change` objects
  like `PE_ORDINAL_RETARGETED` through `_change_to_dict`, so those already
  picked up `reachability_state` for free — only the no-backing-`Change`
  case was missing it.) Since D3/D4 both commit to `reachability_state`
  being "always present", omitting it here broke that promise for exactly
  the scoped-gate-failure shape most likely to appear in a failing CI run.
  Fixed by adding `"reachability_state": ReachabilityState.UNKNOWN.value`
  (JSON) / `"reachabilityState": ReachabilityState.UNKNOWN.value` (SARIF
  `properties`) to both synthetic entries — `UNKNOWN` because a missing
  symbol/version is a hard absence, not a reachability question, so there is
  no stronger claim to make. No `impact_assessment`/`impactAssessment` is
  added (there is no signal beyond the default to report).

## Slice 2 — `FindingDecision.suppression_rule`

Landed in a follow-up commit on the same PR, closing the one slice-1 gap that
did not need a new ADR decision (only new data on an existing, already-public
field) — `SuppressionOutcome` gained a fourth field rather than reusing an
existing one:

- **`suppression.SuppressionOutcome.matched_rule: Suppression | None`** —
  the rule that actually suppressed a change, when `suppressed` is `True`.
  Before this, `SuppressionList.evaluate`'s success branch returned
  `SuppressionOutcome(suppressed=True)` with no record of *which* rule
  matched — `withheld_rule`/`withheld_unknown_rule` only ever covered the
  two *refused*-match diagnostics (ADR-044 D4), never the ordinary
  successful-suppress case.
- **`Change.suppression_rule: str | None`** — a new, additive `Change`
  field (matching the precedent every other G29/ADR-048 field on `Change`
  already set: default `None`, no existing caller affected). Set to
  `matched_rule.label or matched_rule.reason` (both are optional/free-form
  on a `Suppression` rule, so this can still end up `None`) at the three
  call sites that move a change into `DiffResult.suppressed_changes`:
  `checker._filter_suppressed_changes`, `checker._filter_pattern_synthetic`,
  `post_processing.ApplySuppression.run`. **Not** the two call sites in
  `appcompat.py`/`cli_compare_helpers.py` — those discard a suppressed
  consumer/runtime overlay `Change` outright (never append it anywhere), so
  there is no `Change` object left for the label to matter to.
- `engine.assess_change` reads `Change.suppression_rule` into
  `FindingDecision.suppression_rule` unconditionally (see D1 above) —
  `reporter._suppressed_change_entry` (Slice 1's suppression-audit-trail
  fix) picks it up with no further wiring, since it already calls
  `assess_change(c, suppressed=True)` for every entry in
  `suppressed_changes[]`.

`post_processing.py` was already at the AI-readiness 2000-line hard cap
(same constraint D6's implementation in ADR-046 hit). A Codex-review
follow-up caught that the initial landing missed `_merge_findings_respecting_suppression`
— the shared helper `DetectCppPatterns`/`DetectTemplatePatterns`/
`DetectNamespacePatterns` route through for their own late-built findings,
a second real call site beyond `ApplySuppression.run` where a change moves
into `ctx.suppressed` — so a late-detector finding a rule actually
suppressed (not just withheld) still had no `suppression_rule` stamped.
Fixing both call sites while staying at the 2000-line cap needed one more
round: the label-or-reason selection moved into a new
`SuppressionOutcome.rule_label()` method (`suppression.py`, not
line-constrained), so each of the two `post_processing.py` call sites
(plus `checker.py`'s two, for the same reason) shrank to a single
`c.suppression_rule = outcome.rule_label()` line instead of duplicating the
fallback logic inline three or five times over.

## Slice 3 — `--report-mode root-cause`

Landed in a follow-up commit on the same PR — the first slice of the plan's
root-cause grouping, deliberately scoped to JSON only:

- **`reporter._to_json_root_cause`** groups `result.changes` (after
  `--show-only` filtering) by `Change.caused_by_type`, falling back to the
  change's own `symbol` for an ungrouped, singleton finding — reusing the
  field `diff_filtering.py`'s redundancy collapse and
  `internal_leak.py`'s call-graph-leak overlay (`_build_call_graph_leak_change`)
  already set, rather than requiring new producer wiring. Each group gets a
  `root_cause_id` (a stable hash of the grouping key — **not** the eventual
  `RootCauseCorrelator`'s own identifier scheme), a `root`, a
  `finding_count`, and `findings` (the same `_change_to_dict()` dicts also
  present in the flat `changes` array, which root-cause mode still emits in
  full — every other report mode provides `changes` for backward
  compatibility, `--report-mode leaf` included, so root-cause mode does
  too rather than breaking that contract).
- **`--report-mode root-cause`** added to the CLI's `click.Choice`.
  Initially **JSON-only** (Slice 4 below adds markdown/text); `sarif.py`/
  `junit_report.py` still do not gain a matching branch, so `--format sarif`/
  `junit` render as `full` — the same precedent `--report-mode leaf` already
  set for those two formats (neither module's rendering function even
  accepts a `report_mode` parameter today).
- `REPORT_SCHEMA_VERSION` 2.15 → 2.16 (two new additive, root-cause-mode-only
  top-level keys: `root_causes`, `root_cause_count`).

**Follow-up fixes (Codex review), same PR:**

- The `caused_by_type` → `symbol` fallback originally collapsed every
  finding with neither set (empty `symbol`, no `caused_by_type` — e.g.
  `SOURCE_FACT_COVERAGE_INCOMPLETE`/`SOURCE_BINARY_PROVENANCE_MISMATCH`)
  onto one shared `root: ""` group. Fixed with a three-tier key
  (`_root_cause_key_and_display` in `reporter.py`): `caused_by_type`, else
  non-empty `symbol`, else a unique per-finding key — so uncorrelated
  anonymous findings stay singleton.
- The `symbol` tier above then over-corrected the other way: two
  *independent* findings sharing a non-empty symbol with no
  `caused_by_type` at all (e.g. a `func_return_changed` and a
  `func_params_changed` finding both on `foo`) grouped together purely
  because the key matched, contradicting the same "only `caused_by_type`
  correlates" contract. Fixed by computing `referenced_causes` — the set
  of `caused_by_type` values actually present across the batch — first;
  a bare symbol is only used as a *grouping* key when some other
  finding's `caused_by_type` names it, otherwise it keys uniquely (via
  finding id) while still showing the symbol as its own singleton
  group's display root.
- The `--used-by`/`--required-symbol` scoped-gate fold-in
  (`cli_compare_fold._fold_scoped_compat_into_text`) appends its
  synthetic scoped-only/missing-contract entries to the flat `changes[]`
  *after* `_to_json_root_cause` has already built `root_causes` — so a
  scoped gate whose only failure is one of these synthetic entries
  reported `root_cause_count: 0`, losing the only gate failure for a
  root-cause consumer. Fixed via `reporter._add_entries_to_root_causes`,
  which folds additional `(key, root, entry)` triples into an
  already-built root-cause payload, called from the same fold-in.
- The fix above still had a gap (Codex review, later commit): when a
  scoped-only finding's `caused_by_type` matched an existing *real*
  change's symbol, `_to_json_root_cause` had already grouped that change
  under its own unique per-finding key (since, at that point, nothing in
  `result.changes` alone referenced its symbol) — so the fold-in's later
  merge attempt found no existing group to join and created a second,
  disagreeing `root_causes` entry for the same logical cause, unlike
  SARIF (which computes its grouping in one pass and got this right from
  the start). Fixed by having `_to_json_root_cause` fold
  `scoped_only_changes`' `caused_by_type` values into its own
  `referenced_causes` computation up front
  (`reporter_markdown._group_changes_by_root_cause` gained an
  `extra_causes` parameter for this), mirroring `sarif.to_sarif`'s
  identical computation, so both passes agree on which symbols are
  "referenced" before either one runs.

**Follow-up fix (Codex review), later commit:** `_to_json_root_cause` built
its JSON payload from scratch instead of reusing `_add_changes_block`,
silently dropping the `redundant_count`/`pattern_modulations` audit-trail
fields `full`/`leaf` JSON both carry when non-empty. Fixed by adding the same
two conditional fields to the root-cause payload.

**Follow-up fix (Codex review), later commit:** the `suppression_rule`
attribution fix earlier in this slice covered `DetectCppPatterns`/
`DetectTemplatePatterns`/`DetectNamespacePatterns` via
`_merge_findings_respecting_suppression`, but missed a fourth late-detector
path: `DetectVersionedSymbolScheme` suppressed its
`versioned_symbol_scheme_detected` advisory with the cheaper
`SuppressionList.is_suppressed` and appended it to `ctx.suppressed` directly,
leaving a labelled rule's match unattributed. Fixed by routing it through the
same shared helper (`_merge_findings_respecting_suppression(changes,
[advisory], ctx)`) instead of duplicating the `evaluate()`/stamp logic
inline — which also keeps `post_processing.py` from growing past the
AI-readiness file-size hard cap a naive inline fix would have pushed it over.

## Slice 4 — `--report-mode root-cause` markdown/text rendering

Landed in a follow-up commit on the same PR. Adds `reporter_markdown._to_markdown_root_cause`,
wired into `to_markdown`'s dispatch alongside the existing `leaf` branch —
covers both `--format markdown` and the default `--format text` output
(`to_markdown` backs both; there is no separate "text" renderer). Renders one
`### root (N findings)` heading per root-cause group instead of `full` mode's
severity-bucketed sections, reusing `_format_change_md` for each finding's
line (kind, description, old/new value, impact) so the per-finding detail
matches every other markdown mode.

To let markdown and JSON share the exact same grouping decision without a
markdown → JSON import (`reporter_markdown.py` is a leaf module `reporter.py`
imports from, never the reverse — see that module's own docstring), the
grouping logic itself moved: `_finding_id`,
`_root_cause_key_and_display`, and a new `_group_changes_by_root_cause`
(factored out of `_to_json_root_cause`, which now calls it too) all now live
in `reporter_markdown.py`, with `reporter.py` importing them back via its
existing re-export block. Both renderers therefore call the identical
grouping function — they cannot disagree about which findings share a root
cause the way two independently-written implementations could drift.
`--report-mode root-cause` still renders as `full` for `--format junit`
(and, prior to Slice 5 below, `sarif` too).

**Follow-up fix (Codex review), same PR:** the initial version of
`_to_markdown_root_cause` did not accept/forward `show_impact`, so
`--report-mode root-cause --show-impact` silently dropped the Impact
Summary table that full/leaf markdown both append. Fixed by threading
`show_impact` through to `_build_impact_table`, matching the other two
markdown modes.

**Follow-up fix (Codex review), later commit:** `_to_markdown_root_cause`
grouped only `result.changes` -- a `--used-by`/`--required-symbol`
scoped-only finding or missing-contract label was still only listed
separately, in `cli_compare_fold.py`'s flat "## Additional scoped-gate
findings" appendix, even when its `caused_by_type`/symbol correlated with
an existing group, under-reporting that group's `finding_count` and
hiding the correlation (unlike the JSON/SARIF paths, which already fold
these in). Fixed by moving `_resolve_scoped_gate_findings` from
`cli_compare_fold.py` to `reporter_markdown.py` (a leaf module both sides
can import from, mirroring `_finding_id`/`_group_changes_by_root_cause`'s
own earlier move for the identical reason) so `_to_markdown_root_cause`
can call it directly: it now groups `changes + scoped_only_changes`
together in one pass (real `Change` objects merge naturally), and keys
each missing-contract label with the same `_root_cause_key_and_display`
logic, joining an existing group when referenced or forming its own
singleton otherwise. `cli_compare_fold._fold_scoped_compat_into_text`
gained a `report_mode` parameter and now skips its own appendix for
markdown/text root-cause mode specifically, to avoid double-listing the
same findings; `review` format ignores `report_mode` (no root-cause
rendering exists for it) and always keeps the appendix.

**Follow-up fix (Codex review), later commit:** merging scoped-only/missing
findings into the same groups (the fix immediately above) exposed a second
bug in the surrounding empty-state check. `_to_markdown_root_cause` decided
whether to print `_No ABI changes detected._` by looking only at
`result.changes`; once a scoped-only change or missing-contract label could
be the *only* displayed finding (`result.changes` itself empty, e.g. an
identical old/new snapshot pair scoped only via `--used-by`), the report
printed a populated `## Root Causes` section immediately followed by the
contradictory "no changes" note. Fixed by tracking
`has_root_cause_entries = bool(groups or missing_labels)` and gating the
empty-state note on `not changes and not has_root_cause_entries` instead of
`not changes` alone.

**Follow-up fix (Codex review), later commit:** the `report_mode` parameter
that lets `cli_compare_fold._fold_scoped_compat_into_text` skip its own
appendix in root-cause mode (two fixes above) was threaded through the CLI's
primary render call site, but `mcp_server.abi_compare`'s identical fold-in
call was missed and kept the default `"full"` — an MCP client combining
`used_by`/`required_symbols` with `report_mode="root-cause"` still got the
same scoped-only/missing-contract finding duplicated in the embedded
`response["report"]` text, even though the top-level JSON fields were
already correct. Fixed by passing `report_mode=report_mode` through that
call too.

**Follow-up fix (Codex review), later commit:** the `## Severity
Configuration` table (built by `_build_severity_summary_md`, shared with
`full`/`leaf` markdown) was populated from `result.changes` before the
scoped-gate resolution a few fixes above runs — a `--used-by`/
`--required-symbol` run whose only breaking issue was a scoped-only change
or missing-contract label showed every category at `Count 0`/"no exit
impact" directly above a `## Root Causes` section naming that same real,
gate-blocking finding. Fixed by moving the `_resolve_scoped_gate_findings`
call ahead of the severity table and passing `_build_severity_summary_md`
two new optional overrides, `scoped_counts`/`scoped_blocking_categories`,
sourced from `result.scoped_severity_counts`/`scoped_blocking_categories` —
the same already-computed numbers the JSON fold-in's `severity`/
`full_severity` swap in `cli_compare_fold.py` uses, so markdown and JSON
report identical scoped counts instead of two independently-derived ones.
`full`/`leaf` markdown's own severity-table call sites have the identical
structural gap (their `_build_severity_summary_md` calls also predate
scoped-gate resolution) but were not touched here — Codex's finding was
scoped to the root-cause renderer this slice touches; fixing `full`/`leaf`
too is deferred to a future pass rather than folded into this one.

## Slice 5 — `--report-mode root-cause` SARIF properties

Landed in a follow-up commit on the same PR. Unlike JSON/markdown, SARIF's
`runs[].results[]` is a flat, one-result-per-finding array with no natural
place for a nested grouping structure — GitHub Code Scanning and other SARIF
consumers expect that shape. Restructuring it (e.g. one result per root
cause, findings nested underneath) would break every existing SARIF
consumer of abicheck's output for a mode that is opt-in by design. Instead,
`to_sarif`/`to_sarif_str` gain a `report_mode` parameter; when
`"root-cause"`, every result (from `result.changes`, `scoped_only_changes`,
and synthetic missing-contract labels alike) gets two additional
`properties`: `rootCauseId` (a stable hash of the grouping key, identical to
JSON's `root_causes[].root_cause_id` for the same finding) and `rootCause`
(the human-readable root). A consumer that wants grouped output can bucket
`results` by `properties.rootCauseId` itself; one that doesn't care ignores
the two extra properties, exactly like any other additive SARIF property
this ADR has added (`reachabilityState`, `impactAssessment`, etc.).

The grouping key/referenced-causes computation is the same
`_root_cause_key_and_display` (`reporter_markdown.py`) JSON/markdown already
share — SARIF computes its own `referenced_causes` set spanning `changes`
and `scoped_only_changes` up front (mirroring the identical computation in
`cli_compare_fold.py`'s JSON scoped-gate fold-in) since SARIF builds every
result in one function rather than fold-in-after-the-fact. `report_mode` is
threaded through `service_render.render_output` and
`mcp_server._render_output`'s `sarif` branches, both of which previously
accepted (but silently dropped) the parameter for that format.

`--report-mode root-cause` rendered as `full` for `--format junit` through
Slice 5; Slice 6 (below) closes that gap the same way SARIF did — additive
attributes, no restructuring — rather than the symbol-keyed `<testcase>`
regrouping this section originally worried about.

**Follow-up fix (Codex review), same PR:** `to_sarif`'s `referenced_causes`
was originally computed from an *unfiltered* preview of
`scoped_only_changes`, read before the same list's own `--show-only`
filtering ran later in the function — so a scoped-only finding hidden by
`--show-only` could still leak its `caused_by_type` into `referenced_causes`
and wrongly group two unrelated *visible* findings sharing its symbol,
disagreeing with JSON/markdown root-cause mode (which computes
`referenced_causes` from the filtered set only). Fixed by computing the
filtered `scoped_only_changes` once, up front, and reusing that single list
for both `referenced_causes` and the results loop.

## Slice 6 — JUnit root-cause rendering + `occurrence_id` (G29 Phase 3 follow-up)

Closes two of the four items Slices 1-5 left open ("Deliberately not
implemented this slice," below) — landed after
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md)'s D1
`occurrence_id` half and D6 structured-path selector shipped, both of which
this slice builds directly on.

**JUnit root-cause rendering** (`abicheck/junit_report.py`): rather than
SARIF's per-*result* additive properties, JUnit gets per-*failure* additive
attributes — `_root_cause_lookup(changes, missing_labels, gate_scope)`
precomputes `finding_id -> (root_cause_id, root_display)` once per
testsuite (the same `_root_cause_key_and_display`/hash JSON/markdown/SARIF
already share, so no format can disagree about a finding's root cause), and
`_add_failure` sets `rootCauseId`/`rootCause` on each `<failure>` element
when a lookup entry exists. This sidesteps the "what if a testcase's
changes disagree on root cause" question this ADR originally raised for a
symbol-keyed regrouping: there is no regrouping — `<testcase>` still groups
by symbol exactly as before, and a symbol with multiple changes gets
multiple `<failure>` children, each carrying only its *own* change's root
cause. `to_junit_xml`/`to_junit_xml_multi`/`_build_testsuite` gained a
`report_mode` parameter (mirroring `to_sarif`/`to_sarif_str`); any value
other than `"root-cause"` renders identically to before this slice.
Missing-contract labels (`_emit_missing_contract_testcases`) get the same
treatment, mirroring `sarif._missing_contract_result`'s
`_root_cause_for(None, label, rule_id, label)` handling.

**The actual end-to-end gap, found while wiring this up:**
`service_render.render_output`'s `"junit"` branch called `to_junit_xml`
without forwarding its own `report_mode` parameter at all — so
`--format junit --report-mode root-cause` silently rendered as plain `full`
with no error, for every caller (CLI, MCP, Python API) that went through
`render_output`, not just a JUnit-internal limitation. Fixed by forwarding
`report_mode=report_mode` in that branch.

**`occurrence_id`** (`abicheck/buildsource/graph_impact.py`,
`impact/model.py`, `impact/engine.py`): `_path_occurrence_id(path)` folds a
structured path's edges' own `GraphEdge.occurrences`
([ADR-046 D1](046-source-graph-identity-v2-and-evidence-merge.md#d1-implementation-g29-phase-2-slices-3-and-6-both-halves))
into one hash, set as `Change.impact_occurrence_id` by
`attach_impact_metadata` and surfaced as `GraphProofPath.occurrence_id` by
`assess_change`. `None` whenever no edge on the path carries occurrence-level
attrs — still every finding today, since no producer populates them (D1's
own opt-in note). `root_cause_id`/`impact_group_id` are **not** included in
this slice — see "Deliberately not implemented" below, unchanged from why
Slices 1-5 left them out.

`tests/test_junit_report_root_cause.py` (split from `test_junit_report.py`,
already at the line-count cap): full mode never sets the new attributes;
root-cause mode sets them; shared `caused_by_type` findings get the same
`rootCauseId`; unrelated findings get different ones; two failures on one
testcase get independent root causes; a missing-contract label gets one
too; other report modes (e.g. `"leaf"`) behave like `"full"`;
`to_junit_xml_multi` forwards `report_mode`; and a direct regression test
for the `render_output` forwarding gap. `tests/test_graph_impact.py`
(`TestPathOccurrenceId`) covers `_path_occurrence_id` directly and its
propagation through `attach_impact_metadata`/`assess_change`.

## Slice 7 — `root_cause_id`/`root_cause_display`/`impact_group_id` on `ImpactAssessment` (G29 Phase 3 follow-up)

Closes the third item Slices 1-6 left open ("Deliberately not implemented
this slice," below originally argued these "cannot see" whole-`DiffResult`
context and so "do not belong on `ImpactAssessment`/`GraphProofPath` at
all" — that reasoning was about what a *single `Change`'s own read view*
can compute, not about whether the field could exist on the dataclass at
all. This slice adds the fields without contradicting it: `ImpactAssessment`
itself stays a pure, single-`Change` read view (`assess_change` still
doesn't traverse `result.changes`) — the report-level caller resolves the
value and passes it in as a plain parameter, the same pattern `occurrence_id`
(Slice 6) established for a different reason (needing D1's edge-occurrence
data, not whole-report context).

`assess_change(change, *, root_cause: tuple[str, str] | None = None)` gained
the parameter; when given, it fans out to `root_cause_id`/
`root_cause_display`/`impact_group_id` (`impact_group_id` is always set
equal to `root_cause_id` — see "Deliberately not implemented" below for why
they aren't yet distinct concepts). `reporter_markdown.py` gained two
sibling helpers next to `_group_changes_by_root_cause`: `root_cause_for_change`
(one change's `(root_cause_id, root_display)`, or `None` for the trivial
self-referencing singleton case — a finding with no `caused_by_type` that
also isn't named by any other finding's `caused_by_type`) and
`root_cause_lookup_for_changes` (builds a `finding_id -> (id, display)` dict
once per report/scope, the same amortization pattern Slice 6's
`_root_cause_lookup` used for JUnit). Every JSON/SARIF call site that builds
an `ImpactAssessment` now resolves its own lookup, scoped to whichever list
of changes is actually in play for that call site, and threads the result
through:

- `reporter.py`: `_to_json_leaf`'s leaf/non-type entries, the root-cause JSON
  builder's `entry_by_id`, `_add_suppression` (scoped to
  `result.suppressed_changes` itself — a suppressed finding's root cause is
  resolved relative to other suppressed findings, not folded into the kept
  `changes[]` list's own grouping), and `_add_changes_block`/appcompat's
  `relevant_changes` block.
- `cli_compare_fold.py`: the scoped-only-changes JSON fold-in, reusing the
  same `referenced_causes` that fold-in already computes for its own
  complete root-cause-mode grouping (`root_cause_entries`, which
  deliberately still includes singletons — that list feeds `--report-mode
  root-cause`'s exhaustive grouping, a different contract than this
  per-finding field's singleton-omission rule).
- `sarif.py`: `_result_for` gained a second, independent `impact_root_cause`
  parameter (distinct from its existing `root_cause` parameter, which stays
  exclusive to `--report-mode root-cause`'s own `properties.rootCauseId`/
  `rootCause`) — computed unconditionally in `to_sarif` via
  `root_cause_lookup_for_changes(changes + scoped_only_changes)`, regardless
  of `report_mode`, so `properties.impactAssessment.root_cause_id` is always
  populated when a real correlation exists, the same as JSON. Kept as a
  separate parameter specifically so the existing, tested `root_cause_mode`
  gating on the top-level properties couldn't shift.

Every one of these lookups reuses the exact same
`_root_cause_key_and_display` grouping decision `--report-mode root-cause`
computes, so a finding's `impact_assessment.root_cause_id` is always
identical to its `root_causes[].root_cause_id` in JSON root-cause mode or
its `properties.rootCauseId` in SARIF root-cause mode, for the same report —
no format can disagree.

**Correction (G29 Phase 3, review finding, same PR as Slices 8-9 above)**:
that "no format can disagree" claim didn't hold for one case at ship time —
`_add_changes_block` (default/full JSON) and `_to_json_leaf` (`--report-mode
leaf`) built their `root_cause_lookup_for_changes` scoped only to
`result.changes`, unlike `_to_json_root_cause`, `sarif.to_sarif`, and
`junit_report._build_testsuite`, which all fold `result.scoped_only_changes`'
`caused_by_type` values in too (the scoped-gate fold-in appends these
findings *after* the main report is otherwise built). A finding in
`result.changes` correlating only via a scoped-only overlay's
`caused_by_type` silently lost its `impact_assessment.root_cause_id` in full
and leaf mode while still getting one in root-cause mode, SARIF, and JUnit —
dormant in practice (no shipped scoped-only producer sets `caused_by_type`
yet) but a real latent inconsistency. Fixed by factoring the fold-in into a
shared `reporter._scoped_only_extra_causes` helper and wiring it into all
three JSON call sites; `tests/test_reporter.py::TestImpactAssessmentRootCause::
test_correlates_via_a_scoped_only_changes_caused_by_type`/
`test_leaf_mode_also_correlates_via_scoped_only_changes` cover it.

`tests/test_impact_model.py`/`abicheck/impact/engine.py`'s own tests cover
`assess_change`'s new parameter directly;
`tests/test_reporter.py::TestImpactAssessmentRootCause` and
`tests/test_sarif.py::TestImpactAssessmentRootCause` cover the end-to-end
JSON/SARIF behavior — an uncorrelated singleton finding has no
`impact_assessment.root_cause_id` (or, when it has no other signal either,
no `impact_assessment` key at all), correlated findings share one id and
`impact_group_id == root_cause_id`, two independent findings sharing only a
symbol stay separate, and the unconditional id matches
`--report-mode root-cause`'s own id for the same finding.

## Slice 8 — D2 direction flip, scoped to one producer (G29 Phase 3 follow-up)

D2's original decision text called for five producer modules
(`post_processing.MarkReachability`, `source_graph_findings.py`,
`internal_leak.py`, `suppression.py`, `appcompat.py`) to construct
`ImpactAssessment` directly, with the flat `Change` fields becoming derived
views over it. This slice does **not** attempt that in full — the
"Deliberately not implemented" section below (unchanged reasoning, carried
forward from Slices 1-7) explains why forcing the whole flip through in one
pass would be exactly the rushed, high-blast-radius change this ADR's own
"needs its own ADR/scoped design pass" bar exists to prevent, particularly
for `MarkReachability`'s suppression-safety-critical walk (ADR-044).

Instead, this slice delivers a **verifiably safe, narrower** version:

- **`Change.impact_assessment: ImpactAssessment | None = None`** (new field,
  `checker_types.py`) — purely additive, defaults to `None`, so every
  existing `Change(...)` call site (hundreds, across every detector and
  test) is unaffected.
- **One producer wired**: `internal_leak.py`'s `_build_leak_change`/
  `_build_call_graph_leak_change` (the two `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`/
  `INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API` synthetic-finding builders) now
  call `impact.engine.assess_change(change)` on the just-constructed
  `Change` and attach the result to `change.impact_assessment` — reusing
  the existing, tested derivation logic itself (not a second,
  independently-maintained computation), so the cached object is
  byte-identical to what an on-demand call would have produced.
- **Verified safe to cache, not assumed**: a pipeline-ordering audit of
  `post_processing.DEFAULT_PIPELINE` confirmed `MarkReachability` — the
  *only* step anywhere in the codebase that mutates
  `public_reachable`/`reachability_state`/`reachability_kind`/
  `reachability_proof_path` on an existing `Change` — runs **before**
  `DetectInternalLeaks`, which *appends new* `Change` objects to
  `ctx.changes` after `MarkReachability` has already finished. A leak
  finding's own reachability/evidence fields are therefore self-contained
  and provably never mutated after construction by anything later in the
  pipeline (`DemoteUnreachableInternalChurn`, `DetectCppPatterns`,
  `DetectNamespacePatterns`, `DetectTemplatePatterns`,
  `DetectVersionedSymbolScheme`, `EscalateFrozenNamespaceViolations` — none
  of them touch these fields at all, confirmed by a repo-wide grep, not
  read from this ADR's own claim alone).
- **`decision`/`root_cause_id` are never cached** — `impact.engine.
  assess_change` reuses a cached `impact_assessment`'s *evidence* fields
  only (`reachability_state`/`public_reachable`/`reachability_kind`/
  `confidence`/`proof_path`/`evidence_category`/`correlated_change_kind`);
  `decision` (which depends on `suppression_rule`/`modulation_reason`/
  `effective_verdict` — fields suppression/pattern-modulation passes *do*
  set after construction) and `root_cause_id`/`root_cause_display`/
  `impact_group_id` (whole-`DiffResult` context) are always recomputed
  fresh from the `Change`'s *current* state on every call, exactly as
  before this slice — so a finding suppressed after construction still
  reports `decision.state == "suppressed"` correctly, even though its
  evidence came from a cache built before suppression ran.
- **The other four producer modules are untouched** — `source_graph_findings.py`,
  `post_processing.py`, `suppression.py`, `appcompat.py` still
  independently set the overlapping flat `Change` fields exactly as
  before, and `impact.engine.assess_change` still derives their
  `ImpactAssessment` on demand from those flat fields (the Slice 1 path,
  unchanged). `Change.impact_assessment` stays `None` for every finding
  those modules produce.
- `tests/test_impact_model.py::TestAssessChangeWithCachedImpactAssessment`:
  cached evidence is reused verbatim; `decision`/`root_cause_id` are always
  recomputed, never read from the cache (including when flat fields were
  mutated *after* the cached object was built); a cached assessment built
  from the same flat fields an on-demand derivation would use produces an
  identical result; `impact_assessment=None` falls back to the unchanged
  derivation path. `tests/test_internal_leak.py::TestBuildChangeAttachesImpactAssessment`:
  both builders attach a correct `impact_assessment`;
  `assess_change(change) == change.impact_assessment` for both (proving
  the two code paths never disagree); the cached evidence survives a
  simulated later suppression pass untouched while `decision` correctly
  reflects it.

## Slice 9 — D2 direction flip, second producer (G29 Phase 3 follow-up)

Migrates `appcompat.py`'s single `CONSUMER_REQUIRED_SYMBOL_REMOVED` overlay
construction (`scope_diff_to_app`) the same way Slice 8 migrated
`internal_leak.py`'s two builders — chosen next because, like Slice 8's
targets, it is a single, well-isolated construction site rather than the
nine-site sweep `source_graph_findings.py` would need (see "Deliberately not
implemented this slice" below for why that one stays open).

- `overlay_change.impact_assessment = assess_change(overlay_change)` is set
  immediately after `make_change(ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
  ...)` constructs it, before `suppression.evaluate(overlay_change)` runs.
- **Verified safe, not assumed**: `Suppression.evaluate`/`matches`/
  `would_withhold`/`would_withhold_unknown_reachability` (`suppression.py`)
  are confirmed pure reads of the `Change` passed in — none assigns to it —
  so nothing between the cache write and any later `assess_change()` read
  touches `overlay_change`'s evidence fields. `_build_suppression_overreach_change`
  (`post_processing.py`), which `scope_diff_to_app` calls on a withheld
  match, builds a *different* `Change` (`SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`)
  that sets no reachability fields of its own — left uncached, matching
  Slice 8's own precedent of only caching a finding that actually carries
  evidence.
- `tests/test_appcompat.py::test_consumer_required_symbol_removed_carries_impact_assessment`:
  mirrors `test_internal_leak.py`'s cache-assertion pattern —
  `assess_change(overlay) == overlay.impact_assessment`.
- **Two producer sites now remain unmigrated**: `source_graph_findings.py`
  and `post_processing.MarkReachability` — down from three after Slice 8.
  `suppression.py`'s D2 role (see below, resolved in Slice 11) is a
  separate documentation question, not a third producer to migrate.

## Slice 10 — `MarkReachability` measurement + caching, and the `source_graph_findings.py` audit (G29 Phase 3 follow-up, 2026-08-09)

Closes the two remaining items "Deliberately not implemented this slice"
(below, pre-Slice-10 text) left open: the unmeasured `MarkReachability`
question, and the nine-site `source_graph_findings.py` sweep. Both turned
out to have one answer, not two independent ones — see "How the two connect"
below.

### The `MarkReachability` measurement

The open question was whether a single `compare` invocation ever calls
`assess_change()` more than once for the same `Change` object — the ADR text
speculated `reporter.py` alone has three call sites (`_leaf_entry`, the
suppressed-changes entry builder, the main JSON entry builder) "though
normally each `Change` only ever passes through one of the three." That
speculation about `reporter.py`'s own three call sites is correct (they
render disjoint change-list memberships) — but it was the wrong place to
look. `sarif.py` has its own, independent `assess_change` call site, and
`compare --format <fmt1> --secondary-format <fmt2> --secondary-output <path>`
(a real, existing CLI feature, `cli_compare_helpers.py`) renders the
*identical* `DiffResult`/`Change` objects through two different formats in
one process — the code comment there says so explicitly ("Reuses the same
already-computed `result` — no second comparison run").

**Measured, not assumed**: `tests/test_cli_unit.py::TestCompareSecondaryFormat::
test_json_then_sarif_secondary_calls_assess_change_twice_per_change`
monkeypatches `assess_change` in `impact.engine`/`reporter`/`sarif` to count
calls keyed by `id(change)`, then runs `compare --format json
--secondary-format sarif` (via `CliRunner`) over a two-snapshot pair with one
removed function. Result: **the same `Change` object was assessed twice** —
once by `reporter.py`'s JSON render, once by `sarif.py`'s SARIF render, in
the same process. This is the real, non-hypothetical repeat-call scenario
the ADR asked to be measured before deciding.

### The `MarkReachability` caching implementation

`post_processing_reachability.py`'s `MarkReachability.run()` now sets
`c.impact_assessment = assess_change(c)` at the point it finalizes each
change's reachability fields (all three per-change exit paths: the two early
`continue`s and the natural loop fallthrough for the
tagged/`PROVEN_UNREACHABLE`/`UNKNOWN` branches) — right where Slice 8's own
comment already knew this step is "the *only* step anywhere in the codebase
that mutates `public_reachable`/`reachability_state`/`reachability_kind`/
`reachability_proof_path` on an existing `Change`" (re-confirmed by a fresh
repo-wide grep for this slice, not carried forward on faith).

**Verified safe, not assumed**, per the same discipline Slice 8/9 used:

- `confidence` and the `attach_impact_metadata` proof-path fields
  (`impact_proof_path`/`affected_public_roots`/`impact_is_direct`/
  `impact_alternative_paths`/`impact_discarded_path_count`/
  `impact_occurrence_id`) are always set (if at all) at `Change` construction
  time, before this step ever runs — stable by the time it caches.
- One residual field checked and found *not* to break this, rather than
  overlooked: `Change.evidence_category` has exactly one other
  post-construction mutator, `diff_reconcile.reconcile_build_context_findings`
  (`checker.py`, gated on `--reconcile-build-context`) — but it runs *after*
  `_run_post_processing` (this step included) and only ever sets
  `evidence_category` on a change it is simultaneously moving out of `kept`
  into `DiffResult.reconciled_changes`. `reporter._add_reconciled` renders
  that list from its own hand-built dict and never reads
  `Change.impact_assessment` (confirmed by reading `reporter.py`), so a
  reconciled change's by-then-stale cached `evidence_category` is never read
  through `impact_assessment` by any current report path.
- `decision`/`root_cause_id`/`root_cause_display`/`impact_group_id` are —
  exactly as Slice 8/9 established — never read from the cache; they are
  always recomputed fresh on every `assess_change()` call from the `Change`'s
  *current* state, so a later suppression/pattern-modulation pass changing
  `suppression_rule`/`modulation_reason`/`effective_verdict` is still
  reflected correctly even though the cached evidence predates it.

### How the two connect: `source_graph_findings.py` re-audited

Re-auditing `abicheck/buildsource/source_graph_findings.py`'s nine
per-family helpers (ten `Change(...)` construction sites —
`_public_reachability_findings` has two) found they are **not** individually
safe to cache the way `internal_leak.py`'s builders were in Slice 8, for the
opposite pipeline-position reason: `internal_leak.py`'s `DetectInternalLeaks`
is itself a `DEFAULT_PIPELINE` *step*, registered after `MarkReachability`,
so its `Change` objects don't exist yet when `MarkReachability` runs.
`source_graph_findings.py`'s findings are different — their caller
(`cli_buildsource_helpers.prepare_embedded_build_source`) folds them into
`checker.compare`'s `extra_changes`, which `checker.compare` merges into
`changes` **before** calling `_run_post_processing` (i.e. before
`DEFAULT_PIPELINE.run()`, `MarkReachability` included). None of the ten
sites set `public_reachable`/`reachability_state`/`reachability_kind`/
`reachability_proof_path` themselves (confirmed by reading the whole file),
so caching at construction time would freeze those fields at their unset
defaults — exactly the fields `MarkReachability` still goes on to tag for
every one of these findings whenever a suppression file needs reachability
evidence.

Rather than force a mismatched "cache at construction" edit onto nine sites
whose own pipeline position makes it wrong, each of the ten sites got a
short code comment recording this finding (one detailed audit comment at
the first site, `_mapping_drift_findings`, and a pointer comment at the
other nine) — **and no site was migrated to cache directly**. This is not a
gap: `MarkReachability`'s own new caching (this slice) already reaches every
`source_graph_findings.py` finding once it's merged into the pipeline and
tagged, giving all nine families a correctly cached `impact_assessment`
exactly the way Slice 8 gave `internal_leak.py`'s findings one — just from a
different, but safe, cache-write point. `tests/test_source_graph_findings_impact.py`
covers both halves: `test_findings_are_not_eagerly_cached_with_impact_assessment`
(construction-time state) and
`test_source_graph_finding_gets_cached_assessment_once_it_reaches_mark_reachability`
(the pipeline-ordering regression test — the cache reflects the *post*-tag
value, not the pre-tag default a naive construction-time cache would have
captured). `tests/test_reachability_state.py::TestMarkReachabilityImpactAssessmentCache`
covers the `MarkReachability` step itself directly (both early-`continue`
branches, the fallthrough branch, and the no-op gate leaving
`impact_assessment` at `None` when no suppression needs reachability
evidence, unchanged from before this slice).

- **Zero producer sites now remain unmigrated as "not yet looked at"** —
  `post_processing.MarkReachability` is migrated;
  `source_graph_findings.py`'s ten sites are covered transitively through
  it, each with a recorded reason for not caching directly.
  `suppression.py`'s D2 role (Slice 9's note) was the one remaining open
  item — Slice 11 (below) resolves it: never a producer to migrate in the
  first place.

## Slice 11 — the `suppression.py` D2 role, resolved by inspection (G29 Phase 3 follow-up, 2026-08-10)

Closes the one item Slices 8-10 left as "a separate, unresolved
documentation question" rather than a fourth producer: what D2's original
decision text meant by naming `suppression.py` alongside
`post_processing.MarkReachability`, `source_graph_findings.py`,
`internal_leak.py`, and `appcompat.py`. Resolved by re-reading the module
itself, not by guessing at authorial intent.

**Finding**: `abicheck/suppression.py` constructs no `Change` and mutates no
`Change` field, anywhere in the module — confirmed by grepping every
assignment target in the file. `Suppression.matches`/`would_withhold`/
`would_withhold_unknown_reachability` and `SuppressionList.evaluate` only
*read* `change.reachability_state`/`change.public_reachable`/etc. for rule
matching, returning a `SuppressionOutcome` (`suppressed`, `withheld_rule`,
`withheld_unknown_rule`, `matched_rule`) that carries no evidence of its
own. `SuppressionAudit` (the `--audit-suppressions` surface) is the same
shape one level up: it only reads already-suppressed `Change` objects to
group them into `stale_rules`/`high_risk_matches`/etc.

The actual mutation the D2 text was reaching for happens one layer out, at
the two call sites that consume a `SuppressionOutcome`:
`checker._filter_suppressed_changes` (for the top-level `compare` path) and
`post_processing.ApplySuppression`/`_merge_findings_respecting_suppression`
(for the post-processing pipeline and its late-detector followers) — both
already do exactly what D2 describes: `c.suppression_rule =
outcome.rule_label()`, migrated in **Slice 2**, long before this question
was ever raised. And by the time either call site runs,
`c.impact_assessment` is already populated for every reachability-tagged
`Change` — `MarkReachability` runs *before* `ApplySuppression` in
`DEFAULT_PIPELINE` (ADR-044 D1's ordering requirement, unrelated to this
ADR but load-bearing here too), and `_filter_suppressed_changes`'s own call
sites in `checker.py` only ever run after `_run_post_processing` has
already tagged everything `MarkReachability` covers. So Slice 10's
`MarkReachability` caching already reaches every `Change` the suppression
subsystem evaluates, by construction of the pipeline order — there was no
gap for a suppression-adjacent caching step to close, once `checker.py`/
`post_processing.py` (not `suppression.py`) are recognized as the real
producers.

**Conclusion**: D2's original text conflated "the suppression *subsystem*"
(the `checker.py`/`post_processing.py` call sites that apply a
`SuppressionOutcome` to a `Change`) with "the `suppression.py` *module*"
(the pure rule-matching engine that decision text happened to name). Read
as the former, D2's suppression-related scope was already fully delivered
by Slice 2 (`suppression_rule`) and Slice 10 (`impact_assessment` caching,
transitively, via pipeline ordering) — nothing here needed a Slice 11 code
change, only the recognition that there was never a fifth producer to
migrate. `suppression.py` itself stays exactly what it already is: a pure,
`Change`-mutating-nothing predicate engine, which is the correct shape for
it to have — `checker.py`/`post_processing.py` deciding *what to do* with
an evaluation outcome, and `suppression.py` deciding only *whether*, is
the separation of concerns this module was written with from the start,
not a gap to close.

No test changes: this slice asserts nothing new, since nothing behavioral
changed — the existing `tests/test_reachability_state.py::
TestMarkReachabilityImpactAssessmentCache` and `test_suppression.py`'s own
suite already cover the two real halves (the cache write, and the pure
evaluation) this finding connects.

## Deliberately not implemented this slice

Per the "ship each phase independently" mitigation this initiative committed
to from the start, and matching exactly how ADR-046 documented its own
partial slices (D1's `occurrence_id` half, D4, D5's `effect_transitions`, D6's
remaining four tiers):

- **`changed_entities`/`affected_consumers`/`affected_use_cases`/`coverage`**
  — the remainder of the plan's full `ImpactAssessment` field list
  (`root_cause_id`/`root_cause_display`/`impact_group_id` shipped in Slice 7
  above). None of these four have a data source yet:
  `affected_consumers`/`affected_use_cases` need Phase 4's consumer/use-case
  graph (unbuilt), and `coverage` needs the per-(kind,role) matrix wired all
  the way through the impact layer. Adding empty placeholder fields for data
  no producer can populate yet would be exactly the speculative-surface
  pattern ADR-046 D5 explicitly declined (`effect_transitions`, "no current
  walk needs it") — so they are left out of `ImpactAssessment` entirely
  rather than added as permanently-`None` fields.
- **The full D2 direction flip** (every flat `Change` field becoming a
  derived view over `ImpactAssessment`, across all five producer modules
  D2's decision text names) — still not attempted in the *literal* sense of
  five independent construction-site migrations; Slices 8-11 instead
  deliver a verifiably safe subset chosen by real pipeline-ordering/module
  audits rather than forcing the whole flip through in one pass — see
  "Slice 10"/"Slice 11" above for why `MarkReachability` caching (rather
  than nine/ten independent `source_graph_findings.py` edits) turned out to
  be the correct shape for the remaining scope, and why `suppression.py`
  needed no migration at all:
  - **`source_graph_findings.py`** and **`post_processing.MarkReachability`**
    — both closed by Slice 10 (above): the former is not individually
    cacheable at construction time (audited, ten sites, all found unsafe for
    the same reason — see Slice 10), but every finding it produces still
    ends up correctly cached once `MarkReachability` tags it, which Slice 10
    also implemented after measuring (not assuming) that doing so is worth
    it.
  - **`suppression.py`** — closed by Slice 11 (above): direct inspection
    found **no** `Change(...)` construction or mutation inside this module
    at all; it only *reads* `public_reachable`/`reachability_state` for
    rule matching and returns a `SuppressionOutcome` the caller acts on. The
    real mutation D2's text was naming happens at the caller —
    `checker._filter_suppressed_changes`/`post_processing.ApplySuppression`
    — already migrated in Slice 2, and already covered by Slice 10's
    `MarkReachability` caching (which runs earlier in the pipeline, so
    every `Change` those callers touch already carries a cached
    `impact_assessment`). There was never a fifth producer to migrate —
    D2's text conflated the suppression *subsystem* (the callers) with the
    `suppression.py` *module* (a pure predicate engine with nothing to
    migrate) — see Slice 11 for the full resolution.

  This closes every item in D2's original five-producer scope: two
  migrated directly (Slices 8-9), two covered transitively through
  `MarkReachability` caching (Slice 10), and one resolved as never having
  been a producer at all (Slice 11).
- **The full `RootCauseCorrelator` correlation across consumer-overlay
  findings that don't share a `caused_by_type` today** — Slices 3-5 shipped
  the `caused_by_type`-based first cut (JSON, markdown/text, and SARIF
  properties), and Slice 6 extended the same first cut to JUnit (additive
  `<failure>` attributes, not a restructuring). Phase 6's
  `RootCauseCorrelator` is still the fuller job that adds correlation for
  findings with no `caused_by_type` link at all — none of Slices 1-6 attempt
  it.
- **Stable `finding_id` independent of `description` text** —
  `reporter._finding_id` already exists (schema 2.3) and is stable across
  repeated runs, but (unlike the plan's stated goal) it *does* include
  `description` text as a discriminator by design — disambiguating
  same-kind/same-symbol findings that would otherwise collide (e.g. two
  parameters of one function both changing pointer depth). Changing that
  derivation to drop `description` would itself be a breaking change to an
  already-published, schema-2.3 field's values — out of scope for an
  additive slice, and not attempted here. `occurrence_id` **is** now
  populated (Slice 6, above) — it needed only ADR-046 D1's `occurrence_id`
  half, which has since landed, and it is computable from a single
  `Change`'s own path. `root_cause_id`/`root_cause_display`/
  `impact_group_id` **are** now populated too (Slice 7, above) — but note
  they stay report-level concepts computed relative to whole-`DiffResult`
  context (`referenced_causes` — see `_root_cause_key_and_display`) and
  passed into `assess_change` as a plain parameter; `ImpactAssessment`
  itself never gained the ability to compute them from a single `Change` in
  isolation, matching the [Detector Impact Contract](../detector-impact-contract.md)'s
  reasoning for future detectors. `impact_group_id` diverging from
  `root_cause_id` still needs Phase 6's `RootCauseCorrelator`.
- **`docs/reference/source-graph-schema.md`,
  `docs/contribute/detector-impact-contract.md`** — both now exist (G29
  Phase 2/3 follow-up), once D1/D5/D6/Slice 6 gave them enough real surface
  to document. `docs/learn/impact-analysis.md` remains the narrative
  canonical page; the two reference pages summarize it rather than
  duplicating its explanation.

## Non-goals

- **Not** a change to any `ChangeKind`'s default verdict, to
  `BREAKING_KINDS`/`API_BREAK_KINDS`/`RISK_KINDS`/`COMPATIBLE_KINDS`
  membership, or to which findings suppression withholds — this ADR is a
  read view and a reporting addition underneath the existing tri-state
  reachability model (ADR-044, ADR-046, ADR-048), not a policy change.
- **Not** removing, renaming, or reshaping any existing JSON/SARIF/JUnit
  field. `public_reachable`/`reachability_kind`/`reachability_proof_path`/
  `affected_public_roots`/`impact_proof_path`/`impact_is_direct`/
  `correlated_change_kind` all stay exactly as they are; `impact_assessment`,
  `impact_alternative_paths`/`impact_discarded_path_count`/
  `impact_occurrence_id`, JUnit's `rootCauseId`/`rootCause`, and
  `impact_assessment.root_cause_id`/`root_cause_display`/`impact_group_id`
  (Slice 7) are all additive.
- **Not** the fuller `RootCauseCorrelator` (Phase 6) — Slices 3-6 ship the
  `caused_by_type`-based first cut for every format including JUnit (Slice
  6); correlating findings with no `caused_by_type` link at all is still
  deferred (see "Deliberately not implemented this slice" above).

## Consequences

**Positive:** `reachability_state` is finally visible to any JSON/SARIF
consumer — a `PROVEN_UNREACHABLE` finding and an `UNKNOWN` one (narrowed or
degraded coverage) are now distinguishable without re-running abicheck with
`-v` or reading `docs/learn/graph-coverage.md`'s prose description of the
gap. `impact_assessment` gives a consumer building tooling on top of
abicheck one object to query for "was this reachable, how, and what's the
proof" instead of five separately-named, independently-nullable keys.

**Costs:** `impact_assessment` duplicates data already present at the
top level for findings where both are emitted — an accepted, documented
redundancy (D3 above), not an oversight. This ADR does not reduce the
scattered-field problem Phase 3 exists to solve at the *producer* level
(D2) — only at the *reporting* level. The remaining phases (the D2 flip,
Phase 4's consumer/use-case join, Phase 5's new graph families, Phase 6's
detectors and the fuller `RootCauseCorrelator` beyond Slices 3-6's shipped
`--report-mode root-cause`, including JUnit) are unaffected by and do not
depend on anything in this ADR being done differently.

## References

- `abicheck/impact/model.py`, `abicheck/impact/engine.py`
- `abicheck/reporter.py` — `_change_to_dict`, `_leaf_entry`, `_suppressed_change_entry`, `_to_json_root_cause`
- `abicheck/sarif.py` — `_result_for`, `_missing_contract_result`
- `abicheck/cli_compare_fold.py` — `_fold_scoped_compat_into_text`
- `abicheck/cli.py` — `--report-mode` `click.Choice`
- `abicheck/suppression.py` — `SuppressionOutcome.matched_rule`/`rule_label`
- `abicheck/checker.py`, `abicheck/post_processing.py` — `Change.suppression_rule` set at suppression time (`_filter_suppressed_changes`, `_filter_pattern_synthetic`, `ApplySuppression.run`, `_merge_findings_respecting_suppression`)
- `abicheck/schemas/compare_report.schema.json`, `abicheck/schemas/__init__.py`
- `tests/test_impact_model.py`, `tests/test_suppression.py`, `tests/test_sarif.py`, `tests/test_cov95_cli.py`, `tests/test_reporter.py`, `tests/test_reachability_aware_suppression.py`
- `docs/learn/impact-analysis.md`, `docs/use/output-formats.md`
- [G29](../plans/g29-impact-analysis-layer.md) — Phase 3
- [ADR-044](044-reachability-aware-suppression.md),
  [ADR-046](046-source-graph-identity-v2-and-evidence-merge.md),
  [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
