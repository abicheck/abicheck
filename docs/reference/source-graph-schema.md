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

## The virtual-dispatch half of the graph (G29 Phase 5 item 3)

Unlike every sibling section above, `abicheck/buildsource/
virtual_dispatch_graph.py` shells out to no compiler at all: both edge kinds
below are **pure graph transformations** over `call_graph.py`/
`type_graph.py`/`override_graph.py` facts the graph already carries, driven
by `inline_graph_fold.fold_virtual_dispatch_graph` immediately after
`fold_override_graph` (all three inputs must have already run).

| Kind | Status | Meaning |
|---|---|---|
| `vtable` *(node)* | populated | One node per genuinely polymorphic class (`vtable://<record-type-identity>`) — the one new node kind this Phase 5 family introduces (items 1/2/6 all reused pre-existing kinds). Minted at most once per `record_type` node. |
| `VIRTUAL_CALL_MAY_DISPATCH_TO` *(edge)* | populated | The calling `source_decl` → an override-candidate `source_decl`. Joins a virtual `DECL_CALLS_DECL` edge (`call_kind == "virtual"`, pointing at the statically-resolved base method) against every `METHOD_POSSIBLE_OVERRIDE` edge naming that base method as its target. `attrs.resolution` is always `"overapprox"`, never `"exact"`; `attrs.base_method` names the base method; `attrs.override_resolution` mirrors the joined override edge's own `resolution`. `CONF_REDUCED` — a static approximation of a runtime dispatch decision. |
| `TYPE_HAS_VTABLE` *(edge)* | populated | `record_type` → `vtable`. Per the Itanium ABI rule, a class is polymorphic iff it declares or inherits ≥1 virtual function. `CONF_HIGH` — once a class is known to own or inherit a virtual slot, this is a structural, provable fact. Seeded three independent ways: an overriding/overridden method named by a `METHOD_POSSIBLE_OVERRIDE` edge; an `is_virtual`-tagged `decl://` node (a leaf virtual method with no override candidate anywhere in scope); or a `has_virtual_destructor`-tagged `record_type` node (a class whose only virtual member is its own destructor — destructors are deliberately excluded from override-EDGE matching, so this is a separate, narrower seed, `override_graph.parse_clang_ast_virtual_destructor_owners`). |
| `DECL_OVERRIDES_DECL` *(edge)* | **satisfied by an existing kind, no producer** | `override_graph.py`'s `METHOD_POSSIBLE_OVERRIDE` edge with `attrs.resolution == "override_confirmed"` (clang's own `OverrideAttr`) already carries this exact fact — see that section above. Registered so a hand-built/future graph naming it directly is never rejected, but nothing in this codebase emits it, deliberately: minting a second edge kind for the same fact would fork one piece of evidence into two. |
| `VTABLE_SLOT_MAPS_TO_DECL` *(edge)* | reserved | A precise per-slot Itanium vtable layout (offset-to-top/typeinfo slots, primary vs. secondary vtables under multiple inheritance, virtual-inheritance vtables, covariant-return thunks) is a much harder, easy-to-get-subtly-wrong claim than "this class has a vtable" or "this call's target set may include these declarations" — see the module's own docstring. Deliberately not attempted this slice; `diff_elf_layout.py`'s existing binary-only vtable-slot-*count* detector is unrelated, complementary infrastructure (no per-slot identity), not unified with this family. |

**`VIRTUAL_CALL_MAY_DISPATCH_TO` never re-emits the base method itself as a
target** — it is already the joined `DECL_CALLS_DECL` edge's own `dst`, so
restating it here would be a redundant self-fact. A virtual call to a base
method with **no** recorded override candidates (a leaf virtual method)
emits no `VIRTUAL_CALL_MAY_DISPATCH_TO` edge at all, not a spurious
self-edge: there is no dispatch ambiguity to represent when the call graph
already names the only possible target directly.

**`TYPE_HAS_VTABLE`'s owner join is deliberately exact-match only.** Since
`METHOD_POSSIBLE_OVERRIDE` edges name methods, not their owning types, the
module recovers the owner by decoding a method's own mangled identity via
`diff_cxx_rules.itanium_scope_components`/`msvc_scope_components` (the same
structural, no-external-demangler decoders `diff_cxx_rules.owner_class_of`
already uses elsewhere) and dropping the leaf component. This is matched
against a `record_type` node's identity **exactly**, never as a fuzzy/bare-
suffix match: for a class-template specialization, the decoder keeps the
*raw* Itanium template-argument encoding rather than the spelled form
`type_graph.py`'s record identities use, so an exact match never fires for a
templated owner — a silent, conservative false negative (a template-
instantiated polymorphic class may go undetected), never a wrong claim on an
unrelated same-named type. For every non-template class the two forms
coincide exactly, since a plain identifier mangles to itself length-prefixed.

