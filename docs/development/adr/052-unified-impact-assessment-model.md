# ADR-052: Unified Impact Assessment Model (G29 Phase 3, slices 1-5)

**Date:** 2026-07-22
**Status:** Accepted — slices 1-5 implemented.
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
#607, but `reporter.py`/`sarif.py` never serialize it.** A JSON/SARIF
consumer today can see `public_reachable: false` for two changes — one the
graph walk *proved* unreachable, one it never examined at all (`UNKNOWN`,
e.g. because the relevant `extractor_passes` family was narrowed/degraded) —
with no way to tell them apart. That is exactly the "no evidence ≠ proof of
absence" distinction `docs/concepts/graph-coverage.md` already documents for
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

`REPORT_SCHEMA_VERSION` 2.13 → 2.14 (additive: two new optional keys, no
existing key removed or reshaped). `abicheck/schemas/compare_report.schema.json`
gains `reachability_state` (enum, matching `ReachabilityState`'s three
values) and `impact_assessment` (object, matching `ImpactAssessment.to_dict()`'s
shape) on each `changes[]` entry; `scripts/publish_schemas.py` republishes
the synced copy under `docs/schemas/v1/`.

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
  text and in `docs/concepts/impact-analysis.md`) but unreachable from any
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
- `REPORT_SCHEMA_VERSION` 2.14 → 2.15 (two new additive, root-cause-mode-only
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

`--report-mode root-cause` still renders as `full` for `--format junit` —
JUnit's `<testcase>` model already groups by *symbol* (`_partition_changes`),
not by finding, so a caused_by_type-keyed grouping would need to decide what
happens when a multi-change testcase's changes disagree on root cause; left
for a dedicated slice rather than bolted on here.

## Deliberately not implemented this slice

Per the "ship each phase independently" mitigation this initiative committed
to from the start, and matching exactly how ADR-046 documented its own
partial slices (D1's `occurrence_id` half, D4, D5's `effect_transitions`, D6's
remaining four tiers):

- **`changed_entities`/`affected_consumers`/`affected_use_cases`/`coverage`/
  `root_cause_id`** — the plan's full `ImpactAssessment` field list. None of
  these have a data source yet: `affected_consumers`/`affected_use_cases`
  need Phase 4's consumer/use-case graph (unbuilt), `coverage` needs the
  per-(kind,role) matrix wired all the way through the impact layer, and
  `root_cause_id` needs Phase 6's `RootCauseCorrelator`. Adding empty
  placeholder fields for data no producer can populate yet would be exactly
  the speculative-surface pattern ADR-046 D5 explicitly declined
  (`effect_transitions`, "no current walk needs it") — so they are left out
  of `ImpactAssessment` entirely rather than added as permanently-`None`
  fields.
- **The D2 direction flip** (`Change` fields becoming derived from
  `ImpactAssessment` rather than the reverse) — deliberately not attempted.
  This touches the core control flow of five producer modules at once
  (`post_processing.MarkReachability`, `source_graph_findings.py`,
  `internal_leak.py`, `suppression.py`, `appcompat.py`), several of them
  performance-sensitive graph walks under active suppression-safety
  guarantees (ADR-044) — the same risk class this ADR's own D2 section
  already flagged. Forcing it through in the same pass as slices 1-2 would
  be exactly the kind of rushed, high-blast-radius change the "needs its
  own ADR/scoped design pass" bar (this ADR's own header, ADR-046 D4, and
  CLAUDE.md "M1-3") exists to prevent — a real regression here would be to
  suppression correctness, not just to this reporting layer. Left for a
  dedicated slice.
- **The full `RootCauseCorrelator` correlation across consumer-overlay
  findings that don't share a `caused_by_type` today** — Slices 3-5 above
  ship the `caused_by_type`-based first cut (JSON, markdown/text, and SARIF
  properties); Phase 6's `RootCauseCorrelator` is the fuller job that adds
  correlation for findings with no `caused_by_type` link at all. `--format
  junit` still renders `root-cause` mode as `full` (Slice 5's ADR section
  above explains why JUnit's symbol-grouped `<testcase>` model doesn't take
  the same properties-only approach SARIF did).
- **Stable `finding_id`/`occurrence_id`/`root_cause_id`/`impact_group_id`
  identifiers independent of `description` text** — `reporter._finding_id`
  already exists (schema 2.3) and is stable across repeated runs, but (unlike
  the plan's stated goal) it *does* include `description` text as a
  discriminator by design — disambiguating same-kind/same-symbol findings
  that would otherwise collide (e.g. two parameters of one function both
  changing pointer depth). Changing that derivation to drop `description`
  would itself be a breaking change to an already-published, schema-2.3
  field's values — out of scope for an additive slice, and not attempted
  here. `occurrence_id`/`root_cause_id`/`impact_group_id` have no producer to
  populate them from yet (the first two need ADR-046 D1's undone
  `occurrence_id` half and Phase 6's correlator respectively).
- **`docs/reference/source-graph-schema.md`,
  `docs/development/detector-impact-contract.md`** — reference docs for the
  full edge/detector surface Phases 2/5/6 will add; premature while those
  surfaces don't exist yet. This ADR adds `docs/concepts/impact-analysis.md`
  instead, scoped to what this slice actually ships.

## Non-goals

- **Not** a change to any `ChangeKind`'s default verdict, to
  `BREAKING_KINDS`/`API_BREAK_KINDS`/`RISK_KINDS`/`COMPATIBLE_KINDS`
  membership, or to which findings suppression withholds — this ADR is a
  read view and a reporting addition underneath the existing tri-state
  reachability model (ADR-044, ADR-046, ADR-048), not a policy change.
- **Not** removing, renaming, or reshaping any existing JSON/SARIF field.
  `public_reachable`/`reachability_kind`/`reachability_proof_path`/
  `affected_public_roots`/`impact_proof_path`/`impact_is_direct`/
  `correlated_change_kind` all stay exactly as they are; `impact_assessment`
  is additive.
- **Not** a new CLI flag or user-facing behavior change — `--report-mode
  root-cause` is explicitly deferred (see above).
- **Not** JUnit surfacing, for the same reason ADR-048 D4 already gave.

## Consequences

**Positive:** `reachability_state` is finally visible to any JSON/SARIF
consumer — a `PROVEN_UNREACHABLE` finding and an `UNKNOWN` one (narrowed or
degraded coverage) are now distinguishable without re-running abicheck with
`-v` or reading `docs/concepts/graph-coverage.md`'s prose description of the
gap. `impact_assessment` gives a consumer building tooling on top of
abicheck one object to query for "was this reachable, how, and what's the
proof" instead of five separately-named, independently-nullable keys.

**Costs:** `impact_assessment` duplicates data already present at the
top level for findings where both are emitted — an accepted, documented
redundancy (D3 above), not an oversight. This slice does not reduce the
scattered-field problem Phase 3 exists to solve at the *producer* level
(D2) — only at the *reporting* level. The remaining phases (the D2 flip,
Phase 4's consumer/use-case join, Phase 5's new graph families, Phase 6's
detectors/root-cause correlator/`--report-mode root-cause`) are unaffected
by and do not depend on anything in this slice being done differently.

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
- `docs/concepts/impact-analysis.md`, `docs/user-guide/output-formats.md`
- [G29](../plans/g29-impact-analysis-layer.md) — Phase 3
- [ADR-044](044-reachability-aware-suppression.md),
  [ADR-046](046-source-graph-identity-v2-and-evidence-merge.md),
  [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
