# ADR-041: Compiler-Facts Semantic Impact Graph — Roadmap and P0 Slice

**Date:** 2026-07-12
**Status:** Accepted — P0 slice 1 (`type_graph.py`) and P0 slice 2 (semantic
graph diff over the full dependency-edge family) implemented; the rest of
this ADR is a roadmap, not a commitment to ship on any timeline.
**Decision maker:** Nikolay Petrov (@napetrov)

---

## Context

ADR-031 gave the L5 source graph (`source_graph.py`) a node/edge schema wide
enough for a real compiler-derived *semantic impact graph* — provenance,
confidence, compact storage, external-graph refs, coverage honesty, and
`graph explain` localization. ADR-031 D4 (phase 6) then populated exactly one
edge family from it: `DECL_CALLS_DECL`, via `call_graph.py`'s
`clang -ast-dump=json` replay. Four more edge kinds were reserved in the schema
from the start —`DECL_REFERENCES_DECL`, `DECL_HAS_TYPE`, `TYPE_HAS_FIELD_TYPE`,
`TYPE_INHERITS` — but, until this ADR's P0 slice, nothing produced them from
the primary extraction path. `crosscheck.py`'s `public_to_internal_dependency`
check (ADR-035 D4) already *reads* `DECL_REFERENCES_DECL` and `DECL_HAS_TYPE`
alongside `DECL_CALLS_DECL` — it was wired to a source of facts that did not
exist yet.

That gap matters because a large class of real API/ABI risk is not a call at
all:

```cpp
// A public struct with a private field type. No call graph sees this.
struct Public { detail::PrivateType* p; };

// A public inline body reading an internal constant. The call graph only
// sees a DeclRefExpr, never classifies it as a dependency edge.
inline int f() { return DETAIL_CONSTANT + 1; }
```

Separately, `SourceAbiTu.source_edges` (`source_abi.py`) has existed since
ADR-030 as a normalized carrier for exactly this kind of fact and has never
been populated by any extractor (castxml, clang, Android, or the
build-integrated plugin — ADR-038 Flow C always emits `"source_edges": []`).

This ADR records the fuller roadmap for turning the L5 graph from "optional
call graph" into a genuine compiler-derived semantic impact graph, and ships
its first slice.

## The one rule that does not change

Same authority boundary as ADR-028 D3 and the `buildsource/CLAUDE.md` "one
rule": artifact-backed L0/L1/L2 evidence stays authoritative for shipped-ABI
verdicts. Everything in this ADR — call edges, type edges, reference edges,
object/link provenance, impact closures — can **explain, localize, scope, add
confidence/provenance, or correlate** a finding. It can *elevate* a RISK/
API_BREAK finding's confidence and it can *select scope* (which TUs to
replay). It can never manufacture a `BREAKING_KINDS` verdict on its own, and
graph *absence* must never read as "no risk" (coverage honesty, ADR-031 D9 /
ADR-035 D4) — virtual dispatch, function pointers, templates, macros,
generated code, and LTO all defeat a static graph, so a missing path is
evidence of nothing.

## Decision — P0 slice 1 (this change)

Add `abicheck/buildsource/type_graph.py`, architecturally mirroring
`call_graph.py`:

- `parse_clang_ast_types()` — a **pure** function over a
  `clang -ast-dump=json` tree, unit-tested without a compiler. Extracts:
  - `TYPE_INHERITS` (`CXXRecordDecl.bases`) — a record's base class.
  - `TYPE_HAS_FIELD_TYPE` (`FieldDecl`) — a record's field type.
  - `DECL_HAS_TYPE` (`ParmVarDecl` under a function-like decl) — a
    function/method's parameter type.
  - `DECL_REFERENCES_DECL` (`DeclRefExpr` to a `VarDecl`/`EnumConstantDecl`,
    not a call target) — a function body reading an internal global/constant.
- `ClangTypeGraphExtractor` — the thin, side-effecting `clang` wrapper
  (integration-only), reusing `call_graph.py`'s vetted parse-only argv
  allowlist so both passes stay in lockstep on what is safe to replay.
