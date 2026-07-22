# ADR-046: Source Graph Identity v2 — USR-Based Entity Resolution and Evidence-Preserving Merge

**Date:** 2026-07-19
**Status:** Accepted — D1, D2, and D3 slices implemented: role-aware edge
identity (`GraphEdge.relation_key()`/`edge_relation_key`, the `relation_key`
half of D1's split — `occurrence_id` remains open), the evidence-preserving
node/edge merge (`GraphFact`/`FactConflict`/`merge_graph_facts`, replacing
`SourceGraphSummary.add_node`/`add_edge`'s v1 first-writer-wins drop), and the
per-(kind,role) coverage matrix for `inline_graph_fold.fold_type_graph`. D4
(`EntityResolver`/`SOURCE_GRAPH_VERSION = 2`), D5 (`TraversalPolicy`), and D6
(proof-path preference order) remain open — see "D1 implementation"/"D2
implementation"/"D3 implementation" below.
**Decision maker:** (pending — recorded per repository convention, the same
caveat ADR-048's header carries; a single-maintainer repo where merging the
implementing PR is the acceptance mechanism.)

---

## Context

ADR-031 defined the L5 `SourceGraphSummary` schema (`GraphNode`/`GraphEdge`,
`SOURCE_GRAPH_VERSION = 1`) and its node-id scheme (`abicheck/buildsource/
source_graph.py`'s `_decl_node_id`/`_type_node_id`/`_symbol_node_id`/…, each a
deterministic string hash of one identity signal — a mangled symbol, a
qualified name, a `SourceEntity.identity()` tuple). ADR-044's P0/P1 slices
(this repository's tri-state reachability work, most recently PR #607) then
built two independent walks on top of that v1 graph —
`internal_leak.compute_leak_paths` (layout/type-graph reachability) and
`internal_leak.compute_call_graph_leak_paths` (L5 call/reference-graph
reachability) — and, through eight-plus rounds of automated review on that
PR, exposed the same underlying shape of gap over and over: **the v1 graph
identifies an entity by exactly one signal at a time, chosen ad hoc per
producer, with no reconciliation when two producers disagree or use a
different signal for the same real declaration.**

Concretely, three problems recur across the codebase today:

1. **Identity fragmentation.** A single C++ declaration is name-addressed
   differently by different evidence: `dwarf_snapshot.py`/`elf_metadata.py`
   see its mangled linker symbol; `dumper_castxml.py`/`dumper_clang.py` see
   its demangled qualified name; `source_link.py`'s L4 replay sees a
   `SourceEntity.identity()` tuple (kind + qualified name + signature hash);
   `call_graph.py`/`type_graph.py`'s L5 extractors see whatever clang's AST
   dump names the node. `post_processing.py`'s `MarkReachability` (PR #607)
   already had to hand-roll a "try `root`, then try `c.qualified_name`"
   fallback at *every* lookup site into `reachable_types`/`call_reachable`/
   `known_type_names` — the graph itself does not resolve these to one
   entity, so every consumer re-implements a partial version of that
   resolution, and each hand-rolled fallback is a fresh chance to miss a
   case (as PR #607's review history shows).
2. **First-writer-wins merge.** `SourceGraphSummary.add_node` (`source_graph.py:344-348`)
   literally drops a second node registration for the same `id`, keeping
   only the first producer's `attrs`/`confidence`/`provenance`. When a
   build-integrated pass and a header-only pass (`header_graph.py`) both see
   the same declaration with different confidence or a different
   `consumer_compiled_body` value, whichever ran first silently wins — there
   is no `conflicts` record, and no way for a later consumer to know a
   disagreement even happened.
3. **Coverage is family-grained, not (kind, role)-grained.** `extractor_passes`
   (the completeness signal Phase 1's `_call_graph_fully_trusted` gates on)
   is one boolean per pass name (`"call_graph"`, `"type_graph"`). A producer
   that reliably covers a function's *return type* but has a known gap on
   *parameter types* (a real clang-plugin limitation noted in ADR-041) has no
   way to say so — the family flag is all-or-nothing, so a consumer either
   over-trusts a partially-covered family or under-trusts a fully-covered
   one.

This ADR is the "own ADR" gate the impact-analysis-layer plan
(`docs/development/plans/g29-impact-analysis-layer.md`, Phase 2) set for
itself: an identity/version-bump and merge-semantics change carries the same
bar ADR-044 D1 set for a pipeline-order fix — a recorded decision, not a
drive-by refactor.

## The one rule that does not change

Everything below is new L5 *graph* machinery. ADR-028 D3's authority rule is
unchanged: L3/L4/L5 evidence — however it is resolved, merged, or scored for
confidence — may explain, localize, or corroborate a break, but **never**
silently deletes one an artifact diff (L0/L1/L2) already proved. Nothing in
this ADR touches that boundary; `checker_policy.BREAKING_KINDS` membership
still requires artifact-level evidence.

## Decision

### D1. Split edge identity: `relation_key` vs. `occurrence_id`

`GraphEdge.key()` today is `(src, dst, kind)` — the same signature two
structurally different call sites collapse onto if they share endpoints and
kind (e.g. "used as return type" and "used as parameter type" both produce a
`DECL_HAS_TYPE` edge with the same `(src, dst)`; two calls to the same
function under different `#ifdef` branches both produce the same
`DECL_CALLS_DECL` edge). Split the identity two ways:

- **`relation_key = (src, dst, kind, semantic_role)`** — adds one more
  discriminator (`attrs.get("role")`, e.g. `"return"` vs. `"parameter"` vs.
  `"field"`) to the existing triple. This is what closure/diff computations
  key on — the same shape `EdgeKind`-level reasoning uses today, just finer.
- **`occurrence_id = (relation_key, source_location, configuration_id,
  instantiation_id, callsite_id)`** — the full, non-deduplicated evidence
  trail. A `relation_key` can back many `occurrence_id`s (the same
  `DECL_CALLS_DECL:direct` relation observed at three call sites); collapsing
  to `relation_key` for graph-shape reasoning must never discard the
  `occurrence_id` list, since a `graph explain` / proof-path answer wants the
  concrete call site, not just "some call exists."

`GraphEdge.key()` keeps its current `(src, dst, kind)` return type and
becomes a documented alias for the *coarsest* projection of `relation_key`
(role-blind) — existing callers (`diff_source_graph`'s edge-set comparison)
are unaffected; new code that needs role-awareness calls a new
`GraphEdge.relation_key()` instead.

### D2. Evidence-preserving node/edge merge, replacing first-writer-wins

Replace `add_node`'s silent drop-on-duplicate with an explicit merge:

```python
@dataclass
class GraphNode:
    id: str
    kind: str
    label: str = ""
    facts: list[NodeFact] = field(default_factory=list)   # NEW
    resolved: dict[str, Any] = field(default_factory=dict) # NEW
    conflicts: list[FactConflict] = field(default_factory=list)  # NEW
    # attrs/provenance/confidence become derived *views* over facts[0]
    # (the highest-confidence fact) for read-compatibility with v1 code.
```

- `NodeFact = {producer: str, confidence: str, attrs: dict[str, Any]}` — one
  entry per producer that ever registered this node id. Order of insertion
  is irrelevant to the final `resolved` dict (see next point) but is kept for
  provenance/debugging.
- `resolved` is a deterministic, **order-independent** fold over `facts`: for
  each key present in more than one fact, resolution precedence is fixed
  (higher `confidence` wins; a tie between equal-confidence facts is broken
  by a stable producer-name sort, never by arrival order) — the same result
  regardless of which producer's pass ran first. This is the property PR
  #607's own review repeatedly needed and had to hand-verify per call site
  ("both sides", "trusted graph on the relevant side only") — making the
  merge itself order-independent removes a whole class of that bug.
- A genuine disagreement (two facts disagree on a key `resolved` cannot
  silently pick a winner for — e.g. `is_virtual: true` vs. `is_virtual:
  false` from two producers of equal confidence) is recorded in `conflicts`,
  not dropped. `conflicts` is advisory (RISK-tier, never authoritative on
  its own — ADR-028 D3) but visible, unlike today's silent first-writer-wins.
- `GraphEdge` gets the analogous `facts`/`resolved`/`conflicts` treatment.

Backward compatibility: `GraphNode.attrs`/`.provenance`/`.confidence` remain
real fields (not removed), populated from `resolved` at write time — any v1
reader touching `.attrs` directly keeps working unchanged; only code that
wants merge-awareness needs to look at `.facts`/`.conflicts`.

### D3. Per-(kind, role) coverage matrix

Extend `extractor_passes`/`narrowed_passes`/`degraded_passes` (currently
`dict[str, bool]` / `dict[str, ...]` keyed by pass name only) with an
additional finer key form: `"{pass_name}:{edge_kind}:{role}"` (e.g.
`"call_graph:DECL_HAS_TYPE:parameter"`), populated *in addition to* the
existing family-level key — the family key remains the union/AND of its role
keys, so every existing consumer (`_call_graph_fully_trusted` in Phase 1,
`mark_source_edges_extractor_coverage`) keeps working unchanged against the
coarser key. New code (e.g. a future `TraversalPolicy.minimum_confidence`
check, D5 below) can consult the finer key when it needs to know "did this
specific role get examined," rather than being stuck with the family's
weakest-covered role's honesty.

### D4. `EntityResolver` — USR-based canonical identity, `SOURCE_GRAPH_VERSION = 2`

New `abicheck/buildsource/entity_resolver.py`:

```python
@dataclass
class EntityResolver:
    """Canonical identity for one real declaration/definition, resolved
    across every evidence source that can name it."""
    canonical_id: str            # clang USR when available, else a v1-style hash
    aliases: list[str]           # old_v1_node_id, mangled_symbol, qualified_name,
                                  # signature_hash, source_location — every signal
                                  # any producer used to name this entity
    kind: str
```

- **Primary key is the clang USR** (`Unified Symbol Resolution` — clang's own
  stable, mangling-independent, cross-TU identity string) when the producer
  is clang-based (`call_graph.py`/`type_graph.py`/the clang AST frontend);
  USRs are already available in clang's `-ast-dump=json` output and are the
  standard "same declaration across TUs" key clang tooling (clangd, IWYU)
  itself relies on.
- **Every other identity signal a v1 node carried becomes an alias**, not a
  replacement: `_decl_node_id`'s v1 hash, the mangled symbol, the demangled
  qualified name, the `SourceEntity.identity()` signature hash, and the
  declaring `source_location`. `EntityResolver.resolve(any_alias) ->
  canonical_id` is the one lookup every consumer should use instead of
  re-implementing the "try root, then qualified_name" fallback pattern.
- A castxml-only or DWARF-only pipeline (no clang AST, hence no USR) falls
  back to `canonical_id = _decl_node_id(...)` unchanged — v1 behavior,
  degraded gracefully, not a hard requirement on clang.
- `SOURCE_GRAPH_VERSION = 2`. A v2 reader (`GraphNode.from_dict`) accepts a
  v1 pack's node/edge ids **as aliases** of a synthesized canonical id (there
  is no USR in a v1 pack, so `canonical_id` falls back to the v1 id itself) —
  an existing `collect`-produced pack on disk keeps loading and comparing
  correctly against a v2-built pack; no forced re-collection.

### D5. `TraversalPolicy` — formalize the five propagation shapes

`internal_leak.py`'s `is_consumer_compiled_public_entry` today encodes one
detector's implicit knowledge of "don't walk through an ordinary
out-of-line helper" — a real, load-bearing rule (PR #607's sixth Codex
round depended on it) that exists as one function's docstring, not a named,
reusable policy. Formalize the five traversal shapes the original
impact-analysis review distinguished — layout propagation (by-value
embedding), symbol-availability propagation (does the symbol still link),
source-contract propagation (does the source still compile — default args,
concepts, etc.), behavioral propagation (does the runtime behavior change —
inline/template body edits), deployment propagation (build-flag/toolchain
drift) — as one shared type:

```python
@dataclass
class TraversalPolicy:
    allowed_edges: frozenset[str]        # which EDGE_KINDS this walk may follow
    stop_conditions: Callable[[GraphNode], bool]  # e.g. is_consumer_compiled_node
    effect_transitions: dict[str, str]   # how "effect" changes crossing an edge kind
                                          # (e.g. crossing DECL_CALLS_DECL:virtual
                                          # downgrades exact-path to over-approximation)
    minimum_confidence: str              # walk ignores an edge below this confidence
```

`compute_leak_paths`/`compute_call_graph_leak_paths` become thin callers that
each construct one named `TraversalPolicy` instance instead of hand-coding
their stop/expand rules inline — the policy object is what a future detector
reuses instead of re-deriving the same "don't cross an ordinary exported
function's own body" rule PR #607 needed a targeted fix for.

### D6. Proof-path selection preference order

Replace plain shortest-BFS path selection (`min(paths, key=len)` in
`post_processing.py` today) with an explicit preference order when multiple
proof paths exist for the same finding:

1. Consumer-proven (a real `--used-by` consumer binary actually references
   this symbol — the strongest possible evidence)
2. Exact, high-confidence path (a single, unambiguous edge chain at
   `confidence >= CONF_HIGH`)
3. Public-header structural path (a `ScopeOrigin.PUBLIC_HEADER`-tagged
   direct match — today's `direct_public_symbol` tag)
4. Multi-producer-confirmed (the same relation independently observed by ≥2
   producers — visible via D2's `facts` list)
5. Reduced-confidence name resolution (a bare-name/suffix match, no USR
   confirmation)
6. Virtual/indirect over-approximation (a vtable/function-pointer edge —
   real but structurally imprecise)

The finding keeps `primary_path` (the highest-preference path found) plus
`alternative_paths[0..N]` and a `discarded_path_count` — visibility into
"how much evidence was there, and how strong was the best of it," not just
one arbitrary shortest path.

## D1 implementation (G29 Phase 2, slice 3 — the `relation_key` half)

- `abicheck/buildsource/graph_facts.py`: `edge_relation_key(src, dst, kind,
  resolved)` and `GraphEdge.relation_key()` — the `(src, dst, kind, role)`
  tuple, reading `role` from `resolved` (D2's merged view) when populated,
  falling back to raw `attrs` for a bare edge that hasn't gone through
  `add_edge`/`ensure_facts_and_resolve` yet. Purely additive: `GraphEdge.key()`
  keeps its exact `(src, dst, kind)` shape and every existing caller
  (`SourceGraphSummary.add_edge`'s dedup, `diff_source_graph`'s edge-set
  comparison) is unaffected — per the Decision text above, this is new
  surface for role-aware code, not a behavior change to today's dedup/diff.
- **Not implemented**: `occurrence_id` (the full, non-deduplicated
  per-call-site/per-configuration evidence trail one `relation_key` can back
  many of). The ADR's own Costs section flags this specifically as needing "a
  measured check against the existing scan-level cost model... before this
  lands on a default, always-on path rather than an opt-in one" — that
  measurement hasn't been done, so this slice stops at the identity split and
  does not add per-occurrence storage.
- `tests/test_source_graph_v2.py` (`TestRelationKey`): role discriminates two
  edges that collapse on `key()`; defaults to `""` when no role attr;
  defaults absent; reads the D2-merged `resolved` view (not whichever fact
  registered first) rather than a raw/stale attr; the free function and
  method agree.

### Mechanical follow-up: `GraphNode`/`GraphEdge` moved into `graph_facts.py`

Landing D1 alongside D2/D3 pushed `source_graph.py` back over the
AI-readiness 2000-line hard cap. Rather than trim prose further (this module
is already dense with Codex-review-driven rationale that would lose meaning
compressed), `GraphNode`/`GraphEdge` themselves — not just the D1/D2 merge
functions — moved into `graph_facts.py`, which already held their supporting
`GraphFact`/`FactConflict`/merge machinery. This *simplified* `graph_facts.py`
rather than complicating it: the `_FactHolder` structural `Protocol` that
previously stood in for `GraphNode`/`GraphEdge` (to avoid importing them and
risking a cycle) is gone — with the real classes now defined in the same
module, `ensure_facts_and_resolve`/`register_fact` just type-hint
`GraphNode | GraphEdge` directly. `source_graph.py` re-exports both names
(alongside the existing `CONF_HIGH`/`GraphFact`/`FactConflict` re-exports) so
every existing `from .source_graph import GraphNode` call site is unaffected.

## D2 implementation (G29 Phase 2, slice 1)

Implemented as the first slice of Phase 2, chosen because D2 is the change
this ADR's own Context section cites the most concrete recurring evidence
for (PR #607's repeated "which producer wins" bugs), and because it is
self-contained: no other decision in this ADR needs to land first, and
downstream consumers (`crosscheck.py`, `internal_leak.py`,
`source_graph_findings.py`) never read `GraphNode.attrs`/`GraphEdge.attrs`
differently — they still see one merged dict, now assembled honestly instead
of by first-writer-wins.

- New `abicheck/buildsource/graph_facts.py`: `GraphFact` (`producer`/
  `confidence`/`attrs`), `FactConflict`, `merge_graph_facts()` (the
  order-independent fold described in D2 above — highest confidence wins per
  key, ties broken by a stable producer-name sort, a genuine value
  disagreement recorded as a `FactConflict` instead of dropped),
  `ensure_facts_and_resolve()` and `register_fact()` (the two operations
  `SourceGraphSummary.add_node`/`add_edge` now delegate to). Deliberately a
  dependency-free leaf module — `_FactHolder` is a structural `Protocol`
  describing the `GraphNode`/`GraphEdge` shape it needs, so this module never
  imports `source_graph.py` (not even under `TYPE_CHECKING`) and cannot form
  an import cycle with it (CLAUDE.md "M1-3"); `source_graph.py` imports and
  re-exports its public names (`CONF_HIGH`/`CONF_REDUCED`/`CONF_UNKNOWN`/
  `GraphFact`/`FactConflict`) so existing `from .source_graph import
  CONF_HIGH` call sites are unaffected. Also the mechanical reason for the
  split: `source_graph.py` was already the largest module in `buildsource/`
  and D2's dataclass/merge code would have pushed it past the AI-readiness
  2000-line hard cap.
- `GraphNode`/`GraphEdge` (`source_graph.py`) gain `facts: list[GraphFact]`,
  `resolved: dict[str, Any]`, `conflicts: list[FactConflict]`. `attrs`/
  `provenance`/`confidence` remain real fields (v1 read-compatibility, exactly
  as D2 specified) but are now populated from the merged view at write time
  instead of frozen at first registration.
- `SourceGraphSummary.add_node`/`add_edge` replace the v1 silent
  drop-on-duplicate with `register_fact()`: a second (or third, …) producer's
  attrs are folded into `resolved`/`conflicts` instead of vanishing.
  `kind`/`label` (structural identity, not evidence) still keep the first
  registration's value — only the accumulated `attrs` evidence merges.
- `GraphNode.from_dict`/`GraphEdge.from_dict` accept a v1 pack with no
  `facts`/`resolved`/`conflicts` keys unchanged — synthesizing the single
  fact its `attrs`/`provenance`/`confidence` already imply (D4's compat rule,
  applied to D2's schema) — and deliberately never trust a *stored*
  `resolved`/`conflicts` value from any pack: both are always recomputed from
  `facts`, so a hand-edited or stale pack self-heals to what the facts
  actually support rather than silently persisting a bad merge.
  `SourceGraphSummary.__post_init__` runs the same backfill/re-derive step
  for every node/edge reachable from a constructor-seeded summary (the
  pattern most of the existing test suite and a few builders use, bypassing
  `add_node`/`add_edge` entirely) — the invariant "`facts` is never empty,
  `resolved` is derived from `facts`" holds for every `GraphNode`/`GraphEdge`
  regardless of how it entered the graph.
- `tests/test_source_graph_v2.py` (new): order-independence (same facts,
  opposite registration order → identical `resolved`/`confidence`/
  `provenance`), the confidence-then-producer-name tie-break, conflict
  recording vs. silent agreement, edge merge parity with node merge,
  constructor-seeded backfill, and v1-pack/v2-pack round-trip compatibility.

**Not yet implemented at this point** (D1, D4-D6): `relation_key`/
`occurrence_id` edge identity splitting, the USR-based `EntityResolver`
(`SOURCE_GRAPH_VERSION` therefore still `1`, not `2` — D2 alone does not need
the version bump since it changes no on-disk key shape a v1 reader couldn't
already tolerate additively), `TraversalPolicy`, and the six-tier proof-path
preference order all remain open follow-up work under this same ADR — see
"D3 implementation" below for the one further slice landed since.

## D3 implementation (G29 Phase 2, slice 2)

- `abicheck/buildsource/inline_graph_fold.py`: `ROLE_COVERAGE_MATRIX` (the
  exact role vocabulary `type_graph.py`'s `_walk_types` emits per edge kind —
  `TYPE_INHERITS:base`, `TYPE_HAS_FIELD_TYPE:{field,alias}`,
  `DECL_HAS_TYPE:{var,return,param}`, `DECL_REFERENCES_DECL:ref`),
  `role_pass_covered()` (the read helper new code consults, falling back to
  the family-level flag for an untracked role), and `_mark_role_coverage()`
  (sets every matrix key alongside a confirmed family flag). Wired into
  `fold_type_graph()`: a confirmed full *or* narrowed pass earns the finer
  `extractor_passes`/`narrowed_passes` keys too, not just the family key.
- **Deliberately scoped to `inline_graph_fold.fold_type_graph`'s
  build-integrated pass only** — the primary, most-exercised collection
  path. Two things explicitly *not* covered by this slice, both documented
  as open follow-up rather than silently assumed:
  - `header_graph.py`'s header-only type-graph pass (reuses the same
    `type_graph.py` walker for its clang path, so the same role matrix would
    likely apply there too — not yet wired).
  - The ADR-038 C.8 clang-plugin producer (`abicheck-clang-plugin`, ingested
    via `inputs_pack.py`) — the *actual* motivating case for a per-role gap
    (it structurally never emits `DECL_HAS_TYPE` for a variable's or
    typedef's own type, only return/parameter types). Its L4-level
    `fact_family_states`/`fact_set` coverage schema (`source_abi.py`,
    `fact_set.py`) only tracks one flat `source_edges` family today, with no
    role subdivision — extending *that* schema to plumb a per-role claim
    through to `source_graph.mark_source_edges_extractor_coverage` is a
    materially larger change than this slice (touches the L4 fact-set
    contract, not just the L5 graph), so it stays open.
  `call_graph.py`'s `DECL_CALLS_DECL` is not given role granularity either:
  its unconditional walk visits every call expression the same way
  regardless of `call_kind` (direct/virtual/function_pointer/template), so
  there is no structural per-role gap to make the matrix honest about — a
  confirmed family flag already means every call kind was examined.
- `tests/test_inline_changed_paths.py`: a confirmed full pass sets every
  matrix key; a narrowed pass sets the narrowed-side keys only (never the
  family-level `extractor_passes` side); `role_pass_covered()`'s direct-hit
  and family-fallback behavior.

## Non-goals

- **Not** a change to any `ChangeKind`'s default verdict or to
  `BREAKING_KINDS`/`API_BREAK_KINDS`/`RISK_KINDS` membership — this ADR is
  graph plumbing underneath the existing tri-state reachability model
  (ADR-044 P0/P1, PR #607), not a policy change.
- **Not** a new CLI flag or user-facing behavior change in this ADR alone —
  D1-D6 are internal `buildsource/` schema/API changes. A later G29 Phase 3
  ADR is where `--report-mode root-cause` and structured proof-path JSON
  output land.
- **Not** retiring the v1 node-id functions (`_decl_node_id` et al.) — they
  remain the non-USR fallback path (D4) and the v1-pack-compat alias source.
- **Not** requiring every extractor to emit USRs immediately — D4's
  `EntityResolver` degrades gracefully per-producer; a non-clang producer
  keeps working exactly as it does today.

## Consequences

**Positive:**

- Removes the class of bug PR #607's review repeatedly found by hand:
  "which identity signal does this lookup use, and did the producer that
  built the graph use the same one" — `EntityResolver` centralizes that
  resolution once instead of at every call site.
- `conflicts` gives cross-producer disagreement a visible home instead of
  silent first-writer-wins — directly useful for `crosscheck.py`'s existing
  provider-agreement matrix (ADR-035 D4/D8), which currently has no graph-
  level disagreement signal to draw on.
- `TraversalPolicy` turns "detector-specific tribal knowledge" into a named,
  testable, reusable object — the next detector that needs a graph walk
  (G29 Phase 6) starts from a policy, not a hand-rolled BFS.
- Backward compatible at the pack level: a v1 pack on disk still loads and
  compares correctly against v2-produced code (D4's alias fallback).

**Costs / risks:**

- Every `source_graph.py`/`internal_leak.py`/`crosscheck.py`/
  `source_graph_findings.py` call site that reads `GraphNode.attrs`/
  `GraphEdge.attrs` directly needs an audit to confirm the D2 derived-view
  compatibility path actually covers its access pattern — a real,
  non-trivial migration even though the field names don't change.
  `buildsource/` is already at nine modules over the 1500-line AI-readiness
  soft limit; this is schema surgery on the most heavily depended-on module
  in that tree (`source_graph.py`, re-exported by `source_graph_findings.py`,
  `internal_leak.py`, `crosscheck.py`, `header_graph.py`, `call_graph.py`,
  `type_graph.py`, `poi.py`).
- `EntityResolver`'s USR dependency means the improvement is clang-only in
  its strongest form; a castxml-only pipeline sees no identity-fragmentation
  improvement from D4 (still falls back to v1 hashing), only the D2/D3/D5/D6
  benefits.
- `occurrence_id`'s extra granularity (D1) grows pack size for a
  template-heavy codebase (more distinct occurrences per relation) — needs a
  measured check against the existing scan-level cost model
  (`docs/development/performance.md`) before this lands on a default,
  always-on path rather than an opt-in one.
- This is explicitly **schema-only** groundwork: none of D1-D6 alone changes
  a single `ChangeKind`'s output. The payoff is realized only once G29
  Phases 3-6 (reporting, consumer join, new edge families, new detectors)
  are built on top of it — shipping D1-D6 in isolation is infrastructure
  investment with no immediately observable user-facing effect, and should
  be communicated as such (e.g. in its own changelog fragment) rather than
  implied to close a user-visible gap by itself.

## Relationship to existing ADRs

- **ADR-031** (source/implementation graph augmentation) defined the v1
  schema this ADR versions past (`SOURCE_GRAPH_VERSION 1 -> 2`); D1-D6 are
  additive/versioned changes to that schema, not a replacement of its node/
  edge-kind vocabulary (`NODE_KINDS`/`EDGE_KINDS` are unchanged).
- **ADR-041** (compiler-facts semantic impact graph) is where the
  per-(kind,role) coverage gap (D3) and the `is_consumer_compiled_node`
  predicate (D5's starting point) were first introduced; this ADR
  generalizes both rather than duplicating them.
- **ADR-044** (reachability-aware suppression) is the direct trigger: its
  P0/P1 slices (and PR #607's eleven-plus rounds of review) are the concrete
  evidence this ADR's Context section cites for identity fragmentation and
  first-writer-wins merge being real, recurring correctness gaps, not a
  speculative concern.
- **`docs/development/plans/g29-impact-analysis-layer.md`** (Phase 2) is
  this ADR's origin — the plan explicitly gated Phase 2 code on "needs its
  own ADR," and this is that ADR. Phases 3-6 of the same plan consume D1-D6
  but are out of scope here.

## References

- `abicheck/buildsource/source_graph.py` — `SourceGraphSummary`,
  `SOURCE_GRAPH_VERSION` (v1 schema container); `add_node`/`add_edge` now
  delegate to `graph_facts.py` (D2, implemented). `GraphNode`/`GraphEdge`
  themselves live in `graph_facts.py`, re-exported here.
- `abicheck/buildsource/graph_facts.py` — the `GraphNode`/`GraphEdge` schema
  itself, D1's `edge_relation_key`/`GraphEdge.relation_key` (implemented) and
  D2's `GraphFact`/`FactConflict`/`merge_graph_facts`/
  `ensure_facts_and_resolve`/`register_fact` (implemented).
- `abicheck/buildsource/inline_graph_fold.py` — D3's `ROLE_COVERAGE_MATRIX`/
  `role_pass_covered`/`_mark_role_coverage`, wired into `fold_type_graph`.
- `abicheck/internal_leak.py` — `compute_leak_paths`/
  `compute_call_graph_leak_paths`, `is_consumer_compiled_node`/
  `is_consumer_compiled_public_entry` (D5's starting point, not implemented).
- `abicheck/post_processing.py` — `MarkReachability` (PR #607), the
  root/qualified_name fallback pattern D4 centralizes, `min(paths, key=len)`
  proof-path selection D6 replaces (both not implemented).
- `tests/test_source_graph_v2.py` — D2 unit tests.
- `tests/test_inline_changed_paths.py` — D3 unit tests.
- PR #607 review history — the concrete, repeated instances of identity/
  coverage-granularity bugs this ADR's Context section draws on.
- [ADR-031](031-source-implementation-graph-augmentation.md), [ADR-041](041-compiler-facts-semantic-impact-graph.md), [ADR-044](044-reachability-aware-suppression.md), [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
- `docs/development/plans/g29-impact-analysis-layer.md`
