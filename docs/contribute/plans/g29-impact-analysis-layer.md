# G29 — Impact-Analysis Layer: Unified Graph-Driven Impact Model

**Origin:** External impact-analysis-layer architecture review (2026-07) —
audited how far the optional L5 source/call/type graph (ADR-031, ADR-044) has
grown into a real decision-making layer (version-over-version graph diff,
public reachability, suppression gating, consumer scoping, proof paths) versus
where it still stops short of a unified model. Phase 1's P0 slice is
implemented ([PR #607](https://github.com/abicheck/abicheck/pull/607)); Phase
2 (ADR-046), Phase 3 (ADR-052) and Phase 4's first slice (ADR-057) have since
landed, and the rest of Phases 4–6 is the remainder of the review's roadmap,
scoped below.
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
- **G29.2** (Phase 3, **slices 1-10 done, ADR-052**) — A single
  `abicheck/impact/` package with `ImpactAssessment`, `GraphProofPath`, and
  `FindingDecision` dataclasses. **Slices 1-7 implement the read-view
  direction only**: the dataclasses exist and `reporter.py`/`sarif.py`/
  `junit_report.py` (Slice 6) surface them (including the suppression audit
  trail, slice 2, and `--report-mode root-cause` grouping in JSON,
  markdown/text, SARIF properties, and — Slice 6 — additive JUnit `<failure>`
  attributes). **Slices 8-10 deliver the D2 direction flip**:
  `internal_leak.py` (Slice 8) and `appcompat.py` (Slice 9) populate
  `Change.impact_assessment` directly for their single-purpose finding
  builders (each verified safe by its own audit — a pipeline-ordering audit
  for `internal_leak.py`, confirmation that `suppression.evaluate()` is a
  pure read for `appcompat.py`); Slice 10 closes the remaining two named
  producer sites, each for a documented, code-inspected reason (see ADR-052's
  "Slice 10" and "Deliberately not implemented this slice" sections for the
  full detail):
  - `post_processing.MarkReachability` — the open measurement question is
    **resolved, migrated**: a real instrumented `compare --format json
    --secondary-format sarif` run (`tests/test_cli_unit.py::
    TestCompareSecondaryFormat::test_json_then_sarif_secondary_calls_assess_change_twice_per_change`)
    confirmed `assess_change()` is genuinely called more than once for the
    same `Change` object within one process (`reporter.py`'s JSON path and
    `sarif.py`'s SARIF path each call it independently over the identical,
    already-computed `DiffResult`). `MarkReachability` now caches
    `impact_assessment` right after it finalizes each change's reachability
    fields — confirmed (fresh repo-wide grep, not carried over from Slice 8's
    claim) to be the only step that mutates those fields on an existing
    `Change`, so nothing downstream invalidates the cache.
  - `source_graph_findings.py` — re-audited: none of its `Change(...)`
    construction sites are individually cacheable at construction time,
    unlike `internal_leak.py`'s builder (itself a later `DEFAULT_PIPELINE`
    step) — these builders' output is merged into `checker.compare`'s
    `changes` *before* `_run_post_processing`/`DEFAULT_PIPELINE.run()`, so
    `MarkReachability` still runs downstream of them and would invalidate an
    eagerly-cached assessment. Each site got a brief comment documenting
    this instead of a (would-be-wrong) construction-time cache write — but
    the practical gap is closed anyway: `MarkReachability`'s own new caching
    (above) reaches every `source_graph_findings.py` finding too, once it's
    tagged. See the Phase 3 section below ("Slice 10") for the exact
    per-function site count and breakdown.

  A third entry D2's original decision text named, `suppression.py`,
  contains **no** `Change(...)` construction at all (confirmed by direct
  search, unchanged from Slice 8/9's finding) — the diagnostic construction
  near it (`SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`) actually lives in
  `post_processing.py` and carries no reachability evidence to cache. **G29
  Phase 3 Slice 11 resolved this**, the one item from D2's original scope
  still open after Slice 10: `suppression.py` mutates no `Change` field
  anywhere in the module (confirmed by grepping every assignment target),
  so it was never a producer to migrate — the mutation D2's text was
  naming is `checker._filter_suppressed_changes`/
  `post_processing.ApplySuppression`, already migrated in Slice 2, and
  already covered by Slice 10's `MarkReachability` caching (which runs
  earlier in `DEFAULT_PIPELINE`, ADR-044 D1's ordering, so every `Change`
  those callers see already carries a cached `impact_assessment`). D2's
  text conflated "the suppression subsystem" (the caller sites) with "the
  `suppression.py` module" (a pure predicate engine with nothing to
  migrate, by design — see ADR-052 Slice 11 for the full resolution).
