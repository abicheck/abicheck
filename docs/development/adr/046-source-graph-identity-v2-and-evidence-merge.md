# ADR-046: Source Graph Identity v2 — USR-Based Entity Resolution and Evidence-Preserving Merge

**Date:** 2026-07-19
**Status:** Accepted — D1, D2, D3, a partial D5, and a partial D6 slice
implemented: role-aware edge identity (`GraphEdge.relation_key()`/
`edge_relation_key`, the `relation_key` half of D1's split — `occurrence_id`
remains open), the evidence-preserving node/edge merge (`GraphFact`/
`FactConflict`/`merge_graph_facts`, replacing `SourceGraphSummary.add_node`/
`add_edge`'s v1 first-writer-wins drop), the per-(kind,role) coverage matrix
for `inline_graph_fold.fold_type_graph`, a named `TraversalPolicy` reifying
the call-graph leak walk's own edge-kind/stop/confidence rules
(`internal_leak.TraversalPolicy`/`CALL_GRAPH_TRAVERSAL_POLICY` — see "D5
implementation" for what's covered and what's deferred), and a two-tier
proof-path preference (`internal_leak.select_preferred_path`, wired into
`post_processing.py`'s layout-walk selection only — see "D6 implementation"
for what's covered and what's deferred). D4 (`EntityResolver`/
`SOURCE_GRAPH_VERSION = 2`) remains open, and is a deliberate stop, not an
oversight — see "D4: deliberately deferred" below for why. See "D1
implementation"/"D2 implementation"/"D3 implementation"/"D5 implementation"/
"D6 implementation"
below.
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
  `add_edge`/`ensure_facts_and_resolve` yet. `GraphEdge.key()` keeps its
  exact `(src, dst, kind)` shape and `diff_source_graph`'s edge-set
  comparison keeps using it unchanged. **`SourceGraphSummary.add_edge`'s
  dedup is not unaffected** — a same-PR follow-up fix (see "Follow-up fix:
  `add_edge` dedup granularity" below) switched its dedup key from `key()`
  to `relation_key()`, since deduping on the coarse key alone silently
  folded two real, role-distinct edges into one before `relation_key()`
  could ever observe both roles on a real, `add_edge`-built graph.
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

### Follow-up fix: `add_edge` dedup granularity (Codex review)

The first `relation_key` landing left a gap `relation_key()` itself could not
close: `SourceGraphSummary.add_edge` still deduplicated on the coarse
`GraphEdge.key()` triple (`src, dst, kind`), so two edges that differ only by
role (e.g. the same function's return-type and parameter-type dependency on
the same private type — `DECL_HAS_TYPE:return` vs. `DECL_HAS_TYPE:param`)
were folded into one `GraphEdge` object by `register_fact` before either
role's `relation_key()` could ever be observed on a real, `add_edge`-built
graph. `relation_key()` worked correctly on hand-constructed test objects
(which is why the original D1 tests missed this), but the actual ingestion
path silently lost the second role's identity — the exact "role-distinct
edges stay distinct" property D1 exists to guarantee.

Fix: `add_edge`'s dedup key (and `__post_init__`'s edge-index construction)
now use `relation_key()` (the 4-tuple including role) instead of `key()`.
Two edges that only differ by role now persist as separate `GraphEdge`
objects with independent `facts`/`resolved`/`conflicts`, instead of the
second silently merging into the first's fact list under the wrong role.
`diff_source_graph`'s edge-set comparison deliberately stays at the coarse
`key()` granularity — an explicit, documented boundary matching the original
"existing callers unaffected" promise, not an oversight — since role-level
diff granularity is out of scope for this ADR's D1 slice.

`tests/test_source_graph_v2.py`: `test_add_edge_preserves_role_distinct_edges_as_separate_objects`
is the regression test (two role-distinct edges through the real `add_edge`
path stay separate, with correct `relation_key()`s and no fact
cross-contamination); `test_add_edge_dedups_true_duplicates_on_relation_key`
confirms a genuine same-role duplicate still merges as before.

### Follow-up fix: `compute_graph_id` hashed the coarse key (Codex review)

A second gap the `add_edge` dedup-granularity fix above exposed: before that
fix, two role-distinct edges sharing `(src, dst, kind)` could never coexist
in one graph (the coarse dedup silently merged them), so `compute_graph_id`
hashing `e.key()` (role-blind) was harmless — there was no role-only graph
difference for it to miss. Once `add_edge` started keying on `relation_key()`,
that stopped being true: two graphs differing only in which role an edge
carries (e.g. the same `DECL_HAS_TYPE` edge as `role="return"` in one graph,
`role="param"` in the other) are genuinely different content, but
`compute_graph_id`'s edge list still canonicalized on the coarse key, so both
graphs produced the identical `graph_id` — a real hash collision anything
keyed on `graph_id` (a pack reference, a future content-addressed cache, a
comparison shortcut) could silently miss.

Fix: `compute_graph_id` now hashes `e.relation_key()` (role-aware) instead of
`e.key()`. This changes the `graph_id` value computed for every graph, not
just role-distinct ones (the tuple shape gains a role element throughout) —
`graph_id` carries no documented cross-version stability contract today (only
`SOURCE_GRAPH_VERSION` does), so this is a correctness fix, not a compat
break.

### Follow-up fix: resolve before indexing, not after (Codex review)

A third gap in the same family, caught in code review of PR #620:
`SourceGraphSummary.add_edge` computed `rkey = edge.relation_key()` *before*
calling `ensure_facts_and_resolve(edge)` — so an edge whose role lives only
in `facts` (not yet mirrored into `attrs` at construction time) had its
dedup key computed against an empty `attrs`/`resolved` view (`relation_key()`
falls back to `self.resolved or self.attrs`, and both are empty
pre-resolution), producing the wrong blank-role key instead of the edge's
true, post-resolution one. Two genuinely same-role edges built this way
would fail to merge (each computing a distinct-looking blank-role key that
happened to coincide, or not, depending on registration order); two
genuinely role-distinct edges could collapse onto the same blank-role key
and incorrectly merge. `SourceGraphSummary.__post_init__` had the identical
ordering bug for constructor-seeded edges (`_edge_keys`/`_edge_by_key` built
before the backfill/resolve loop that follows it).

No real producer hits this today — `type_graph.py` (the actual role-emitting
call site) constructs edges with `attrs={"role": ...}` directly, so
`relation_key()`'s `self.attrs` fallback already sees the role before
resolution ever runs. This is a latent correctness gap for any future
producer or constructor-seeded builder that populates `facts` directly
without going through `attrs` first (a valid, undocumented-as-forbidden
construction shape the dataclass itself allows). (See the next follow-up fix
below, though, for a *different* gap in this same producer — the role never
even reached `add_edge` for one of two role-distinct relations.)

Fix: both `add_edge` and `__post_init__` now resolve first, index second —
`ensure_facts_and_resolve` runs before `relation_key()` is ever computed for
indexing, in both places.

`tests/test_source_graph_v2.py`: `test_add_edge_resolves_role_only_in_facts_before_indexing`/
`test_add_edge_resolves_role_only_in_facts_stays_role_distinct` (the
`add_edge` path) and `test_constructor_seeded_edge_index_uses_resolved_relation_key`
(the constructor-seeded path) — all three fail without the fix (verified by
temporarily reverting it) and pass with it.

### Follow-up fix: `type_graph.py`'s own upstream dedup dropped a role before `add_edge` ever saw it (Codex review)

A fourth gap, one level upstream of everything else in this family: even
after `add_edge`/`__post_init__` correctly dedup on the role-aware
`relation_key()`, the actual production ingestion path
(`ClangTypeGraphExtractor.extract_from_build` → `parse_clang_ast_types` →
`augment_graph_with_types` → `graph.add_edge`) never gave `add_edge` the
chance: `type_graph.py`'s own `_dedupe_edges` (the per-TU pass inside
`parse_clang_ast_types`) and the `add_edges` closure inside
`extract_from_build` (the cross-TU merge) both deduplicated raw `TypeEdge`s
on the coarse `(src, dst, kind)` triple — **without role** — before a
`GraphEdge` was ever constructed. A function that both returns and takes
the same private type (`detail::Impl foo(detail::Impl)`) emits two real,
role-distinct `TypeEdge`s from `_walk_types` (`role="return"` and
`role="param"`, both `DECL_HAS_TYPE`, same `src`/`dst`); `_dedupe_edges`
silently kept only whichever was emitted first (`"return"`, since return
types are walked before parameters), so the `"param"` `TypeEdge` never
reached `augment_graph_with_types`/`add_edge` at all — no amount of
role-awareness downstream could recover evidence that was already dropped.
This also means the D3 coverage matrix could mark
`type_graph:DECL_HAS_TYPE:param` "covered" for a confirmed pass even when
this exact scenario silently ate the param edge for one specific
declaration — the *pass* genuinely examined every role, but this one
relation's evidence didn't survive to be stored.

Fix: both `_dedupe_edges` and `extract_from_build`'s cross-TU `add_edges`
now key on `(src, dst, kind, role)`, matching `add_edge`'s own dedup
granularity. `_merge_type_edges` (the cross-TU confidence/`dst_file` merge)
needed no change — two edges that now share a dedup key by construction
already share a role too.

`tests/test_type_graph.py`:
`test_same_private_type_as_return_and_param_stays_role_distinct` builds a
synthetic AST for exactly the return+param-same-type shape and asserts
`parse_clang_ast_types` emits both roles (fails without the fix, verified by
temporarily reverting it — only `"return"` survived).

### Follow-up fix: `add_node`/`add_edge`'s duplicate branch flattened an already multi-fact incoming entity (Codex review)

A fifth gap, back in `graph_facts.py`/`source_graph.py` themselves rather
than an upstream producer: `SourceGraphSummary.add_node`/`add_edge`'s
duplicate-registration branch called `register_fact(existing,
incoming.provenance, incoming.confidence, incoming.attrs)` — correct for the
common case where *incoming* is a bare, single-producer `GraphNode(...)`/
`GraphEdge(...)` construction (after resolution, its own `facts` list holds
exactly one entry matching that triple), but wrong whenever *incoming*
already carries **multiple** facts of its own — e.g. a node/edge re-added
from an already evidence-merged graph (a graph-combining scenario like
`cli_buildsource.py`'s pack merge, not yet exercised by any current call
site, but exactly the shape D2 exists to handle correctly). `register_fact`
only ever appends one new, freshly-flattened fact; it had no way to unpack
*incoming*'s own multi-producer history, so re-merging an already-merged
node/edge silently collapsed its accumulated facts (and any `conflicts` it
had already recorded) into a single derived one.

Fix: new `graph_facts.merge_entity_facts(existing, incoming)` resolves
*incoming* first (so evidence living only in its `facts`, not yet mirrored
into `attrs`, isn't missed either — same principle as the resolve-before-
index fix above), then merges every fact in *incoming*'s own `facts` list
into *existing* one at a time (a duplicate fact is a no-op, matching
`register_fact`'s own idempotence). `add_node`/`add_edge`'s duplicate
branches now call this instead of `register_fact`. `register_fact` itself
is unchanged and still used by `fold_source_edges`'s ad hoc single-fact
backfill, which isn't merging a full entity.

`tests/test_source_graph_v2.py`:
`test_re_adding_an_already_multi_fact_node_preserves_all_its_facts`/
`test_re_adding_an_already_multi_fact_edge_preserves_all_its_facts` build an
*incoming* node/edge with two facts already attached and assert both
survive the merge (fail without the fix, verified by temporarily reverting
it — only one of the two incoming producers survived).

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
  dependency-free leaf module — `GraphNode` and `GraphEdge` are defined here
  alongside the fact/merge machinery, so the helpers type directly against
  those classes; this module never imports `source_graph.py` (not even
  under `TYPE_CHECKING`) and cannot form an import cycle with it (CLAUDE.md
  "M1-3"); `source_graph.py` imports and re-exports its public names
  (`CONF_HIGH`/`CONF_REDUCED`/`CONF_UNKNOWN`/`GraphFact`/`FactConflict`) so
  existing `from .source_graph import CONF_HIGH` call sites are unaffected.
  Also the mechanical reason for the split: `source_graph.py` was already
  the largest module in `buildsource/` and D2's dataclass/merge code would
  have pushed it past the AI-readiness 2000-line hard cap.

  (At the time this D2 slice landed, `GraphNode`/`GraphEdge` still lived in
  `source_graph.py` and a structural `_FactHolder` `Protocol` stood in for
  them here to avoid an import cycle; the "Mechanical follow-up:
  `GraphNode`/`GraphEdge` moved into `graph_facts.py`" section above
  describes the later D1 slice that moved the classes themselves into this
  module and removed `_FactHolder` as no longer needed — this section is
  written to describe the module's current, post-D1 shape throughout.)
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

## D5 implementation (G29 Phase 2, slice 5 — partial)

- `abicheck/internal_leak.py`: `TraversalPolicy` (`allowed_edges`,
  `stop_conditions`, `minimum_confidence`) and `CALL_GRAPH_TRAVERSAL_POLICY`
  — the call-graph leak walk's own rules (`{DECL_CALLS_DECL,
  DECL_REFERENCES_DECL}`, the existing `is_consumer_compiled_node` stop
  check) reified as one named, reusable object instead of the inline
  `edge_kinds` frozenset + hard-coded `_is_consumer_compiled_node` call
  `_consumer_compiled_reachability`/`compute_call_graph_leak_paths` had
  before. `stop_conditions` matches the Decision text's own polarity (True =
  "do not expand past this node," the node itself still counts as
  reachable) — the inverse sense of `is_consumer_compiled_node`, which the
  policy's `stop_conditions` lambda negates.
- `minimum_confidence` is real, wired filtering — `_consumer_compiled_reachability`
  now skips any edge whose confidence rank is below the policy's floor
  before building its adjacency map — not just a passthrough field. The
  default `CONF_UNKNOWN` floor preserves today's behavior exactly (no edge
  is excluded), so `compute_call_graph_leak_paths` is unchanged for every
  existing caller; a policy built with a stricter floor (e.g. `CONF_HIGH`)
  is new capability future code can opt into without this walk's own logic
  changing again.
- **Not implemented this slice**: `effect_transitions` (how a walk's
  precision label changes crossing a particular edge kind) — no current
  walk needs it, so it would be speculative surface with no caller.
  `compute_leak_paths`'s layout/type-graph walk (the other of the two walks
  D5's Decision text names) does **not** adopt `TraversalPolicy` — it
  traverses `RecordType`/typedef structures, not the L5 `GraphNode`/
  `GraphEdge` graph this policy shape describes, so it would need its own
  data-model change first, which is out of scope here (matches the
  same-shaped scoping decision D3 made for `inline_graph_fold` vs.
  `header_graph.py`/the clang-plugin producer).
- `tests/test_internal_leak.py` (`TestTraversalPolicy`): the shared policy's
  `allowed_edges`; a custom policy's `minimum_confidence` actually excludes
  a lower-confidence edge from reachability (not merely accepted and
  ignored); a custom `stop_conditions` halts expansion past a chosen node
  while still recording that node itself as reachable; the public
  `compute_call_graph_leak_paths` entry point still routes through
  `CALL_GRAPH_TRAVERSAL_POLICY` end to end.

## D6 implementation (G29 Phase 2, slice 4 — partial)

- `abicheck/internal_leak.py`: `select_preferred_path(paths: list[list[str]])
  -> list[str]` implements the two tiers this walk's own per-path signals
  already support out of the Decision text's six-tier order — "exact" (a
  value-propagating path, tier 2 in the six-tier list) and "virtual/indirect
  over-approximation" (tier 6). A pointer/reference-only path never wins over
  an available value-propagating one just because it's shorter (what plain
  `min(paths, key=len)` did); within a tier, shortest still wins. The other
  four tiers (consumer-proven, public-header structural, multi-producer-
  confirmed, reduced-confidence name resolution) need structured per-hop
  evidence (confidence, producer, `ScopeOrigin`) this walk's plain
  `list[str]` path representation doesn't carry — not implemented.
- Wired into two call sites over the same layout-walk path representation:
  `post_processing.py`'s `MarkReachability` proof-path selection (the
  `reachable_types`-keyed branch), and `internal_leak._build_leak_change`
  (the `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` synthetic finding's own
  displayed proof path — CodeRabbit review caught that the first wiring
  missed this second consumer of the same `paths` shape). The
  call-graph-walk's own `min(call_paths, key=len)` selection is **deliberately
  left unchanged** — `call_paths` there are already-formatted strings with no
  structured value/indirection signal to tier on, so `select_preferred_path`
  cannot apply without first changing that walk's path representation, which
  is out of scope for this slice.
- `post_processing.py` was already at the AI-readiness 2000-line hard cap
  before this change; wiring in `select_preferred_path` was paired with
  collapsing the layout walk's separate `reachability_kind`
  value-propagating-check and `reachability_proof_path` `min(...)` call into
  one shared `preferred_path = select_preferred_path(paths)` — net negative
  line count even after the new import, and the two derived fields are now
  provably consistent (`reachability_kind` reads `"value_embedding"` exactly
  when the selected proof path itself is value-propagating, not "any path
  is" independently of which one gets shown).
- `tests/test_internal_leak.py` (`TestSelectPreferredPath`): value-propagating
  preferred over a shorter indirect path; shortest wins within the same tier;
  a pointer/signature-only path beats a pure-indirect one; a single path
  returns unchanged. `TestBuildLeakChangePreferredPath`: the
  `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` finding's own `reachability_proof_path`
  also prefers the value-propagating path (verified to fail without the fix);
  the description's "+N more paths" count still reflects the full,
  unreordered candidate collection.
- **Not implemented**: the `primary_path`/`alternative_paths[0..N]`/
  `discarded_path_count` finding shape the Decision text describes, and the
  four evidence-requiring tiers listed above — this slice only replaces the
  layout walk's path-selection comparator, not the finding schema around it.

## D4: deliberately deferred

D4 (`EntityResolver`, USR-based canonical identity, `SOURCE_GRAPH_VERSION =
2`) is the one Phase 2 decision this pass does **not** implement even
partially — a deliberate scope stop, not an oversight, for two reasons:

1. **[ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
   (G31 Phase B, accepted and implemented after this ADR was written)
   already ships a narrower, working identity-resolution module —
   `entity_identity.CanonicalIdentity` — that solves the two problems D4 was
   actually motivated by: safe old/new reconciliation (ADR-048 D2's
   `graph_reconcile.py`) and impact/proof-path linking (ADR-048 D4's
   `graph_impact.py`). It does this **without** touching `GraphNode.id` or
   bumping `SOURCE_GRAPH_VERSION` — both explicit non-goals ADR-048 itself
   records. ADR-048's own "Relationship to ADR-046" section confirms the
   two are compatible, not competing: `CanonicalIdentity` is "the natural
   first alias `EntityResolver.aliases` would fold in," should D4 ever be
   built.
2. **A full D4 would still require changing `GraphNode.id` generation
   itself** (`_decl_node_id`/`_type_node_id`/`_symbol_node_id` et al. in
   `source_graph.py`) and bumping `SOURCE_GRAPH_VERSION` — a materially
   larger, more invasive change than any other slice landed in this pass:
   every producer that constructs a `GraphNode` (`source_graph.py`,
   `call_graph.py`, `type_graph.py`, `header_graph.py`, `include_graph.py`,
   the L4 `source_link.py` fold) would need updating in lockstep, plus a
   genuine v1-pack/v2-graph compatibility test matrix — categorically
   different in risk from D1/D3/D5/D6's single-call-site or single-module
   slices, none of which touched node-id generation at all.

Given ADR-048 already delivers D4's practical value with a smaller, safer
footprint, and a full `GraphNode.id`/`SOURCE_GRAPH_VERSION=2` rewrite is a
large undertaking that deserves its own scoped design pass rather than a
drive-by addition at the tail of this one, D4 stays open. Should a future
need arise that `entity_identity.CanonicalIdentity` genuinely cannot serve
(e.g. an on-disk v2 pack format, or cross-TU USR-based node identity that
`GraphNode.id` itself must carry), it should get the same "own ADR" bar
noted at the top of this file's Context section — a bar this deferral
respects rather than sidesteps.

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
  `ensure_facts_and_resolve`/`register_fact`/`merge_entity_facts`
  (implemented).
- `abicheck/buildsource/inline_graph_fold.py` — D3's `ROLE_COVERAGE_MATRIX`/
  `role_pass_covered`/`_mark_role_coverage`, wired into `fold_type_graph`.
- `abicheck/internal_leak.py` — `compute_leak_paths`/
  `compute_call_graph_leak_paths`, `is_consumer_compiled_node`/
  `is_consumer_compiled_public_entry` (D5's starting point), `TraversalPolicy`/
  `CALL_GRAPH_TRAVERSAL_POLICY` (D5, partial, implemented),
  `select_preferred_path` (D6, partial, implemented).
- `abicheck/post_processing.py` — `MarkReachability` (PR #607), the
  root/qualified_name fallback pattern D4 centralizes (not implemented), now
  calls `select_preferred_path` for its layout-walk proof-path selection (D6,
  partial); the call-graph walk's own shortest-path selection is unchanged.
- `tests/test_source_graph_v2.py` — D1/D2 unit tests, including the
  `add_edge` role-distinct-edge regression test.
- `tests/test_inline_changed_paths.py` — D3 unit tests.
- `tests/test_internal_leak.py` — `TestTraversalPolicy` (D5 unit tests),
  `TestSelectPreferredPath` (D6 unit tests).
- PR #607 review history — the concrete, repeated instances of identity/
  coverage-granularity bugs this ADR's Context section draws on.
- [ADR-031](031-source-implementation-graph-augmentation.md), [ADR-041](041-compiler-facts-semantic-impact-graph.md), [ADR-044](044-reachability-aware-suppression.md), [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
- `docs/development/plans/g29-impact-analysis-layer.md`