- `augment_graph_with_types()` — folds edges into the `SourceGraphSummary`,
  reusing the existing `decl://`/`type://` node-id scheme so an edge whose
  endpoint already exists (e.g. folded from L4 with real visibility) attaches
  to it rather than creating a duplicate (first-writer-wins `add_node`).

Wiring (`inline.py`): `_fold_type_graph()` runs immediately after
`_fold_call_graph()`, gated on the same `with_call_graph` flag (an S4/S5
semantic source mode) and using the same changed-path/`headers-only`-scope
precedence, so the two passes share one scoping decision. A missing `clang++`
degrades to a `type_graph:clang` *failed* extractor row — never aborts
collection (ADR-028 D3).

Consumer (`crosscheck.py`): `_DEPENDENCY_EDGE_KINDS` now includes
`TYPE_HAS_FIELD_TYPE` and `TYPE_INHERITS` (it already listed
`DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`), so
`public_to_internal_dependency` fires on type-level dependencies with **no
detector-side change** — it was already reading edges that just never
existed.

Coverage honesty (`source_graph.py` `finalize()`): two new coverage buckets,
`type_edges` and `reference_edges`, each with an independent `collected`
flag — a graph can have call edges but no type edges (e.g. an older pack, or
a build where the second clang pass failed), and the report must say so
rather than reading falsely clean.

Two follow-up fixes landed in the same slice after review (both correctness,
not scope, changes):

- **Unqualified type-name resolution.** clang's `qualType` prints a type *as
  written* in the source, not fully qualified — a field typed `Base` inside
  `namespace ns { struct Widget { Base *p; }; }` prints as `"Base"`, not
  `"ns::Base"`. A naive textual match would create a disconnected
  `type://Base` node instead of joining the L4-derived `type://ns::Base`
  node. `parse_clang_ast_types` now runs a first pass
  (`_index_declared_entities`) over the whole AST to index every record's
  qualified name, then resolves an unqualified spelling against the nearest
  enclosing scope (`_resolve_type_name`) — approximate unqualified-name
  lookup, not real semantic resolution. An edge whose target could not be
  resolved is kept (best effort) at `CONF_REDUCED` rather than silently
  claiming a confident match it doesn't have.
- **Provenance on AST-only destination nodes.** The primary case this module
  exists for — a public decl/type reaching a *private*-header type/variable —
  is exactly the case where the destination is **not** already in the L4
  surface (L4 only captures the public-reachable surface), so the new node
  `augment_graph_with_types` creates for it previously carried no
  `visibility`/`defined_in_project` marker and `public_to_internal_dependency`
  could not classify it as internal — the feature would silently produce no
  finding on its own headline scenario. The same first pass also indexes each
  declaration's file; `augment_graph_with_types` now takes the same
  `project_files` set `augment_graph_with_calls` already computes
  (`call_graph.project_source_files`) and marks a new destination node
  `defined_in_project` when its file is one of the project's own sources/
  private headers, mirroring the call graph's existing convention exactly.

A second review pass caught two more instances of the same class of bug —
both fixed the same way, by widening what the first indexing pass covers:

- **Enum/typedef/type-alias targets weren't indexed.** The scope-resolution
  and provenance fixes above only indexed `_RECORD_DECL_KINDS`, so a private
  `enum`/`typedef`/`using` used as a field or parameter type fell through
  unqualified and un-provenanced exactly like an un-indexed record did before
  the first fix. `_index_declared_entities` now indexes `EnumDecl`/
  `TypedefDecl`/`TypeAliasDecl` the same way as records — `_resolve_type_name`
  and the `decl_file` lookups in `_walk_types` needed no changes, since they
  were already generic over whatever `name_index`/`decl_file` contain.
- **An incomplete `DeclRefExpr.referencedDecl` stub broke the headline
  scenario.** clang commonly represents a variable reference as
  `{"kind": "VarDecl", "name": "k"}` with no `mangledName`/`loc`, even when
  the full `VarDecl` elsewhere in the same TU carries both. Keying the edge
  off that stub's bare-name identity meant `inline int f() { return
  detail::k; }` — the PR's own motivating example — created a
  `DECL_REFERENCES_DECL` edge to `decl://k` with no `dst_file`, so the
  private constant it names could never be marked `defined_in_project`. The
  index pass now also builds a bare-name → full-identity map for
  `VarDecl`/`EnumConstantDecl` declarations; `_resolve_ref_identity` prefers
  the stub's own identity when it already resolves, and otherwise falls back
  to the unique full declaration sharing its bare name — an ambiguous bare
  name (two different variables named `k` in different scopes) is left
  unresolved rather than guessed.

