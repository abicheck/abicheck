# ADR-041: Compiler-Facts Semantic Impact Graph — Roadmap and P0 Slice

**Date:** 2026-07-12
**Status:** Accepted — P0 slice 1 implemented (`type_graph.py`); the rest of
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

**Known limitation, accepted for this slice**: this is a **second**,
independent `clang -ast-dump=json` pass per translation unit, alongside the
call graph's own pass — the exact "AST replay is expensive, run it once"
concern the wider plan below raises. Unifying the two into one parse (or
better, one Flow-C plugin emission, see P1 below) is deliberately deferred
rather than risking `call_graph.py`'s existing tested extraction path in this
change.

## Roadmap (not committed — scope/sequence per the usual planning process)

### P0 — remaining high-value, low-risk work

1. ~~Populate `DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/
   `TYPE_INHERITS`~~ — **done, this ADR.**
2. **Semantic graph diff.** `diff_source_graph()` (ADR-031 D6) is structural
   (nodes/edges added/removed). Extend it to notice: same public decl, new
   internal-dependency edge (already partly covered by
   `PUBLIC_TO_INTERNAL_DEPENDENCY`); same public type, different field/base
   dependency closure; same public decl, different `body_hash`/`type_hash`
   (already on `SourceEntity`, cf. `source_diff.py`'s nine findings) *combined
   with* a new/changed graph edge, so a report can say "X now reaches
   internal Y, defined in changed file Z" instead of two disjoint findings.
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