This pass never touches a compiler itself — its coverage is entirely
*derived* from `call_graph`/`type_graph`/`override_graph`'s own already-
stamped coverage (worst-wins: a degraded or never-run prerequisite outranks
a narrowed one, which outranks a fully-covered one), so a reader checking
whether "zero edges" from this pass means anything should read this pass's
own derived stamp, not re-check the three prerequisites by hand. The three
resulting stamps are mutually exclusive, mirroring every other clang-backed
pass's own `if`/`elif`/`elif` shape: `extractor_passes
["virtual_dispatch_graph"]` only when all three prerequisites are
themselves fully covered; `narrowed_passes["virtual_dispatch_graph"]` (plus
`narrowed_scope["virtual_dispatch_graph"]`, copied from whichever
prerequisite carries a scope) when none is degraded/missing but at least
one is narrowed; `degraded_passes["virtual_dispatch_graph"]` when any
prerequisite is degraded or never ran at all. `callback_graph.py`'s own
Part A join propagates `call_graph`'s coverage state the identical way —
see that family's own coverage row below.

## The callback/function-pointer half of the graph (G29 Phase 5 item 4)

`abicheck/buildsource/callback_graph.py` closes the plugin/event-loop/C-API
callback blind spot: a public registration function stashing a private
handler's address into a slot that is later invoked indirectly. Split like
item 3, by whether new compiler evidence is needed — but unlike item 3, this
family's Part A depends on Part B's own edges already being folded, so both
run inside one `inline_graph_fold.fold_callback_graph` call rather than two
separate `fold_*` functions.

| Kind | Status | Meaning |
|---|---|---|
| `DECL_REGISTERS_CALLBACK` *(edge)* | populated | The address-taking function → the slot. A function's address is a direct argument of a plain `CallExpr`, at a position whose callee parameter is itself function-pointer-typed (`signal(SIGINT, handler)`). `CONF_HIGH` — an exact structural match. |
| `DECL_TAKES_ADDRESS_OF` *(edge)* | populated | The address-taking function → the slot. A broader catch-all: an address-of/decay flows into a function-pointer-typed variable/field via a plain assignment or its own initializer, not necessarily a direct call argument. `CONF_REDUCED` — telling a real callback wire-up from an incidental address-of (printing/comparing it) is out of scope. |
| `CALLBACK_MAY_INVOKE` *(edge)* | populated | The original calling `source_decl` → a registered function `source_decl`. A pure join, no new clang pass: joins `call_graph.py`'s already-folded function-pointer-kind `DECL_CALLS_DECL` edge (caller → slot) against every `DECL_REGISTERS_CALLBACK`/`DECL_TAKES_ADDRESS_OF` edge naming that same slot. `attrs.resolution` is always `"overapprox"`, never `"exact"`; `attrs.slot` names the joined slot; `attrs.registration_kind` records which of the two edge kinds contributed the candidate. `CONF_REDUCED`. A slot with no function registered anywhere this pass examined contributes no edge — not a spurious self-edge and not a "definitely unused" claim: the fact is genuinely unknown, not empty. |
| `FUNCTION_POINTER_HAS_SIGNATURE` *(edge)* | **registered, no edge producer — populated as a node-level fact instead** | Investigated and found genuinely unmet by any pre-existing edge (unlike `DECL_OVERRIDES_DECL` in the virtual-dispatch family, which was already covered) — but a function pointer's signature is a property of exactly one declaration, not a relation between two entities, so it doesn't fit this schema's edge shape. `callback_graph.py` instead stamps a `function_pointer_signature` **node-level** fact (the slot's own desugared-preferred `qualType` spelling) on the slot's `source_decl` node whenever a `DECL_REGISTERS_CALLBACK`/`DECL_TAKES_ADDRESS_OF` join succeeds. Registered as edge vocabulary only so a hand-built or future graph naming it directly is never rejected. |

No new **node** kind — both `source_decl` endpoints of `DECL_REGISTERS_CALLBACK`/
`DECL_TAKES_ADDRESS_OF` are minted when missing rather than requiring a
pre-existing node from `call_graph.py`/`type_graph.py` (a private
callback-only handler, or a registration API's own callback parameter,
routinely has neither) — the exception to the join-only-onto-an-existing-
node discipline every sibling family in this phase otherwise reapplies, the
same precedent `override_graph.py` already establishes for its own
fully-resolved edge endpoints. See
`docs/contribute/plans/g29-impact-analysis-layer.md`'s G29 Phase 5 item 4
section for the full reasoning.

**Identity design — the single load-bearing correctness property.** Part A's
join only connects when Part B's edges land on *exactly* the same `dst`
identity `call_graph.py` itself already used for the same slot. Investigated
by reading `call_graph._classify_call`/`_resolve_ref_callee_identity`: a call
through a variable/parameter/field resolves its callee identity via an
`id_index` populated **only** for `FunctionDecl`-kind nodes — never for a
`VarDecl`/`ParmVarDecl`/`FieldDecl` — so the lookup always misses and falls
back to the reference stub's own bare, unqualified `mangledName or name` (a
parameter named `h` in two unrelated functions both resolve to the identical
bare identity `"h"`). `callback_graph.py`'s Part B deliberately computes the
same slot identity the same way, rather than a stronger scope-qualified one
that would simply never match anything `call_graph.py` produces — mirroring
an existing, already-shipped limitation of the graph it joins onto, not
inventing a new one.

**A load-bearing negative empirical finding, partially closed by a later fix,
not fully: a struct-field-typed callback slot invoked through member-call
syntax (`w->cb(x)`) still never joins in Part A.** Originally confirmed by
compiling real code through `call_graph.parse_clang_ast_calls`: clang emits a
`MemberExpr`'s own callee reference as
`"referencedMemberDecl": "<node-id-string>"` — a bare string, not a nested
dict — which `call_graph._find_referenced_decl` did not recognize, so the
DFS fell through to the *base object's* own reference instead (`w`, not
`cb`) — a **wrong** edge. A later Codex-review fix (fresh evidence, same PR)
found the identical root cause independently, for a virtual method call
(`p->f()`) `virtual_dispatch_graph.py` depends on, and fixed it in
`call_graph.py` itself: a new `member_index` resolves a string
`referencedMemberDecl`, but only for `_FUNCTION_DECL_KINDS` nodes — a
`FieldDecl` (what a callback slot's own declaration always is) is never one
of those, so it's still never indexed. Re-verified against the identical
repro after that fix landed: the call now resolves to no edge at all rather
than the wrong `w`-attributed one — an improvement, not a close. Part B
still records a real, individually correct `DECL_REGISTERS_CALLBACK`/
`DECL_TAKES_ADDRESS_OF` edge naming the field itself, but Part A's join
against it still only fires when some other code path calls through the
field in a form `call_graph.py` resolves to the field directly — extending
`member_index` to also cover `FieldDecl` stays its own scoped follow-up.

Scoped to a **plain, free-function** `CallExpr` for the registration case —
not `CXXMemberCallExpr`/`CXXOperatorCallExpr` — mirroring
`override_graph.py`'s own "constructors/destructors deliberately out of
scope for this first slice" precedent. See `callback_graph.py`'s own module
docstring for the full empirical AST-shape findings (an explicit `&func`
`UnaryOperator`, an implicit `FunctionToPointerDecay` cast, a typedef'd
function-pointer type's `desugaredQualType`) and what's deliberately
deferred (extending `call_graph.py`'s own identity/reference resolution,
`CXXMemberCallExpr` registration detection).

Coverage is tracked at `extractor_passes["callback_graph"]`/
`narrowed_passes["callback_graph"]`/`degraded_passes["callback_graph"]`
(mutually exclusive, same as every other Clang-backed pass) — driven by
`inline_graph_fold.fold_callback_graph`, run after `fold_macro_graph` (its
own Part A needs `fold_call_graph`'s edges already folded). Unlike a plain
single-extractor pass, this stamp is the worst of *two* independent
signals: this pass's own Part B clang run, and `call_graph`'s own already-
recorded coverage state — because Part A's `CALLBACK_MAY_INVOKE` join reads
`call_graph`'s function-pointer-kind `DECL_CALLS_DECL` edges, a degraded or
never-run `call_graph` pass can hide a real dispatch target even when this
pass's own clang run examined the whole compile DB cleanly, mirroring how
`virtual_dispatch_graph.py` derives its own coverage from its three
prerequisites (see above).

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
`"type_graph:DECL_HAS_TYPE:var"` — so a producer that covers
return/parameter types but not variable/typedef-underlying types can
honestly report partial coverage per role instead of one blanket family
flag. See [Graph Coverage & Negative Evidence](../learn/graph-coverage.md)
for why an absent edge is never proof of an absent dependency.

The roles `type_graph.py` populates (G29 Phase 5 item 5 brought this set to
the parity the plan asked for):

| Edge kind | Role | What the edge means |
|---|---|---|
| `TYPE_INHERITS` | `base` | a record's base class |
| `TYPE_HAS_FIELD_TYPE` | `field` | a record's field type |
| `TYPE_HAS_FIELD_TYPE` | `alias` | a typedef/type-alias — **and an alias template** — target type |
| `TYPE_HAS_FIELD_TYPE` | `enum_underlying` | an enum's fixed underlying type (`enum class Color : detail::Handle`) |
| `TYPE_HAS_FIELD_TYPE` / `DECL_HAS_TYPE` | `template_param` | a **non-type** template parameter's own type (`template <detail::Handle H>`) |
| `TYPE_HAS_FIELD_TYPE` / `DECL_HAS_TYPE` | `default_template_arg` | a template parameter's default *type* argument (`template <class T = detail::Impl>`) |
| `DECL_HAS_TYPE` | `var` | a namespace/class-scope variable's own type |
| `DECL_HAS_TYPE` | `return` | a function's return type |
| `DECL_HAS_TYPE` | `param` | a function's parameter type |
| `DECL_REFERENCES_DECL` | `ref` | a body referencing a variable/enumerator (non-call) |

The last three roles the plan item names need no role of their own:
**member-pointer type** (`int Owner::*`, `void (Owner::*)(int)`) and
**function-pointer signature** (`void (*)(detail::Impl *)`) are reached by
the same nested type-name walk that already extracts template arguments, so
they surface under whichever role the enclosing declaration carries; the
**typedef target** is `alias`. The two `template_param`/
`default_template_arg` rows carry *two* edge kinds because the role is
attributed to the **templated entity**, not to the template wrapper (which
is not a node): a class/alias template's parameter dependency lands on its
`record_type` node, a function/variable template's on its `source_decl` one
— the same nodes those entities' own field/signature edges already use.

Three type dependencies are **not** represented as edges here: a *non-type*
template parameter's default **value**, a *template template* parameter's
default, and a **concept/constraint** dependency. The first two are absent
because clang's JSON carries nothing to resolve; the third needs a graph node
kind that does not exist yet. The AST evidence for each, and what closing the
third would take, is recorded once in
[G29 Phase 5 item 5](../contribute/plans/g29-impact-analysis-layer.md#phase-5-new-semantic-graph-families)
— the rationale owner for these decisions — rather than restated here.

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

---

## L4 and L5 records in the build/source pack

The L4 source-declaration and L5 graph-edge records the pack stores, with
the practical reading of each (moved here from
[Source & Build Data](../learn/build-source-data.md), which keeps the
narrative).

### L4 — one source declaration (`SourceAbiTu.macros[] / .functions[] / …`)

Per-TU source replay produces one `SourceEntity` per declaration, grouped by
kind (`declarations`, `types`, `functions`, `variables`, `macros`, `templates`,
`inline_bodies`, `constexpr_values`). The fields that matter for diffing are
`signature_hash` (stable across a *value*-only edit), `body_hash` (inline/
template bodies), and `value` (the normalized macro/`constexpr`/default-arg
string):

```json
{
  "id": "src://cart.h#CART_MAX_ITEMS",
  "kind": "macro",
  "qualified_name": "CART_MAX_ITEMS",
  "value": "64",
  "visibility": "public_header",
  "api_relevant": true,
  "confidence": "high"
}
```

A later TU dump with `"value": "128"` on the same `qualified_name` is exactly
what `diff_source_abi()` turns into `public_macro_value_changed` — the macro
never becomes a symbol, so this record is the *only* place that fact exists.
The same shape carries a function's default-argument value (`value`) separately
from its `signature_hash`, which is what lets abicheck tell "the default
changed" (`API_BREAK`) apart from "the parameter type changed" (a different
symbol, an add+remove).

### L5 — one graph edge (`SourceGraphSummary.edges[]`)

The graph is nodes (`GraphNode`: `id`, `kind` — `target`/`source`/`header`/
`source_decl`/`binary_symbol`/…) linked by typed, directed `GraphEdge`s. This is
the record `graph explain` walks to answer "what does this declaration reach":

```json
{
  "edge": "SOURCE_DECL_MAPS_TO_SYMBOL",
  "src": "decl://_ZNK4cart4Cart5totalEb",
  "dst": "binary_symbol://_ZNK4cart4Cart5totalEb",
  "provenance": "source_abi_link",
  "confidence": "high"
}
```

A `decl://` node id is `SourceEntity.identity()` — the **mangled** name when
one exists, not the qualified source name — precisely so overloads
(`total(bool)` vs. a hypothetical `total()`) get distinct source-decl nodes
instead of colliding on one `Cart::total`. For a C++ method this makes the
`decl://` and `binary_symbol://` ids look identical modulo prefix; that's
expected — the edge still records *two separate nodes* (a source declaration
and an exported binary symbol) so a rename on one side without the other shows
up as the edge moving to a different `dst`.