`_fill_missing_dst_files` (a post-pass edge backfill inherited from an
earlier single-pass draft) was removed as dead code once the two-pass design
made it provably a no-op: `decl_file` is fully built by
`_index_declared_entities` *before* `_walk_types` creates any edge, so every
`decl_file.get(...)` lookup inside the walk already sees the complete index
regardless of declaration order.

A third review pass (CodeRabbit + Codex, on the second fix commit) found two
more instances of the same two bug classes, deeper in each:

- **A `_type_confidence` case CodeRabbit found and Codex found again in a
  different shape.** A uniquely-resolved *global* declaration (`"Base"` at
  namespace scope, where the resolved spelling equals the raw spelling
  because there was nothing to qualify) was mis-scored `CONF_REDUCED` by a
  naive `raw == resolved` check. Fixed by having `_resolve_type_name` return
  an explicit `(name, matched)` tuple instead of inferring success from
  string equality — the confidence call sites now use `matched` directly.
- **Field/base types resolved against the wrong scope.** A field/base type
  naming a sibling nested in the *same* record (`Outer::Inner` referenced as
  bare `"Inner"` from inside `Outer`) was resolved against the record's
  *enclosing* scope, not its own — real C++ member lookup checks the
  record's own body first. Fixed by resolving base/field types against
  `child_scope` (`[*scope, name]`) instead of `scope`; `_resolve_type_name`'s
  existing descending-prefix loop still falls through to every shorter
  (enclosing) prefix, so this is a strict superset of the old lookup, not a
  narrowing.
- **Cross-TU edge merging discarded richer provenance.** `ClangTypeGraphExtractor
  .extract_from_build`'s per-TU dedup kept whichever TU's edge for a given
  `(src, dst, kind)` key was seen *first* — if that TU didn't include the
  header declaring a private `dst` (so no `dst_file`) while a later TU did,
  the richer edge was silently dropped. Fixed with `_merge_type_edges`:
  keeps the stronger `confidence` and backfills a missing `dst_file` from
  either edge.
- **Only a partially-qualified spelling was handled, not the fully general
  case, and only handled at edge-creation time.** Two more instances of the
  earlier "unqualified name" and "provenance only set on the winning branch"
  bug classes, found one layer deeper:
  - `_resolve_type_name`'s `"::" in raw` early-return treated *any* spelling
    containing `::` as already fully qualified. That's wrong for a
    **partially** qualified spelling — `detail::Impl` written inside
    `namespace ns { namespace detail { ... } }` prints exactly as
    `"detail::Impl"`, not `"ns::detail::Impl"`, so the old shortcut still
    created a disconnected `type://detail::Impl` node. The fix generalizes
    the *fully*-unqualified-name logic to handle both cases uniformly: index
    lookups key on the spelling's *last* component, candidates are filtered
    to those whose full qualified name equals or ends with `"::" + raw`
    (so `detail::Impl` only matches a candidate ending `"::detail::Impl"`,
    never an unrelated `other::Impl`), then the same nearest-enclosing-scope
    search applies.
  - `augment_graph_with_types` only set `defined_in_project` when *creating*
    a node. A private type first observed as another edge's `src` (e.g.
    `detail::Impl`'s own base-class edge) got a bare, unannotated node; a
    later edge establishing it as a project-internal `dst` had nothing left
    to attach the marker to, since the node already existed. Fixed by
    tracking a local `node_by_id` map and backfilling the marker onto an
    already-existing node — unless that node already carries a `visibility`
    attr (real L4 evidence), which this best-effort AST-only marker must
    never override.

**Known limitation, accepted for this slice**: this is still a **second**,
independent `clang -ast-dump=json` pass per translation unit, alongside the
call graph's own pass — the exact "AST replay is expensive, run it once"
concern the wider plan below raises. Unifying the two into one parse (or
better, one Flow-C plugin emission, see P1 below) is deliberately deferred
rather than risking `call_graph.py`'s existing tested extraction path in this
change.

