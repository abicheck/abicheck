---
doc_type: reference
audience:
  - contributor
summarizes:
  - impact-analysis
depends_on:
  - abicheck/buildsource/graph_facts.py
  - abicheck/impact/consumer_graph.py
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

1. **Consumer-proven** — a real `--used-by` consumer binary requires a node on the path ([ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md)).
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
  public/private surface information. It also implements **tier 1**, read
  straight off the graph it is given: whenever a consumer graph has been
  folded in (`impact.consumer_graph.join_consumer_graph`, ADR-057), a path
  touching a `CONSUMER_REQUIRES_SYMBOL` target is consumer-proven. With no
  consumer facts in the graph — every run without `--used-by` — that set is
  empty and the tier is inert. Tier 1 stays out of scope for
  `internal_leak.select_preferred_path`.

Tier 1 matches on the path's **endpoint**, and the tier-6 overapprox check
still runs first and wins — so in practice tier 1 means "consumer-proven
*and* exactly resolved". See
[ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md) D4 for why
it is scoped that way.

Both break ties within a tier by shortest path (fewest hops).

## The consumer half of the graph (ADR-057)

`compare --used-by <app>` can fold the consumer's own requirements into the
library's graph, so a `consumer_required_symbol_removed` finding can name the
public entry point behind the dependency instead of only the missing symbol.

| Kind | Status | Meaning |
|---|---|---|
| `consumer_binary` *(node)* | populated | The `--used-by` application binary. |
| `consumer_object` / `runtime_probe` *(nodes)* | reserved | Need consumer-side build evidence / trace ingestion. |
| `CONSUMER_REQUIRES_SYMBOL` *(edge)* | populated | The consumer's undefined-symbol requirement, scoped to the target library's exports. |
| `CONSUMER_REQUIRES_VERSION` *(edge)* | populated | An ELF version tag the consumer needs, targeting the `DT_NEEDED` soname's `external_dependency` node. |
| `CONSUMER_INSTANTIATES_DECL` / `CONSUMER_COMPILED_FROM_HEADER` / `RUNTIME_FAILED_TO_RESOLVE_SYMBOL` *(edges)* | reserved | Same "registered, no data source yet" pattern as the archive/linker kinds. |

There is deliberately **no** `consumer_required_symbol` node kind: a
requirement is an edge onto the *existing* `binary_symbol://<symbol>` node the
library graph already uses for that export, and that one shared node id is the
entire join — see
[ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md) D1.

The vocabulary constants live in `abicheck/buildsource/graph_facts.py` and are
unioned into `source_graph.NODE_KINDS`/`EDGE_KINDS`; the producer is
`abicheck/impact/consumer_graph.py`.

## The archive/object half of the graph (G29 Phase 5 item 6)

`source_graph._fold_link_provenance` (ADR-041 P1 #2) already creates an
`object_file`/`static_library` node for each `BuildEvidence` link input, by
filename suffix alone. `abicheck/buildsource/archive_graph.py` is the real
`ar`-index introspection that fills in the rest, driven by
`inline_graph_fold.fold_archive_graph` whenever a graph carries at least one
`static_library` node — no compiler required.

| Kind | Status | Meaning |
|---|---|---|
| `archive_member` *(node)* | populated | One member (object file) of a `static_library`, scoped by its owning archive's label (`archive_member://<archive>::<member>`) — two archives may share a member name without colliding. |
| `ARCHIVE_CONTAINS_OBJECT` *(edge)* | populated | `static_library` → `archive_member`, one per member the archive's headers name. |
| `OBJECT_DEFINES_SYMBOL` *(edge)* | populated | `archive_member` → `binary_symbol`, one per symbol the archive's own linker-written index attributes to that member. |
| `linker_script` / `export_map` / `comdat_group` *(nodes)* | reserved | No normalized data source yet. |

Evidence source is deliberately the archive's **own symbol index** (GNU
`/`/`/SYM64/`, or BSD/Mach-O `__.SYMDEF`/`__.SYMDEF_64`; both plain and
*thin* (`ar rcT`) archives) — the same table the linker itself reads to
decide which member to pull in — not a per-member ELF/COFF/Mach-O symbol
table walk: the index is format-agnostic (one parser covers all three
object formats) and encodes exactly "this member defines this symbol",
which is what `OBJECT_DEFINES_SYMBOL` means. An archive built without an
index (`ar rc` with no `s`, or a stripped `ranlib`-less one) still yields
`archive_member` nodes from its header chain, just no `OBJECT_DEFINES_SYMBOL`
edges — recorded as a diagnostic, not inferred around.