`diff_source_graph_findings()` compares the *edge set*, not individual nodes:
if this exact `(src, dst, kind)` triple disappears and a *different* `dst`
appears for the same `src`, that is `source_to_binary_mapping_changed` — the
declaration now compiles down to a different exported symbol, a fact neither
the binary diff nor the source diff alone would name.

### Why this matters in practice

None of L3/L4/L5's records are byte offsets or machine code — they are
normalized *facts about intent* (a flag, a macro value, a reachability edge),
which is exactly why the [authority rule](../learn/evidence-and-detectability.md#how-they-combine)
caps them at `API_BREAK`/`risk`: they describe what the source or build says
should happen, not what the compiler actually emitted. Only L0/L1 — the
`AbiSnapshot` derived straight from the binary and its DWARF/PDB — records what
*did* happen, which is why it alone can prove `BREAKING`.

---

## `reachability_state`

Every finding in a full JSON or SARIF report now carries `reachability_state`
(`sarif`: `reachabilityState`), one of:

- `reachable` — the finding's subject was proven public-reachable (the same
  signal that sets `public_reachable: true`).
- `unreachable` — the reachability walk positively found this finding's
  subject **not** part of the effective public ABI.
- `unknown` — no walk reached a verdict at all, or the only evidence
  available (typically the optional [L5 source graph](../learn/build-source-data.md))
  is itself flagged narrowed or degraded for the relevant edge family. See
  [Graph Coverage & Negative Evidence](../learn/graph-coverage.md) for why `unknown`
  is not the same claim as `unreachable`.