## Decision — P0 slice 2 (this change)

`diff_source_graph_findings()`'s `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` check
(`_internal_dependency_findings` in `source_graph.py`) was the version-over-
version analogue of `crosscheck.py`'s intra-version
`public_to_internal_dependency` check — but only over `DECL_CALLS_DECL`
edges, while the intra-version check already read the full five-edge
dependency family (`_DEPENDENCY_EDGE_KINDS`, ADR-041 P0 slice 1). A public
struct that gained a private field type or base class between two versions,
or a public function that gained a private parameter type or a reference to
an internal constant, was invisible to the version diff even though the
same-version cross-check would have caught it — exactly the "same public
type, different field/base dependency closure" gap this roadmap item names.

Fixed by generalizing the closure computation:

- `source_graph.DEPENDENCY_EDGE_KINDS` — the same five edge kinds
  (`DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/
  `TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS`) `crosscheck.py` already used, now the
  single shared source of truth; `crosscheck._DEPENDENCY_EDGE_KINDS` is an
  alias onto it so the two checks cannot drift apart on what "reaches an
  internal entity" means.
- `_dependency_reachability()` — generalizes `_public_entry_call_reachability`
  from a call-only closure to one over all five kinds. Entries are decls
  backing an exported symbol (as before) *union* public types
  (`_public_types()`, new) — a public struct/enum/typedef rarely has its own
  exported symbol, so it needs to be its own closure-walk starting point to
  catch a newly-added private field/base edge hanging directly off it.
- `_public_entry_internal_reach()` now walks that broader closure and
  classifies an internal *target* as any `source_decl`/type-kind node absent
  from the broadened public set (`_public_decls() | _public_types()`), not
  `source_decl` alone — so a private type reached as a field/base/parameter
  type is recognized as internal, not silently dropped for the wrong node kind.
- `_has_internal_reach_coverage()` gates on *any* dependency edge kind (not
  `DECL_CALLS_DECL` specifically) being present on both sides, preserving the
  existing coverage-honesty rule: an evidence-poor baseline (no semantic pass,
  or no public closure) makes the check skip rather than flag every
  pre-existing dependency as newly added.

A review pass (Codex) caught one more instance of the same coverage-honesty
class of bug: gating on *any* dependency edge kind being present on both sides
is too coarse once the closure spans five kinds instead of one. A baseline
that only ever ran the call graph (`DECL_CALLS_DECL`) while the new side
additionally ran the type-graph pass for the first time still passes that
gate — it has *a* dependency edge on both sides — but the closure itself then
walks `TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS`/`DECL_HAS_TYPE`/
`DECL_REFERENCES_DECL` edges the baseline could never have collected, so every
target reachable only through one of those kinds reads as newly internal —
purely from re-scanning unchanged source with better tooling, not a code
change. Fixed with `_common_dependency_edge_kinds()`: the closure is
restricted, per version-diff comparison, to the intersection of dependency
edge kinds actually collected on *both* sides; `_dependency_reachability()`
and `_public_entry_internal_reach()` take that kind set as an explicit
parameter (rather than always closing over the full
`DEPENDENCY_EDGE_KINDS`) so a collector-coverage improvement on one side can
never manufacture a finding on its own.

A second Codex pass on that fix caught the opposite failure mode: intersecting
per *exact* edge kind is too strict. `type_graph.augment_graph_with_types`
folds `DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/
`TYPE_INHERITS` from a single AST pass — so a baseline that already has (say)
a `DECL_HAS_TYPE` edge but happens to have zero `TYPE_HAS_FIELD_TYPE` edges ran
the *same* pass as a new side that has both; a first-ever `TYPE_HAS_FIELD_TYPE`
edge there is a real new dependency, not a collector-coverage gap, and the
per-exact-kind intersection dropped it regardless. Fixed by judging coverage
at extractor-pass granularity: `_DEPENDENCY_EDGE_FAMILIES` groups the five
kinds into the two passes that actually emit them together (`call_graph`'s
`{DECL_CALLS_DECL}`, `type_graph`'s other four as one family); a family counts
as common when *any* of its kinds is present on both sides, and then every
kind in that family — including one with zero prior edges — is eligible for
the closure.

A third Codex pass found the residual gap in *that* fix: per-family edge
*presence* is still an indirect proxy for "the pass ran" — a pass can run to
completion and legitimately find zero edges of its whole family on one side
(e.g. no public struct anywhere had a private field yet), which reads
identically to "the pass never ran" if presence is the only signal available.
Until this fix, `SourceGraphSummary` had no field for "ran, zero output"
distinct from "never ran" — not even the `coverage.type_edges.collected` flag
from P0 slice 1, which is *also* edge-presence-derived. Fixed by adding real
pass-level provenance: `SourceGraphSummary.extractor_passes: dict[str, bool]`
(a new additive field, round-tripped through `to_dict`/`from_dict` with
defensive `.get()` parsing — no `SOURCE_GRAPH_VERSION` bump needed, same
forward-compat rule as the reserved-then-populated edge kinds). `inline.
_fold_call_graph`/`_fold_type_graph` stamp `extractor_passes["call_graph"]` /
`["type_graph"]` to `True` right after a successful extraction, regardless of
how many edges it added. `_dependency_kinds_covered()` (shared by
`_common_dependency_edge_kinds()` and `_has_internal_reach_coverage()`) now
checks the recorded flag first and only falls back to edge presence when it is
absent — a hand-built test graph or a pack from before this fix. `finalize()`'s
`coverage.call_edges`/`type_edges`/`reference_edges.collected` flags gained the
same fix, so the human-readable coverage report is honest about this too, not
just the internal helper.

No new `ChangeKind`: this reuses `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`,
broadening its recall exactly as slice 1 broadened `public_to_internal_dependency`'s
— the type/reference edges the P0 slice 1 extractor started producing were
already the only missing ingredient.

The remaining half of P0 item 2 — combining a `body_hash`/`type_hash` change
(`source_diff.py`'s nine findings) with a new/changed graph edge into one
finding — is still open, along with item 3 (`graph explain` proof paths).

## Roadmap (not committed — scope/sequence per the usual planning process)

### P0 — remaining high-value, low-risk work

1. ~~Populate `DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/
   `TYPE_INHERITS`~~ — **done, ADR-041 P0 slice 1.**
2. **Semantic graph diff.**
   ~~Same public decl/type, new internal-dependency edge over the full
   dependency-edge family~~ — **done, ADR-041 P0 slice 2**
   (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`, generalized beyond
   `DECL_CALLS_DECL`). Still open: same public decl, different
   `body_hash`/`type_hash` (already on `SourceEntity`, cf. `source_diff.py`'s
   nine findings) *combined with* a new/changed graph edge, so a report can
   say "X now reaches internal Y, defined in changed file Z" instead of two
   disjoint findings.
3. **`graph explain` proof path per finding.** `localize_symbol` already walks
   symbol → target → source decls → headers → build options → static callees
   (ADR-031 D7). Thread a path (not just an endpoint list) into
   `PUBLIC_TO_INTERNAL_DEPENDENCY` / `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED`
   findings' evidence, so a report shows the concrete edge chain, not just
   "tool says risk."
4. ~~Coverage counters per edge family~~ — **done, this ADR** (`type_edges`/
   `reference_edges`); extend further per P1 item 4 below when object/link
   provenance lands.

### P1 — stronger ABI/API intelligence

1. **Move type/reference extraction into Flow C (the ADR-038 plugin).**
   `contrib/abicheck-clang-plugin/` already rides the compiler's own AST for
   the L4 entity facts (functions/types/macros/hashes) with **zero extra
   frontend passes** — the plugin hardcodes `"source_edges": []` today. Once
   it emits `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE`/
   `DECL_REFERENCES_DECL`/`DECL_CALLS_DECL` into `source_edges` during the
   *real* product compile, `inputs_pack.py`'s ingest path folds them for free
   and both `call_graph.py`'s and `type_graph.py`'s standalone replay passes
   become optional (CI/no-build-integration fallback only) rather than the
   only source. This is the direct fix for the "two separate expensive AST
   passes" limitation above, and for the wider "AST replay vs. compiler facts
   during build" tension the original proposal opens with.
2. **Object/link provenance graph.** New node kinds
   (`object_file`/`archive_member`/`static_library`/`linker_script`/
   `version_script`/`export_map`/`comdat_group`) and edges
   (`COMPILE_UNIT_EMITS_OBJECT`, `OBJECT_DEFINES_SYMBOL`,
   `ARCHIVE_CONTAINS_OBJECT`, `LINK_UNIT_EXPORTS_SYMBOL`, …) so a symbol
   change can be attributed to "which object/archive member/link step" rather
   than only "which target." Explains cases `TARGET_DEPENDENCY_ADDED` /
   `EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED` currently can't: an accidental
   export from a static archive, a COMDAT/weak-symbol resolution change, a
   new transitive `DT_NEEDED` traced to a specific object.
3. **Public-entry impact closure.** A changed-file → affected-public-API BFS
   over the existing graph (`poi.py`'s `resolve_symbol_tus` already does the
   reverse direction — export delta → declaring TU). Feeds PR-scoped deep
   scans ("this PR touches `src/detail/cache.cpp`; only 3 public entries are
   reachable from it; replay only those") on top of the existing
   changed-path/`headers-only` scoping (ADR-035 D7).
4. **Explicit per-edge confidence/provenance model.** `GraphEdge.confidence`
   already exists (`CONF_HIGH`/`CONF_REDUCED`/`CONF_UNKNOWN`); extend the
   *labels* (not the field) to the call graph's existing `call_kind`/
   `resolution` pattern for every edge family — a `TYPE_INHERITS` edge from a
   textual base-class match is not the same confidence as one resolved
   through a linked type; make that explicit rather than implicit in "this
   module always emits `CONF_HIGH`."
5. **Stable cross-clang-version identity.** Today identity is
   `mangled_name` else `qualified_name#signature_hash` (`SourceEntity.identity()`)
   for decls, and a bare textual base-type spelling
   (`type_graph._base_type_name()`) for AST-only type nodes — accepted
   collision risk documented inline. A USR-based identity (clang already
   computes USRs) would remove that collision risk without changing the
   public schema.

### P2 — advanced / differentiating

1. Virtual-dispatch/class-hierarchy graph with possible-override edges (the
   call graph already labels a virtual call `CALL_KIND_VIRTUAL` /
   `RESOLUTION_OVERAPPROX`; this closes the loop to the actual override set).
2. Template pattern ↔ instantiation ↔ exported-symbol graph (partially
   present via `source_link.py`'s `template_instantiation_symbol_to_decl`
   attribution; not yet a graph edge).
3. Macro expansion/reference graph for public headers (`DECL_USES_MACRO`) —
   `preprocessor_scan.py` (ADR-035 D2) already captures macro facts at the S2
   tier; this would connect them into the same graph instead of a separate
   advisory channel.
4. Kythe/CodeQL/clangd as an alternate P0/P1 edge source — `graph_backends.py`
   already ingests both into the same edge vocabulary (`external_graph_refs`
   records provenance); this ADR's edge kinds are exactly what a Kythe/CodeQL
   export would also produce, so P1/P2 items apply equally whichever backend
   fills them in.

## Consequences

- `crosscheck.py`'s `public_to_internal_dependency` check gets materially
  more recall for free (no detector change) wherever `--depth source`/
  `--source-method s4+` already runs with `clang++` available — it was
  wired to edge kinds nothing produced.
- A second `clang -ast-dump=json` pass runs per TU whenever the semantic
  source mode is active, roughly doubling the L5 AST-parsing wall-clock
  documented in `docs/development/performance.md` § L4/L5 (mitigated by the
  same RAM-aware job cap `call_graph.py` uses). P1 item 1 is the intended fix,
  not a promise for this change.
- No schema version bump: `SOURCE_GRAPH_VERSION` node/edge kinds were already
  reserved (ADR-031 D2); this ADR only starts populating them. Older readers
  ignore edge kinds they don't recognize (`GraphEdge.from_dict` is
  defensive), so no forward-compat break.