Like the consumer join, **`OBJECT_DEFINES_SYMBOL` only ever joins onto a
`binary_symbol` node the graph already carries** — an archive's internal-only
indexed symbols (never exported by any side) mint no node, keeping the graph
compact (ADR-031 D7). `archive_graph.defining_members(graph, symbol)` is the
localization read view: every `(archive label, member name)` pair the graph
records as defining a symbol, for a "`cache_dispatch.o` in
`libinternal_dispatch.a`" finding detail.

Coverage is tracked at `extractor_passes["archive_graph"]` (every
`static_library` node the graph named was found, read, and index-backed) /
`degraded_passes["archive_graph"]` (some archive was missing, unreadable, not
an archive, or lacked an index) — the same family-level coverage-honesty
contract the call/type/include-graph passes use (see "Coverage matrix"
below), just gated on disk access rather than clang availability.

## The template-instantiation half of the graph (G29 Phase 5 item 1)

A template's own declaration is often internal-type-free
(`template <typename T> struct Wrapper { T value; };`), but a specific
**instantiation** (`Wrapper<internal::Detail>`) can both depend on an
internal type through its arguments and emit a real, linkable symbol for
its instantiated members — neither of which the pre-existing
`type_graph`/`call_graph` passes capture, since they only ever see the
template *pattern*. `abicheck/buildsource/template_graph.py` is a third,
independent `clang -ast-dump=json` pass (alongside the call and type graph
passes) closing that gap, driven by `inline_graph_fold.fold_template_graph`
whenever the call/type graph passes run (`with_call_graph`).

| Kind | Status | Meaning |
|---|---|---|
| `template_decl` *(node)* | populated | The abstract template pattern (`template_decl://<qualified name>`), e.g. `template_decl://Wrapper`. |
| `template_instantiation` *(node)* | populated | One concrete instantiation, keyed by its own human label (`template_instantiation://Wrapper<internal::Detail>`). |
| `DECL_INSTANTIATES_TEMPLATE` *(edge)* | populated | `template_instantiation` → `template_decl`. |
| `TEMPLATE_USES_TYPE` *(edge)* | populated | `template_instantiation` → the `record_type`/`enum_type`/`typedef` node a resolved template argument names — clang's own `decl` cross-reference on the `TemplateArgument` node, not a textual heuristic. |
| `INSTANTIATION_EMITS_SYMBOL` *(edge)* | populated | `template_instantiation` → `binary_symbol`, for a function instantiation's own mangled name or a class instantiation's instantiated member functions. |
| `TEMPLATE_USES_DECL` / `INSTANTIATION_MAPS_TO_EXPORT` / `DECL_USES_DEFAULT_TEMPLATE_ARG` / `CONSTRAINT_DEPENDS_ON_DECL` *(edges)* | reserved | See the module's own docstring for why each is deferred (a non-type/function-pointer argument, redundancy with `BINARY_EXPORTS_SYMBOL` on the already-joined symbol node, explicit-vs-defaulted argument detection, and C++20 concepts respectively). |

Like the archive/object join, **`TEMPLATE_USES_TYPE`/`INSTANTIATION_EMITS_SYMBOL`
only ever join onto a node the graph already carries** — an unresolved
template argument (a builtin type, a non-type literal) contributes no edge,
and an instantiated member the linker discarded (never ODR-used, or inlined
away) mints no symbol node, keeping the graph compact (ADR-031 D7).

