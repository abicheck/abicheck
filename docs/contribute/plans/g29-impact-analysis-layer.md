# G29 — Impact-Analysis Layer: Unified Graph-Driven Impact Model

**Origin:** External impact-analysis-layer architecture review (2026-07) —
audited how far the optional L5 source/call/type graph (ADR-031, ADR-044) has
grown into a real decision-making layer (version-over-version graph diff,
public reachability, suppression gating, consumer scoping, proof paths) versus
where it still stops short of a unified model. Phase 1's P0 slice is
implemented ([PR #607](https://github.com/abicheck/abicheck/pull/607)); Phases
2–6 are the rest of the review's roadmap, scoped below.
**ADR:** builds on [ADR-044](../adr/044-reachability-aware-suppression.md)
(reachability-aware suppression) and [ADR-031](../adr/031-source-implementation-graph-augmentation.md)
(source implementation graph augmentation). Phase 2 onward needs its **own** ADR before
implementation starts — it changes graph node/edge identity
(`SOURCE_GRAPH_VERSION = 2`) and suppression-adjacent semantics, which is
exactly the class of change ADR-044's own "Post-merge review rounds" note
says needs a recorded decision, not a routine PR.
**Type:** Initiative plan (cross-cutting; not tied to a single
`usecase-registry.yaml` gap — spans `abicheck/buildsource/`,
`post_processing.py`, `suppression.py`, `appcompat.py`, `reporter.py`,
`sarif.py`, and the docs/examples catalog).
**Effort:** XL (phased) · **Risk:** high overall — Phase 2 changes graph
identity, Phase 3 changes reporting-contract shape, Phase 4 adds a whole new
evidence source (consumer/use-case), Phase 5 adds ~15-20 new graph edge
kinds, Phase 6 adds ~8 new detector surfaces (6 `ChangeKind`s and 2
report-level overlays). Mitigated by shipping each phase
independently, keeping every new signal additive/opt-in (mirrors how L3-L5
evidence already never overrides L0-L2 authority — ADR-028 D3), and requiring
the shared new-`ChangeKind` checklist (below) per kind.

---

## Problem

The graph is already a real detector input, not a debug dump: it drives
version-over-version diff findings (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`,
`CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED`, `INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT`,
etc. — `source_graph_findings.py`), computes transitive public reachability
with BFS proof paths (`internal_leak.py`), gates suppression before it can
hide a public-reachable break (`post_processing.MarkReachability` /
`suppression.py`, ADR-044, now with tri-state `ReachabilityState` — Phase 1),
and intersects real `--used-by` consumer binaries against the diff
(`appcompat.py`).

What is still missing, per the review, is that this stays a **flat `Change` +
several independently-computed graph-derived annotations**, not a unified
model:

- `source_graph_findings.py`, `internal_leak.py`, `post_processing.py`,
  `suppression.py`, and `appcompat.py` each answer overlapping "is this
  reachable / why / how confidently" questions independently, with no shared
  object.
- Graph node/edge identity is a `(src, dst, kind)` triple with a fallback
  identity chain (mangled name → qualified name + signature hash → qualified
  name) — no canonical USR-based identity, no relation-vs-occurrence split, so
  semantically distinct dependencies (e.g. "used as return type" vs. "used as
  parameter type") can collapse onto the same edge.
- Node/edge merge is largely first-writer-wins — a later graph producer can
  fail to add missing facts to a node an earlier producer already created.
- `reachability_proof_path` is one human-readable string, not a structured,
  machine-walkable sequence of typed steps.
- There is no consumer *graph* (only a symbol-level `--used-by` intersection)
  and no use-case concept at all for runtime/business scenarios (the existing
  `usecase-registry.yaml` tracks abicheck's *own* feature coverage, a
  deliberately different thing — see Phase 4).
- Several graph families the review calls out as open (template instantiation,
  virtual dispatch, macro/config dependency, callback/function-pointer,
  object/archive link provenance) don't exist yet.

## Goal & acceptance criteria

- **G29.1** (Phase 1, **DONE**) — `Change.reachability_state` tri-state
  (`PROVEN_REACHABLE`/`PROVEN_UNREACHABLE`/`UNKNOWN`) replaces the
  boolean-only reachability signal for the purposes suppression needs; a new
  opt-in `reachability: proven-unreachable-only` gate refuses to match on
  `UNKNOWN` unless `allow_unknown_reachability: true` is set explicitly. See
  [PR #607](https://github.com/abicheck/abicheck/pull/607) and
  `docs/learn/graph-coverage.md`.
- **G29.2** (Phase 3, **slices 1-6 done, ADR-052**) — A single
  `abicheck/impact/` package with `ImpactAssessment`, `GraphProofPath`, and
  `FindingDecision` dataclasses. **Slices 1-6 implement the read-view
  direction only**: the dataclasses exist and `reporter.py`/`sarif.py`/
  `junit_report.py` (Slice 6) surface them (including the suppression audit
  trail, slice 2, and `--report-mode root-cause` grouping in JSON,
  markdown/text, SARIF properties, and — Slice 6 — additive JUnit `<failure>`
  attributes), but `source_graph_findings.py`/`internal_leak.py`/
  `suppression.py`/`appcompat.py` do not yet populate `ImpactAssessment`
  directly — they still independently set the overlapping `Change` fields
  it derives from (see ADR-052 D2). The originally-stated direction (those
  four modules populate `ImpactAssessment`, and the flat `Change` fields
  become derived views over it) remains open follow-up work under the same
  ADR.
- **G29.3** (Phase 2, **D1/D2/D3/D5 done, D4 deliberately deferred,
  ADR-046**) — Graph core v2: relation/occurrence identity split (**done**),
  an evidence-preserving (order-independent) node/edge merge (**done**), a
  per-kind/per-role coverage matrix (**done**, extending `extractor_passes`
  beyond the two families Phase 1 already consults), and a USR-based
  canonical `EntityResolver` with `SOURCE_GRAPH_VERSION = 2` (v1 IDs kept as
  aliases — no forced re-collection) — **deliberately deferred**;
  [ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md)
  already delivers its practical value without the identity-generation
  rewrite a full `EntityResolver` would need.
- **G29.4** (Phase 2/3, **mostly done**) — Structured, machine-walkable proof
  paths (JSON node/edge sequence, not a formatted string) surfaced in JSON —
  **done** (`impact_proof_path`, `impact_assessment.proof_path.steps`); SARIF
  gets additive `properties` instead of a `codeFlows` restructuring — **that
  specific `codeFlows` shape is not implemented and not currently planned**.
  A decision-audit object per finding (`kept`/`suppressed` + reason code) —
  **done** (`FindingDecision`; `suppression_withheld` as a distinct state
  beyond `kept`/`suppressed` is not implemented — suppression's own
  `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`/`SUPPRESSION_REACHABILITY_UNKNOWN`
  diagnostics cover that case today). Root-cause grouping
  (`--report-mode root-cause`) — **done** for every format including JUnit
  (Slice 6). Stable `finding_id` — **done, but by design still includes
  `description` text** (unlike this criterion's original wording — see G24's
  shared checklist reasoning and ADR-052's "Deliberately not implemented"
  section for why changing that would be a breaking change to an
  already-published field). `occurrence_id` — **done** (ADR-046 D1 +
  ADR-052 Slice 6). `root_cause_id`/`root_cause_display`/`impact_group_id`
  as *per-finding* `ImpactAssessment` fields — **done** (ADR-052 Slice 7):
  the report-level caller (`reporter_markdown.root_cause_lookup_for_changes`)
  resolves the value from whole-`DiffResult` context and passes it into
  `assess_change`, so `ImpactAssessment` itself stays a pure single-`Change`
  read view — see the
  [Detector Impact Contract](../detector-impact-contract.md).
  `impact_group_id` is currently always an alias of `root_cause_id`;
  making it independently meaningful still needs Phase 6's
  `RootCauseCorrelator`.
- **G29.5** — A consumer graph (`CONSUMER_REQUIRES_SYMBOL`, `CONSUMER_COMPILED_FROM_HEADER`,
  …) that joins with the source graph so a `CONSUMER_REQUIRED_SYMBOL_REMOVED`
  finding can name the public entry point that produced the dependency, plus
  an optional `impact-use-cases.yaml` manifest (declared entrypoints/tests,
  explicitly **not** a reuse of `usecase-registry.yaml`) and best-effort
  runtime-trace ingestion.
- **G29.6** — The five open graph families (template instantiation, virtual
  dispatch, macro/config, callback/function-pointer, object/archive link
  provenance) implemented behind the same coverage-honesty discipline as the
  existing call/type graph (narrowed/degraded flags, `extractor_passes`).
- **G29.7** — The minimal new user-facing detector set from the review
  (8 detector surfaces: 6 `ChangeKind`s and 2 report-level overlays — see
  Phase 6) plus `case194`-`case205` positive/negative example pairs and the
  corresponding FP-rate-gate corpus entries.
- **Acceptance gate (every phase):** the shared new-`ChangeKind` checklist
  from [G24](g24-linux-abi-gap-closure.md#shared-checklist-every-new-changekind-in-this-plan)
  applies verbatim here too — partition assertion, registry entry, detector,
  tests, docs mention, example fixture where applicable, FP-corpus case for
  any heuristic kind.

## Design (phases)

### Phase 1 — Correctness & unified reachability model (P0) — **DONE**

Implemented in [PR #607](https://github.com/abicheck/abicheck/pull/607):

- `ReachabilityState` enum (`checker_policy.py`) + `Change.reachability_state`
  field (`checker_types.py`), set alongside the existing boolean
  `public_reachable` everywhere a producer already sets it.
- `MarkReachability` (`post_processing.py`) computes the tri-state per
  change: a declared-type-domain change (layout/type-graph walk — always
  trustworthy, a complete closure over the snapshot's own declared types) is
  `PROVEN_UNREACHABLE` when examined-and-not-found; a function/variable-shaped
  change is `PROVEN_UNREACHABLE` only when the *relevant side(s)* (old for
  `*_removed`, new for `*_added`, both for changed-in-place) have a call graph
  with both `extractor_passes["call_graph"]`/`["type_graph"]` confirmed
  complete **and** the subject is internal-namespaced (a trusted call graph
  never proves an *exported* symbol's own reachability — it only walks
  dependencies of consumer-compiled public entries); otherwise `UNKNOWN`.
- `suppression.py`: new `reachability: proven-unreachable-only` value +
  `allow_unknown_reachability` rule field; `Suppression.would_withhold_unknown_reachability`;
  `SuppressionOutcome.withheld_unknown_rule`.
- New advisory `ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN` diagnostic,
  registered in `change_registry_suppression.py`, wired through every
  suppression call site that already emits `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`
  (`post_processing.ApplySuppression`, `checker._filter_suppressed_changes`/
  `_filter_pattern_synthetic`; **not** `appcompat.py`/`cli_compare_helpers.py`,
  whose consumer/runtime-proven overlay findings are always constructed
  `PROVEN_REACHABLE` and can never hit the `UNKNOWN` branch).
- `docs/learn/graph-coverage.md` (new) explains narrowed/degraded coverage
  and why an absent edge isn't proof of an absent dependency;
  `docs/use/suppressions.md` documents the new rule field.
- `tests/test_reachability_state.py` (new) — tri-state tagging across the
  declared-type/internal-callee/exported-symbol/removed-vs-added axes,
  suppression gate behavior, diagnostic emission, YAML load round-trip.

**Explicitly out of scope for Phase 1** (this is why Phases 2-6 exist): no
unified `ImpactAssessment` object yet — `reachability_state` is still one
field alongside `public_reachable`/`reachability_kind`/`reachability_proof_path`,
each producer still sets it independently, and the proof path is still one
formatted string.

### Phase 2 — Graph core v2 — **ADR accepted; D1/D2/D3/D5/D6 implemented, D4 deliberately deferred**

[ADR-046](../adr/046-source-graph-identity-v2-and-evidence-merge.md) records
the D1-D6 decisions below — the "needs its own ADR" gate this phase set for
itself. **D1 (both the `relation_key` and `occurrence_id` halves), D2 (the
evidence-preserving node/edge merge), D3 (the per-(kind,role) coverage
matrix), D5 (`TraversalPolicy` including a real `effect_transitions`), and
D6 (a proof-path preference order split across two selectors by how
structured their walk's path representation is, plus the
`primary_path`/`alternative_paths`/`discarded_path_count` finding shape) are
implemented** — see ADR-046's "D1 implementation"/"D2 implementation"/"D3
implementation"/"D5 implementation"/"D6 implementation" sections,
`abicheck/buildsource/graph_facts.py`, `abicheck/buildsource/graph_impact.py`,
`abicheck/buildsource/inline_graph_fold.py`, `abicheck/internal_leak.py`'s
`TraversalPolicy`/`CALL_GRAPH_TRAVERSAL_POLICY`/`select_preferred_path`,
`tests/test_source_graph_v2.py`, `tests/test_inline_changed_paths.py`,
`tests/test_internal_leak.py`'s `TestTraversalPolicy`/
`TestSelectPreferredPath`, `tests/test_internal_leak_effect_transitions.py`,
and `tests/test_graph_impact.py`. **D4 (`EntityResolver`/
`SOURCE_GRAPH_VERSION = 2`) is deliberately deferred, not just unstarted** —
see ADR-046's "D4: deliberately deferred" section:
[ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md)
(G31 Phase B, shipped after ADR-046 was written) already delivers D4's
practical value — safe old/new reconciliation and impact-path linking — via
`entity_identity.CanonicalIdentity`, without touching `GraphNode.id` or
bumping `SOURCE_GRAPH_VERSION`. A full D4 would still mean changing
`GraphNode.id` generation across every graph producer plus a v1/v2 pack
compatibility matrix — categorically larger and riskier than any slice
landed in this phase, and deserving its own scoped design pass rather than
being folded in here. D4, and two narrower items D5/D6 explicitly still
leave open (adopting `TraversalPolicy` on the layout walk's non-graph data
model; a "consumer-proven" tier and a genuinely finer
"reduced-confidence name resolution" axis, both needing evidence that
doesn't exist yet), remain open follow-up work under the same accepted ADR.
See [Source Graph Schema Reference](../../reference/source-graph-schema.md)
for the exhaustive schema this phase produced.

- `abicheck/buildsource/source_graph.py`/`graph_facts.py`: split edge identity
  into a `relation_key = (src, dst, kind, semantic_role)` (used for
  closure/diff) and an `occurrence_id` hash over `(relation_key,
  source_location, configuration_id, instantiation_id, callsite_id)` (keeps
  the exact evidence trail — e.g. "used as return type" vs. "used as
  parameter type" vs. "used under `#ifdef WIN32`" no longer collapse onto one
  edge). `occurrence_id` is opt-in by construction (costs nothing, and
  `GraphEdge.occurrences` stays empty, unless a fact already carries one of
  the four occurrence attrs) — no current producer populates them yet.
- Evidence-preserving node/edge merge: each node/edge accumulates a `facts:
  list[{producer, confidence, attrs}]` plus a deterministic `resolved:
  dict[str, Any]` merge (order-independent — same result regardless of
  producer ingestion order) and a `conflicts: list[...]` when two producers
  disagree. Replaces the current first-writer-wins behavior.
- Per-kind/per-role coverage matrix: extend today's family-level
  `extractor_passes`/`narrowed_passes`/`degraded_passes` (Phase 1 already
  consults `"call_graph"`/`"type_graph"`) to a `(kind, role)` grain — e.g.
  `"DECL_HAS_TYPE:variable"` vs. `"DECL_HAS_TYPE:parameter"` — so a producer
  that covers return/parameter types but not variable/typedef-underlying
  types (a real, ADR-noted clang-plugin gap) can honestly report partial
  coverage per role instead of one blanket family flag.
- `EntityResolver`: canonical identity keyed on the clang USR when available,
  with `aliases: [old_v1_id, mangled_symbol, qualified_name, signature_hash,
  source_location]` — resolves binary symbol / header declaration / source
  definition / debug type / consumer import / template instantiation to one
  entity. `SOURCE_GRAPH_VERSION = 2`; a v2 reader accepts v1 IDs as aliases so
  existing collected packs keep working. **Deliberately deferred** — see
  above.
- A common `TraversalPolicy` (`allowed_edges`, `stop_conditions`,
  `effect_transitions`, `minimum_confidence`) formalizes the five traversal
  shapes the review distinguishes (layout/symbol-availability/source-contract/
  behavioral/deployment propagation) instead of leaving "don't walk through an
  ordinary out-of-line helper" as one detector's implicit knowledge
  (`is_consumer_compiled_public_entry` today). All four fields are
  implemented and wired: `allowed_edges`/`stop_conditions`/
  `minimum_confidence` reused by `compute_call_graph_leak_paths` via the
  named `CALL_GRAPH_TRAVERSAL_POLICY` instance; `effect_transitions` maps a
  virtual/function-pointer call's `call_kind` to a downgraded
  `"overapprox"` precision label, propagated sticky through
  `_consumer_compiled_reachability`'s `degraded` return set and surfaced as
  an `"overapprox: "` prefix on the affected proof path. Adoption by
  `compute_leak_paths`'s layout walk (a different, non-graph data model)
  remains open.
- Proof-path selection preference order (consumer-proven > exact high-confidence
  path > public-header structural path > multi-producer-confirmed >
  reduced-confidence name resolution > virtual/indirect over-approximation),
  replacing plain shortest-BFS; keep `primary_path`/`alternative_paths[0..N]`/
  `discarded_path_count` on the finding. Two selectors implement different
  slices of the six tiers, split by how much per-hop structure their walk's
  path representation carries: `internal_leak.select_preferred_path` (the
  layout walk's plain `list[str]` paths) covers 2 tiers (exact,
  virtual/indirect); `buildsource.graph_impact.select_preferred_graph_path`
  (a structured `list[GraphEdge]` path — real per-edge confidence, fact-
  producer count, node visibility) covers 4 tiers (exact, public-header
  structural, multi-producer-confirmed, and a reduced-confidence residual),
  wired into `source_graph_findings.py`'s `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`
  producer in place of its own `min(..., key=len)`. The
  `primary_path`/`alternative_paths`/`discarded_path_count` finding shape is
  on `impact.model.GraphProofPath`, populated by
  `graph_impact.attach_impact_metadata`. Still open: the consumer-proven
  tier (needs Phase 4's consumer graph) and a genuinely finer
  reduced-confidence-name-resolution axis beyond the residual case.

**ADR-046 accepted and implemented** — see the Phase 2 heading above for the
current per-decision status (D1/D2/D3/D5/D6 implemented, D4 deliberately
deferred); this paragraph originally described the pre-implementation
"needs a recorded decision" gate (ADR-044's own bar) before the ADR existed.

### Phase 3 — Reporting & root causes — **slices 1-7 implemented (ADR-052)**

[ADR-052](../adr/052-unified-impact-assessment-model.md) records the slice 1
decisions: `abicheck/impact/model.py`'s `ImpactAssessment`/`GraphProofPath`/
`FindingDecision` dataclasses (a narrower field set than originally planned
below — `changed_entities`/`affected_consumers`/`affected_use_cases`/
`coverage` have no data source yet and are deliberately absent rather than
added as permanently-`None` placeholders) and
`abicheck/impact/engine.py`'s `assess_change`, a **pure read view** built
from the `Change` fields `source_graph_findings.py`/`internal_leak.py`/
`post_processing.py`/`suppression.py`/`appcompat.py` already independently
set — none of those producers changed in slice 1 (see ADR-052 D2: the
plan's originally-stated "existing fields become derived views over
`ImpactAssessment`" direction is *not* implemented yet; this slice derives
the other way, `ImpactAssessment` read from `Change`). `reporter.py`/
`sarif.py` gained `reachability_state` (always present — the tri-state
signal has existed since PR #607 but was never serialized before this,
closing a real gap: `PROVEN_UNREACHABLE` and `UNKNOWN` were previously
indistinguishable in JSON/SARIF, both showing as an absent `public_reachable`
key) and `impact_assessment` (emitted only when it carries information
beyond the all-defaults case). `REPORT_SCHEMA_VERSION` 2.14 → 2.15. Slice 2
closed `FindingDecision.suppression_rule`: `suppression.SuppressionOutcome`
gained `matched_rule`, and the three call sites that move a change into
`DiffResult.suppressed_changes` (`checker._filter_suppressed_changes`/
`_filter_pattern_synthetic`, `post_processing.ApplySuppression`,
`_merge_findings_respecting_suppression`) now stamp `Change.suppression_rule`
from it. Slice 3 added `--report-mode root-cause`: initially JSON-only,
grouping findings by the existing `Change.caused_by_type` field rather than
waiting on Phase 6's `RootCauseCorrelator`. `REPORT_SCHEMA_VERSION` reached
2.15. Slice 4 added the matching markdown/text rendering
(`reporter_markdown._to_markdown_root_cause`) reusing the same grouping
function slice 3's JSON path now also calls (`_group_changes_by_root_cause`).
Slice 5 extended `--report-mode root-cause` to `--format sarif`: rather than
restructuring SARIF's flat one-result-per-finding shape (which would break
every existing SARIF/code-scanning consumer), each result gains additive
`properties.rootCauseId`/`properties.rootCause` computed via the same
`_root_cause_key_and_display` JSON/markdown share. Slice 6 (G29 Phase 2/3
follow-up, after ADR-046 D1/D6 landed) closed two of the four remaining
items: `--format junit` now gets the same additive treatment as SARIF —
`rootCauseId`/`rootCause` attributes on each `<failure>`, `<testcase>` still
grouped by symbol exactly as before (`to_junit_xml`/`to_junit_xml_multi`/
`_build_testsuite` gained a `report_mode` parameter; the actual end-to-end
gap turned out to be `service_render.render_output`'s `"junit"` branch never
forwarding its own `report_mode` argument at all, fixed alongside the JUnit
rendering itself) — and a stable, `description`-independent `occurrence_id`
now exists on `GraphProofPath`, built directly on ADR-046 D1's
`occurrence_id` half (`buildsource.graph_impact._path_occurrence_id` folds a
path's edges' own `GraphEdge.occurrences` into one hash; `None` whenever no
edge on the path carries occurrence-level attrs, still every finding today
since D1's `occurrence_id` stays opt-in with no current producer). Slice 7
(G29 Phase 3 follow-up) closed the remaining per-finding-identifier item:
`root_cause_id`/`root_cause_display`/`impact_group_id` now exist on
`ImpactAssessment` — computed report-wide by
`reporter_markdown.root_cause_lookup_for_changes` (the same
`_root_cause_key_and_display` grouping decision `--report-mode root-cause`
uses) and passed into `assess_change` as a plain parameter, so
`ImpactAssessment` itself stays a pure single-`Change` read view rather than
gaining the ability to see whole-`DiffResult` context on its own.
`impact_group_id` is currently always identical to `root_cause_id` — an
alias, not yet a distinct concept. `REPORT_SCHEMA_VERSION` 2.16 → 2.17.
**Still open under this same ADR**: the D2 direction flip (deliberately not
attempted — touches five producer modules' core control flow at once,
several of them performance-sensitive graph walks under active
suppression-safety guarantees; see ADR-052's "Deliberately not implemented"
section) and the full `RootCauseCorrelator`-based correlation across
consumer-overlay findings with no `caused_by_type` link (Phase 6) — which is
also what would ever make `impact_group_id` diverge from `root_cause_id`.
The two reference docs below now exist (Slice 6 gave them enough real
surface to be worth writing, closing what ADR-052 originally called
premature) — the rest of this list is the original Phase 3 scope this
section describes, most of it still open:

- `abicheck/impact/model.py`: `ImpactAssessment` (`reachability_state`,
  `contract_effect`, `changed_entities`, `public_entries`, `proof_paths`,
  `affected_consumers`, `affected_use_cases`, `coverage`, `confidence`,
  `root_cause_id`, `decision`), `GraphProofPath` (root/target/effect/confidence/
  steps, each step typed with edge kind, consumer-compiled flag, provenance,
  location), `FindingDecision` (state/reason_code/suppression_rule/demotion).
  **Implemented, narrower than this original list**: `reachability_state`,
  `confidence`, `decision`, `proof_path` (singular — `root`/`target`/
  `is_direct`/`steps`/`prose`, plus Slice 6's `occurrence_id`, ADR-046 D6's
  `alternative_paths`/`discarded_path_count`), and — Slice 7 —
  `root_cause_id`/`root_cause_display`/`impact_group_id`. **Still absent**:
  `contract_effect`/`changed_entities`/`public_entries`/`affected_consumers`/
  `affected_use_cases`/`coverage` — no data source yet (Phase 4/5), left out
  entirely rather than added as permanently-`None` placeholders.
- `source_graph_findings.py`, `internal_leak.py`, `suppression.py`,
  `appcompat.py` populate `ImpactAssessment` instead of independently setting
  overlapping `Change` fields; the existing `public_reachable`/
  `reachability_kind`/`reachability_proof_path`/`reachability_state` fields
  become **derived, backward-compatible views** over it (no JSON/SARIF
  breaking change). **Still open** — this is the D2 direction flip above;
  none of Slices 1-6 attempt it.
- `reporter.py`/`sarif.py`: structured `impact` object in JSON (**done**,
  `impact_assessment`), `codeFlows`/`threadFlows` in SARIF (**not done** —
  SARIF's root-cause mode is additive `properties.rootCauseId`/`rootCause`
  instead, not a `codeFlows` restructuring; keep `properties.reachabilityProofPath`
  as a derived string for old consumers — **done**).
- `--report-mode root-cause`: groups findings sharing a root cause — **done**
  for JSON/markdown/text/SARIF/JUnit (Slices 3-6), all keyed on the existing
  `caused_by_type` field; extending the grouping to cover consumer-overlay
  findings with no `caused_by_type` link at all still needs
  `RootCauseCorrelator` in Phase 6.
- Stable `finding_id` (structured discriminator — parameter index, member ID,
  graph entity ID — not `description` text, so a wording change or a new
  proof path doesn't change identity): **not implemented, and not planned as
  originally described** — `reporter._finding_id` already exists (schema
  2.3) and is stable across runs, but deliberately keeps `description` as a
  discriminator (changing that would break an already-published field's
  values). `occurrence_id`: **done** (Slice 6, above). `root_cause_id`/
  `root_cause_display`/`impact_group_id`: **done** (Slice 7, above) — as
  report-level-resolved fields passed into `assess_change`, not computed by
  `ImpactAssessment` from a single `Change` in isolation.
- `docs/reference/source-graph-schema.md` (new): the ADR-046 D1-D6 identity/
  merge/traversal-policy/proof-path-preference schema — **done**.
  `docs/contribute/detector-impact-contract.md` (new): the required-evidence
  contract every new detector from Phase 5/6 must declare — **done**, ahead
  of Phase 5/6 themselves, since D5/D6/Slice 6 already provide enough real
  machinery (`TraversalPolicy`, `select_preferred_graph_path`,
  `attach_impact_metadata`) for the contract to point at working code rather
  than aspirational surface.

### Phase 4 — Consumer / use-case join

- `abicheck/impact/consumer_graph.py`: promotes `AppRequirements`
  (`appcompat.py`) to graph facts — `consumer_binary`/`consumer_object`/
  `consumer_required_symbol`/`runtime_probe` nodes,
  `CONSUMER_REQUIRES_SYMBOL`/`CONSUMER_REQUIRES_VERSION`/
  `CONSUMER_INSTANTIATES_DECL`/`CONSUMER_COMPILED_FROM_HEADER`/
  `RUNTIME_FAILED_TO_RESOLVE_SYMBOL` edges. Joins with
  `SOURCE_DECL_MAPS_TO_SYMBOL` so a `CONSUMER_REQUIRED_SYMBOL_REMOVED` finding
  can report *why* — e.g. "`training-service` requires
  `detail::train_ops_dispatcher` because its call graph reaches it from public
  `train()`" — not just "requires missing symbol X".
- `abicheck/impact/use_cases.py` + optional `impact-use-cases.yaml` manifest
  (`use_case`/`entrypoints`/`tests`); `use_case`/`test_case` graph nodes,
  `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE`/`TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` edges. Explicitly a **separate schema/file** from
  `docs/contribute/usecase-registry.yaml` (that registry tracks abicheck's
  *own* feature coverage — reusing it for a project's business use cases would
  conflate "abicheck supports header-only analysis" with "the DAL training
  workflow uses `train()`", per the review's own caution).
- `docs/use/use-case-impact.md` (new): manifest format, entrypoint
  mapping, test association, trace ingestion, declared-vs-observed use,
  full-library-vs-consumer-scoped verdict semantics (absence of a trace must
  never read as "not used").

### Phase 5 — New semantic graph families

In review-stated priority order:

1. **Template instantiation**: `DECL_INSTANTIATES_TEMPLATE`,
   `TEMPLATE_USES_DECL`/`TEMPLATE_USES_TYPE`, `INSTANTIATION_EMITS_SYMBOL`,
   `INSTANTIATION_MAPS_TO_EXPORT`, `DECL_USES_DEFAULT_TEMPLATE_ARG`,
   `CONSTRAINT_DEPENDS_ON_DECL` — closes the "public template → concrete
   instantiation → internal specialization → emitted exported symbol →
   consumer requirement" chain.
2. **Macro/config dependency**: `DECL_USES_MACRO`, `MACRO_EXPANDS_TO_VALUE`/
   `MACRO_EXPANDS_TO_TYPE`, `MACRO_CONTROLS_DECL`/`MACRO_CONTROLS_EDGE`, each
   edge carrying a configuration condition (`_WIN32`, feature flags).
3. **Virtual dispatch**: `DECL_OVERRIDES_DECL`, `VIRTUAL_CALL_MAY_DISPATCH_TO`
   (explicitly `overapprox`, never `exact`), `VTABLE_SLOT_MAPS_TO_DECL`,
   `TYPE_HAS_VTABLE` — distinguishes "the vtable slot provably changed" from
   "the possible runtime dispatch target set changed".
4. **Callback/function-pointer**: `DECL_TAKES_ADDRESS_OF`,
   `DECL_REGISTERS_CALLBACK`, `CALLBACK_MAY_INVOKE`,
   `FUNCTION_POINTER_HAS_SIGNATURE` — closes the plugin/event-loop/C-API
   callback blind spot the review calls out.
5. **Full type-role coverage** to parity: variable type, typedef target,
   alias-template target, enum underlying type, non-type template argument,
   default template argument, concept/constraint dependency, function-pointer
   signature, member-pointer type — feeds the Phase 2 per-role coverage
   matrix.
6. **Object/link provenance**: a real `ar`/`nm`-style extractor for the
   currently schema-only `ARCHIVE_CONTAINS_OBJECT`/`OBJECT_DEFINES_SYMBOL`
   edges, so a removed-symbol finding can localize to
   "`cache_dispatch.o` in `libinternal_dispatch.a`".

### Phase 6 — New detectors, examples, FP gates

Per the review, the goal is **not** a new `ChangeKind` per graph edge (the
registry is already large) — raw contract change stays separate from
impact/composition evidence. Minimal new user-facing set:

| Detector | Classification |
|---|---|
| `PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED` | `API_BREAK`/`RISK`; `BREAKING` only with artifact/consumer proof |
| `PUBLIC_TEMPLATE_INSTANTIATION_TARGET_CHANGED` | source risk or consumer-proven break |
| `PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED` | `RISK`, correlated with existing vtable findings |
| `PUBLIC_MACRO_CONTRACT_CHANGED` | `API_BREAK` or behavioral `RISK` |
| `PUBLIC_CALLBACK_TARGET_CHANGED` | `RISK`; break only with proven signature/symbol mismatch |
| `GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION` | quality/coverage diagnostic (the Phase 1 `SUPPRESSION_REACHABILITY_UNKNOWN` already covers the suppression-specific case this generalizes) |
| `CONSUMER_IMPACT_PATH_CONFIRMED` | impact overlay on an existing raw break, not a new raw break |
| `USE_CASE_IMPACT_CONFIRMED` | report-level impact, not a new ABI `ChangeKind` |

Plus a `RootCauseCorrelator` composer (not a detector) that groups
`FUNC_REMOVED`/`INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API`/
`CONSUMER_REQUIRED_SYMBOL_REMOVED`/`RUNTIME_LOAD_FAILED` into one root cause
with per-piece evidence levels (feeds Phase 3's `root_cause_id`).

New examples (each needs a negative twin, per the review):

| Case | Scenario |
|---|---|
| `case194` | Real consumer compiles a public inline wrapper requiring an internal exported dispatcher — full `consumer → symbol ← public entry` proof |
| `case195` | Public template instantiates a removed internal specialization |
| `case196` | Internal type as field by-value vs. pointer — value path blocks suppression, pointer-only doesn't |
| `case197` | Stable public virtual call, changed override set — over-approx proof, no false `BREAKING` |
| `case198` | Macro/default-argument change, export table identical — source/behavioral finding, no binary-break claim |
| `case199` | Public registration API holds a function pointer to an internal callback |
| `case200` | Old-side graph partial/degraded — `UNKNOWN`, finding stays, coverage diagnostic (already exercised at the `reachability_state` level by Phase 1's tests; this case exercises the full `compare` pipeline end to end) |
| `case201` | Old side header-only, new side full source graph — no false "dependency added" from a collector upgrade |
| `case202` | One dispatcher feeds two use cases but not a third — root-cause grouping and exact blast radius |
| `case203` | Consumer/use case don't require the changed branch — scoped verdict compatible, full-library verdict unchanged |
| `case204` | Mangled/qname/USR identity forms of one entity — stable graph join, no duplicate nodes (Phase 2) |
| `case205` | Removed symbol localized to its object/archive member (Phase 5 item 6) |

New CI gates (extend the existing FP-rate/tier-accuracy/mutation pattern):
false-positive-rate additions for the new detectors, collector-upgrade
stability (case201-shaped), suppression-safety regression (the Phase 1
`test_reachability_state.py` suite is the seed), proof-path JSON-schema
validation, consumer/use-case attribution checks.

## Files & surfaces

New:
```text
abicheck/buildsource/graph_facts.py  # GraphFact/FactConflict/merge, relation_key/occurrence_id (Phase 2 D1/D2, DONE)
abicheck/buildsource/graph_impact.py  # select_preferred_graph_path, attach_impact_metadata, _path_occurrence_id (Phase 2 D6/ADR-052 Slice 6, DONE — landed here, not under impact/)
abicheck/internal_leak.py   # TraversalPolicy + effect_transitions (Phase 2 D5, DONE — landed here, not a separate impact/traversal.py)
abicheck/impact/
    model.py           # ImpactAssessment, GraphProofPath, FindingDecision (Phase 3 slices 1/7, DONE — ADR-052)
    engine.py           # assess_change(...) (Phase 3 slices 1/7, DONE — ADR-052)
    correlation.py       # RootCauseCorrelator (Phase 6, not started)
    root_causes.py
    consumer_graph.py    # Phase 4
    use_cases.py         # Phase 4
docs/learn/impact-analysis.md          # Phase 3 slices 1/6/7, DONE (Phase 4 join still open)
docs/reference/source-graph-schema.md     # Phase 2 D1-D6 identity/merge/traversal-policy schema, DONE
docs/learn/graph-coverage.md           # Phase 1, DONE
docs/use/use-case-impact.md        # Phase 4
docs/contribute/detector-impact-contract.md  # DONE, ahead of Phase 5/6 themselves — see Phase 3 section above
examples/case194.../case205.../           # Phase 6
```

Modified (recurring across phases): `abicheck/buildsource/source_graph.py`,
`source_graph_findings.py`, `internal_leak.py`, `post_processing.py`,
`suppression.py`, `appcompat.py`, `reporter.py`, `sarif.py`,
`junit_report.py`, `service_render.py`, `change_registry*.py`,
`checker_policy.py`, `checker_types.py`.

## Tests

- `tests/test_reachability_state.py` — Phase 1, done.
- `tests/test_source_graph_v2.py` — Phase 2 D1/D2 (including
  `TestOccurrenceId`), done.
- `tests/test_internal_leak.py`'s `TestTraversalPolicy`/`TestSelectPreferredPath` — Phase 2 D5/D6, done.
- `tests/test_internal_leak_effect_transitions.py` — Phase 2 D5's
  `effect_transitions`, done (split out to stay under the line-count cap).
- `tests/test_graph_impact.py`'s `TestSelectPreferredGraphPath`/
  `TestAttachImpactMetadataAlternatives`/`TestPathOccurrenceId` — Phase 2 D6's
  structured-path selector and ADR-052 Slice 6's `occurrence_id`, done.
- `tests/test_impact_model.py` — Phase 3 slice 1, done.
- `tests/test_junit_report_root_cause.py` — Phase 3 Slice 6's JUnit
  root-cause rendering, done (split out from `test_junit_report.py`).
- `tests/test_reporter.py::TestImpactAssessmentRootCause` /
  `tests/test_sarif.py::TestImpactAssessmentRootCause` — Phase 3 Slice 7's
  per-finding `root_cause_id`/`root_cause_display`/`impact_group_id`, done.
- New per remaining phase: `tests/test_entity_resolver.py` (Phase 2 D4, not
  planned — see D4's deliberate-deferral note above),
  `tests/test_consumer_graph.py` / `tests/test_use_cases.py` (Phase 4), one
  `test_diff_<family>.py` per Phase 5 graph family,
  `tests/test_root_cause_correlator.py` (Phase 6).
- `tests/test_abi_examples.py` picks up `case194`-`case205` automatically once
  `ground_truth.json` is updated (existing harness, no new test file needed).

## Effort & risk

Phased XL; each phase is independently shippable and additive (mirrors how L3-L5
evidence already never overrides L0-L2 authority). Highest risk items: Phase 2's
identity/version bump (needs its own ADR + a careful v1-alias migration test),
Phase 5's virtual-dispatch over-approximation (must never fabricate a `BREAKING`
from a possible-target-set change alone), and Phase 6's detector count growth
(mitigated by the "composer, not detector, for aggregation" split the review
itself insists on).

## Out of scope

Deferred by the original review, not attempted here either:

- A maintained devcontainer image baking in castxml/libabigail/abi-compliance-checker
  (pixi already solves "one command, working environment" without the
  image-maintenance burden).
- A trend-reporting database persisting `check_tier_accuracy.py`/`check_fp_rate.py`/
  mutation-score history across runs (needs a storage/retention decision
  first).
- A full behavioral baseline / task-suite leaderboard beyond `agent-evals/`'s
  current one-task harness (should grow from real usage, not be
  speculatively built).
