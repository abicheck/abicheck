---
doc_type: reference
audience:
  - contributor
summarizes:
  - impact-analysis
depends_on:
  - abicheck/buildsource/graph_facts.py
  - abicheck/buildsource/source_graph.py
  - abicheck/internal_leak.py
  - abicheck/buildsource/graph_impact.py
  - abicheck/buildsource/entity_resolver.py
lifecycle: active
generated: false
---

# Source Graph Schema Reference

The optional L5 source graph (`SourceGraphSummary.nodes`/`.edges`) is what
`graph explain`, the version-over-version graph findings, and reachability
suppression gating all read. [Build-Source Data](../learn/build-source-data.md)
walks one node/edge through the L0-L5 layer model end to end; this page is
the exhaustive reference for the graph's own *identity, evidence-merge, and
traversal-policy* schema — the machinery [ADR-046](../contribute/adr/046-source-graph-identity-v2-and-evidence-merge.md)
(G29 Phase 2) added on top of the plain node/edge shape. It does not
re-list every `NODE_KINDS`/`EDGE_KINDS` value (the exhaustive, authoritative
list lives in `abicheck/buildsource/source_graph.py`) — see
[Build-Source Data](../learn/build-source-data.md) for illustrative examples.

## `GraphNode` / `GraphEdge`

Both dataclasses (`abicheck/buildsource/graph_facts.py`) share the same
evidence shape:

| Field | Type | Meaning |
|---|---|---|
| `id` / (`src`, `dst`, `kind`) | string | Node identity, or an edge's endpoints + kind. |
| `label` | string | Human-readable name/path. |
| `attrs` | object | The merged (`resolved`) view, mirrored here for v1 read-compatibility. |
| `provenance` | string | The top-precedence fact's producer name. |
| `confidence` | string | `"high"` / `"reduced"` / `"unknown"` — the top-precedence fact's confidence. |
| `facts` | array | Every producer's contribution — see below. |
| `resolved` | object | The order-independent fold of `facts` — see below. |
| `conflicts` | array | Genuine cross-producer disagreements found while folding `facts`. |
| `occurrences` *(edges only)* | array of string | Per-call-site occurrence ids — see [`occurrence_id`](#relation_key-and-occurrence_id) below. |

### Evidence-preserving merge (ADR-046 D2)

Before this schema, a second producer registering an already-known node/edge
silently lost to the first (`SourceGraphSummary.add_node`/`add_edge`'s old
first-writer-wins behavior). Now every registration becomes a `GraphFact`:

```json
{"producer": "type_graph", "confidence": "high", "attrs": {"role": "return"}}
```

`merge_graph_facts()` folds the accumulated `facts` list into `resolved`,
one key at a time: the highest-confidence fact wins per key, a tie breaks on
producer name, a further tie (the same producer contributing two facts with
different content — e.g. an initial registration and a later backfill)
breaks on a deterministic JSON-content sort. The result never depends on
registration order. A genuine disagreement — two facts of equal precedence
naming different values for the same key — becomes a `FactConflict`
(`key`, `winning_value`/`winning_producer`, `losing_value`/`losing_producer`)
instead of one value silently winning with no trace of the other.

### `relation_key` and `occurrence_id`

Edge identity has three layers, coarsest to finest (ADR-046 D1):

| Layer | Shape | Used by |
|---|---|---|
| `key()` | `(src, dst, kind)` | `diff_source_graph`'s edge-set comparison — deliberately role-blind. |
| `relation_key()` | `(src, dst, kind, role)` | `SourceGraphSummary.add_edge`'s dedup key, `compute_graph_id()`'s hash. |
| `occurrence_id` | opt-in, see below | The full per-call-site evidence trail underneath one `relation_key`. |

`relation_key()` (`edge_relation_key()`) adds `resolved.get("role", "")` as a
fourth discriminator, so two structurally different dependencies that share
`(src, dst, kind)` — e.g. a type used as a `"return"` type on one edge and a
`"param"` type on another, both `DECL_HAS_TYPE` — stay distinguishable.

`occurrence_id` (`edge_occurrence_id()`, `GraphEdge.occurrences`) is
`relation_key`'s finer-grained sibling: a stable `sha256:<hex>` hash over
`(relation_key, source_location, configuration_id, instantiation_id,
callsite_id)`, read from a fact's own `attrs`. Two facts that share a
`relation_key` (and so collapse onto one `GraphEdge`) but come from
different call sites, `#ifdef` configurations, or template instantiations
get distinct occurrence ids, appended to that edge's `occurrences` list —
preserving the full evidence trail a `relation_key`-deduped edge would
otherwise discard. **Deliberately opt-in**: `edge_occurrence_id()` returns
`None` when a fact carries none of the four occurrence keys, so `occurrences`
stays empty — and costs nothing to compute — until a producer populates
them. No current producer does; this is forward-compatible surface, not a
promise that today's packs carry per-call-site data.

## `TraversalPolicy` (ADR-046 D5)

`abicheck/internal_leak.py`'s `TraversalPolicy` formalizes a graph walk's
rules into one reusable object, instead of re-deriving the same edge-kind
set and stop check inline per walk:

| Field | Type | Meaning |
|---|---|---|
| `allowed_edges` | `frozenset[str]` | Edge kinds the walk may traverse. |
| `stop_conditions` | `Callable[[node_id, node_by_id], bool]` | `True` means "do not expand past this node" — the node itself still counts as reached; only its outgoing edges are not queued. |
| `minimum_confidence` | string | Edges below this confidence rank are excluded from traversal entirely. |
| `effect_transitions` | `dict[str, str]` | Maps an edge's resolved `call_kind` to the walk-level precision label crossing it downgrades to — see below. |

`CALL_GRAPH_TRAVERSAL_POLICY` is the call-graph leak walk's own policy
instance: `allowed_edges={"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"}`,
`stop_conditions` halting at a node whose own body isn't compiled into
consumer code, and `effect_transitions={"virtual": "overapprox",
"function_pointer": "overapprox"}`.

### `effect_transitions`: precision downgrade

A virtual or function-pointer call is never statically exact (ADR-031 D4's
own `resolution="overapprox"` label on that `CallEdge`) — `effect_transitions`
mirrors that at the *path* level. Once `_consumer_compiled_reachability`'s
walk crosses an edge whose resolved `call_kind` is a key in
`effect_transitions`, that target node — and every node reached transitively
past it — is recorded in the walk's `degraded` set, sticky for the rest of
that branch. `compute_call_graph_leak_paths` prefixes a degraded path's
formatted string with `"overapprox: "`, so a proof that only exists because
it crossed a possible-dispatch-target edge is distinguishable from one
proven through an unbroken chain of direct calls.

Not (yet) adopted by `compute_leak_paths` (the layout/type-graph walk) — that
walk traverses `RecordType`/typedef structures, not `GraphNode`/`GraphEdge`,
so `TraversalPolicy` doesn't naturally fit without a data-model change first.

## Proof-path preference order (ADR-046 D6)

When more than one candidate path reaches the same target, plain
shortest-wins can pick a weaker proof over a stronger one just because it's
fewer hops. The ADR's target six-tier order — best to worst — is:

1. **Consumer-proven** — a real `--used-by` consumer binary proves the path (Phase 4, not implemented — needs the consumer graph).
2. **Exact / high-confidence** — every edge is `CONF_HIGH`.
3. **Public-header structural** — every node on the path has a `public_header`/`generated` (`PUBLIC_VISIBILITIES`) visibility, not a private-header/source one.
4. **Multi-producer-confirmed** — some edge has more than one distinct fact producer (ADR-046 D2).
5. **Reduced-confidence name resolution** — no stronger signal found (the residual case).
6. **Virtual/indirect over-approximation** — crosses an `effect_transitions`-tagged edge.

Two selectors implement different slices of this order, split by how much
structured per-hop data their walk's path representation carries:

- **`internal_leak.select_preferred_path`** (the layout/type-graph walk's
  plain `list[str]` hop-token paths) implements tiers 2 and 6 only — its
  path representation carries no confidence/producer/visibility signal for
  the other four.
- **`buildsource.graph_impact.select_preferred_graph_path`** (a structured
  `list[GraphEdge]` path, e.g. `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`'s
  proof) implements tiers 2-5 — real `GraphEdge` objects carry confidence,
  fact-producer count, and (via each endpoint node's `visibility` attr)
  public/private surface information. Tier 1 stays out of scope for both
  (Phase 4).

Both break ties within a tier by shortest path (fewest hops).

## `primary_path` / `alternative_paths` / `discarded_path_count`

`select_preferred_graph_path`'s caller (`buildsource.graph_impact.attach_impact_metadata`)
attaches the chosen path as `Change.impact_proof_path` (the "primary"),
plus up to 3 runner-up candidates as `Change.impact_alternative_paths` and
the count of any further candidates beyond that cap as
`Change.impact_discarded_path_count`. `impact.engine.assess_change` surfaces
all three as `GraphProofPath.alternative_paths`/`discarded_path_count` on
`impact_assessment.proof_path` — see [Unified Impact Assessment](../learn/impact-analysis.md).
`impact_assessment.proof_path.occurrence_id` is the same finding's
`occurrence_id` follow-up: a hash over the primary path's edges' own
`occurrences`, `None` whenever none of them carry occurrence-level attrs
(still the common case today, per `occurrence_id`'s opt-in note above).

## Coverage matrix

`extractor_passes`/`narrowed_passes`/`degraded_passes` track coverage at the
family level (`"call_graph"`, `"type_graph"`, …); `ROLE_COVERAGE_MATRIX`
(`abicheck/buildsource/inline_graph_fold.py`, ADR-046 D3) extends this to a
`(kind, role)` grain, e.g. `"type_graph:DECL_HAS_TYPE:param"` vs.
`"type_graph:DECL_HAS_TYPE:variable"` — so a producer that covers
return/parameter types but not variable/typedef-underlying types can
honestly report partial coverage per role instead of one blanket family
flag. See [Graph Coverage & Negative Evidence](../learn/graph-coverage.md)
for why an absent edge is never proof of an absent dependency.

## `EntityResolver` (ADR-046 D4, scoped implementation)

`abicheck/buildsource/entity_resolver.py`'s `EntityResolver` computes a
USR-preferring canonical identity for a `GraphNode` — reusing
`entity_identity.resolve_identity_for_node`
([ADR-048](../contribute/adr/048-canonical-entity-identity-and-graph-reconciliation.md))
as its resolution source — and records the result as an alias:

```json
{
  "aliases": {"decl://ns::foo": "usr:c:@F@foo#"},
  "conflicts": [
    {"canonical_id": "usr:c:@F@bar#", "node_ids": ["decl://bar_v1", "decl://bar_header_variant"]}
  ]
}
```

`aliases` maps each resolved v1 `GraphNode.id` to its canonical identity;
`conflicts` records the identity-fragmentation case — two *different* v1 ids
resolving to the *same* canonical identity — with the first-seen v1 id
staying that identity's representative. `GraphNode.id` generation itself is
unchanged: `EntityResolver` computes this identity *alongside* the existing
v1 id, never in place of it.

`SourceGraphSummary.entity_resolver: EntityResolver` is populated only when
a caller explicitly calls `resolve_entities()` — opt-in, the same "no cost
until asked for" discipline `occurrence_id`/`effect_transitions` follow — so
`to_dict()` omits the `entity_resolver` key entirely unless it has been
called. `SOURCE_GRAPH_VERSION` bumped 1 → 2 marks this capability's
availability as a signal, not a breaking schema change: a v1 pack
(`schema_version: 1`, no `entity_resolver` key) still loads and compares
correctly through its existing `GraphNode.id` values, with no forced
re-collection.

**What this does not do:** change `GraphNode.id` generation itself across
every graph-producing module, or provide an on-disk v2 pack format keyed by
canonical identity — see [ADR-046](../contribute/adr/046-source-graph-identity-v2-and-evidence-merge.md)'s
"D4 implementation" section for the full scoping rationale.