- **G29.3** (Phase 2, **D1-D6 all done, ADR-046**) — Graph core v2:
  relation/occurrence identity split (**done**), an evidence-preserving
  (order-independent) node/edge merge (**done**), a per-kind/per-role
  coverage matrix (**done**, extending `extractor_passes` beyond the two
  families Phase 1 already consults), and a USR-based canonical
  `EntityResolver` with `SOURCE_GRAPH_VERSION = 2` (v1 IDs kept as aliases —
  no forced re-collection) — **done, as a deliberately scoped subset**:
  [ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md)'s
  `entity_identity.CanonicalIdentity` is the resolution source
  `EntityResolver.resolve` reuses; changing `GraphNode.id` generation itself
  across every graph producer stays out of scope (still the "materially
  larger" rewrite that would need its own design pass).
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
- **G29.5** (Phase 4, **slices 1-2 done, ADR-057**) — A consumer graph
  (`CONSUMER_REQUIRES_SYMBOL`, `CONSUMER_REQUIRES_VERSION`, …) that joins
  with the source graph so a `CONSUMER_REQUIRED_SYMBOL_REMOVED` finding can
  name the public entry point that produced the dependency — **done**
  (`abicheck/impact/consumer_graph.py`; the join is one shared
  `binary_symbol://` node id, not a parallel node kind, and the walk reuses
  ADR-046 D5's `CALL_GRAPH_TRAVERSAL_POLICY` rather than a fresh BFS). This
  also closed ADR-046 D6's tier 1 ("consumer-proven"), which had been
  unreachable since it was written. **Slice 2 also done**: the optional
  `impact-use-cases.yaml` manifest (declared entrypoints/tests, explicitly
  **not** a reuse of `usecase-registry.yaml`) — `abicheck/impact/use_cases.py`
  parses the manifest and builds/joins `use_case`/`test_case` nodes and
  `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE` edges onto the library graph,
  mirroring slice 1's build/join API and mutation-safety discipline.
  **Update (2026-09-02, also done):** `abicheck/impact/use_case_impact.py`'s
  `build_use_case_impact` is a further real step past graph-building alone
  — `compare --use-cases MANIFEST` is a real flag on the native `compare`
  command, and it attributes a comparison's changed symbols to the declared
  use cases whose entrypoints reach them as a genuine report-level
  `DiffResult.use_case_impact` surface (emitted through the JSON/markdown/
  text/review formats; see ADR-057). Still open: best-effort runtime-trace
  ingestion, and a per-finding, report-*schema* `Change.affected_use_cases`
  field / `USE_CASE_IMPACT_CONFIRMED` finding kind wired into `compare`'s
  own change objects/exit code (G29 Phase 6) — the reserved edge kinds
  ADR-057 registers (`CONSUMER_INSTANTIATES_DECL`/
  `CONSUMER_COMPILED_FROM_HEADER`/`RUNTIME_FAILED_TO_RESOLVE_SYMBOL`/
  `TRACE_OBSERVED_ENTRY`/`TRACE_OBSERVED_EDGE`) mark where that work
  attaches.
- **G29.6** — The five open graph families (template instantiation, virtual
  dispatch, macro/config, callback/function-pointer, object/archive link
  provenance) implemented behind the same coverage-honesty discipline as the
  existing call/type graph (narrowed/degraded flags, `extractor_passes`).
  **Object/archive link provenance is done** (`archive_graph.py`,
  `extractor_passes["archive_graph"]`/`degraded_passes["archive_graph"]` —
  see Phase 5 item 6 above). **Template instantiation is done**
  (`template_graph.py`, `extractor_passes["template_graph"]`/
  `degraded_passes["template_graph"]` — see Phase 5 item 1 above), for the
  four populated edge kinds (`TEMPLATE_USES_DECL` closed as a follow-up,
  see item 1's own entry above); the three remaining reserved ones
  (`INSTANTIATION_MAPS_TO_EXPORT` — intentionally never populated, see
  item 1 — `DECL_USES_DEFAULT_TEMPLATE_ARG`, `CONSTRAINT_DEPENDS_ON_DECL`)
  stay open, each its own follow-up. **Macro/config dependency is
  done** (`macro_graph.py`, `extractor_passes["macro_graph"]`/
  `degraded_passes["macro_graph"]` — see Phase 5 item 2 above), for the two
  populated edge kinds (`MACRO_CONTROLS_DECL`/`DECL_USES_MACRO`); the three
  reserved ones (`MACRO_EXPANDS_TO_VALUE` etc.) remain open, each its own
  follow-up. **Virtual dispatch is partially done**
  (`virtual_dispatch_graph.py`, `extractor_passes["virtual_dispatch_graph"]`
  — see Phase 5 item 3 above): `VIRTUAL_CALL_MAY_DISPATCH_TO`/
  `TYPE_HAS_VTABLE` are populated (needing no new clang invocation at all —
  a pure transform over already-folded call/type/override graph state);
  `DECL_OVERRIDES_DECL` needed no producer (already satisfied by
  `override_graph.py`'s existing confirmed-override edges) and
  `VTABLE_SLOT_MAPS_TO_DECL` remains reserved (needs a real per-slot Itanium
  layout model, deliberately not attempted). **Callback/function-pointer is
  done, for three of the four vocabulary members** (`callback_graph.py`,
  `extractor_passes["callback_graph"]` — see Phase 5 item 4 above):
  `DECL_REGISTERS_CALLBACK`/`DECL_TAKES_ADDRESS_OF` are populated via a new
  Clang AST pass, and `CALLBACK_MAY_INVOKE` is populated via a pure join
  (needing no new clang invocation) over `call_graph.py`'s already-folded
  function-pointer-kind `DECL_CALLS_DECL` edges;
  `FUNCTION_POINTER_HAS_SIGNATURE` is registered vocabulary with no edge
  producer — a real, investigated gap (unlike `DECL_OVERRIDES_DECL` above),
  but populated instead as a `function_pointer_signature` node-level fact on
  the slot's own `source_decl` node, since a signature is a property of one
  declaration, not a relation between two.
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

### Phase 2 — Graph core v2 — **ADR accepted; D1-D6 all implemented (D4 scoped)**

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
`SOURCE_GRAPH_VERSION = 2`) is now implemented too, as a deliberately
*scoped* subset of the originally sketched decision** — see ADR-046's "D4
implementation" section: `abicheck/buildsource/entity_resolver.py`'s
`EntityResolver` reuses `entity_identity.CanonicalIdentity`
([ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md),
G31 Phase B, shipped after ADR-046 was written) as its resolution source,
recording `aliases[v1_id] = canonical_id` rather than replacing
`GraphNode.id` generation itself. `SOURCE_GRAPH_VERSION` bumped 1 → 2 as a
signal (nothing branches on it), populated only when a caller opts in via
`SourceGraphSummary.resolve_entities()` — a v1 pack with no
`entity_resolver` key still loads and compares correctly with no forced
re-collection. What stays out of scope, and why, is exactly what the
original deferral flagged as the risky part: changing `GraphNode.id`
generation itself across every graph producer plus a v1/v2
*identity-level* compatibility matrix (not just the pack-loading
compatibility this implementation already provides) — categorically larger
and riskier than any slice landed in this phase, still deserving its own
scoped design pass if ever attempted. Two narrower items D5/D6 named as
open have since resolved differently: D6's "consumer-proven" tier is
**done** for `select_preferred_graph_path` (G29 Phase 4, ADR-057, reads the
consumer graph straight off the structured-path selector's own `graph`
argument) — it remains out of scope only for `select_preferred_path`'s
plain `list[str]` layout-walk paths, for a structural reason (no per-hop
node identity to test), not an evidence gap. D5's `TraversalPolicy`
adoption on the layout walk and D6's genuinely finer
"reduced-confidence name resolution" axis (tier 5, beyond the residual
case) were both re-investigated (2026-08) and confirmed to need more than
evidence to close: the layout walk has no `allowed_edges`/`stop_conditions`
analogue at all (it walks every base/field/typedef target unconditionally,
by design), and the six-tier order is fixed by this accepted ADR with no
slot to insert a finer axis into without an amendment — see ADR-046's D5/D6
implementation sections for the full evidence.
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
  existing collected packs keep working. **Implemented, scoped** — see
  above: `entity_resolver.EntityResolver.resolve(node) -> canonical_id`,
  `aliases`/`conflicts`, opt-in via `resolve_entities()`. The originally
  listed richer alias tuple (mangled symbol/qualified name/signature hash/
  source location as *separate* alias entries, not just the one canonical
  id) is narrower in the shipped version — `EntityResolver.aliases` maps
  `v1_id -> canonical_id` only; the finer-grained alias set is what
  `entity_identity.CanonicalIdentity.aliases` (which `EntityResolver.resolve`
  already reads from) itself carries, one level down, for a caller that
  needs it.
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
  producer count, node visibility) covered 4 tiers at the time this was
  written (exact, public-header structural, multi-producer-confirmed, and a
  reduced-confidence residual); **now covers 5** — G29 Phase 4 (ADR-057)
  wired the consumer-proven tier into the same selector once the consumer
  graph existed to read (see G29.5 below), reading the required-node set
  straight off its own `graph` argument, inert for every run without
  `--used-by`. Wired into
  `source_graph_findings.py`'s `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`
  producer in place of its own `min(..., key=len)`. The
  `primary_path`/`alternative_paths`/`discarded_path_count` finding shape is
  on `impact.model.GraphProofPath`, populated by
  `graph_impact.attach_impact_metadata`. Still open: only a genuinely finer
  reduced-confidence-name-resolution axis beyond the residual case
  (investigated, needs an ADR amendment to insert a new tier — see
  ADR-046's D6 implementation section).

**ADR-046 accepted and implemented** — see the Phase 2 heading above for the
current per-decision status (D1-D6 all implemented, D4 as a deliberately
scoped subset); this paragraph originally described the pre-implementation
"needs a recorded decision" gate (ADR-044's own bar) before the ADR existed.

### Phase 3 — Reporting & root causes — **slices 1-10 implemented (ADR-052)**

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
alias, not yet a distinct concept. `REPORT_SCHEMA_VERSION` 2.16 → 2.19 (2.17
and 2.18 went to ADR-050 D2's comparability-gate work and the P0
evidence-provider audit's `"unattributed"` status respectively, both merged
to `main` first — see `abicheck/schemas/__init__.py`'s version-history
docstring).
Slices 8-9 (G29 Phase 3 follow-up) then delivered the D2 direction flip as a
deliberately *scoped* subset: `Change.impact_assessment` (new, additive
field) is populated directly by two producers — `internal_leak.py`'s two
leak-finding builders (Slice 8), verified safe by an explicit
pipeline-ordering audit (`post_processing.MarkReachability` is the only step
that mutates a `Change`'s reachability/evidence fields, and it runs *before*
these findings are even constructed); and `appcompat.py`'s one
consumer-overlay builder (Slice 9), verified safe by confirming
`suppression.evaluate()`/`matches()`/`would_withhold()` are pure reads of the
`Change` passed in — with `impact.engine.assess_change` reusing the cached
evidence for both while always recomputing `decision`/`root_cause_id` fresh.
Slice 10 (G29 Phase 3 follow-up) then closed both remaining named producer
sites, each resolved by a real audit rather than an assumption:
- `post_processing.MarkReachability` — the open measurement question is
  **resolved, migrated**. `tests/test_cli_unit.py::
  TestCompareSecondaryFormat::test_json_then_sarif_secondary_calls_assess_change_twice_per_change`
  instruments `assess_change` and runs a real `compare --format json
  --secondary-format sarif` invocation, confirming the same `Change` object
  is assessed twice in one process (`reporter.py`'s JSON path, `sarif.py`'s
  SARIF path — both read the identical, already-computed `DiffResult`).
  `post_processing_reachability.py`'s `MarkReachability.run()` now caches
  `impact_assessment` right after finalizing each change's reachability
  fields (all three per-change exit paths), re-confirming via a fresh
  repo-wide grep (not carried over from Slice 8's own claim) that it is
  still the only step that mutates those fields on an existing `Change`.
- `source_graph_findings.py` — re-audited: **ten** separate `Change(...)`
  construction sites across nine finding functions
  (`_mapping_drift_findings`, `_public_reachability_findings` ×2,
  `_generated_public_closure_findings`, `_call_reachability_findings`,
  `_include_graph_drift_findings`, `_build_option_reach_findings`,
  `_internal_dependency_findings`, `_target_dependency_findings`,
  `_symbol_owner_findings`). None are safe to cache at construction time —
  unlike `internal_leak.py`'s builder (a later `DEFAULT_PIPELINE` step),
  these builders' findings are merged into `checker.compare`'s `changes`
  *before* `_run_post_processing`/`DEFAULT_PIPELINE.run()`, so
  `MarkReachability` still runs downstream and would invalidate an
  eagerly-cached assessment. Each site got a brief comment recording this
  instead of a construction-time cache write; `MarkReachability`'s own new
  caching reaches every one of these findings anyway, once tagged.

A third entry the original decision text named, `suppression.py`, is
**resolved (G29 Phase 3 Slice 11)** rather than a producer site: direct
search found no `Change(...)` construction *or mutation* anywhere in this
module — it only reads `Change` fields for rule matching, returning a
`SuppressionOutcome` the caller acts on. The nearby diagnostic
(`SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`) actually lives in
`post_processing.py`. The mutation D2's original text was naming happens at
the caller — `checker._filter_suppressed_changes`/
`post_processing.ApplySuppression` — already migrated in Slice 2, and
already covered by Slice 10's `MarkReachability` caching (which runs
earlier in the pipeline, so every `Change` those callers see already
carries a cached `impact_assessment`). D2's text conflated the suppression
*subsystem* (the callers) with the `suppression.py` *module* (a pure
predicate engine, correctly untouched) — see ADR-052 Slice 11 for the full
resolution. This closes the last item from D2's original five-producer
scope.

Also still open: the full `RootCauseCorrelator`-based correlation across
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
- `source_graph_findings.py`, `internal_leak.py`, `post_processing.py`,
  `appcompat.py` populate `ImpactAssessment` instead of independently setting
  overlapping `Change` fields; the existing `public_reachable`/
  `reachability_kind`/`reachability_proof_path`/`reachability_state` fields
  become **derived, backward-compatible views** over it (no JSON/SARIF
  breaking change). **Partially done (Slices 8-10)**: `internal_leak.py` and
  `appcompat.py` construct `Change.impact_assessment` directly for their
  finding builders (Slices 8-9); `post_processing.MarkReachability` now
  caches it for every change it tags (Slice 10), which transitively covers
  `source_graph_findings.py`'s findings too, without any of its own ten
  construction sites caching directly (found unsafe by Slice 10's audit —
  see above). The flat fields stay as real fields (not converted to derived
  properties) rather than the originally-described full flip, since that
  conversion touches every existing `Change(...)` construction site
  repo-wide and was judged out of scope for a verifiably-safe slice.
  `suppression.py`'s D2 role — the one remaining item from D2's original
  scope — **is resolved (Slice 11, above)**: the module has no `Change`
  construction/mutation site to migrate.
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

### Phase 4 — Consumer / use-case join — **slices 1-2 implemented (ADR-057)**

[ADR-057](../adr/057-consumer-graph-and-impact-join.md) records the slice 1
decisions.

- `abicheck/impact/consumer_graph.py`: promotes `AppRequirements`
  (`appcompat.py`) to graph facts — **done**, with one deliberate deviation
  from the sketch below. `consumer_binary` is populated;
  `consumer_object`/`runtime_probe` are registered but reserved (no
  normalized data source — `AppRequirements` is whole-binary and static);
  and there is **no** `consumer_required_symbol` node kind at all (ADR-057
  D1): a requirement is a `CONSUMER_REQUIRES_SYMBOL` edge onto the *existing*
  `binary_symbol://<symbol>` node, because one shared node id is the whole
  join mechanism — a parallel node kind would have produced two structurally
  similar, completely disjoint graphs needing a later name-matching pass to
  reunite. `CONSUMER_REQUIRES_SYMBOL`/`CONSUMER_REQUIRES_VERSION` are
  populated; `CONSUMER_INSTANTIATES_DECL`/`CONSUMER_COMPILED_FROM_HEADER`/
  `RUNTIME_FAILED_TO_RESOLVE_SYMBOL` are reserved. The vocabulary lives in
  `buildsource/graph_facts.py` (unioned into `source_graph.NODE_KINDS`/
  `EDGE_KINDS`) because `source_graph.py` is at its 2000-line hard cap and
  the producer imports it.
  Joins with `SOURCE_DECL_MAPS_TO_SYMBOL` so a
  `CONSUMER_REQUIRED_SYMBOL_REMOVED` finding reports *why* — "`training-service`
  requires `detail::train_ops_dispatcher` because its call graph reaches it
  from public `train()`" — **done**, wired through `appcompat.scope_diff_to_app`
  onto the overlay finding it already builds, as `affected_public_roots` +
  `impact_proof_path` + a prose `reachability_proof_path` (no new
  `ChangeKind`, no report-schema bump — `impact.engine.assess_change` and
  every reporter already read those fields). The walk reuses
  `internal_leak._consumer_compiled_reachability` under ADR-046 D5's
  `CALL_GRAPH_TRAVERSAL_POLICY` rather than a fresh BFS, so a consumer proof
  path can never contradict an internal-leak one over the same graph.
- ADR-046 D6's **tier 1 ("consumer-proven") is now computable** —
  `graph_impact.select_preferred_graph_path` reads the consumer-required node
  set off the graph it is already given, so the tier needs no new parameter
  and stays inert for every run without `--used-by`. Deliberately narrower
  than "the endpoint is consumer-required": the overapprox check still runs
  first and still wins, so tier 1 means "consumer-proven *and* exactly
  resolved" (ADR-057 D4).
- `abicheck/impact/use_cases.py` + optional `impact-use-cases.yaml` manifest
  (`use_case`/`entrypoints`/`tests`); `use_case`/`test_case` graph nodes,
  `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE`/`TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` edges. Explicitly a **separate schema/file** from
  `docs/contribute/usecase-registry.yaml` (that registry tracks abicheck's
  *own* feature coverage — reusing it for a project's business use cases would
  conflate "abicheck supports header-only analysis" with "the DAL training
  workflow uses `train()`", per the review's own caution). **Done** (slice
  2): `load_use_case_manifest`/`parse_use_case_manifest` (hard-error on a
  malformed document via `UseCaseManifestError`, silent skip on one
  unresolvable entrypoint), `build_use_case_graph`/`join_use_case_graph`
  (deep-copy join, mirroring `consumer_graph`'s slice 1 API/discipline
  exactly). Only `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE` are populated;
  `TRACE_OBSERVED_ENTRY`/`TRACE_OBSERVED_EDGE` stay reserved — **runtime-trace
  ingestion itself is still not implemented**. **Done** (this pass): the
  manifest's first CLI front door, `abicheck project validate-use-cases
  <manifest> [--against <snapshot>]` (`cli_project.py`) — without `--against`
  it only validates the manifest's own structure (malformed document, exit
  64); with it, it resolves each declared use case's entrypoints against a
  real embedded L5 graph and reports resolved vs. unresolved per use case
  (text or `--format json`), reusing the new
  `resolve_use_case_entrypoints()` wrapper around
  `build_use_case_graph`'s own internal join so the CLI can never disagree
  with what the graph actually records. An unresolved entrypoint is never a
  command failure (only a malformed manifest or a graph-less/unreadable
  `--against` snapshot is), matching the manifest format's own
  absence-is-not-evidence discipline. **Done** (this pass, closing the
  "manifest folded into a real diff" gap `use-case-impact.md` named): a new
  `impact.use_cases.explain_use_case_impact(definitions, library_graph,
  symbols)` — for each of a real diff's own changed symbols, which declared
  use case(s)' own resolved entrypoints reach it, via the identical
  restricted call-graph walk (`internal_leak._consumer_compiled_reachability`
  under `CALL_GRAPH_TRAVERSAL_POLICY`) `consumer_graph.
  explain_required_symbols` already uses for `--used-by`, just rooted at the
  manifest's own entrypoints instead of every public entry in the library —
  attributing a change to *every* use case whenever *any* public entry
  reaches it would make the field meaningless. `project
  validate-use-cases`'s new `--against-new <snapshot>` option (requires
  `--against`) is its CLI front door: diffs OLD against NEW via the Tier-2
  `service.compare_snapshots` (same routing every other front end uses,
  `cli-contract` AI-readiness check), then reports which use case each
  resulting change reaches, per use case, text or JSON. Explains against
  **both** sides' own graphs and unions the per-symbol result (Codex
  review, fresh evidence: a symbol added on NEW never existed in OLD's own
  graph at all — e.g. a use case's own entrypoint just introduced — so
  resolving only against OLD's graph silently read every added change as
  unattributed regardless of what NEW's graph could prove; mirrors
  `post_processing_reachability.MarkReachability`'s own `old_paths +
  new_paths` merge for the identical old/new asymmetry). Deliberately a
  **read-only report view**, not a `Change` mutation or a `compare`-native
  surface — no new node/edge, no field set on any `Change` object, no
  schema bump, no effect on any exit code — the same scope boundary D9's
  contract-relevance work drew between "answer the question" and "gate on
  the answer." Two structural limitations carried over unmodified from
  `explain_required_symbols` rather than introduced here: only a
  function/variable-shaped change (backed by a `SOURCE_DECL_MAPS_TO_SYMBOL`
  edge) can be named, never a type layout change; and only a
  consumer-compiled entrypoint can walk transitively past its own
  declaration. **Update (2026-09-02, done):** `compare --use-cases <manifest>`
  is now a real flag on the native `compare` command itself (see the update
  above) — `project validate-use-cases --against-new` remains a separate,
  earlier-built entry point over the same two snapshots, not the only one.
  Still not done: any `Change.affected_use_cases` field, any
  `USE_CASE_IMPACT_CONFIRMED` finding (both G29 Phase 6, needing their own
  schema bump and FP-gate examples), and runtime-trace ingestion.
- `docs/contribute/use-case-impact.md` (new, **done**): manifest format, entrypoint
  mapping, test association, declared-vs-observed use (**trace ingestion
  itself remains unimplemented** — documented honestly as not-yet-built, not
  described as working), full-library-vs-consumer-scoped verdict semantics
  (absence of a trace must never read as "not used").

### Phase 5 — New semantic graph families

In review-stated priority order. Items 1, 2, 5 and 6 are **done**, items 3
and 4 partially (each with named, reserved vocabulary members and a recorded
reason per member); item 6 shipped out of order first since it needed no
compiler frontend, unlike the rest, which all depend on a Clang AST pass —
and items 3 and 5 turned out to need no *new* pass either, item 3 being a
pure transform over already-folded graph state and item 5 an extension of
the existing `type_graph.py` walk.

1. **Template instantiation — done.** `abicheck/buildsource/template_graph.py`
   is a third, independent `clang -ast-dump=json` pass (alongside the call
   and type graph passes), driven by `inline_graph_fold.fold_template_graph`
   whenever they run. Populates `template_decl`/`template_instantiation`
   nodes and `DECL_INSTANTIATES_TEMPLATE`/`TEMPLATE_USES_TYPE`/
   `INSTANTIATION_EMITS_SYMBOL` — closing the "public template → concrete
   instantiation → internal specialization → emitted exported symbol" chain
   for the load-bearing half: an instantiation's own template-argument
   types (via clang's own `decl` cross-reference on the `TemplateArgument`
   node — exact, not a textual heuristic) and its instantiated members'
   emitted symbols (joined onto an existing `binary_symbol` node only,
   ADR-057 D1's "one shared node id is the whole join mechanism" rule,
   reapplied). `TEMPLATE_USES_DECL` was reserved at this point and closed
   as a follow-up — see this item's own dedicated entry below.
   `INSTANTIATION_MAPS_TO_EXPORT`/`DECL_USES_DEFAULT_TEMPLATE_ARG`/
   `CONSTRAINT_DEPENDS_ON_DECL` remain
   reserved, unpopulated — see the module's own docstring (a non-type/
   function-pointer argument needing its own AST verification, redundancy
   with `BINARY_EXPORTS_SYMBOL` on the already-joined symbol node,
   explicit-vs-defaulted argument detection, and C++20 concepts needing a
   separate AST subsystem, respectively — none attempted this slice). See
   `docs/reference/source-graph-schema.md` for the field-level detail,
   including the two load-bearing empirical AST findings (the explicit-
   instantiation detachment quirk, and typedef-alias argument resolution).
   **`TEMPLATE_USES_DECL` — implemented (2026-08, G29 Phase 5 follow-up),
   closing both blockers a prior investigation round found and left
   unimplemented.** Confirmed against real Clang 18: the canonical
   `ClassTemplateSpecializationDecl`'s own `TemplateArgument` child for a
   declaration-valued NTTP (`template <auto Fn> struct Holder;` /
   `Holder<&detail::f>`) is exactly `{"kind": "TemplateArgument", "decl":
   {"id": "...", "kind": "FunctionDecl", "name": "f", "type": {"qualType":
   "void ()"}}}` — no `type`/`value` key at the `TemplateArgument`'s own
   top level. `_template_arg_use` now recognizes this shape directly (a
   top-level `decl` dict), and `_is_opaque_template_argument` no longer
   treats it as opaque (it's distinct from a template-template argument's
   *entirely* bare `{"kind": "TemplateArgument"}`, verified empirically
   against both real shapes). The resolution blocker — `id_to_qname`
   (built by `_index_type_decls` for `_RECORD_DECL_KINDS` only) never
   indexed a `FunctionDecl`/`VarDecl` id — is closed by a **second,
   independent** whole-TU pass, `index_value_decls` (`template_graph_
   value_decls.py`, split out to keep `template_graph.py` under its own
   2000-line hard cap), scoped to free
   functions and namespace-scope variables only (deliberately not
   widening `_index_type_decls` itself, since its nested-specialization
   scoping logic is written specifically for record-shaped children).
   It computes each target's own `decl://`-node identity via
   `source_graph.function_decl_identity` — the exact same computation
   `call_graph.py`/`type_graph.py` already use for their own function/
   variable declaration nodes — so a resolved `TEMPLATE_USES_DECL` edge
   lands on the *same* node those modules' own edges would. The second,
   independent label-collision risk the prior round also flagged (a bare,
   unqualified `f"&{name}"` spelling letting `Holder<&ns1::f>` and
   `Holder<&ns2::f>` collide onto one `template_instantiation` node) is
   closed the same way: `arg_label_spelling` uses the *resolved*
   `target_qname` (the unique identity string) for a decl-referencing
   argument's contribution to the instantiation label, not the bare
   `spelling`, falling back to the bare spelling only when unresolved (the
   same degrade-gracefully behavior an unresolved type argument already
   has). A member-function/static-data-member NTTP target is a known,
   left-open gap — `index_value_decls` deliberately skips class-scope
   descent entirely rather than risk misattributing a member's identity to
   its enclosing namespace. See `template_graph.py`'s own docstring and
   `tests/test_template_graph_value_decls.py`'s dedicated collision
   regression test
   (`test_two_instantiations_sharing_only_a_bare_callee_name_stay_distinct`).
   A separate gap the same investigation round originally flagged and this
   pass closed too: an argument whose target *is* a decl-referencing NTTP
   but resolves to no declaration this TU's AST indexes anywhere (e.g. a
   C++17 address-of-local-static NTTP, which `index_value_decls`
   deliberately never descends into) used to fall back to the same bare,
   collidable `spelling` `arg_label_spelling` uses for an unresolved *type*
   argument — `_has_unresolved_decl_argument` now detects this case (by
   comparing the pre-resolution argument's own `target_decl_kind` against
   the post-resolution `target_qname`) and skips the whole instantiation,
   the same discipline the opaque-argument guard above already applies.
2. **Macro/config dependency — done.** `abicheck/buildsource/macro_graph.py`
   is a two-pass extractor (a Clang AST pass indexing every declaration's
   own `(file, begin_line, end_line)` span, plus a pure raw-text scan for
   conditional regions and macro definitions — clang's own AST carries no
   representation of preprocessor conditionals at all, confirmed
   empirically), driven by `inline_graph_fold.fold_macro_graph` alongside
   the other Clang-backed passes. Populates `MACRO_CONTROLS_DECL` (macro →
   source_decl, `CONF_HIGH` — a declaration compiled only under a simple
   `#ifdef`/`#ifndef`/`#if defined`/`#if !defined` guard) and
   `DECL_USES_MACRO` (source_decl → macro, `CONF_REDUCED` — a declaration's
   own text references a macro defined earlier in the same file, a textual
   heuristic). `MACRO_EXPANDS_TO_VALUE`/`MACRO_EXPANDS_TO_TYPE`/
   `MACRO_CONTROLS_EDGE` remain reserved, unpopulated — see the module's own
   docstring (real macro-*expansion* tracing for the first two; per-edge
   rather than per-declaration conditional attribution for the third; none
   attempted this slice). A compound condition (`#if defined(X) &&
   defined(Y)`) or an `#elif` chain is deliberately unmodeled but still
   correctly maintains nesting depth across it, so a sibling/enclosing
   simple guard is never desynchronized. No new node kind — both edges join
   onto the existing `macro`/`source_decl` nodes only (ADR-057 D1's
   join-only-onto-an-existing-node rule, reapplied). See
   `docs/reference/source-graph-schema.md` for the field-level detail,
   including the load-bearing empirical AST-dump finding this slice
   depends on: a declaration's `range.begin`/`range.end` share one sticky,
   whole-document `(file, line)` cursor with every other AST node's `loc`
   (not just file-only sibling tracking, the way the pre-existing
   `type_graph.py` pass already threads) — printed only when it changes
   from the immediately preceding node's, in document-traversal order.
   Post-merge Codex review (PR #708) found and fixed three real bugs in the
   first cut: `_LocationCursor` didn't unwrap clang's nested
   `spellingLoc`/`expansionLoc` shape for a macro-generated declaration's own
   location (only the flat `file`/`line` keys), desyncing the sticky cursor
   for every later sibling too; `ClangMacroGraphExtractor` read a compile
   unit's out-of-source relative `file` spelling against the process's own
   cwd instead of that compile unit's replayed cwd, silently finding no
   source text (or, worse, the wrong file); and `extract_from_build`'s
   `(identity, file)` dedup discarded a real definition's span whenever its
   forward declaration was visited first (now `(identity, file, begin_line,
   end_line)`). One further finding was accepted as a **documented, known
   gap rather than fixed**: `MACRO_CONTROLS_DECL` rarely fires for the two
   negated guard forms (`#ifndef X`/`#if !defined(X)`/an `#ifdef X`'s
   `#else` branch), since the guard macro is by definition typically
   undefined in the scanned configuration and so was never seeded as a
   `macro` node to join onto — fixing that for real would mean overriding
   the join-only-onto-an-existing-node invariant this module's own test
   suite pins down (`test_augment_never_mints_new_nodes`), which is its own
   scoped design decision, not a drive-by override; see the module
   docstring's `EDGE_MACRO_CONTROLS_DECL` entry for the full reasoning.
3. **Virtual dispatch — partially done.** `abicheck/buildsource/
   virtual_dispatch_graph.py` populates two of the four edge kinds this item
   originally sketched, both as **pure graph transformations** — unlike every
   other item in this phase, it shells out to no compiler at all, since both
   parts read already-collected `call_graph.py`/`type_graph.py`/
   `override_graph.py` facts and write more graph. Driven by
   `inline_graph_fold.fold_virtual_dispatch_graph`, run immediately after
   `fold_override_graph` in `fold_semantic_graphs` (all three of its inputs
   must have already run).
   - `VIRTUAL_CALL_MAY_DISPATCH_TO` (`CONF_REDUCED`, explicitly
     `resolution: "overapprox"`, never `"exact"` — the design brief's own
     instruction) joins a virtual `DECL_CALLS_DECL` edge (`call_kind ==
     "virtual"`, pointing at the statically-resolved base method) against
     every `METHOD_POSSIBLE_OVERRIDE` edge naming that same base method as
     its target, re-pointing from the original *caller* to each override
     candidate. A leaf virtual method with no recorded override candidates
     emits nothing — no spurious self-edge back to the base method, since the
     `DECL_CALLS_DECL` edge already names it and there is no dispatch
     ambiguity to represent.
   - `TYPE_HAS_VTABLE` (`CONF_HIGH`, the one genuinely **new node kind** this
     phase introduces — items 1/2/6 all reused pre-existing node kinds)
     derives, per the Itanium ABI rule "a class is polymorphic iff it
     declares or inherits ≥1 virtual function", which `record_type` nodes are
     polymorphic: seeded from a `record_type`'s exact-match ownership of a
     method appearing in a `METHOD_POSSIBLE_OVERRIDE` edge (decoded from the
     method's own Itanium/MSVC mangled identity via
     `diff_cxx_rules.itanium_scope_components`/`msvc_scope_components` — the
     same structural, no-external-demangler decoders
     `diff_cxx_rules.owner_class_of` already uses elsewhere), then closed
     transitively over `TYPE_INHERITS`. Mints at most one `vtable` node per
     polymorphic class (`vtable://<record-type-identity>`), bounded by class
     count. The owner-join is deliberately **exact-match only** (a known,
     conservative false negative for a class-template specialization, whose
     raw Itanium encoding doesn't equal `type_graph.py`'s spelled record
     identity — never a wrong match on an unrelated same-named type).
   - Post-merge Codex review (PR #708) found and fixed two real correctness
     gaps in the first cut, both in the same "one-hop only" shape: (1)
     `VIRTUAL_CALL_MAY_DISPATCH_TO` only reached the *direct*, one-hop
     override candidates of a call's static target — `override_graph.py`
     records each override against its nearest declaring ancestor only
     (chains, never flattened), so a multi-level chain `Base::f <- Mid::f <-
     Derived::f` previously reached `Mid::f` but not the equally real
     runtime target `Derived::f`. Fixed with `_all_reachable_overrides`, a
     cycle-safe transitive closure over `METHOD_POSSIBLE_OVERRIDE`, each
     candidate keeping its own edge's resolution label. (2) `TYPE_HAS_VTABLE`
     seeded polymorphism only from `METHOD_POSSIBLE_OVERRIDE` edges — blind
     to arguably the single most common polymorphic-class shape, a virtual
     method with no override anywhere in the scanned codebase (a base
     class's own leaf virtual method, or any method on a class with no base
     at all). Fixed by extending `override_graph.py` itself: a new pure
     function, `parse_clang_ast_virtual_methods`, returns every virtual
     method's identity (own or inherited) independent of whether it has an
     override candidate; `ClangOverrideGraphExtractor.last_virtual_methods`
     exposes the per-TU-aggregated set the same side-effecting way
     `last_jobs`/`diagnostics` already are, and
     `augment_graph_with_overrides`'s new `virtual_methods` parameter stamps
     an `is_virtual: True` fact onto each identity's *already-existing*
     `source_decl` node (never minting one, same join-only discipline) —
     which `augment_graph_with_vtable_presence`'s seed loop now also reads.
   - A third Codex-review round found a deeper root cause than either fix
     above touched: `VIRTUAL_CALL_MAY_DISPATCH_TO` was effectively **inert**
     for the single most common call shape, `p->f()`, because the upstream
     `call_graph.py` (ADR-031 D4, predates this item) never even classified
     it as `call_kind="virtual"` in the first place. Verified against real
     Clang 17/18 output: `MemberExpr.referencedMemberDecl` is a bare node-id
     *string*, not the nested dict `call_graph._find_referenced_decl` only
     recognized — so the DFS fell through to the *receiver*'s own
     `DeclRefExpr` (`p`, a `ParmVarDecl`) and misclassified the whole call as
     `CALL_KIND_FUNCTION_POINTER` through `p`. Fixed in `call_graph.py`
     itself (foundational, shared by every consumer of `DECL_CALLS_DECL`,
     not scoped to this item): a new `member_index` (clang node id -> full
     decl node, built the same way the existing `id_index` already is)
     resolves a string `referencedMemberDecl` back to the real
     `CXXMethodDecl`, carrying its own `virtual`/`type.qualType` fields.
     An id not found in `member_index` (a forward reference, or — see
     G29 Phase 5 item 4's own callback-graph section — a `FieldDecl`, never
     indexed at all since only `_FUNCTION_DECL_KINDS` populate it) resolves
     to no edge at all rather than falling through to the receiver: ADR-028
     D3's "degrade to no fact, never a wrong one" rule, applied to a call
     classification for the first time. `p->f()` now correctly classifies
     as `call_kind="virtual"`, which is what makes `VIRTUAL_CALL_MAY_DISPATCH_TO`
     actually reachable in the common case this feature exists for.
   - **A fourth and fifth Codex-review round found two more real gaps in
     `call_graph.py`'s own resolution, both empirically verified against
     real Clang 17 output and both fixed without touching this item's own
     modules.** (1) `member_index` was built *incrementally* during
     `_walk_calls`'s single combined pre-order walk
     (`_enter_function_scope`'s `member_index.setdefault`), so a call to a
     member declared *later* in the same class body silently dropped
     instead of resolving — confirmed for
     `struct A { virtual void f(){ g(); } virtual void g(); };`: clang
     visits `f`'s body (and its call to `g`) before `g`'s own
     `CXXMethodDecl` sibling, so `member_index` doesn't yet have `g` at the
     moment the call needs it, and the earlier fix's own "unresolved id ->
     no edge" rule (correctly) dropped it rather than misattributing it.
     Fixed with a new, separate whole-AST pre-pass (`_index_member_decls`,
     no scope tracking needed — a plain `id -> node` index is
     order-independent) run once at the top of `parse_clang_ast_calls`,
     before `_walk_calls` starts resolving any call site; the existing
     incremental population is left in place (harmless — `setdefault`
     against an already-populated id) rather than removed. (2) Separately,
     when the resolved *static* target of a member call is itself an
     override, clang commonly omits `"virtual": true` from that override's
     own `CXXMethodDecl` (only the slot's *original* declaring ancestor
     carries it) — confirmed for
     `struct B { virtual void f(); }; struct D : B { void f() override; void h(){ f(); } };`:
     `D::f` (the resolved target of `h()`'s call) carries no
     `"virtual": true`, so `_classify_call` misclassified the call as
     `direct`/`exact`, excluding a real further-derived-override chain from
     `VIRTUAL_CALL_MAY_DISPATCH_TO` entirely. The first fix attempt threaded
     a precomputed `virtual_identities` set from
     `override_graph.parse_clang_ast_virtual_methods` into `_classify_call`
     — correct in isolation, but it makes `call_graph.py` import from
     `override_graph.py`, which already function-locally imports several
     helpers *from* `call_graph.py`
     (`_safe_clang_args_from_compile_unit`/`_call_graph_jobs`/
     `_deadline_bound_worker`) — even as a function-local import, the
     AI-readiness `import-cycle-growth` gate walks *all* `Import`/
     `ImportFrom` nodes regardless of nesting, so it correctly flagged the
     resulting new cycle
     (`call_graph -> override_graph -> type_graph -> call_graph`) as an
     ERROR; confirmed reproducing the same rejection locally before
     reverting that approach. Fixed instead with a self-contained,
     dependency-free check: clang always marks a written `override`/`final`
     keyword with an `OverrideAttr`/`FinalAttr` child on the override's own
     declaration (verified against the same real AST — `D::f` carries an
     `OverrideAttr` child; a second real-clang repro with `final` instead of
     `override` confirmed the identical `FinalAttr` shape), and the `ref`
     `_classify_call` receives for a `CXXMemberCallExpr` is always the
     *full* declaration node (resolved through `member_index`, never a
     compact stub) — so a new `_ref_is_virtual()` helper checks
     `ref["virtual"]` first, then falls back to scanning `ref`'s own
     `inner` children for `OverrideAttr`/`FinalAttr`, entirely within
     `call_graph.py`. A derived method that redeclares a virtual signature
     with *neither* the `override`/`final` keyword *nor* a repeated
     `"virtual": true` (legal but unusual C++ style) remains a documented,
     conservative false negative — the same
     false-negative-over-false-positive default this module already uses
     throughout (ADR-028 D3), and out of scope for a fix that specifically
     targets the empirically-confirmed common case.
   - **A sixth and seventh Codex-review round, on the same `bb7d139` push,
     found the fourth/fifth rounds' own fixes were each themselves too
     aggressive in one real case, both verified against real Clang 18
     output.** (1) The inherited-virtual fix from the fourth/fifth rounds
     made `_classify_call` classify **every** `CXXMemberCallExpr` whose
     resolved target is virtual (own or via `OverrideAttr`/`FinalAttr`) as
     `virtual`/`overapprox` — but an **explicitly qualified** call,
     `obj.Base::f()`, suppresses virtual dispatch by C++ rule regardless of
     `Base::f`'s own virtuality; clang's static resolution still names
     `Base::f` as the target either way, so nothing in `ref` alone
     distinguishes the two. Confirmed clang's JSON AST dump carries **no**
     qualifier information at all — its text `-ast-dump` shows a sibling
     `NestedNameSpecifier TypeSpec 'B'` node for `obj.B::f()` that is
     entirely absent from the identical AST's JSON form (byte-for-byte
     identical `MemberExpr` key sets, including `inner`, between
     `obj.B::f()` and `obj.f()`). Fixed by deriving qualification from
     source-range arithmetic the JSON *does* carry instead
     (`_member_expr_is_qualified`): an unqualified access's member-name
     token begins immediately after the receiver's own end plus one
     operator character (`.`/`->`); a qualifier occupies the extra bytes in
     between. A **second** real gap surfaced while verifying this against
     the common "call the base implementation from an override" pattern,
     `struct D : B { void f() override { B::f(); } };` — clang anchors a
     genuinely *implicit* `this` receiver's own synthesized position at the
     member name itself (not before a written qualifier), so the
     receiver-to-member gap always reads as zero for this shape regardless
     of whether `B::` is present. `_is_implicit_this_receiver` distinguishes
     a synthesized `this` (`CXXThisExpr` with `"implicit": true`, optionally
     wrapped in an `UncheckedDerivedToBase` cast) from an explicitly written
     `this->f()` (no `implicit` key at all) — verified against three
     distinct real ASTs (implicit unqualified `f()`, implicit qualified
     `B::f()`, explicit `this->f()`/`this->B::f()`) — and for the implicit
     case measures the `MemberExpr`'s own begin-to-end span against the bare
     member-name length instead. Only a strictly-larger-than-expected gap
     counts as qualified in either branch; a missing offset/tokLen field
     degrades to "not qualified" (the pre-existing over-approximation),
     never the reverse — wrongly suppressing a real virtual call would
     silently drop a genuine dispatch target, a worse error than the
     over-approximation this narrows. (2) Separately, in `callback_graph.py`
     (not `call_graph.py`): `_address_taken_function` only unwrapped
     `ParenExpr` around a `&func`/decay shape, so an explicitly cast
     callback argument — `register_cb((handler_t)handler)` (`CStyleCastExpr`,
     `castKind == "NoOp"`, wrapping the identical
     `ImplicitCastExpr`/`FunctionToPointerDecay`) or
     `register_cb(static_cast<handler_t>(handler))`
     (`CXXStaticCastExpr`/`CXXReinterpretCastExpr` over the same shape) —
     silently produced no `DECL_REGISTERS_CALLBACK` edge, for any API that
     requires or commonly receives a cast callback argument. Fixed by also
     unwrapping the named-cast kinds (`CStyleCastExpr`/`CXXStaticCastExpr`/
     `CXXReinterpretCastExpr`/`CXXConstCastExpr`/`CXXFunctionalCastExpr`/
     `CXXDynamicCastExpr`) the same way `ParenExpr` already was.
   - **An eighth finding was a genuine, pre-existing, unrelated test bug
     surfaced by CI running the sixth/seventh rounds' own new code on
     platforms this session's Linux sandbox can't reach.** Windows CI
     failed `test_callback_graph.py::test_extractor_unavailable_returns_empty_and_records_diagnostic`:
     it passed an empty `BuildEvidence()` (no compile units) to
     `extract_from_build`, which hits that method's own "nothing to do"
     early return *before* its availability check ever runs — the identical
     ordering `ClangCallGraphExtractor`/`ClangTypeGraphExtractor`/
     `ClangOverrideGraphExtractor.extract_from_build` all share, and whose
     own sibling tests always pass at least one real compile unit for
     exactly this reason (confirmed by reading
     `test_call_graph.test_extractor_missing_clang_returns_empty`). This
     test alone used an empty `BuildEvidence()`, which happened to still
     record a diagnostic on this session's Linux sandbox's earlier ad hoc
     verification (a real, present `clang` on that machine took a different
     code path than the CI runner's `clang-does-not-exist`), masking the gap
     until CI's genuinely-missing-binary case exercised it. Fixed the test to
     match the established sibling convention (one real `CompileUnit`), not
     the shared production ordering — with no work to attempt, recording a
     diagnostic would be noise, not signal, so the production behavior is
     correct as-is. A **second**, separately-triaged Windows CI failure in
     the same run, `test_macro_graph.py::test_parse_real_clang_ast_decl_ranges_end_to_end`,
     was a genuine, pre-existing, unrelated test bug: it filtered ranges by
     an Itanium-mangled prefix (`identity.startswith("_Z7guardedv")`), but a
     Windows runner's real `clang` targets the MSVC ABI by default, mangling
     the same function as `?guarded@@YAXXZ` — both schemes embed the plain
     identifier as a literal substring, so the filter was changed to
     `"guarded" in r.identity` (mangling-scheme-agnostic) instead of an
     Itanium-only prefix match. A **third** Windows-only failure in the same
     run, `test_build_source_cli.py::test_collect_source_graph_folds_override_and_macro_graph_passes`
     showing `override_graph`/`virtual_dispatch_graph` as `degraded_passes`
     rather than fully covered, was investigated but **not fixed this
     round**: the job log shows the derived `virtual_dispatch_graph`
     correctly propagating `override_graph`'s own degraded state — this
     session's inline_graph_fold.py fix (documented above under Codex-review
     finding "Preserve narrowed coverage for derived virtual passes")
     working exactly as designed — but the *cause* of `override_graph`
     itself degrading is a real Clang/MSVC-target compiler behavior on the
     Windows CI runner this session's Linux sandbox cannot reproduce or
     safely guess at (the extractor's own per-TU diagnostic text wasn't
     captured in the truncated CI log excerpt read during triage). Left
     open, flagged in the PR thread rather than blind-patched.
   - **A ninth, tenth, and eleventh finding, on the very push that added the
     sixth/seventh rounds' own qualified-call/cast-unwrap fixes, found two
     more real gaps and one real bug in that same code.** (9) The
     qualified-call heuristic (`_member_expr_is_qualified`) also misfires on
     legal whitespace/comments between the receiver and the member —
     `obj . f()`, `ptr /* note */ -> f()` — since the JSON offset arithmetic
     cannot distinguish two incidental whitespace bytes from a real
     `Base::` qualifier's bytes; confirmed against a real Clang 18 AST for
     `obj . f()`. **Investigated and deliberately left open, not patched**:
     closing this exactly needs the actual source text between the two
     offsets, which `parse_clang_ast_calls` (a pure function over the AST
     dict alone, by design — unlike `macro_graph.py`'s own Pass B, which has
     a source-file path to read from) does not have; no fixed numeric
     threshold soundly separates "a couple of stray spaces" from "a real
     single-letter class name plus `::`" in the general case, and giving
     this module source-text access is a distinct, larger architectural
     change out of scope for a drive-by fix. Documented in
     `_member_expr_is_qualified`'s own docstring and pinned by a dedicated
     regression test
     (`test_parse_call_with_whitespace_around_dot_is_a_known_false_positive`)
     recording the current, accepted behavior — this only misfires on
     non-idiomatic whitespace styling no common formatter (clang-format
     included) produces. (10) Separately, in `callback_graph.py`:
     `_FUNCTION_POINTER_TYPE_RE` only allowed whitespace between `*` and `)`
     in a function-pointer declarator (`"(*)("`), so a top-level
     cv-qualifier on the pointer itself — `void (*const)(int)`, the
     desugared spelling of `using H = void (*)(int); void reg(H const h);`
     — was rejected outright, silently omitting the registration for any
     cv-qualified function-pointer parameter. Confirmed against a real
     Clang 18 AST (`desugaredQualType` is exactly `"void (*const)(int)"`
     for a `const`-qualified typedef'd parameter). Fixed by extending the
     regex to accept `const`/`volatile` (in either order, or both) between
     `*` and `)`. (11) A genuine mutual-exclusion bug in
     `fold_virtual_dispatch_graph` itself: the seventh round's own
     narrowed/degraded-propagation fix *added* the propagation but left the
     prior unconditional `graph.extractor_passes["virtual_dispatch_graph"]
     = True` assignment in place alongside it — so a narrowed or degraded
     run stamped BOTH the narrowed/degraded key AND the full-coverage key
     for the same pass, contradicting the persisted coverage contract every
     other clang-backed pass's own `if fully_covered: ... elif narrowed:
     ... elif degraded: ...` chain already enforces (confirmed against real
     data: the Windows CI log from the eighth finding's own investigation
     already showed exactly this — `virtual_dispatch_graph` present in both
     `extractor_passes` and `degraded_passes` simultaneously, which the
     eighth finding's triage read past without flagging as its own separate
     bug). Fixed by converting to the same mutually-exclusive if/elif/else
     shape as the sibling passes.
   - **A twelfth finding, on the very fix that closed the eleventh, found the
     mutual-exclusion fix's own `elif`/`else` split was still too permissive
     — "not narrowed and not degraded" is not the same claim as "fully
     covered."** When `clang`/`clang++` isn't on `PATH` at all, each of
     `fold_call_graph`/`fold_type_graph`/`fold_override_graph` returns early
     after recording a `"failed"` `ExtractorRecord` — setting **none** of
     `extractor_passes`/`narrowed_passes`/`degraded_passes` for its own pass
     (there's no per-TU diagnostic to attach a degraded stamp to; the
     extractor never ran at all). The eleventh finding's own fix read
     `narrowed`/`degraded` as both `False` in exactly this state — nothing
     is narrowed, nothing recorded a diagnostic — so it fell through to the
     `else` branch and claimed full virtual-dispatch coverage from zero
     prerequisite facts, the identical class of bug the eleventh finding
     itself closed, one level down. Fixed by requiring `fully_covered` —
     every one of the three prerequisites has its *own* `extractor_passes`
     entry set, not merely the absence of narrowed/degraded — before
     stamping this pass's own full coverage; the fallback (neither narrowed
     nor fully covered) now stamps `degraded_passes`, covering both "some
     prerequisite recorded a real diagnostic" and "some prerequisite never
     ran at all" uniformly. This also changed what a hand-built test fixture
     with no prerequisite coverage stamps at all means: it is now
     indistinguishable, from this pass's own vantage point, from a real
     clang-unavailable run — so the existing unit tests were updated to
     explicitly mark the three prerequisites `extractor_passes`-covered
     before asserting this pass's own full coverage, and a new dedicated
     test pins the previously-mishandled all-prerequisites-absent case.
   - **A thirteenth finding, on the same push, found the third round's own
     fix still ordered its checks wrong.** The three prerequisites stamp
     independently, so one can land in `narrowed_passes` (a clean,
     explicitly-scoped run) while another lands in `degraded_passes` (a real
     per-TU clang failure) — e.g. `call_graph` narrows cleanly while
     `override_graph` degrades. Checking `narrowed` before `degraded` (the
     third round's own order) stamped only the narrowed key for that mix,
     silently dropping the untracked gap from the failed TUs — contradicting
     `SourceGraphSummary.degraded_passes`'s own documented precedence ("a
     narrowed run with diagnostics lands here too ... since it is even less
     trustworthy than either"). Fixed by folding the "some prerequisite
     recorded a real diagnostic" case and the "some prerequisite never ran
     at all" case (the twelfth finding above) into one `degraded` check,
     checked *first* — only when nothing is degraded or missing, and at
     least one prerequisite is narrowed, does this pass count as narrowed.
     A dedicated regression test covers the mixed narrowed+degraded case.
   - **Three more findings in the same review round, unrelated to the
     coverage-stamping bug above.** (1) `macro_graph.py`'s unmodeled-
     conditional fallback (`_IF_RE`) matched only bare/compound `#if`, not
     `#ifdef`/`#ifndef` — `\b` fails right after "if" since "d"/"n" are word
     characters too — so a malformed-but-compiler-accepted directive with
     trailing tokens (`#ifdef FEATURE_X extra`, which real compilers accept
     with a warning) or a line continuation matched none of the four simple
     patterns *and* none of the fallback, pushing no frame at all; the
     matching `#endif` then popped the *enclosing* guard's frame instead,
     truncating it early — a real violation of the module's own documented
     "an unmodeled block never desyncs an enclosing guard's depth"
     invariant. Fixed by widening `_IF_RE` to also match `#ifdef`/`#ifndef`.
     (2) `test_inline_graph_folds_macro_edges_when_clang_available` (a fast,
     non-`integration`-marked test) faked `ClangCallGraphExtractor`/
     `ClangTypeGraphExtractor`/`ClangMacroGraphExtractor` but not
     `ClangOverrideGraphExtractor`/`ClangTemplateGraphExtractor`/
     `ClangCallbackGraphExtractor` — all three of which `with_call_graph=
     True` also constructs via `fold_semantic_graphs` — so the test could
     shell out to a real `clang++` if one happened to be on the runner's
     PATH. Fixed by faking all six. (3) `docs/reference/source-graph-schema.
     md`'s callback-family section still said every edge "joins onto
     pre-existing `source_decl` nodes only," contradicting the callback-
     endpoint-minting fix from an earlier round (documented correctly in
     this plan doc, but not in the schema reference) — fixed to describe the
     implemented minting behavior instead. Two lower-priority defensive
     nitpicks from the same round were also applied: `callback_graph.py`'s
     and `virtual_dispatch_graph.py`'s own edge-folding loops now iterate a
     `list(graph.edges)` snapshot rather than the live list before calling
     `graph.add_edge()` inside the loop — currently safe only because every
     edge kind each loop adds is itself rejected by that loop's own kind
     filter, a coupling a future edge kind could silently break.
   - **`DECL_OVERRIDES_DECL` needed no new producer at all — a closed gap,
     not a deferred one.** `override_graph.py` (ADR-041 P2 item 1, which
     predates this item) already emits `METHOD_POSSIBLE_OVERRIDE` edges whose
     `resolution` is `"override_confirmed"` when clang's own `OverrideAttr`
     (the `override` keyword, compiler-checked) is present — that edge
     already carries the exact fact `DECL_OVERRIDES_DECL` would. Minting a
     second, redundant edge kind for the same fact would fork one piece of
     evidence into two with no consumer able to tell which is authoritative —
     the same "closed a gap by discovering pre-existing coverage" pattern
     this plan used for ADR-057 D6's tier-1 finding. `DECL_OVERRIDES_DECL`
     stays registered in `graph_facts.VIRTUAL_DISPATCH_EDGE_KINDS` so a
     hand-built or future graph naming it directly is never rejected, but no
     producer emits it.
   - **`VTABLE_SLOT_MAPS_TO_DECL` remains reserved, deliberately not
     attempted.** A precise per-slot Itanium vtable layout (offset-to-top and
     typeinfo pointer slots, primary vs. secondary vtables under multiple
     inheritance, virtual-inheritance vtables, covariant-return thunks
     shifting a slot's target) is exactly the class of ABI-layout complexity
     `diff_elf_layout.py`'s own binary-only vtable-slot-*count* detector
     documents in its own module docstring — a much harder, easy-to-get-
     subtly-wrong claim than "this class has a vtable" or "this call's
     target set may include these declarations." A naive "declaration order"
     per-slot model would get exactly those cases wrong, not merely
     approximately right, and this codebase's discipline is to degrade to no
     fact rather than emit a wrong one (ADR-028 D3) — so this edge waits for
     a real, verified Itanium layout model, matching this item's own design
     brief's distinction between "the vtable slot provably changed" and "the
     possible runtime dispatch target set changed". `diff_elf_layout.py`
     itself is unrelated infrastructure (binary-only, no per-slot identity)
     and is not unified with this family.
4. **Callback/function-pointer — done, for three of the four vocabulary
   members.** `abicheck/buildsource/callback_graph.py` closes the
   plugin/event-loop/C-API callback blind spot the review calls out, split
   the same way item 3 is: a pure join (Part A, no new clang pass) plus a
   genuinely new Clang AST pass (Part B) — but unlike item 3's two
   independent `fold_*` functions, this family's Part A depends on Part B's
   own edges already being in the graph, so both run inside one
   `inline_graph_fold.fold_callback_graph` call.
   - `DECL_REGISTERS_CALLBACK` (`CONF_HIGH`) — a function's address is a
     direct argument of a plain `CallExpr` at a position whose callee
     parameter is itself function-pointer-typed (`signal(SIGINT, handler)`).
     `DECL_TAKES_ADDRESS_OF` (`CONF_REDUCED`) — the broader case: an
     address-of/decay flows into a function-pointer-typed variable/field via
     assignment or its own initializer, not necessarily a direct call
     argument. Both are a new Clang AST pass
     (`callback_graph.parse_clang_ast_callbacks`), empirically verified
     against real Clang 18 output for an explicit `&func`
     (`UnaryOperator`/`"&"`), an implicit function-to-pointer decay
     (`ImplicitCastExpr`/`"FunctionToPointerDecay"`), and a typedef'd
     function-pointer type's `desugaredQualType` spelling.
   - `CALLBACK_MAY_INVOKE` (`CONF_REDUCED`, `resolution: "overapprox"`,
     never `"exact"` — the same instruction this whole graph family follows)
     joins `call_graph.py`'s already-folded function-pointer-kind
     `DECL_CALLS_DECL` edge (caller → slot) against every
     `DECL_REGISTERS_CALLBACK`/`DECL_TAKES_ADDRESS_OF` edge naming that same
     slot — no new clang pass. A slot with no function registered anywhere
     this pass examined contributes no edge (the fact is genuinely unknown,
     not empty, the same reasoning item 3 uses for a leaf virtual method
     with no override candidates).
   - **Identity design — the single load-bearing correctness property.**
     Investigated (not assumed) by reading
     `call_graph._classify_call`/`_resolve_ref_callee_identity`: a call
     through a variable/parameter/field resolves its callee identity via an
     `id_index` populated only for `FunctionDecl`-kind nodes, so it always
     falls back to the reference stub's own bare, unqualified name (a
     parameter named `h` in two unrelated functions both resolve to the
     identical bare identity). Part B deliberately computes the same slot
     identity the same (weak) way, rather than a stronger one that would
     simply never match anything `call_graph.py` produces — mirroring an
     existing, already-shipped limitation of the graph it joins onto.
   - **A load-bearing negative empirical finding, partially closed by a
     later same-PR fix, not fully.** Originally found that a struct-field-
     typed callback slot invoked through member-call syntax (`w->cb(x)`)
     never joined in Part A, because `call_graph._find_referenced_decl`
     could not recognize a `MemberExpr`'s own `referencedMemberDecl` (a bare
     node-id *string* in real clang output, not a nested dict) and fell
     through to the base object's own reference instead — a **wrong** edge
     (attributed to the receiver `w`), not merely a missing one. A separate
     Codex-review finding (fresh evidence, same PR) on `virtual_dispatch_graph.py`'s
     own `VIRTUAL_CALL_MAY_DISPATCH_TO` producer hit the identical root
     cause for a virtual **method** call (`p->f()`) and fixed it in
     `call_graph.py` directly: a new `member_index` (id -> full decl node,
     built alongside the existing `id_index`) resolves a string
     `referencedMemberDecl`, but only for `_FUNCTION_DECL_KINDS` nodes — a
     `FieldDecl` (what a callback slot always is) is never one of those, so
     it is never indexed. Re-verified against the identical field-callback
     repro after that fix landed: the call now resolves to **no edge at
     all**, not the wrong `decl://w` edge originally found — an improvement
     (ADR-028 D3: no fact beats a wrong one), but the field case still
     doesn't join. Extending `member_index` to also cover `FieldDecl` stays
     its own scoped follow-up, not attempted here.
   - **`FUNCTION_POINTER_HAS_SIGNATURE` — investigated, found genuinely
     unmet, implemented as a node-level fact instead of an edge.** Checked
     (not assumed) whether `type_graph.py`'s existing `DECL_HAS_TYPE`/
     `TYPE_HAS_FIELD_TYPE` edges already carry a callback slot's signature
     anywhere: they don't — a callback's own parameter/return types are
     folded under the *enclosing* declaration's role, losing the ordered
     signature shape, and no edge attribute anywhere records the slot's raw
     type string. A real, unmet gap. But once populated, a signature is a
     property of exactly one declaration, not a relation between two graph
     entities, so it doesn't fit an edge shape — stays registered in
     `graph_facts.CALLBACK_EDGE_KINDS` for vocabulary compatibility, with the
     real data populated as a `function_pointer_signature` **node-level**
     fact on the slot's own `source_decl` node.
   - Deliberately scoped to a **plain, free-function** `CallExpr` for the
     registration case (not `CXXMemberCallExpr`/`CXXOperatorCallExpr`),
     mirroring `override_graph.py`'s own first-slice AST-node-kind
     narrowing. Deferred, each needing its own scoped follow-up: extending
     `call_graph.py`'s own `member_index` to also cover `FieldDecl` nodes
     (would close the remaining field-callback join gap above and
     strengthen every function-pointer `DECL_CALLS_DECL` edge, not just this
     module's own — shared infrastructure, not a same-slice fix);
     `CXXMemberCallExpr`/`CXXOperatorCallExpr` registration detection.
   - Post-merge Codex review found and fixed one real completeness gap, and
     surfaced (without fixing — see below) one real correctness gap already
     latent in the design. **Fixed:** an earlier, strict join-only-onto-an-
     existing-node version of `augment_graph_with_callback_registrations`
     silently dropped the whole registration whenever either endpoint had
     no pre-existing `source_decl` node — the common case for a private
     handler used *only* as a callback (never itself called, so
     `call_graph.py` never creates a node for it either) or a registration
     API's own callback parameter (never a standalone type-graph node).
     Fixed by minting the missing endpoint instead, the same precedent
     `override_graph.augment_graph_with_overrides` already establishes in
     this family (this module's own AST pass already has complete, real
     information about both declarations — minting isn't a guess).
     **Surfaced, not fixed — a genuine false-positive risk, not merely a
     missing-edge one:** because Part A's join keys strictly on the shared,
     unqualified slot identity (see "Identity design" above), two
     *unrelated* functions each declaring their own same-named
     function-pointer parameter (`register(handler_t h)` and
     `invoke(handler_t h)`) collapse onto one `decl://h` node — so a
     function registered only via `register`'s `h` is reported as a
     possible target of a call made through `invoke`'s completely unrelated
     `h`. This is the sharpest concrete case for the scope-qualified-
     identity follow-up already named above; a fix confined to this module
     alone cannot close it, since the ambiguity originates in
     `call_graph.py`'s own edge identity. Pinned by a dedicated regression
     test documenting the current, known-bad behavior rather than silently
     accepted.
   - **A fourteenth finding: `fold_callback_graph`'s own coverage stamp
     ignored `call_graph`'s coverage state entirely.** Part A's
     `CALLBACK_MAY_INVOKE` join reads `call_graph`'s own already-folded
     function-pointer-kind `DECL_CALLS_DECL` edges, so a degraded or
     never-run `call_graph` pass means a real dispatch target can be
     silently absent even when this pass's own clang run (Part B) examined
     the whole compile DB cleanly — the identical class of bug the
     `fold_virtual_dispatch_graph` propagation fix (findings eleven through
     thirteen above) already closed for its own three prerequisites, just
     not yet applied here. Fixed the same way: this pass's own extractor-run
     state and `call_graph`'s recorded state are combined worst-wins
     (`degraded`/missing > `narrowed` > `full`) before stamping
     `extractor_passes`/`narrowed_passes`/`degraded_passes` for
     `callback_graph`, with a dedicated regression test class
     (`TestFoldCallbackGraphPropagatesCallGraphCoverage`) covering all four
     combinations. A companion, much narrower finding in the same round:
     `docs/_meta/topics.yaml`'s `impact-analysis` topic listed
     `graph_impact.py` as a fact source but not `graph_facts.py` — the
     shared node/edge kind and confidence-tier vocabulary every graph module
     in this family (`macro_graph.py`/`virtual_dispatch_graph.py`/
     `callback_graph.py`/...) actually builds on. Added.
   - **A fifteenth finding, investigated and confirmed real, deliberately
     not implemented: a callback propagated through an intermediate
     parameter-to-slot assignment is never joined at all.** A registration
     API that stashes its own *parameter* (not a function) into a stored
     slot — `void reg(handler_t h) { stored = h; }`, with the eventual
     indirect call made through `stored`, not `h` — breaks Part A's join.
     Reproduced empirically end to end against real Clang 18: `call_graph.py`
     correctly emits `DECL_CALLS_DECL(invoke -> stored, function_pointer)`,
     and this module correctly emits `DECL_REGISTERS_CALLBACK(my_handler ->
     h)` for the outer `reg(my_handler)` registration call — but
     `stored = h;` inside `reg` is never captured as any edge at all, since
     the RHS-resolution helper `_address_taken_function` only recognizes a
     real function address/decay, not a parameter/variable/field alias. The
     whole chain — a real, runtime-reachable `invoke -> my_handler`
     relationship — therefore produces no `CALLBACK_MAY_INVOKE` edge, a
     false negative distinct from every other gap documented above (those
     under-report a slot that itself IS named; here the actually-invoked
     slot never gets linked to the originally-registered function at all).
     A sound fix needs two new pieces, not one: a new intra-procedural
     "slot aliases slot" edge for a function-pointer-to-function-pointer
     assignment, and a transitive closure in Part A's join with its own
     cycle guard (an alias chain can be arbitrarily long and could
     self-reference). Both are a scoped follow-up of their own — attempting
     the transitive-closure half without careful cycle protection risks
     turning a currently-safe "degrade to no fact" gap into a
     non-terminating or spuriously overapproximating one, worse than the
     status quo. Pinned by a dedicated regression test
     (`test_callback_propagated_through_stored_parameter_slot_is_not_joined`)
     documenting the current, known-incomplete behavior rather than
     silently accepted.
   - **A sixteenth finding, confirmed real and fixed: a class whose only
     virtual member is its destructor was invisible to every existing
     vtable-presence seed.** `override_graph._OVERRIDE_CANDIDATE_KINDS`
     deliberately excludes `CXXDestructorDecl` from override-EDGE matching
     (the module's own documented Itanium D1/D2 dual-mangling concern), so
     `virtual_dispatch_graph.augment_graph_with_vtable_presence`'s
     override-edge seed can never see a destructor at all — and its
     leaf-virtual-method seed reads `is_virtual`-tagged `decl://` nodes,
     which a bare, uncalled destructor typically has none of (unlike an
     ordinary member, `type_graph.py` doesn't mint a `decl://` node for a
     destructor purely from its own declaration). Reproduced empirically
     end to end against real Clang 18: `struct Base { virtual ~Base(); };`
     with a real derived class produced no `TYPE_HAS_VTABLE` edge for
     either side at all before the fix. Closed with a new, narrower pure
     function, `override_graph.parse_clang_ast_virtual_destructor_owners`
     — deliberately *not* extending `_OVERRIDE_CANDIDATE_KINDS` (that would
     reopen the D1/D2 concern this module's docstring already explains) —
     which only asks "does this destructor's own AST node carry
     `virtual: true`", the identical direct signal every other virtual
     method already carries, no override-pair matching required. Its
     output feeds a third, independent vtable-presence seed stamped
     directly onto the owning class's own `record_type` node (not routed
     through a `decl://` node the way the other two seeds are), since a
     bare destructor declaration often has no `decl://` node to join onto
     at all — matching the identical join-only-onto-an-existing-node
     discipline the rest of this module family uses. **One residual gap,
     shared with the two pre-existing seeds, not new to this fix:** a
     class isolated enough that nothing else in the graph (no
     `TYPE_INHERITS` edge, no override edge) already minted its bare
     `record_type` node still seeds nothing — verified empirically that
     `type_graph.py`'s own `CXXRecordDecl` walk, for a fully isolated
     class, mints only the class's own injected-class-name identity (e.g.
     `record::Base::Base`), never the bare `Base` identity every seed's
     owner-recovery logic produces, unless something else (inheritance, an
     override) independently triggers the bare node's creation. Confirmed
     this is not a fix-specific regression: the exact same isolated-class
     shape already failed to seed a plain `virtual void run()` with no
     override or inheritance anywhere in scope, via the pre-existing
     leaf-virtual-method seed, before this fix touched anything.
   - **A seventeenth finding, confirmed real and fixed: a leading same-line
     block comment before a real directive was never recognized.** A real
     compiler treats `/* note */ #ifdef X` as a live directive (the comment
     is whitespace to it), but `scan_conditional_regions`'s directive-family
     regexes are all anchored `^\s*#`, so a leading, still-present comment
     defeated every one of them — not just missing the guarded declaration's
     macro edge, but, when nested, leaving no stack frame for the
     un-recognized inner directive so its own `#endif` popped the enclosing
     guard's frame instead (reproduced empirically: an `#ifdef OUTER` /
     `/* note */ #ifdef INNER` / `#endif` / `#endif` nest truncated `OUTER`'s
     region three lines early). Fixed with a new `_strip_leading_inline_
     comment()`, applied identically in both `scan_conditional_regions` and
     `_macro_definition_lines` — deliberately scoped to a comment that both
     opens and closes on the same line (cross-line block-comment state is
     `_lines_starting_inside_block_comment`'s own, separate, already-applied
     job) and deliberately not `//` (a genuine `// #ifdef X` line has its
     directive commented out for real, unlike a same-line-closed `/* */`).
   - **An eighteenth finding, confirmed real and fixed: an unnamed callback
     parameter collapsed every unrelated registration API onto one node.**
     `void reg(void (*)(int));` — a common prototype-only registration
     shape — has no `mangledName`/`name` for `call_graph._identity` to read,
     so `_index_walk`'s per-parameter identity resolved to `""`; every
     unnamed callback slot in the whole codebase then joined onto the
     identical `decl://` node, making the otherwise high-confidence
     `DECL_REGISTERS_CALLBACK` edge ambiguous across unrelated APIs.
     Reproduced empirically (a hand-built two-callee fixture: both
     `reg_a`/`reg_b`'s own unnamed slots joined onto one node before the
     fix). Fixed by falling back to `f"{callee_identity}#param{position}"`
     when the parameter's own identity is empty — safe because an unnamed
     parameter can never be referenced by name from within its own function
     body (or anywhere else), so the fallback only needs to be stably unique
     per callee, never to match some other module's own identity scheme.
   - **A nineteenth finding, confirmed real and fixed: a multi-line-opened
     comment closing mid-line before a real directive was skipped
     wholesale.** `/* opening\n*/ #ifdef X` — the second line "starts inside"
     the carried-over block comment per `_lines_starting_inside_block_
     comment`, so the previous per-line gate (skip the whole line or don't)
     never resumed scanning after that specific comment actually closed,
     omitting the guard and, nested, letting its `#endif` pop the enclosing
     guard's frame instead (reproduced empirically, same desync shape as
     the eighteenth finding's sibling). Fixed with `_line_after_carryover_
     comment_closes()`: finding the first `"*/"` on a line already known to
     start inside a comment is unambiguously that comment's own close (an
     open block comment has no internal string/quote semantics to worry
     about), and the live remainder is fed through the same
     `_strip_leading_inline_comment()` path the seventeenth finding's fix
     already established — applied identically in `scan_conditional_
     regions` and `_macro_definition_lines`.
   - **A twentieth finding, confirmed real and fixed: C++ list-
     initialization of a callback slot was never recognized.** `handler_t
     slot{my_handler};` wraps the identical `ImplicitCastExpr`/
     `FunctionToPointerDecay` `_address_taken_function` already recognizes
     inside an `InitListExpr` — verified against real Clang 18 output — so
     passing it straight through returned `None`, silently omitting the
     `DECL_TAKES_ADDRESS_OF` edge (and everything Part A's join could have
     built on it). Fixed by also unwrapping a single-element `InitListExpr`,
     the same way `ParenExpr`/an explicit cast are already unwrapped; scoped
     to exactly one element since a scalar (function-pointer) type
     type-checks to exactly one initializer in valid C++, so a real
     aggregate/multi-element list can never be accidentally swallowed.
   - **A twenty-first finding, confirmed real and fixed: a virtual
     overloaded operator invoked through a base reference/pointer was never
     classified as virtual.** `B &b; b();` — a `CXXOperatorCallExpr`, not a
     `CXXMemberCallExpr` — has a plain `DeclRefExpr` as its own callee, not
     a `MemberExpr`, so `_classify_call`'s virtuality check (which only ever
     looked at `CXXMemberCallExpr`) never fired, silently excluding a real
     derived `operator()` override from `VIRTUAL_CALL_MAY_DISPATCH_TO`.
     Reproduced empirically against real Clang 18. Extending
     `_classify_call` to also check `CXXOperatorCallExpr` alone wasn't
     enough: a `DeclRefExpr`'s own compact `referencedDecl` stub never
     carries `virtual`/`inner` (`OverrideAttr`/`FinalAttr`) the way the full
     declaration node does, and — unlike a `MemberExpr`'s string-shaped
     `referencedMemberDecl`, which `_find_referenced_decl` already resolved
     to the full node via `member_index` — a dict-shaped `referencedDecl`
     was returned as-is, never upgraded. Fixed by extending
     `_find_referenced_decl` to also resolve a dict-shaped stub's own `id`
     through `member_index` when indexed, falling back to the stub itself
     otherwise (preserving `_POINTER_DECL_KINDS` function-pointer-call
     classification for `VarDecl`/`ParmVarDecl`/`FieldDecl` refs, which are
     never added to `member_index`). An explicitly-qualified operator call
     (`b.Base::operator()()`) is a narrower, separately-verified case not
     attempted here.
   - **A twenty-second finding, investigated and deliberately NOT fixed:
     backslash-newline line splicing before a `//` comment.** A real
     preprocessor removes every `\<newline>` pair before tokenizing, so a
     `//` comment ending in `\` carries onto the next physical line too —
     including anything that looks like a directive. Reproduced
     empirically: a `#ifdef OUTER` / `// comment continues \` /
     (spliced-away) `#endif` / real trailing `#endif` nest truncates
     `OUTER`'s region early, the identical desync shape the four other
     comment-tracking fixes in this plan close for a different mechanism
     each (`/* */` nesting, a leading same-line comment, a carried-over
     multi-line comment closing mid-line, and now this one). Deliberately
     not attempted: unlike those three, correctly handling splicing needs a
     genuinely different per-line carry-over mechanism than the existing
     `in_block` state, threaded through every line-number-keyed caller
     without disturbing their existing physical-line-number contract
     (clang's own AST ranges are physical-line-based too) — a same-slice
     fix risks getting that fidelity subtly wrong, worse than the current,
     honest gap. Documented in the module's own docstring ("a third
     accepted, documented limitation") and pinned by a dedicated regression
     test rather than silently accepted.
   - **A twenty-third finding, confirmed real and fixed: `#if defined X`
     (no parentheses) was unrecognized.** `_IF_DEFINED_RE`/`_IF_NOT_DEFINED_
     RE` required `defined(X)`'s parentheses, but `defined X` is equally
     valid, real-compiler-accepted preprocessor syntax — the unparenthesized
     form fell through to the unmodeled `_IF_RE` fallback with no
     diagnostic, so `fold_macro_graph()` could still stamp the pass as
     fully covered despite silently missing the guard's
     `MACRO_CONTROLS_DECL` edge. Fixed by widening both regexes to a proper
     alternation between the parenthesized and bare forms (not
     independently-optional `\(?`/`\)?`, which would have accepted a
     mismatched operand like `defined(X` or `defined X)` as if it were
     valid) — a malformed, unbalanced operand still falls through to the
     unmodeled fallback rather than being guessed at.
   - **A twenty-fourth finding, confirmed real and fixed: C++23's
     `#elifdef`/`#elifndef` desynced the enclosing guard, wrongly, not just
     silently.** `_ELIF_RE` shared the identical `\b`-after-"elif" word-
     boundary gap the `_IF_RE` fix already closed for `#ifdef`/`#ifndef` —
     unrecognized, `#elifdef B` matched no pattern at all, leaving the
     ORIGINAL `#if`'s frame open across it. Reproduced empirically:
     `#if defined(A) ... #elifdef B ... #endif` attributed the `#elifdef
     B` branch's own declarations to `A` with `negated=False` — a wrong,
     high-confidence `MACRO_CONTROLS_DECL` edge, worse than the "silently
     missing" shape most of this family's other gaps produce. Fixed by
     widening `_ELIF_RE` to also match `#elifdef`/`#elifndef`, correctly
     marking the whole chain unmodeled (this module's existing, deliberate
     "don't guess at `#elif`'s branch semantics" contract).
   - **A twenty-fifth finding, investigated and deliberately NOT fixed: a
     block comment appearing mid-directive** (`#/**/ifdef X`,
     `#if/**/defined(X)`). A real preprocessor treats `/* */` as whitespace
     anywhere, but every directive-family regex here only tolerates plain
     whitespace at those positions, and the leading-comment fix only
     strips a comment BEFORE the `#`, not one embedded after it.
     Reproduced empirically: neither shape matches any pattern at all, not
     even the unmodeled fallback — the directive is invisible to this
     scanner entirely. Deliberately not attempted: closing it needs a
     systematic rewrite replacing every `\s*`/`\s+` gap across the whole
     directive-regex pattern set, not a narrow addition, and mid-token
     block comments inside a preprocessor directive are exceedingly rare,
     deliberately obfuscated styling in practice — unlike the leading/
     multi-line comment placements already fixed in this plan, which are
     realistic, commonly-seen shapes. Documented in the module's own
     docstring ("a fourth accepted, documented limitation") and pinned by
     a dedicated regression test.
5. **Full type-role coverage — done for seven of the nine roles as
   originally worded; one (non-type template argument) turned out to name a
   narrower role than implemented, corrected below; the ninth
   (concept/constraint) is investigated and deliberately deferred with its
   evidence recorded.** The item's list was: variable type, typedef target,
   alias-template target, enum underlying type, non-type template argument,
   default template argument, concept/constraint dependency, function-pointer
   signature, member-pointer type — feeding the Phase 2 per-role coverage
   matrix (`inline_graph_fold.ROLE_COVERAGE_MATRIX`, ADR-046 D3). Every claim
   below was checked against **real Clang 18 AST output** before being
   written, not inferred from the role names: the audit found the nine split
   three ways rather than nine-missing.
   **Scope-boundary correction (Codex review, fresh evidence, second
   round):** the original wording "non-type template argument" was
   implemented as `template_param` (below) — the non-type *parameter*'s own
   declared type (`template <detail::Handle H>` depends on
   `detail::Handle`). That is real and correct for what it is, but it is
   not the same claim as resolving a non-type template **argument**'s own
   value: what a `Holder<&detail::f>`-shaped specialization's
   `TemplateArgument.decl` cross-reference points at (confirmed against
   real Clang 18: `ClassTemplateSpecializationDecl`'s own child
   `TemplateArgument` node carries `{"decl": {"kind": "FunctionDecl",
   "name": "f", ...}}`, a clean, non-heuristic reference — but `_walk_types`
   deliberately skips edge emission for `ClassTemplateSpecializationDecl`
   nodes entirely, for an unrelated, already-documented reason: attributing
   one specific instantiation's dependency to the shared generic template
   node would misattribute it). That is exactly item 1's `TEMPLATE_USES_DECL`
   (`template_graph.py`) — this item's `template_param` role did not close
   it (the original bullet's ambiguous wording should not have been read
   as claiming it did); it was later closed as its own follow-up under
   item 1, see that entry.
   - **Five were already covered**, by an existing role, and needed no
     producer — the same "closed a gap by discovering pre-existing coverage"
     pattern this plan used for ADR-057 D6's tier 1 and item 3's
     `DECL_OVERRIDES_DECL`. *Variable type* is `_emit_var_decl_edge`'s `var`
     role and *typedef target* is `_emit_alias_edge`'s `alias` role, both
     pre-existing. *Alias-template target* turned out to reach that same
     `alias` role for a structural reason worth recording: clang nests the
     real `TypeAliasDecl` inside the `TypeAliasTemplateDecl`, and
     `_walk_types` recurses into a template wrapper's children with the
     unchanged scope, so `template <class T> using Ptr = detail::Impl *;`
     arrives as an ordinary `TypeAliasDecl` and emits `api::Ptr ->
     detail::Impl` with no new code at all. *Member-pointer type* (`int
     Owner::*`, `void (Owner::*)(int)`) and *function-pointer signature*
     (`void (*)(detail::Impl *)`) are both reached by
     `_resolve_nested_type_names`'s existing pointer-to-member/declarator
     handling, so they surface under whichever role the enclosing
     declaration carries rather than needing one of their own. All five are
     now pinned by `tests/test_type_graph_roles.py` so a later refactor of
     the walk can't silently drop what was never explicitly claimed.
   - **Three were genuinely missing and are now implemented**, each with a
     real AST source and each landing on the node the entity's own edges
     already use. `enum_underlying` (`TYPE_HAS_FIELD_TYPE`): an `EnumDecl`
     carries **no `type` key at all** — the underlying type lives in its own
     `fixedUnderlyingType` object — so the shared typedef path this kind
     already took read an empty spelling and emitted nothing, and `enum class
     Color : detail::Handle` produced no dependency on the private alias it
     is laid out as. `qualType` (as written, `"detail::Handle"`) is read
     rather than the sibling `desugaredQualType` (`"int"`), which has already
     lost the identity the edge exists to name; an unscoped enum carries no
     `fixedUnderlyingType` at all and a scoped one with no written type gets
     an implicit `"int"` the pre-existing fundamental-type filter drops, so
     neither shape produces a noise edge. `template_param` and
     `default_template_arg` (both on `TYPE_HAS_FIELD_TYPE` *or*
     `DECL_HAS_TYPE`) cover a non-type template parameter's own type
     (`template <detail::Handle H> struct Slot`) and a parameter's default
     *type* argument (`template <class T = detail::Impl> struct Box`). The
     load-bearing correctness property is the **src identity**: a template
     wrapper (`ClassTemplateDecl`/`FunctionTemplateDecl`/`VarTemplateDecl`/
     `TypeAliasTemplateDecl`) is not a node in this graph, and its parameters
     are direct children of the *wrapper* while the templated entity is a
     sibling child — so `_templated_entity_src` re-derives the templated
     child's identity exactly the way the walk does for that child's own kind
     (a record/alias → `record_type` node, hence `TYPE_HAS_FIELD_TYPE`; a
     function/variable → `source_decl`, hence `DECL_HAS_TYPE`). That matters
     concretely for a function template, whose pattern `FunctionDecl` carries
     **no `mangledName`**: both sides must fall through the same
     qualified-name+signature-hash identity or the constraint would sit on an
     orphan node nothing else reaches. Verified end to end against real
     clang, not just at the unit level. Two default-argument shapes are
     deliberately **not** emitted because clang's JSON carries no dependency
     to emit: a *non-type* parameter's default **value** (`template
     <detail::Handle H = detail::K>` dumps as `{"kind": "TemplateArgument",
     "isExpr": true}` with no `type` — the referenced constant is a nested
     `DeclRefExpr`, a `DECL_REFERENCES_DECL` question rather than a type
     role), and a *template template* parameter's default (`template
     <template <class> class C = detail::Def>` dumps as a bare `{"kind":
     "TemplateArgument"}` — neither a type nor a name to resolve), so both
     degrade to no fact rather than a guessed one (ADR-028 D3). Both
     non-emissions are pinned by integration tests against the compiler
     itself, so a future clang that *does* carry them fails a test instead of
     leaving a silent gap.
   - **A real collector-upgrade false positive was found and fixed as a
     direct consequence of adding these three roles (Codex P1 review on PR
     #712, confirmed by direct reproduction).** `ROLE_COVERAGE_MATRIX`/
     `role_pass_covered()` (ADR-046 D3, Phase 2) was built for exactly this
     situation but had **zero production consumers** — only ever stamped by
     `inline_graph_fold._mark_role_coverage`, never read by
     `source_graph_findings._common_dependency_edge_kinds()`, the function
     that actually decides which dependency-edge kinds are safe to
     version-diff. That function trusts a whole `TYPE_HAS_FIELD_TYPE`/
     `DECL_HAS_TYPE` family once both sides confirm the coarse
     `extractor_passes["type_graph"]` flag — so a persisted graph collected
     *before* this item's three new roles existed (flag set, no role key,
     since that producer version never emitted or tracked them) compared
     against one collected *after* (flag set, role key set, first-ever edge
     riding the new role) reported a false `PUBLIC_API_INTERNAL_DEPENDENCY_
     ADDED` purely from re-running a newer abicheck version over unchanged
     source. Fixed with `_role_coverage_disagrees()`, a final, isolated,
     monotonic (subtraction-only) filter over
     `_common_dependency_edge_kinds()`'s existing result: a kind stays
     trusted only when both sides' `extractor_passes`/`narrowed_passes`
     agree, role key by role key, on which `ROLE_COVERAGE_MATRIX` roles were
     actually examined — read directly with no family-flag fallback, since
     that fallback is exactly what let an absent role key silently read as
     "covered." Verified: the version-skew repro now produces zero findings;
     a same-version repro (both sides confirm the same role key) still
     correctly produces one; the full relevant existing test suite (every
     module the fix or its call sites touch) shows zero regressions; three
     new dedicated regression tests pin both directions plus a control case
     (a kind outside `ROLE_COVERAGE_MATRIX`, e.g. `DECL_CALLS_DECL`, is
     unaffected) in `tests/test_l3l4l5_new_kinds.py`. A companion gap in the
     same fix, found by a later review round: the header-only pass
     (`header_graph.py`, ADR-041 header-only-graph addendum) reuses the
     identical `type_graph.parse_clang_ast_types()` walker but never stamped
     a `ROLE_COVERAGE_MATRIX` key at all, so two header-only-collected graphs
     compared before/after this same abicheck upgrade would read as vacuous
     agreement (both sides absent, forever) rather than "coverage unknown" —
     `build_header_only_graph` now stamps role coverage under its own
     `header_type_graph` pass alias the same way the build-integrated pass
     does, and `_role_coverage_disagrees()` checks both the build-integrated
     and header-only key for each side.
   - **Concept/constraint dependency — investigated, deliberately NOT
     implemented, evidence recorded so the follow-up doesn't re-derive it.**
     A public template constrained by an internal concept (`template
     <detail::Storable T> struct Keeper`, or a `requires detail::Storable<T>`
     clause) is a real dependency, but clang's JSON AST **does not name the
     concept at the use site**: a `ConceptSpecializationExpr`'s key set is
     exactly `{id, inner, kind, range, type, valueCategory}` — no name, no
     `conceptId`, no `referencedDecl` — and grepping a whole real dump for
     the concept's own spelling finds it exactly *once*, on the `ConceptDecl`
     itself. There **is** a usable join, verified across three use sites and
     two distinct concepts in one TU: the nested
     `ImplicitConceptSpecializationDecl`'s `loc.offset` is byte-for-byte the
     declaring `ConceptDecl`'s own `loc.offset`, so `(file, offset)` resolves
     the constraint deterministically — but consuming it needs the sticky
     whole-document file cursor (clang omits `loc.file` when unchanged, the
     same quirk `macro_graph.py` documents), which this module tracks only
     file-coarsely today. The larger blocker is the **node model**, not the
     resolution: a concept is a named declaration that is not a type, and
     there is no `concept` node kind in `source_graph.NODE_KINDS` — routing
     it through `DECL_REFERENCES_DECL` (`source_decl` → `source_decl`) would
     put a *class* template's constraint on a `decl://` node while every
     other edge that class has lives on its `record_type` one, fragmenting
     the identity this item's other two roles were careful to keep single.
     Registering a new node kind is exactly the step item 3 treated as
     notable when it introduced `vtable`, so this gets its own scoped
     decision rather than a drive-by addition here.
   - **Wrapper-to-entity bridge — a real, pre-existing gap, found by Codex
     review on PR #712 during this item, recorded as follow-up work rather
     than attempted as a side effect of a role-coverage change.** When the
     build-integrated graph has L4 source evidence but no pre-attached L2
     header graph, `source_extractors/clang.py`'s `_emit_template`
     represents a class/function template *wrapper* itself
     (`ClassTemplateDecl`/`FunctionTemplateDecl`) as an L4 `template`-kind
     `SourceEntity`, joined onto the L5 graph as `decl://<qualified-name>`
     — but `type_graph.py`'s own field/base/role edges for the identical
     templated entity (including this item's three new roles) all land on
     `type://<qualified-name>` (for a class template) or a
     signature-hashed child declaration (for a function template), via the
     *ordinary* record/function walk `_walk_types` already does regardless
     of the entity being templated. The two never join: a standalone
     build-source-collection or directory/package `compare` that only has
     the L4-attached wrapper node public, with no L2 header graph also
     attached, cannot reach the L5 field/role edges from it —
     `public_to_internal_dependency` (`crosscheck.py`) misses exactly the
     template-parameterized private-type dependency this item's new roles
     exist to detect, for that one collection shape. **Confirmed
     pre-existing, not introduced by this item**: checked against real
     Clang 18 for the item's own headline case (`template <detail::Handle
     H> struct Slot { detail::Impl member; };`) — both the new
     `template_param` role and the pre-existing `field` role emit their
     edges from the identical `type://api::Slot` node (`_walk_types`
     reaches a class template's inner `CXXRecordDecl` through the ordinary
     record branch regardless of whether it's templated), so a public
     class template's plain, non-template-parameterized private field is
     equally unreachable from the L4 wrapper today, with or without this
     item's new roles. A fix is worth having — `_templated_entity_src`
     (this item's own machinery) already proves the *identity derivation*
     for "which node does a templated entity's own edges land on" is
     already correct and shared; what's missing is a bridge edge from the
     L4 `template`-kind entity's `decl://` node to that same
     `type://`/`decl://` identity — but it changes how *every* L4
     `template`-kind `SourceEntity` joins the L5 graph, not just the three
     roles this item added, so it needs verification against the
     pre-existing class-template field/base edges and the FP-rate gate
     before landing, not a drive-by addition to a role-coverage change.
6. **Object/link provenance — done.** `abicheck/buildsource/archive_graph.py`
   is the real `ar`-index introspection pass: a pure parser over the
   archive's own linker-written symbol index (GNU `/`/`/SYM64/` and
   BSD/Mach-O `__.SYMDEF`/`__.SYMDEF_64`, both plain and thin-archive
   flavors), driven by `inline_graph_fold.fold_archive_graph` over every
   `static_library` node `source_graph._fold_link_provenance` already
   creates from `BuildEvidence.link_units`. Populates `archive_member`
   nodes and the previously schema-only `ARCHIVE_CONTAINS_OBJECT`/
   `OBJECT_DEFINES_SYMBOL` edges, so a removed-symbol finding can localize
   to "`cache_dispatch.o` in `libinternal_dispatch.a`" via
   `archive_graph.defining_members`. Needs no compiler (unlike items 1-5
   above), so it runs whenever an archive link input is present, gated only
   on finding and reading the archive on disk — never on clang. An
   `OBJECT_DEFINES_SYMBOL` edge only ever joins onto a `binary_symbol` node
   the graph already carries (ADR-057 D1's "one shared node id is the whole
   join mechanism" rule, reapplied here), so a real static library's
   thousands of internal-only indexed symbols mint no new node.
   `linker_script`/`export_map`/`comdat_group` (a member archive's other
   three reserved node kinds) stay unpopulated — no normalized data source
   for those; a real static-library symbol *removal* can already be
   localized end to end today.

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
with per-piece evidence levels (feeds Phase 3's `root_cause_id`). **The
correlator itself is done**: `abicheck/impact/correlation.py`'s
`correlate_root_causes`/`RootCauseGroup` group the four kinds above by
shared symbol identity (`caused_by_type`-else-`symbol`, the same precedence
`reporter_markdown._root_cause_key_and_display` uses) and rank each
member's evidence level (`artifact_proven` → `call_graph_overapprox` →
`call_graph_proven` → `consumer_proven` → `runtime_proven`) — not purely
from `ChangeKind`: promoted when `Change.reachability_kind` states a
stronger tier (`appcompat._attach_consumer_impact` enriching a shared
`FUNC_REMOVED` in place), downgraded when an `INTERNAL_SYMBOL_REQUIRED_BY_
PUBLIC_API` finding's own proof path is `internal_leak`'s
`"overapprox: "`-prefixed over-approximation of a virtual/function-pointer
dispatch target. **Wired into JSON/SARIF (this pass)**: every finding that
is a member of one of the correlator's multi-piece groups now carries
`impact_assessment.root_cause_evidence` (`evidence_level` for this finding,
`strongest_evidence_level`/`evidence_levels` for the whole group), and JSON
`--report-mode root-cause`'s `root_causes[]` groups gain the same
`strongest_evidence_level`/`evidence_levels` fields — computed via a new
`reporter_markdown.root_cause_evidence_lookup_for_changes`, threaded through
`reporter.py`/`sarif.py` alongside the existing `root_cause`/
`impact_root_cause` plumbing (schema 2.29). Deliberately conservative: this
*annotates* the existing `root_cause_id`/`impact_group_id` grouping (still
`reporter_markdown`'s independent `caused_by_type`-else-`symbol` rule,
unchanged) with the correlator's evidence ranking rather than replacing that
grouping's identity scheme or making `impact_group_id` diverge from
`root_cause_id` — the correlator's own `root_cause_id` already hashes the
identical key, so no re-keying was needed, and every existing
`root_cause_id`/`impact_group_id` value for every pre-existing report is
byte-for-byte unchanged. **Still open**: the eight new detector/overlay
`ChangeKind`s below (a materially larger, differently-shaped follow-up —
new producers, new example pairs, new FP-rate corpus cases — deliberately
not attempted in the same pass as the wiring above), and `impact_group_id`
actually diverging from `root_cause_id` by re-bucketing findings through the
correlator's own groups rather than only annotating the existing ones.

**The wiring above needed three follow-up review rounds to land correctly**
(all Codex, same PR): (1) the per-finding lookups were built from `changes`
alone, so a finding correlating only via a scoped-only
(`--used-by`/`--required-symbol`) sibling got no evidence at all —
`RootCauseCorrelator` needs the real sibling `Change` object, not just its
`caused_by_type` string, to recognize a pair as a group; (2) the
`root_causes[]` group-level rollup matched a report group against a
correlator group by `root_cause_id` **equality**, which is wrong whenever
the two grouping schemes disagree on membership — a bare-symbol pair
sharing no `caused_by_type` (`--used-by --verify-runtime`'s real shape) is
one correlator group but two singleton report groups, so the hashes never
matched even though each finding's own per-finding evidence already showed
membership; fixed by folding each group's own members' already-correct
evidence directly instead of re-deriving membership by id; (3) the same gap
existed a layer up, in `cli_compare_fold.py`'s own `--report-mode
root-cause` fold-in (`_add_entries_to_root_causes`), which appends a
scoped-only entry to an existing-or-new group *after* the JSON serializer
already built it, but never recomputed that group's own evidence summary
afterward. Worth recording as its own lesson before attempting any of the
eight new detectors below: a naive `root_cause_id`-equality join between
two independently-computed groupings is not safe by default whenever either
grouping has its own fallback/singleton rule — the correlator's grouping is
strictly *coarser* than the report's in exactly the no-`caused_by_type`
case, and nothing about that asymmetry is visible from either grouping's
own code without deliberately tracing a bare-symbol, no-`caused_by_type`
input through both.

**A related, adjacent gap was found (not fixed) while scoping
`GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`'s "generalizes
`SUPPRESSION_REACHABILITY_UNKNOWN`" description above, and is worth
recording before anyone else picks that item up.** `Suppression.
_passes_reachability_gate` (`suppression.py`) has *two* reachability modes
whose behavior on a graph-coverage gap differs, and only one of them has a
diagnostic today. `reachability: "proven-unreachable-only"` treats
`Change.reachability_state == UNKNOWN` as a **withheld match** unless
`allow_unknown_reachability: true` — this is exactly what
`SUPPRESSION_REACHABILITY_UNKNOWN` reports. `reachability:
"unreachable-only"` (a different, valid value) instead gates on
`not change.public_reachable` — and `Change.public_reachable` defaults to
`False` the same way whether it was *proven* `False` or simply never
examined (`UNKNOWN` reachability with insufficient graph coverage), so a
`reachability: "unreachable-only"` rule can **silently succeed** at
suppressing a change graph coverage never actually cleared, with no
diagnostic at all — a real gap, structurally similar to the one
`SUPPRESSION_REACHABILITY_UNKNOWN` closed for the other mode, but on the
*opposite* failure direction (this one risks a false negative — silently
accepting a suppression that should have been withheld — rather than a
false positive). A single `GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`
`ChangeKind` that literally "generalizes" the existing case (by simply
reusing its detection path under a new name) would not close this gap; closing
it for real needs either extending `unreachable-only`'s own gate to
distinguish proven-`False` from `UNKNOWN` (a semantic change to a suppression
mode real policy files already rely on — needs its own compatibility
analysis, not a drive-by) or a second, independently-designed diagnostic
covering this mode specifically. Not attempted here — flagged rather than
guessed at, per this file's own "known gaps over risky reactive patches"
convention.

**Two more of the eight, investigated (not implemented) in this same pass —
both turn out to be a scoping question first, not an extraction gap:**

- **`CONSUMER_IMPACT_PATH_CONFIRMED`.** `appcompat.py`'s consumer-overlay
  pipeline (`_has_impact_evidence`, `_enrich_covered_changes`,
  `_merge_consumer_impact_paths`) already computes and surfaces exactly this
  information — a confirmed consumer→symbol proof path — today, as an
  in-place mutation of the *existing* `Change`'s `impact_assessment` fields
  (`reachability_kind`, `reachability_state`, the proof-path plumbing this
  plan's Phase 3/4 sections already document as DONE), not as a standalone
  finding. Minting a *new* `ChangeKind` for the same fact would mean either
  (a) a second, parallel representation of information a report already
  carries once (the two-representations-of-one-fact drift this repo's own
  `docs/AGENTS.md` governing rule and `change_registry.py`'s "one
  `ChangeKindMeta` entry" convention both exist to prevent), or (b)
  redefining what the overlay does — attaching a *new* raw finding instead
  of annotating an existing one — which is a real design change to
  `appcompat.py`'s enrichment contract, not a new-detector addition. The
  table entry above ("impact overlay on an existing raw break, not a new raw
  break") already states this outcome as the intent; what's newly confirmed
  is that the current code already delivers it, so there may be nothing left
  to build here beyond documentation — worth a maintainer decision on
  whether to close this row entirely rather than implement it.
- **`USE_CASE_IMPACT_CONFIRMED`.** `abicheck/impact/use_cases.py`'s own
  module docstring is explicit that this is deferred pending new CLI
  surface: `explain_use_case_impact()` exists and is wired only through
  `project validate-use-cases --against-new` (an opt-in diagnostic command),
  not through `compare`'s own report pipeline. Surfacing this as a
  `compare`-time `ChangeKind` needs a real design decision this plan has not
  made — a new `compare --use-cases <manifest>` flag (or equivalent),
  `REPORT_SCHEMA_VERSION` bump, and new FP-gate examples proving a use case
  that doesn't reach the changed branch stays compatible (this is exactly
  `case203` below) — not a drive-by extension of the existing
  `project`-group command. Per this file's own root-command admission bar
  (`AGENTS.md` "Adding a new top-level command"), this also needs to clear
  that bar or land as a `compare` option instead; not attempted here.

**A fourth item was scoped for implementation attempt in this pass
(`PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED`) and deliberately not attempted,
based on evidence rather than a guess.** `abicheck/buildsource/
virtual_dispatch_graph.py` already emits the two graph facts a comparison
detector would need (`VIRTUAL_CALL_MAY_DISPATCH_TO`,
`TYPE_HAS_VTABLE`) — the raw data exists. What stopped the attempt is this
same plan's own recorded history for *that exact module* and its three
siblings (`callback_graph.py`, `macro_graph.py`, `template_graph.py`,
directly above and below this entry): each required double-digit rounds of
Codex review to reach its current DONE/PARTIAL state, and the findings in
those rounds were not stylistic — coverage-stamping bugs that made a
degraded extraction silently read as complete, propagation bugs that lost a
graph fact between fold and report, and at least one review round finding a
correctness bug in a *previous* review round's own fix. A new detector
consuming `VIRTUAL_CALL_MAY_DISPATCH_TO`/`TYPE_HAS_VTABLE` inherits every
one of those failure modes by construction (comparing two runs' worth of
already-fact-checked-fragile graph output, across old/new coverage that can
differ), plus its own new one: this plan's explicit constraint that "a
possible-target-set change must never read as a confirmed break" (echoed
in `resolution` always being `"overapprox"` in the two Part A/B functions'
own docstrings) means the comparison logic itself — not just the extraction
— has to get old-vs-new overapprox-set diffing right without silently
producing a false `BREAKING` the first time a target set merely reorders or
a base's coverage degrades between runs. Building and shipping that
correctly, with its own FP-rate corpus cases (`case197` below) and without
a live oneAPI/multi-round-Codex-review cycle available in this session, is
not achievable to the correctness bar this codebase's own `AGENTS.md`
"Known gaps over risky reactive patches" convention sets — recorded here as
a scoped, concrete blocker (not "too hard, unspecified") for whoever picks
this row up next: start from `virtual_dispatch_graph.py`'s own two
functions, budget for the same class of review-round findings this plan's
Phase 5 section already itemizes for the identical module, and do not skip
straight to a detector without first re-reading that history.

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
abicheck/buildsource/graph_facts.py  # GraphFact/FactConflict/merge, relation_key/occurrence_id (Phase 2 D1/D2, DONE); CONSUMER_NODE_KINDS/CONSUMER_EDGE_KINDS/TEMPLATE_NODE_KINDS/TEMPLATE_EDGE_KINDS/LINK_PROVENANCE_NODE_KINDS/LINK_PROVENANCE_EDGE_KINDS (Phase 4 D1 + Phase 5 items 1/6, DONE — here rather than source_graph.py, which is at its line cap)
abicheck/buildsource/graph_impact.py  # select_preferred_graph_path, attach_impact_metadata, _path_occurrence_id (Phase 2 D6/ADR-052 Slice 6, DONE — landed here, not under impact/)
abicheck/buildsource/entity_resolver.py  # EntityResolver/EntityConflict (Phase 2 D4, DONE — scoped implementation)
abicheck/buildsource/archive_graph.py  # ar-index introspection (Phase 5 item 6, DONE — archive_member/ARCHIVE_CONTAINS_OBJECT/OBJECT_DEFINES_SYMBOL)
abicheck/buildsource/template_graph.py  # Clang template-instantiation pass (Phase 5 item 1, DONE — template_decl/template_instantiation, DECL_INSTANTIATES_TEMPLATE/TEMPLATE_USES_TYPE/INSTANTIATION_EMITS_SYMBOL/TEMPLATE_USES_DECL)
abicheck/buildsource/template_graph_value_decls.py  # index_value_decls/arg_label_spelling, split out for TEMPLATE_USES_DECL (Phase 5 item 1 follow-up, DONE — leaf module, no import of template_graph.py even under TYPE_CHECKING)
abicheck/buildsource/template_graph_extractor.py  # ClangTemplateGraphExtractor, split out of template_graph.py (same line-cap reason); re-exported via a lazy __getattr__ shim
abicheck/buildsource/macro_graph.py  # Clang + raw-text macro/config-dependency pass (Phase 5 item 2, DONE — MACRO_CONTROLS_DECL/DECL_USES_MACRO)
abicheck/buildsource/virtual_dispatch_graph.py  # pure graph transform, no clang (Phase 5 item 3, PARTIAL — VIRTUAL_CALL_MAY_DISPATCH_TO/TYPE_HAS_VTABLE; DECL_OVERRIDES_DECL already satisfied by override_graph.py, VTABLE_SLOT_MAPS_TO_DECL reserved)
abicheck/buildsource/callback_graph.py  # Clang callback/function-pointer pass + pure join (Phase 5 item 4, PARTIAL — DECL_REGISTERS_CALLBACK/DECL_TAKES_ADDRESS_OF/CALLBACK_MAY_INVOKE; FUNCTION_POINTER_HAS_SIGNATURE populated as a node-level fact instead of an edge)
abicheck/internal_leak.py   # TraversalPolicy + effect_transitions (Phase 2 D5, DONE — landed here, not a separate impact/traversal.py)
abicheck/impact/
    model.py           # ImpactAssessment, GraphProofPath, FindingDecision (Phase 3 slices 1/7, DONE — ADR-052)
    engine.py           # assess_change(...) (Phase 3 slices 1/7, DONE — ADR-052)
    correlation.py       # RootCauseCorrelator (Phase 6, DONE — correlate_root_causes/RootCauseGroup; wired into JSON/SARIF root_cause_evidence, schema 2.29)
    root_causes.py
    consumer_graph.py    # Phase 4 slice 1, DONE — ADR-057 (consumer graph + the source join)
    use_cases.py         # Phase 4 slice 2, DONE — ADR-057 amendment (manifest + use_case/test_case graph join; trace ingestion still not started)
docs/learn/impact-analysis.md          # Phase 3 slices 1/6/7 + Phase 4's consumer join (ADR-057), DONE
docs/reference/source-graph-schema.md     # Phase 2 D1-D6 identity/merge/traversal-policy schema, DONE
docs/learn/graph-coverage.md           # Phase 1, DONE
docs/contribute/use-case-impact.md        # Phase 4 slice 2, DONE (manifest format, entrypoint mapping, test association, declared-vs-observed; trace ingestion documented as not-yet-built)
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
- `tests/test_entity_resolver.py` — Phase 2 D4 (scoped implementation),
  done: `EntityResolver.resolve`'s USR/mangled/qualified-signature fallback
  chain, idempotence, alias sharing + conflict recording for two v1 ids
  resolving to one canonical identity, `to_dict`/`from_dict` round-trip,
  `SourceGraphSummary.resolve_entities()` being opt-in and safe to call
  again, sparse `to_dict()` output, and v1-pack (`schema_version: 1`, no
  `entity_resolver` key) load compatibility.
- `tests/test_consumer_graph.py` — Phase 4 slice 1 (ADR-057), done: the
  schema registration and node-id pin that the join rests on,
  `build_consumer_graph`'s requirement/version edges and library scoping,
  `join_consumer_graph`'s fold-onto-one-shared-node and its
  deep-copy/no-mutation guarantee (asserted on object identity and fact
  membership, not node counts — every count is identical under the shallow
  bug), `explain_required_symbol(s)`' entry attribution/direct case/four
  degrade paths/batch equivalence, ADR-046 D6 tier 1 including the
  overapprox-still-wins rule, and an end-to-end `scope_diff_to_app` class
  covering both the enriched overlay and the byte-for-byte-unchanged
  no-graph run.
- `tests/test_use_cases.py` — Phase 4 slice 2 (ADR-057 amendment), done:
  schema registration, manifest parsing (valid, empty, and each malformed
  shape), `build_use_case_graph`'s entrypoint resolution by id and by label,
  the unresolvable-entrypoint silent-skip path, `test_case` node/edge
  emission independent of entrypoint resolution, `join_use_case_graph`'s
  fold-onto-one-shared-node and its deep-copy/no-mutation guarantee
  (asserted on object identity and fact membership, mirroring
  `test_consumer_graph.py`'s own pattern), and an end-to-end scenario joining
  a `use_case` node onto a library graph's public entry node.
- `tests/test_type_graph_roles.py` — Phase 5 item 5, done (a sibling split of
  `test_type_graph.py`, which sits at its own line-count cap): the three new
  roles' emission and node-identity properties, the five roles found already
  covered (pinned so a walk refactor can't drop them), the two deliberately
  unemitted default-argument shapes, an executable
  `ROLE_COVERAGE_MATRIX`-vs-parser agreement check in **both** directions (a
  role the parser emits but the matrix omits is an unclaimed capability; one
  the matrix claims with no producer is a false coverage claim), and
  `integration`-marked tests re-deriving every AST shape the fixtures encode
  from a real compiler.
- `tests/test_template_graph_value_decls.py` — Phase 5 item 1's
  TEMPLATE_USES_DECL follow-up, done (a sibling split of
  `test_template_graph.py`, at its own line-count cap): pointer-to-
  function/pointer-to-variable/reference NTTP resolution to the target's
  real identity, the unresolved-target and empty-decl-name skip cases, the
  label-collision regression (two instantiations sharing only a bare
  callee name across namespaces stay distinct), the `source_decl`-not-
  `type` node-kind routing and shared-node join, and an `integration`-
  marked end-to-end test against a real compiler.
- New per remaining phase: one `test_diff_<family>.py` per Phase 5 graph
  family. `tests/test_root_cause_correlator.py` (Phase 6), done: empty/
  ignored-kind/singleton no-op cases, two- through four-piece correlation
  and evidence-level ranking, first-seen group ordering, the
  outside-the-family non-join guard, the `reachability_kind`-based
  consumer-proven promotion (and its non-demotion of a stronger kind), the
  `"overapprox: "`-prefixed call-graph-path downgrade (and that an exact
  path isn't downgraded, and that it still yields to real consumer proof),
  and `to_dict()` shape.
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