Before this, a JSON/SARIF consumer could only see the boolean
`public_reachable`, which is `false` for **both** `unreachable` and
`unknown` — there was no way to tell "we checked and it's safe to suppress"
apart from "we never checked, don't assume it's safe." `reachability_state`
closes that gap; it is always present (never an absent key), since
`unknown` is itself a meaningful, honest answer.

## `impact_assessment`

`impact_assessment` bundles the finding's reachability/impact fields into
one object, so a consumer doesn't need to stitch together several
independently-nullable keys:

```json
{
  "reachability_state": "reachable",
  "public_reachable": true,
  "reachability_kind": "value_embedding",
  "confidence": "high",
  "proof_path": {
    "target": "ns::internal::Helper",
    "root": "pub",
    "is_direct": false,
    "prose": "fn:pub → base:detail::Helper"
  },
  "decision": {
    "state": "kept"
  }
}
```

- `reachability_state`/`public_reachable`/`reachability_kind` mirror the
  finding's own top-level fields of the same name.
- `proof_path` mirrors `affected_public_roots`/`impact_proof_path`/
  `impact_is_direct`/`reachability_proof_path`, when the finding has any of
  them — `root` and `steps` come from the structured L5 graph walk
  ([ADR-048](../contribute/adr/048-canonical-entity-identity-and-graph-reconciliation.md)),
  `prose` is the human-readable rendering. `steps` is empty when only the
  prose rendering is available. When a producer had more than one candidate
  path and picked this one via the
  [ADR-046 D6 preference order](source-graph-schema.md#proof-path-preference-order-adr-046-d6),
  the runner-ups appear as `alternative_paths` (each its own nested
  `proof_path`-shaped object) and `discarded_path_count` counts any further
  candidates beyond the kept cap — both absent for the common single-candidate
  case. `occurrence_id` is a stable, `description`-independent hash over this
  path's underlying graph occurrences
  ([ADR-046 D1](source-graph-schema.md#relation_key-and-occurrence_id)) —
  absent today for nearly every finding, since no current producer populates
  the per-call-site attrs it's derived from.
- `decision` records whether the finding was kept or suppressed, and (when a
  [pattern-aware modulation](../use/api-surface-intelligence.md) or
  other classification override fired) the reason code and
  `verdict_override` — the overridden verdict, which can be a downgrade
  *or* an escalation (e.g. a `std::`-embedding proof promoting
  `STDLIB_IMPLEMENTATION_CHANGED` to `BREAKING`), not always a demotion.
  `suppression_rule` names the suppression rule that actually suppressed a
  finding (its `label`, falling back to its `reason`) — present only on a
  `suppression.suppressed_changes[]` entry, and only when the matching rule
  set either field.
- `evidence_category`/`correlated_change_kind` mirror the finding's own
  top-level fields when set.
- `root_cause_id`/`root_cause_display`/`impact_group_id` (G29 Phase 3
  follow-up) are this finding's root-cause grouping key/display root — the
  same computation [root-cause grouping](../learn/impact-analysis.md#root-cause-grouping) below uses,
  surfaced per-finding independent of `report_mode`. Present only when the
  finding has a real correlation signal (a `caused_by_type`, or its own
  symbol is referenced by another finding's `caused_by_type`); absent for
  an uncorrelated singleton finding, so a plain finding's
  `impact_assessment` doesn't balloon with a root cause naming nothing but
  itself. `impact_group_id` is currently always identical to
  `root_cause_id` — a placeholder alias until a future revision gives it
  independent meaning.
- `root_cause_evidence` (G29 Phase 6) is this finding's own entry from the
  `RootCauseCorrelator` (`abicheck.impact.correlation.correlate_root_causes`)
  — present only when this finding is a member of one of that composer's
  multi-piece groups: the four load-failure kinds (a symbol vanishing from
  the export table, an internal dependency of a public entry point, a real
  consumer's own unresolved import, and that consumer actually failing to
  load), correlated by shared symbol identity and ranked by evidence
  strength (`artifact_proven` → `call_graph_overapprox` → `call_graph_proven`
  → `consumer_proven` → `runtime_proven`). `evidence_level` is this
  finding's own rank; `strongest_evidence_level`/`evidence_levels` describe
  the whole correlated group, so a consumer can tell "this piece alone is
  only artifact-proven, but the group as a whole also has consumer proof"
  without re-running the correlator itself. Unconditional on `report_mode`,
  same as `root_cause_id`/`impact_group_id`.

`impact_assessment` intentionally duplicates data already published at the
top level — it exists so a consumer can query one object instead of several
separately-named keys, not to replace the existing fields (which stay for
backward compatibility). To keep large reports from filling up with mostly
empty objects, `impact_assessment` is **only emitted when it carries
information beyond the all-defaults case** — a plain finding with no
reachability/impact evidence at all won't have this key, only
`reachability_state: "unknown"`.

Both fields appear everywhere a finding is serialized: the full `changes[]`
list, `--report-mode leaf`'s `leaf_changes[]`/`changes[]` union (root type
changes route through a separate builder that mirrors the same fields), and
each entry in `suppression.suppressed_changes[]` — a suppressed finding's
`decision.state` is always `"suppressed"` there, so its `impact_assessment`
is always present. SARIF carries the same two fields as `properties.reachabilityState`/
`properties.impactAssessment`. JUnit does not carry the full object (a
structured node/edge object is a poor fit for JUnit's `<properties>`
text-value model) — but `--report-mode root-cause --format junit` does add
additive `rootCauseId`/`rootCause` attributes to each `<failure>` element,
without restructuring JUnit's per-symbol `<testcase>` tree; see
[Root-cause grouping](../learn/impact-analysis.md#root-cause-grouping) below.

---

## Coverage pass states

`SourceGraphSummary` records, per extractor pass, how complete its own
coverage was; [Graph Coverage & Negative Evidence](../learn/graph-coverage.md)
explains why the distinction decides whether an *absent* edge means anything.

- `extractor_passes` — the pass ran over the **full** project scope with no
  errors. An edge family with a `extractor_passes` entry is trustworthy for
  both "this edge exists" and "this edge does not exist".
- `narrowed_passes` — the pass ran, but only over a **restricted** scope
  (e.g. a `--changed-paths`-scoped run). An edge found there is still real;
  an edge *not* found there proves nothing about the parts of the project
  the pass never looked at.
- `degraded_passes` — the pass hit collection errors (a translation unit
  failed to parse, a tool crashed) but still folded in whatever edges it
  managed to extract before failing. The edges it *did* find are real; the
  ones it didn't are an unknown, untracked gap — not evidence of absence.