Two AST shapes were the load-bearing empirical findings while building the
parser (see the module's own docstring for the full detail): an *explicit*
instantiation (`template struct Wrapper<int>;`) produces a **detached**
full-content copy of its specialization, sharing its clang node id with an
empty stub nested under the real `ClassTemplateDecl` — resolved by a
two-pass, id-keyed join rather than assuming physical nesting; and a
`using`/typedef-aliased template argument (`Box<internal::DetailAlias>`)
resolves, via clang's own printer, straight to the real record's `decl`
reference — no typedef-chain-following logic needed here.

Coverage is tracked at `extractor_passes["template_graph"]` /
`degraded_passes["template_graph"]`, the same family-level contract the
call/type/include-graph passes use.

## The macro/config-dependency half of the graph (G29 Phase 5 item 2)

Clang's own AST carries **no** representation of preprocessor conditionals
at all — a `#ifdef`/`#define` leaves no trace in a `clang -ast-dump=json`
tree; the declarations it admits or excludes simply appear or don't, with no
marker of which guard let them through. `abicheck/buildsource/macro_graph.py`
closes that gap with two independent passes: a Clang AST pass indexing every
declaration's own `(file, begin_line, end_line)` span, and a pure raw-text
scan of the same files for conditional regions and macro definitions —
joined by line-range containment. Driven by
`inline_graph_fold.fold_macro_graph` alongside the other Clang-backed passes
(`with_call_graph`).

| Kind | Status | Meaning |
|---|---|---|
| `MACRO_CONTROLS_DECL` *(edge)* | populated | `macro` → `source_decl`. A declaration is compiled only under a simple `#ifdef X` / `#ifndef X` / `#if defined(X)` / `#if !defined(X)` conditional region; `attrs.negated` is `true` for the `#else` branch of a simple guard. `CONF_HIGH` — an exact structural fact about the raw text, not a guess. |
| `DECL_USES_MACRO` *(edge)* | populated | `source_decl` → `macro`. A declaration's own signature/body span contains a word-boundary reference to a macro name `#define`d earlier in the same file. `CONF_REDUCED` — a textual heuristic, not semantic preprocessing (see below). |
| `MACRO_EXPANDS_TO_VALUE` / `MACRO_EXPANDS_TO_TYPE` / `MACRO_CONTROLS_EDGE` *(edges)* | reserved | See the module's own docstring for why each is deferred (real macro-*expansion* tracing for the first two; per-edge rather than per-declaration conditional attribution for the third). |

No new **node** kind — both edges join onto the existing `macro`/
`source_decl` node kinds only (join-only-onto-an-existing-node, the same
ADR-057 D1 rule `archive_graph.py`'s `OBJECT_DEFINES_SYMBOL` reapplies): a
macro or declaration this pass discovers in the AST/text scan but the graph
doesn't already carry a node for mints nothing.

**A compound condition (`#if defined(X) && defined(Y)`) or an `#elif` chain
is deliberately unmodeled** — neither its own branch nor (for a compound
condition) its `#else` branch contributes a `MACRO_CONTROLS_DECL` edge,
though nesting depth is still tracked correctly across it (its own `#endif`
still pops the scan's nesting stack, so a sibling or enclosing simple guard
elsewhere in the file is never desynchronized). `DECL_USES_MACRO` is a
textual, not semantic, scan and accepts two documented tradeoffs: it cannot
tell an identifier that merely shares a defined macro's name from a genuine
macro reference (a known, accepted over-match), and it is strictly
same-file — a macro `#define`d in one header and referenced from a
declaration in a different file/TU is not modeled.

One load-bearing empirical AST-dump finding underlies the declaration-range
pass: a declaration's `range.begin`/`range.end` objects do not reliably
carry a `"line"` key — clang's JSON node dumper prints a single,
whole-AST-wide sticky `(file, line)` cursor shared across *every* node's
`loc`, then `range.begin`, then `range.end` (in that field order), printing
a field only when it changes from the immediately preceding one. This is a
stricter version of `type_graph._node_file`'s own file-only sticky tracking
between siblings — the cursor here is one value threaded through the
*entire* document-order traversal (every node, including every statement/
expression inside a function body, not just the declarations this module
indexes), and it tracks `line` as well as `file`. See `macro_graph.py`'s own
module docstring for the full reasoning and verification detail.

Coverage is tracked at `extractor_passes["macro_graph"]` /
`degraded_passes["macro_graph"]`, the same family-level coverage-honesty
contract the call/type/template/include-graph passes use — requiring both a
clean, fully-covered Clang pass *and* a clean raw-text scan (no per-file
read/parse diagnostic) before claiming confirmed coverage.

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
