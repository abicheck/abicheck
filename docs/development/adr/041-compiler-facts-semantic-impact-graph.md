# ADR-041: Compiler-Facts Semantic Impact Graph — Roadmap and P0 Slice

**Date:** 2026-07-12
**Status:** Accepted — P0 slice 1 (`type_graph.py`), P0 slice 2 (semantic
graph diff over the full dependency-edge family), P0 slice 3 (`graph
explain` proof paths), P0 slice 4 (body/type-hash-change correlation), the
header-only-graph addendum (`header_graph.py`, no build integration required,
now also reachable from the standalone `dump --header-graph` CLI, not just
`compare`), and P1 items 1, 2, 3, 4, 5 (plugin injection, object/link
provenance graph, public-entry impact closure — now wired into `scan`'s
PR-scoped replay-seed focusing via `resolve_changed_paths_public_impact` —
per-edge confidence/provenance, and stable cross-clang-version identity)
implemented; the rest of this ADR is a roadmap, not a commitment to ship on
any timeline.
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
build-integrated plugin, ADR-038 Plugin injection — it always emits
`"source_edges": []`).

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
better, one Plugin-injection emission, see P1 below) is deliberately deferred
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

> **Note (docs review, 2026-07):** the "Nth review pass" paragraphs below,
> through the end of P0 slice 3, are an **implementation changelog** — a
> chronological record of bugs found and fixed during code review while this
> slice was landing. They are not additional architectural decisions; the
> decisions are the bulleted items above each chronology and in "Decision —
> P0 slice 3". Kept in place as history per repo convention (ADRs are not
> rewritten), not because each entry is load-bearing for understanding the
> design.

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

A fourth Codex pass found two more instances, both about *how* a candidate
target gets classified internal rather than *whether* the diff runs at all:

- **"Not public" was treated as "internal."** The closure's internal-target
  test was `kinds.get(target) in {source_decl, type-kinds} and target not in
  public` — but "not declared by a public header" is not the same as
  "internal": a third-party or standard-library type used as a new field/
  parameter type (`augment_graph_with_types` creates such a node with no
  `visibility`/`defined_in_project` marker, since its `dst_file` isn't a
  project file) is *also* not declared by any project header, so it looked
  identical to a genuinely private project entity. `crosscheck.py`'s
  `_is_internal_decl` already solved this correctly — positive evidence
  required (explicit `private_header`/`source` visibility, or project-file
  provenance plus a non-system-looking name) — but the version diff had its
  own, weaker approximation. Fixed by promoting the shared vocabulary itself:
  `DECL_NODE_KINDS`/`PUBLIC_VISIBILITIES`/`INTERNAL_VISIBILITIES`/
  `UNANNOTATED_VISIBILITIES`/`looks_like_system_name`/
  `is_public_dependency_node`/`is_internal_dependency_node` now live in
  `source_graph.py` as the single source of truth; `crosscheck.py`'s
  `_DECL_NODE_KINDS`/`_PUBLIC_VISIBILITIES`/`_INTERNAL_VISIBILITIES`/
  `_UNANNOTATED_VISIBILITIES`/`_looks_system`/`_is_public_decl`/
  `_is_internal_decl` are now aliases onto them (not independent copies), so
  the intra-version and inter-version checks classify every node identically
  by construction, not by two authors remembering to keep two definitions in
  sync — the failure mode all four preceding fixes in this slice trace back to.
- **The out-of-band `collect --call-graph` path never recorded pass coverage.**
  The zero-edge coverage fix (third pass, above) only patched
  `inline._fold_call_graph` — the source-tree-centric `dump --sources` path.
  `cli_buildsource_helpers._collect_call_graph` (the `abicheck collect
  --call-graph` path, out-of-band pack collection) still called
  `augment_graph_with_calls()`/`finalize()` without stamping
  `extractor_passes["call_graph"]`, so a version diff over two *collected*
  packs — not two inline dumps — still couldn't tell "ran, zero calls" from
  "never ran." Fixed with the same one-line stamp, mirroring
  `inline._fold_call_graph` exactly. `type_graph.py` has no equivalent
  out-of-band collection path yet (P1 item 1 — moving extraction into Plugin
  injection — is the eventual fix for needing two call sites at all), so
  nothing else
  needed the same patch.

No new `ChangeKind`: this reuses `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`,
broadening its recall exactly as slice 1 broadened `public_to_internal_dependency`'s
— the type/reference edges the P0 slice 1 extractor started producing were
already the only missing ingredient.

The remaining half of P0 item 2 — combining a `body_hash`/`type_hash` change
(`source_diff.py`'s nine findings) with a new/changed graph edge into one
finding — is still open.

## Decision — P0 slice 3 (this change)

Roadmap item 3, "`graph explain` proof path per finding": the two dependency-
reachability findings asserted a fact ("public entry X now reaches internal Y",
"N → M known static callees") without showing *how* — no concrete edge chain,
just endpoints and counts. Fixed:

- `source_graph._dependency_path(graph, edge_kinds, entry, target)` — BFS over
  the same *edge_kinds* adjacency `_dependency_reachability` already builds,
  tracking one predecessor edge per node so a shortest witness chain can be
  reconstructed once `target` is reached. One witness path is enough to
  explain a finding; this is not an exhaustive-paths enumeration.
  `_format_dependency_path()` renders it human-readably, e.g. `pub()
  --[DECL_CALLS_DECL]--> helper() --[DECL_HAS_TYPE]--> detail::Impl`.
- `_internal_dependency_findings` (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`)
  appends a `Proof path(s): ...` clause naming the concrete chain for every
  newly-internal target, not just the target list.
- `_call_reachability_findings` (`CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED`)
  appends an `Example newly-reachable path: ...` clause for one newly-added
  callee (no example when the change is a pure removal — nothing new to show).
- `crosscheck.py`'s intra-version `PUBLIC_TO_INTERNAL_DEPENDENCY` is already a
  single edge (not a transitive closure), so its "chain" is one hop; it now
  names the connecting edge kind (`_public_to_internal_change` takes `edge_kind`)
  instead of only the two endpoint labels.

Both proof-path sites are appended to `Change.description` (no new field on
`Change` — the existing text-evidence convention every other graph finding in
this module already uses) rather than a new structured field, keeping this
additive and low-risk to the wider reporting pipeline (JSON/SARIF/JUnit all
already carry `description` verbatim).

A fifth Codex review, on this slice, caught a regression the P0 slice 2 family-
widening fix (third round) had reintroduced in a new guise: widening credit
from one present kind to its whole family (`_DEPENDENCY_EDGE_FAMILIES`) is only
sound when the *same* extractor pass produced the family — confirmed via
`extractor_passes`. Without that confirmation, `_common_dependency_edge_kinds`
was still widening from bare edge *presence*, and `graph_backends.
ingest_kythe_entries()` only ever emits `DECL_REFERENCES_DECL` for a non-call
Kythe ref (never `TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS`/`DECL_HAS_TYPE`) — so a
Kythe-only baseline's lone ref edge was granting blanket credit to the three
Clang-only type-graph kinds it never touched, exactly reproducing the original
false-positive risk one layer down. Fixed by making family-widening
conditional on **both** sides confirming `extractor_passes[pass_name]`;
without that, `_common_dependency_edge_kinds` falls back to exact per-kind
edge-presence intersection (no widening at all) — the same conservative
behavior the very first fix in this slice used, now correctly scoped to
exactly the case it's sound for.

A sixth Codex review found two more issues, both about a signal claiming more
than it actually proves:

- **`extractor_passes` didn't encode extraction *scope*.** `_fold_call_graph`/
  `_fold_type_graph` stamped the pass-ran flag unconditionally, but the pass
  itself can run over only a *subset* of compile units — a changed-path/
  `--since` scan (parses only the changed TUs) or an unseeded run matched to
  L4's `headers-only` scope (parses only the L4-selected TU). "Ran" is
  necessary but not sufficient for the zero-edge-widening fix (third round)
  to be sound: a scoped baseline's "found nothing" is only true of the TUs it
  actually examined, not the whole codebase, so comparing it against a fuller
  (unscoped) candidate could read edges from TUs the baseline never parsed as
  newly-introduced dependencies. Fixed by tracking a local `narrowed` flag
  through the same scope-selection branches already in each function
  (`_is_header_path`-driven changed-path narrowing, `scoped_units` narrowing)
  and only stamping `extractor_passes[...]` when the run was **not**
  narrowed — a changed-path scan whose changed path is a header (which fans
  out to *all* TUs, per the existing scope-selection comment) still counts as
  unscoped. A narrowed run instead falls back to the pre-existing (already
  reviewed) edge-presence inference, never claiming confirmed coverage it
  cannot back up.
- **A private-header type could be a dependency-closure *entry*.**
  `_public_types()` treated any type reached by a `SOURCE_DECLARES` edge from
  a `header`-kind node as public — but `_augment_with_source_abi`'s
  `header_declares` creates a `header` node for *every* declaring file,
  public or private (privacy lives on the type's own `visibility` attr, not
  the declaring-file node's kind). A private type was therefore eligible as a
  dependency-closure entry (`_dependency_reachability`), so a private type
  gaining its own new private field/base could wrongly emit
  `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` with no public API involved at all.
  Fixed by requiring the type node's own `visibility` attr to be in
  `PUBLIC_VISIBILITIES`, mirroring the same positive-provenance discipline the
  fourth review already established for internal-*target* classification
  (`is_internal_dependency_node`) — now applied symmetrically to what counts
  as a public *entry*.

A seventh Codex review found the last instance of the same "ran" ≠ "fully
observed" gap, one layer deeper than the sixth review's scope fix: even an
*unscoped* run examining the whole compile DB can still fail to observe
anything meaningful. `ClangCallGraphExtractor.extract_from_build`/
`ClangTypeGraphExtractor.extract_from_build` are per-TU best-effort — a clang
crash, timeout, empty stdout, or a degenerate AST that blows Python's
recursion limit degrades that *one* TU to zero edges *silently*, recording
only a `diagnostics` entry; the returned edge list alone cannot distinguish
"every TU parsed cleanly, found nothing" from "some TU never actually got
parsed." An entirely empty target (no compile units at all) has the identical
problem for a different reason: it trivially "finds nothing" without having
looked at anything. Either gap meant a failed/empty baseline extraction could
still stamp `extractor_passes[...] = True`, so a later *successful* run's
first real call/type edge would misread as newly-introduced instead of
"the baseline never actually got to observe this." Fixed with
`call_graph.extractor_pass_fully_covered(target, extractor, narrowed)` — a
single shared predicate (not narrowed, at least one compile unit with a
source, and no diagnostics recorded on `extractor`) now gates every
`extractor_passes[...]` stamp across all three call sites:
`inline._fold_call_graph`/`_fold_type_graph` (which already had `narrowed`
computed) and `cli_buildsource_helpers._collect_call_graph` (which always
passes `narrowed=False`, since that out-of-band path never scopes). Any one
failing TU disqualifies the *whole* pass from claiming confirmed coverage,
even if most TUs succeeded — consistent with this ADR's running theme:
under-call (fall back to the pre-existing edge-presence inference) rather
than risk a false positive on an evidence-poor side.

An eighth Codex review found a distinct false-positive path through the same
finding, this time from *node*-level (not edge-level) evidence improving
between two versions. `_internal_dependency_findings` computed
`_public_entry_internal_reach(new, …) - _public_entry_internal_reach(old, …)`
— a set difference over pairs that are *both* reachable *and* classified
internal. But a target with no classifying provenance at all (e.g. a
Kythe-ingested or older-pack callee with no `SOURCE_DECLARES`/
`defined_in_project` marker) is unclassifiable, so
`_public_entry_internal_reach` silently drops it from the *old* side's set
even though the dependency **edge** already existed and was reachable there.
If the *new* side later gains real provenance for that same, unchanged
target (a `SOURCE_DECLARES` edge marking it `private_header`, say), the pair
reappears in `new`'s internal-reach set but was never in `old`'s — a "newly
internal" delta driven entirely by improved classification metadata, not by
any actual new edge. Fixed by checking raw reachability, not classification,
against the old side: `_internal_dependency_findings` now also computes
`_dependency_reachability(old, common_kinds)` (ignoring internal
classification entirely) and excludes any pair from `new`'s internal-reach
set whose target was already reachable from that entry in `old` — reachable-
but-unclassified in the old graph is still "the edge already existed," so it
must not count as newly added regardless of what classification evidence
either side happens to carry.

A ninth Codex review found two more instances of the same "confirmed evidence
is more precise than mere presence" principle, one on each side of the
extraction pipeline:

- **A clang error exit didn't disqualify pass coverage.**
  `ClangCallGraphExtractor`/`ClangTypeGraphExtractor`'s `_extract_from_safe_args`
  invokes clang with `check=False` and proceeds to parse stdout whenever it is
  non-empty — but `-fsyntax-only -Xclang -ast-dump=json` can exit non-zero on
  real compile errors in the necessarily-approximate replayed flag subset
  while still printing a partial, error-recovered AST (clang's `-ast-dump`
  walks whatever it managed to build). That left `extractor.diagnostics`
  empty, so `extractor_pass_fully_covered` (seventh review) would still mark
  the pass fully covered even though one or more TUs genuinely failed. Fixed
  by recording a diagnostic whenever `proc.returncode != 0`, in both
  extractors — edges are still salvaged from the best-effort AST (unchanged
  best-effort philosophy), but the diagnostic now correctly disqualifies
  confirmed pass coverage for that TU.
- **A one-sided confirmed pass couldn't cover a mixed-format comparison.**
  `_common_dependency_edge_kinds`'s per-kind fallback only counted a kind as
  common when both sides had a concrete *edge* of it — so an old pack that
  ran the type-graph pass and confirmed zero type edges, compared against a
  pre-slice-2 (or Kythe-only) new pack with **no** pass marker at all but a
  first `TYPE_HAS_FIELD_TYPE` edge, yielded an empty intersection for that
  kind and the dependency was skipped — even though the old side's confirmed
  pass already proved its own zero was real. Fixed by evaluating each exact
  kind as "present as an edge, **or** that side's family pass is confirmed" —
  a confirmed pass on *either* side is enough to make its own absence-or-
  presence of that kind trustworthy, without widening to sibling kinds
  neither side has an edge of (that full-family widening still requires
  *both* sides confirmed, per the third/fifth reviews).

A tenth Codex review found the closure's *entry* seeding was itself too
narrow, echoing the sixth review's type-entry fix but for decls this time.
`_dependency_reachability`'s entries were `SOURCE_DECL_MAPS_TO_SYMBOL`-backed
decls union public types — but a public inline/template/constexpr function or
a public variable declared in a public header commonly has **no** exported
binary symbol of its own (it's inlined at every call site, or never emitted
standalone), so it was never a valid entry — missing exactly the ADR's own
headline example, `inline int f() { return detail::SECRET; }`, whenever `f`
isn't separately exported. `crosscheck.py`'s intra-version check already
treats a `visibility="public_header"` decl as public via
`is_public_dependency_node` (shared since the fourth review); the version
diff's closure had its own narrower, inconsistent notion of "entry." Fixed by
seeding entries from `is_public_dependency_node` over every graph node
directly — any exported-symbol-backed decl *or* any decl/type with
public-header visibility — which subsumes the old `SOURCE_DECL_MAPS_TO_SYMBOL`
∪ `_public_types()` union and, as a side effect, makes a public type no
longer a special case in this function (public-header visibility already
covered it uniformly).

An eleventh Codex review found a different scope-comparability gap: a
narrowed (PR/`--since`-scoped) inline run never sets `extractor_passes` for
the family it narrowed — `_fold_call_graph`/`_fold_type_graph`'s local
`narrowed` flag correctly withholds the "confirmed full pass" stamp — but it
still serializes whatever edges it happened to collect from the subset of
compile units it actually walked. `_common_dependency_edge_kinds`'s per-kind
fallback treated any such edge as ordinary evidence of that exact kind's
coverage, with no way to tell "this side's family pass ran over the whole
project and found nothing else" from "this side only ever looked at a few
TUs and this is the one dependency edge it happened to see there." Comparing
a narrowly-scoped baseline against a candidate that ran a confirmed *full*
pass let dependencies in TUs the baseline never inspected — because the
narrowed baseline had *some* unrelated edge of the same kind, from the
subset it did see — pass the coverage gate and be reported as
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`, even though nothing about that
specific TU changed; the baseline simply never had the evidence to know
either way. Fixed by adding a `SourceGraphSummary.narrowed_passes: dict[str,
bool]` field (additive, same round-trip pattern as `extractor_passes`),
stamped by `_fold_call_graph`/`_fold_type_graph` whenever their local
`narrowed` flag is `True`. `_common_dependency_edge_kinds`'s per-kind
fallback now discounts a narrowed side's edge of a given kind specifically
when the *other* side has a confirmed full pass for that family — the
narrowed side's partial view cannot vouch for territory only the full pass
has actually walked. This exclusion is one-directional and scoped tightly:
the common, intended PR-diff workflow of comparing two runs narrowed
identically to the same changed TUs is unaffected, since in that case
neither side has a confirmed full pass to disqualify the other's edges, so
the pre-existing per-kind comparison behavior is preserved exactly.

A twelfth Codex review found the eleventh-round fix itself too narrow: it
only excluded a narrowed side's edge from vouching for a kind when the
*other* side confirmed a full pass — but a side with no pass marker at all
(no `extractor_passes`, no `narrowed_passes` — a pre-slice-2 pack, or one
built from an externally-ingested backend like `graph_backends.py`'s Kythe/
CodeQL ingestion) is not evidence it was equally narrow either; its true
scope relative to the narrowed side is simply unknown. The eleventh-round
condition (`old_narrowed and new_pass`) only fires when the other side
*positively* proves comprehensive coverage, so an unmarked-vs-narrowed
comparison — arguably the more common shape for an old/legacy pack, which
would rarely carry either marker — fell straight through to the pre-existing
per-kind fallback and could still credit a narrowed baseline's unrelated edge
of a kind as coverage for a completely different, never-examined region a
wider (but unmarked) candidate happens to also have an edge of that kind in.
Fixed by generalizing the exclusion from "narrowed vs. confirmed full pass"
to "narrowed vs. anything not narrowed the same way": `old_present`/
`new_present` now read `(kind in old_kinds) and not (old_narrowed and not
new_narrowed)` (and the symmetric form for `new_present`) — a side's edge
counts as coverage for a kind only when the other side is narrowed
identically, or itself has no narrowing at all to be asymmetric against.
Symmetric cases (both narrowed, or neither) are bit-for-bit unaffected;
only genuine narrowed/not-narrowed asymmetry — confirmed full pass,
unmarked pack, or the reverse narrowing — now excludes the kind, regardless
of *why* the other side isn't narrowed the same way.

A thirteenth Codex review found the twelfth round's symmetric generalization
itself over-corrected: `_internal_dependency_findings` only ever computes an
*additions* closure (`new`'s reach minus `old`'s), so the false-positive risk
is one-directional — it lives entirely in whether `old`'s absence of a kind is
trustworthy evidence the dependency truly did not exist before, never in
`new`'s own scope. Gating `new_present` on `new`'s own narrowing (symmetric
with `old_present`) meant a confirmed-full-pass baseline that genuinely found
zero edges of a kind anywhere — an authoritative, verified negative — still
had a narrowed candidate's real, newly-observed edge of that same kind
excluded from `common_kinds`, dropping a genuine `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`
finding with no offsetting false-positive protection: `new` being narrower
than `old` can only ever cause a *missed* addition outside the TUs it
examined (an accepted false negative), never manufacture a false positive,
regardless of how comprehensive or narrow `old`'s own coverage is. Fixed by
dropping the narrowing guard from `new_present` entirely (`new_present = kind
in new_kinds`, unconditional) while leaving `old_present`'s eleventh/twelfth-
round guard exactly as before — the asymmetry-detection logic now applies
only to the side whose *absence* the closure actually leans on.

A fourteenth Codex review found that `old_present`'s guard itself trusted
"both narrowed" too readily: `narrowed_passes` is only a boolean, so two
narrowed sides being "both narrowed" does not mean narrowed to the *same*
compile units — an old run scoped to `changed_paths=("src/a.cpp",)` and a new
run scoped to `changed_paths=("src/b.cpp",)` are each individually narrow but
examine disjoint code, yet the eleventh/twelfth/thirteenth-round formula
(`not (old_narrowed and not new_narrowed)`) treated any "both narrowed" pair
as safely comparable, letting `old`'s edge in the region it examined be
credited as coverage for a kind `new`'s edge (from a wholly different region)
also happens to have. Fixed by tracking the actual scope, not just the
boolean: new `SourceGraphSummary.narrowed_scope: dict[str, frozenset[str]]`
(additive, same round-trip pattern as `narrowed_passes`) records the
`changed_paths` themselves, or the examined `scoped_units`' source paths for
an unseeded run — the concrete scope identifier a narrowed pass was
restricted to. `_common_dependency_edge_kinds` now computes `scope_matches =
bool(old_scope) and old_scope == new_scope` and only trusts `old`'s narrowed
edge when `new_narrowed and scope_matches` — an *identical*, non-empty scope
on both sides, not merely "both happen to be narrowed." The shared scoping
decision in `_fold_call_graph`/`_fold_type_graph` (previously duplicated
between the two, now factored into one `_scope_narrowed_target()` helper to
keep `inline.py` under its line-count cap while adding this field) stamps
`narrowed_scope` alongside `narrowed_passes` whenever `narrowed` is `True`.

A fifteenth Codex review pointed out the fourteenth-round fix was one-sided:
it only used a matched `narrowed_scope` to *exclude* a mismatched comparison,
never to *credit* a matched one. Two sides narrowed to the identical scope ran
the exact same single AST walk, just restricted to that shared region — the
same rationale the confirmed-full-pass family-widening branch already uses,
just scoped smaller. Without crediting this, a same-scope PR scan whose
narrowed baseline genuinely found zero edges of a family (a real, verified
zero *within that shared scope*) couldn't have that zero trusted as coverage,
so a first-ever edge the candidate found in that exact shared TU was silently
dropped instead of reported as `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`. Fixed
in three parts:

- `_common_dependency_edge_kinds` computes `narrowed_confirmed = old_narrowed
  and new_narrowed and scope_matches` and widens to the whole family
  (`common |= family`) exactly like the `old_pass and new_pass` branch already
  does, when either condition holds.
- `_dependency_kinds_covered` (the coarse, single-graph "is there *any* reason
  to trust this graph enough to attempt a closure" gate feeding
  `_has_internal_reach_coverage`) now also accepts `narrowed_passes` as
  evidence "a pass ran," not only `extractor_passes` — a narrowed pass is
  unambiguously not "no semantic pass at all." This is safe on its own: the
  fine-grained per-kind trust decision still lives entirely in
  `_common_dependency_edge_kinds`, so relaxing this coarse gate cannot by
  itself let an untrustworthy kind through — a kind `common_kinds` excludes
  still restricts the closure to zero edges of that kind regardless of
  whether this gate passed.
- Trusting a narrowed pass's *zero-edge* family as real evidence raises the
  stakes on the run having succeeded cleanly, so `call_graph.py` gained
  `narrowed_pass_confirmed()` (sharing its "at least one TU, no diagnostics"
  check with `extractor_pass_fully_covered` via new `_pass_ran_cleanly()`) —
  `narrowed_passes` is now stamped only when the narrowed run itself hit no
  per-TU diagnostics, mirroring the seventh review's rationale for the
  full-pass case: a silently-degraded TU inside the narrow scope must not
  read as "the scope was cleanly examined, zero found."

A sixteenth Codex review found a parallel gap for the *unnarrowed* case: a full
pass that hit per-TU diagnostics correctly never sets `extractor_passes` (the
seventh review's rule), but it still folds edges from the TUs that *did*
parse. Nothing recorded that this happened, so those surviving edges fell
straight into the per-kind fallback — which, per the original (fifth/ninth
review) design, trusts bare edge presence as weak "this kind is comparable"
evidence. A degraded baseline's edge of a kind could therefore be compared
against a clean candidate's edge of the same kind in a wholly different,
never-successfully-parsed TU, reporting a spurious
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`. Fixed with a third coverage-honesty
field, `SourceGraphSummary.degraded_passes: dict[str, bool]` (additive, same
round-trip pattern as the other two) — set whenever a pass examined units but
`extractor.diagnostics` was non-empty (a narrowed run with diagnostics lands
here too, on top of never confirming `narrowed_passes`, since it is even less
trustworthy than either alone). `_common_dependency_edge_kinds`'s `old_present`
guard now also requires `not old_degraded`, extending exactly the same
exclusion logic the narrowed case already uses to this third source of
untrustworthy "coverage." Stamped by `inline._fold_call_graph`/
`_fold_type_graph` and `cli_buildsource_helpers._collect_call_graph` — the
three producers that fold real Clang extraction (`graph_backends.py`'s Kythe/
CodeQL ingestion never runs a pass with diagnostics to report, so it is
unaffected).

This slice also split `_scope_narrowed_target`/`_fold_call_graph`/
`_fold_type_graph` out of `inline.py` into a new sibling module,
`inline_graph_fold.py` (`fold_call_graph`/`fold_type_graph`) — `inline.py` was
sitting at its 2000-line hard cap and every one of the last several rounds'
fixes needed a few more lines there; per the root `CLAUDE.md`'s guidance to
extend a split-out module rather than keep growing the parent toward the cap,
this creates headroom for future rounds instead of re-litigating the same
line-shaving exercise each time.

## Decision — P0 slice 4 (this change)

Roadmap item 2's remaining half, "semantic graph diff — same public decl,
different `body_hash`/`type_hash` combined with a new/changed graph edge, so
a report can say 'X now reaches internal Y, defined in changed file Z'
instead of two disjoint findings." Before this slice, a public entry whose
own implementation changed this version (`source_diff.diff_source_abi`'s
`INLINE_BODY_CHANGED`/`TEMPLATE_BODY_CHANGED`/`PUBLIC_TYPEDEF_TARGET_CHANGED`
— the three of the nine L4 source-replay findings literally keyed on a
`body_hash`/`type_hash` delta) and that *also* gained a new internal
dependency this version (`source_graph`'s `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`)
produced two entirely disjoint `Change` objects with nothing connecting
them — a reader had to notice both findings named the same symbol and infer
the likely causal link themselves.

Fixed by threading the L4 surface diff into the L5 graph diff:
`diff_source_graph_findings(old, new, source_diff_changes=...)` takes an
optional `list[Change]` (the caller's already-computed
`source_diff.diff_source_abi()` output for the same version pair) and passes
it to `_internal_dependency_findings`. New `_public_decl_source_changes()`
maps each public entry's `symbol` (qualified name — the same string
`source_graph`'s decl/type node `label` uses) to its own body/type-hash
`Change`, when one of the three kinds above fired for it. When a newly-
internal-dependency entry has an own-change in that map,
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`'s description gains a sentence naming
it (`"This entry's own implementation also changed this version (<kind>:
<old> → <new>) — likely the source of the new dependency."`) instead of
leaving the correlation implicit. `cli_buildsource_helpers.diff_embedded_build_source`
(the only production caller with both an L4 and L5 diff available) now
passes its `_src` (the L4 findings list) through; `graph compare`
(`cli_graph.py`), which only ever loads bare `SourceGraphSummary` files with
no build-source facts, keeps the default `None` and gets the exact
uncorrelated description as before — this is additive, no existing caller's
behavior changes without also supplying the new argument.

No new `ChangeKind` — same convention as slice 3's proof paths: the
correlation rides in `Change.description`, keeping this additive and
low-risk to the wider reporting pipeline (JSON/SARIF/JUnit all carry
`description` verbatim already).

## Decision — header-only-graph addendum (this change)

Every P0 slice above is an **L4/L5** feature: it needs a real build (a
`compile_commands.json`, per-TU `clang -ast-dump=json` replay of full bodies)
via `inline.collect_inline_pack`. That requirement is not fundamental to the
"not a call at all" risk this ADR opens with — a public struct with a private
field type, or a public class inheriting an internal base, is visible in the
**declarations alone**, with no build and no function body needed. A project
with no `compile_commands.json` at all — the common case for a quick `abicheck
dump libfoo.so -H api.h --public-header api.h` — got none of this ADR's
recall, even for the exact "no call at all" case it was written to close.

Added `abicheck/buildsource/header_graph.py`:

- `build_header_only_graph(snapshot, ast_root, *, public_header_paths,
  public_dir_paths)` — seeds `source_decl` nodes for every function/variable in
  the already-parsed `AbiSnapshot` (visibility straight from
  `Function.origin`/`Variable.origin`, the `ScopeOrigin` classification
  `provenance.apply_provenance` already computes whenever `--public-header`/
  `--public-header-dir` is given — no new classification logic), then folds
  `type_graph.parse_clang_ast_types()`/`call_graph.parse_clang_ast_calls()`
  over the *same* header-aggregate `clang -ast-dump=json` tree the L2 clang
  frontend (`dumper_clang.py`) already produces when `--ast-frontend clang` is
  selected. Both parsers are pure functions over a bare AST dict (P0 slice 1's
  own docstring: "unit-tested without a compiler") — nothing about them
  assumes a real, build-integrated translation unit, so reusing them here
  needed zero changes to either.
- Type-node visibility (public struct vs. private field type) is **not**
  derived by matching `AbiSnapshot.types`/`.enums` against the type graph's
  `type://` node ids: the flat snapshot model records a bare, unqualified type
  name (`dumper_clang._ClangAstParser._build_record` never threads the
  namespace scope into `RecordType.name`), while the type graph's node ids are
  the AST's *resolved qualified* name (`ns::Widget`) — two representations
  that would silently fail to join for any namespaced type. `type_graph.py`
  gained a small additive public wrapper, `index_declared_type_files(ast)`
  (qualified name → declaring file), factored out of the same first-indexing
  pass `parse_clang_ast_types` already runs — rather than thread a new output
  parameter through the hardened, many-times-reviewed
  `_index_declared_entities`/`_walk_types` pair, this duplicates that one AST
  walk, an acceptable cost for a header-only pass. `build_header_only_graph`
  classifies each declaring file via `provenance.classify_origin` (the same
  primitive `apply_provenance` uses) and sets `visibility` directly on the
  type node — covering the ADR's own headline case, since a public struct
  rarely has its own exported binary symbol and needs its `visibility` set
  directly to act as a valid graph "entry" (`is_public_dependency_node`).

**What is structurally available vs. not, from headers alone:**

- `TYPE_INHERITS` / `TYPE_HAS_FIELD_TYPE` / `DECL_HAS_TYPE` / `SOURCE_DECLARES`
  — fully available; a base class, a field type, and a parameter/return type
  are declaration-level facts. This is also exactly the ADR's own motivating
  example.
- `DECL_CALLS_DECL` / `DECL_REFERENCES_DECL` — only for declarations whose
  *body* is actually written in a header (inline/template/constexpr
  functions). An ordinary out-of-line function has a prototype but no body in
  a header, so it contributes no call/reference edges here — a real, honestly
  bounded subset of the L4/L5 graph's recall, not a false claim of parity.
- Anything from the *build*-level schema (`target`/`compile_unit`/
  `build_option` nodes, `TARGET_HAS_SOURCE`, …) — not available at all; there
  is no `BuildEvidence` in a header-only world, so this module never calls
  `build_source_graph`.

**Coverage honesty (ADR-031 D9).** Every node/edge this module creates carries
`provenance="header_ast_l2"`, and the graph's `extractor_passes` use this
module's own pass names, `HEADER_CALL_GRAPH_PASS`/`HEADER_TYPE_GRAPH_PASS`
(`"header_call_graph"`/`"header_type_graph"`), distinct from
`inline_graph_fold`'s build-integrated `"call_graph"`/`"type_graph"` — a
header-only pass is never mistaken for a full L4/L5 build-integrated one.
`SourceGraphSummary.finalize()`'s `type_edges`/`call_edges` coverage flags
recognize both the build-integrated and header-only pass names (an `or` over
both), so the human-readable coverage report is honest either way.
`source_graph_findings._common_dependency_edge_kinds`/`_dependency_kinds_covered`
(the version-diff family-widening logic) also honor the header-only pass names
— but **not** by adding `header_call_graph`/`header_type_graph` as separate
entries in `_DEPENDENCY_EDGE_FAMILIES`: that table's per-kind fallback loop
unions "common" credit across every entry, which is only sound when each entry
owns a disjoint edge-kind set (`call_graph` → `DECL_CALLS_DECL`, `type_graph` →
the other four); a `header_type_graph` entry sharing `type_graph`'s exact kinds
would let a kind correctly excluded under a narrowed/degraded `type_graph` pass
leak back in as "common" under the second, unmarked entry for the same kind
(caught by the existing test suite, then reverted, on the first attempt — a
Codex review on the shipped PR caught the resulting gap: an unfixed version
would silently drop a real `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` finding
whenever a header-only baseline's verified-zero family gained its first edge).
Fixed with `_HEADER_PASS_ALIAS` (`{"call_graph": "header_call_graph",
"type_graph": "header_type_graph"}`) plus small helpers
(`_pass_narrowed`/`_pass_degraded`/`_pass_scope`) that each check *both* names
within the *same*, single loop iteration per family — so a header-only
graph's own narrowed/degraded markers are honored without ever double-counting
a kind across two independent iterations, the exact failure mode the first
attempt hit.

A second Codex review then caught a subtler asymmetry in that same fix's
initial shape: it let a header-only pass's confirmation grant the *same*
full-family widening credit a build-integrated pass's confirmation grants.
That is unsound specifically for `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL` — a
header-only pass structurally cannot see a call/reference inside an
out-of-line function body, so its "zero" for either kind is not evidence of a
project-wide zero, only of "headers can't show this." Comparing a header-only
baseline against a build-integrated candidate could then report
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` for a dependency that already existed
via a call the baseline structurally could never have observed — a real false
positive the moment collection "improves" from header-only to
build-integrated. Fixed with `_pass_trusted_kinds`/
`_HEADER_FULL_VISIBILITY_KINDS`: a header-only confirmation now only vouches
for the three structural kinds (`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/
`TYPE_INHERITS`) it has true project-wide visibility of — never the whole
family, and never the two body-dependent kinds, regardless of the *other*
side's shape (even header-vs-header, which is arguably safe but adds a second
axis of bookkeeping for one kind's marginal recall — not worth it against the
simpler, strictly-safe rule).

**Consumer:** `crosscheck.py`'s `public_to_internal_dependency` and
`source_graph_findings.diff_source_graph_findings`'s
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` both already read
`snapshot.build_source.source_graph` generically — a header-only graph is
just a different (cheaper, always-available) producer of the same edge
vocabulary, so no detector-side change was needed, mirroring exactly how P0
slice 1 wired `type_graph.py` into `crosscheck.py`.

**Wiring:** `service.run_dump(..., header_graph=True)` builds and embeds the
graph uniformly across all three binary formats (ELF/PE/Mach-O) — a
`BuildSourcePack` with only `source_graph` set (`build_evidence`/`source_abi`
stay `None`, since there is no L3/L4 payload in a header-only world). It runs
a second, independent `clang -ast-dump=json` pass over the same header
aggregate `dumper._clang_header_dump` already knows how to build (reused
directly — private only by convention; `dumper.py` sits at its 2000-line hard
cap, so a public wrapper was not added there), and degrades to a graph with
declaration-visibility nodes only (no type/call edges) when clang is
unavailable or the header parse fails — never aborts the dump (ADR-028 D3).
`service.run_dump` is reachable from `compare`'s implicit dump-from-binary
resolution and the buildsource merge/collect paths (`cli_resolve.py`,
`cli_buildsource_helpers.py`). **Update:** the standalone `abicheck dump`
command now also exposes `--header-graph`/`--header-graph-includes` (the
shared `header_graph_options` decorator in `cli_options.py`, applied to both
`compare` and `dump` so the two flags can't drift). `dump` still calls
`dumper.dump()` via its own legacy `cli_dump_helpers.py` path, not
`service.run_dump` — the resolution avoids `dumper.py`'s line-count cap
entirely: `perform_elf_dump` calls `service._attach_header_graph` directly as
a post-processing step on the snapshot `dumper.dump()` already returned, the
exact same wrapper `service.run_dump` itself uses, so `dumper.py` needed no
change at all. Not yet on `scan`, and the ELF path only — `handle_non_elf_dump`
(PE/Mach-O) does not thread the flag through yet.

No new `ChangeKind` — same convention as every other graph slice in this ADR:
this reuses `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` and the intra-version
`public_to_internal_dependency` check, broadening *when they have evidence to
run at all* (no build needed), not what they detect.

**Follow-up: header include graph.** `header_graph.ClangHeaderIncludeExtractor`
adds an optional, separately-opted-in (`service.run_dump(...,
header_graph_includes=True)`) per-header `clang -M` pass —
`COMPILE_UNIT_INCLUDES_FILE` edges from each top-level header to everything it
transitively includes. Reuses `include_graph.ClangIncludeExtractor`'s vetted
depfile-replay logic (argv sanitization, timeouts, diagnostics) through a
throwaway per-header `BuildEvidence`/`CompileUnit` — the header-only world has
no real compile units, so each header's own graph-node id doubles as its
synthetic compile-unit id, letting `include_graph.augment_graph_with_includes`
fold the result with zero id translation. This is advisory structure only
(`graph explain`/future triage material), not a classification override: a
"private" header transitively reached from a public entry is still labelled
by its own declaring-file origin. `build_header_only_graph` also gained a
`header_paths` parameter that pre-seeds a `header` node (with classified
visibility) for every top-level header even when it declares nothing itself
(a pure `#include`-only umbrella header) — both because that's a real,
independently-worth-fixing gap (such a header is still a genuine public entry
point) and because the include-graph edges need a valid source node to attach
to. A Codex review on the shipped commit caught two more issues in the same
slice, both fixed: (1) `_attach_header_graph`'s deferred `-isystem` roots
(from `resolve_inferred_header_roots`) rode only in `gcc_option_tokens`,
which `_clang_header_dump`'s disk cache never hashes — fixed by threading
`extra_hash_dirs=deferred_token_dirs(deferred)` through, mirroring
`_dump_elf`'s own handling; (2) `type_graph.index_declared_type_files`
returned `_index_declared_entities`'s shared `decl_file` dict unfiltered,
which also carries var/enum-constant identities (used only for
`DECL_REFERENCES_DECL` resolution) alongside real type declarations — a
public constant could therefore get mistaken for a record/enum/typedef and
seeded as a bogus `record_type` node; fixed by filtering to exactly the
qualified names `name_index` (the type-only index) actually collected.

**Follow-up: flat-model structural edges (castxml-generalized, no AST at
all).** Every edge above still needed a *second*, independent `clang
-ast-dump=json` invocation over the header aggregate — a real cost when the
main dump used castxml (the default L2 backend), and a project whose headers
don't parse cleanly under a bare `clang++` invocation (GCC-specific
attributes, an MSVC-only codebase, etc.) got no structural edges at all, only
declaration-visibility nodes. But `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/
`DECL_HAS_TYPE` don't actually need an AST walk: every L2 backend (castxml or
clang) already populates `RecordType.bases`/`.fields`, `Function.
return_type`/`.params`, and `Variable.type` on the flat `AbiSnapshot` itself.
`build_header_only_graph` now derives these three structural edge kinds
directly from the snapshot when no `ast_root` is supplied
(`_flat_structural_type_edges`), reusing `augment_graph_with_types` unchanged
— no second compiler invocation, no clang dependency, works identically for
any backend. The tradeoff is resolution confidence: the flat model records
only a bare, unqualified type name (no enclosing-namespace/scope information
survives dumping), so a bare-name lookup against the snapshot's own
declared-type set can only ever reach `RESOLUTION_UNIQUE_CANDIDATE` (the name
is unique across the whole snapshot) or `RESOLUTION_UNRESOLVED` (zero or more
than one same-named declaration) — never the AST path's `RESOLUTION_SCOPE`
tier, and always `CONF_REDUCED`. `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`
remain clang-AST-only in every case: the flat model never records a function
body, so there is nothing to derive them from regardless of backend; the flat
path therefore only ever stamps `HEADER_TYPE_GRAPH_PASS`, never
`HEADER_CALL_GRAPH_PASS`. The AST path (when `ast_root` is available) is
unchanged and still takes priority — the two are mutually exclusive per
`build_header_only_graph` call, not layered, since the AST path's qualified
node ids and the flat path's bare-name node ids are different id spellings
for the same real-world type and would otherwise create parallel,
non-deduplicated node sets for one build.

Two further fixes landed alongside this addendum, both from post-merge Codex
review: (1) `source_graph_findings._common_dependency_edge_kinds`'s per-kind
fallback trusted a header-only side's raw edge *presence* for a
body-dependent kind even after `_pass_trusted_kinds` correctly capped
*confirmation*-based trust — a header-only baseline that happened to fold one
real in-header call edge (an inline function calling another inline function,
both visible in headers) made `DECL_CALLS_DECL` "common" against a
build-integrated candidate, so a pre-existing out-of-line call the baseline
structurally could never have seen surfaced as a false
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` the moment collection improved from
header-only to build-integrated; fixed by gating `old_present` on whether the
graph's *only* confirmation for that pass is the header-only alias, for
every kind outside `_HEADER_FULL_VISIBILITY_KINDS`. (2) The include-graph pass
(`ClangHeaderIncludeExtractor`) always constructed itself with the default
`clang_bin="clang++"`, ignoring `CompileContext.gcc_path`/`gcc_prefix` even
though the AST pass just above honors them — a hermetic/cross toolchain
selected via those flags could silently lose every
`COMPILE_UNIT_INCLUDES_FILE` edge (or resolve them against the host's clang
instead); fixed by resolving the same clang driver (`dumper._resolve_clang_bin`)
for both passes.

**Follow-up: `--call-graph`/`--include-graph` removed from `collect` —
call/type/include graph now fold automatically everywhere.** These two
`collect`-only flags had drifted into a genuine asymmetry with the inline
`dump --sources` path: `--call-graph` was redundant there (the inline path has
folded call/type-graph edges automatically whenever `--source-abi`/`--source-graph`
level L4+L5 are both active since P0 slice 1, no flag at all), while
`--include-graph` was the *opposite* problem — the only way to get
`COMPILE_UNIT_INCLUDES_FILE` edges into the graph at all, entirely absent from
the recommended `dump --sources` path. A user following the documented
"recommended" workflow (`docs/concepts/build-source-data.md`) had no way to
request include edges, while a `collect` user had to remember an
easy-to-miss flag for behavior the other path gave for free.

Fixed by making all three automatic, everywhere, on one shared code path:
`buildsource/inline_graph_fold.py` gained `fold_include_graph()` (mirroring
`fold_call_graph`/`fold_type_graph`'s scoping precedence and graceful clang-
absent degradation, preferring already-recorded build-tool inputs over a
fresh `clang -M` invocation), and `inline._build_inline_graph` now calls it
alongside the other two under the same existing `with_call_graph` gate — so
`dump --sources` gains include-graph edges with no new flag. `collect`'s
`_collect_source_graph` (`cli_buildsource_helpers.py`) was rewritten to call
the *same* `inline_graph_fold` functions directly (deleting its own two
near-duplicate `_collect_call_graph`/`_collect_include_graph` implementations
entirely) whenever `--source-abi` and `--source-graph summary` are both
given — matching the inline path's gate exactly, rather than requiring
either flag. `--call-graph`/`--include-graph` are removed outright (not
deprecated): both were recent, narrow-audience flags with no evidence of
external dependents, and keeping a compatibility shim for a flag whose whole
point was "this behavior should not need a flag" would be self-defeating.
`--kythe-entries`/`--codeql-results` are unaffected — pre-captured,
non-executing external ingestion always needs an explicit file path, so
there is no equivalent "should this be automatic" question for them.

## Roadmap (not committed — scope/sequence per the usual planning process)

### P0 — remaining high-value, low-risk work

1. ~~Populate `DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/
   `TYPE_INHERITS`~~ — **done, ADR-041 P0 slice 1.**
2. ~~**Semantic graph diff.** Same public decl/type, new internal-dependency
   edge over the full dependency-edge family~~ — **done, ADR-041 P0 slice 2**
   (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`, generalized beyond
   `DECL_CALLS_DECL`). ~~Same public decl, different `body_hash`/`type_hash`
   (already on `SourceEntity`, cf. `source_diff.py`'s nine findings) combined
   with a new/changed graph edge~~ — **done, ADR-041 P0 slice 4**
   (`diff_source_graph_findings(..., source_diff_changes=...)` correlates a
   public entry's own body/type-hash change with it newly reaching an
   internal dependency, in one finding's description instead of two disjoint
   ones).
3. ~~`graph explain` proof path per finding~~ — **done, ADR-041 P0 slice 3**
   (`_dependency_path`/`_format_dependency_path`, threaded into
   `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` / `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED`
   / `PUBLIC_TO_INTERNAL_DEPENDENCY`). `localize_symbol`'s own symbol → target →
   decl → header/build-option/callee walk (ADR-031 D7) is unchanged — this slice
   only threaded a path into the two dependency-reachability findings the
   roadmap named, not into `localize_symbol` itself.
4. ~~Coverage counters per edge family~~ — **done, this ADR** (`type_edges`/
   `reference_edges`); extend further per P1 item 4 below when object/link
   provenance lands.

### P1 — stronger ABI/API intelligence

1. ~~**Move type/reference extraction into Plugin injection (the ADR-038
   plugin).**~~ — **done, ADR-038 C.9/C.10.** The plugin emits
   `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE`/
   `DECL_REFERENCES_DECL`/`DECL_CALLS_DECL` into `source_edges` during the
   real product compile (a `CallRefVisitor` sub-walk per function body, no
   second frontend pass), and the reference `clang.py` extractor does the
   same by reusing `call_graph.py`'s/`type_graph.py`'s pure AST parsers on
   the JSON AST it already parsed (ADR-038 C.8). What ADR-038 C.10 closes on
   top of that collection: `source_graph.fold_source_edges()` now actually
   folds these into the L5 graph (previously serialized-but-unused —
   `SourceAbiSurface` had no edge field at all). It does **not** (yet) let
   `inline._build_inline_graph()` skip the separate `call_graph`/
   `type_graph` replay passes: a first attempt at that optimization was
   reverted after a review found the raw `source_edges` wire format carries
   no `dst_file`/project-file provenance, which `crosscheck.
   public_to_internal_dependency` needs to classify an unannotated node as
   internal — see ADR-038 C.10's "still always run" note for the full
   reasoning and the follow-up this leaves open. ADR-038 C.10 did fix the
   callee-identity resolution bug the edges depend on regardless:
   `call_graph.py`'s JSON-AST replay used to resolve an overloaded callee's
   compact `referencedDecl` stub by bare name (real Clang never puts
   `mangledName` on that stub), collapsing overloads onto one endpoint; it
   now builds an id-index from the full declarations seen in the same walk,
   mirroring the fix `type_graph.py` already had. **ADR-038 C.11 closes half
   of the follow-up above** (Codex review): both `source_edges` producers now
   carry `dst_file` for `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL` — the C++
   plugin via a new `declFile()` helper (mirroring `classify()`'s own
   resolution), the Python inline extractor
   (`clang_source_edges.build_source_edges`) by simply forwarding
   `CallEdge.callee_file`/`TypeEdge.dst_file`, both of which already resolved
   it — and `fold_source_edges()` now marks a `dst_file`-matching node
   `defined_in_project`, mirroring `augment_graph_with_calls`/
   `augment_graph_with_types`'s identical marker. `call_graph.py`'s own
   `callee_file` resolution had a gap of its own (Codex review): it only
   recorded a callee's file from a *body*-bearing sibling `FunctionDecl`, so
   a helper only *declared* in this TU (a private header this TU includes,
   its body compiled in a separate TU never present in this AST — a common
   out-of-line-helper shape) left `callee_file` empty. Now records a
   declaration-only file as a fallback (a later body for the same identity
   still upgrades it — the definition is the more authoritative location).
   `link_source_abi()`'s cross-TU `source_edges` dedup had a matching gap
   (Codex review): it keeps only the first-seen `(edge, src, dst)` row across
   every TU, so if TU A's copy of the same logical edge lacked `dst_file`
   (that TU's AST couldn't resolve it) while TU B's copy — the same edge,
   from a TU whose AST does carry the declaration/definition — had it, the
   poorer first row silently won and the richer one was discarded. Now
   merges a missing `dst_file` into the surviving row from any later
   duplicate that has one (additive only — never clobbers an
   already-resolved `dst_file`). The inline extractor already carried
   `dst_file` for **all five** kinds (`type_graph.py`'s two-pass indexing
   resolves a type spelling to its declaring file regardless of edge kind).
   **ADR-038 C.13 closes the matching gap on the plugin side:** its three
   type-edge kinds (`DECL_HAS_TYPE`'s return/parameter roles,
   `TYPE_INHERITS`, `TYPE_HAS_FIELD_TYPE`) previously never attempted to
   resolve `dst` — a printed type spelling (`getAsString(PP)`) — back to a
   decl/file at all. A new `typeDeclFile(QualType)` helper (alongside
   `declFile(const Decl*)`) unwraps a pointer/reference/array down to its
   pointee/element, then resolves a `TypedefType` to the alias's *own*
   declaring file (checked before the pointer/reference unwrap, since
   `isPointerType()`/`isReferenceType()` desugar through a typedef to the
   type it names — stripping first would resolve `using Handle = Impl*`'s
   `dst_file` to wherever `Impl` is declared instead of where `Handle`
   itself is, diverging from what the printed spelling actually names) or a
   `TagDecl` (record/enum) otherwise; a dependent/template-parameter type or
   a builtin yields `""`, matching `declFile()`'s own silent-empty contract.
   Verified against a real Clang 18 build (`cmake`/`make` against
   `llvm-18-dev`/`libclang-18-dev`): compiled fixtures exercising a
   public struct's base class and field type declared in a private header
   (both resolve `dst_file` to that header) and a pointer/reference typedef
   alias declared in a *different* header than its underlying type (resolves
   to the alias's own file, confirming the desugar-order fix); the plugin's
   own C.6 differential-conformance, scan-flow, and public-roots-diagnostic
   test suites all still pass unmodified (this only adds `attrs`, never
   changes an edge's `(kind, src, dst)` identity or count). `inline.
   _build_inline_graph()` still always runs the separate replay passes
   regardless (that skip-optimization needs *every* kind covered with equal
   *breadth*, not just `dst_file` resolution, to be safe — C.12's
   `mark_source_edges_extractor_coverage()` producer gate is exactly this
   distinction: the plugin's `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE` now
   resolve `dst_file` correctly for every public record it walks, but its
   `DECL_HAS_TYPE` still never covers a variable's type or a typedef's own
   underlying type, so the *family* remains degraded even though two of its
   three kinds are now individually reliable — a finer per-kind trust split
   is a candidate follow-up, not attempted here) — this closes the
   `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`-visibility gap for
   Flow-2/plugin-only ingestion paths that never run a replay at all
   (`inputs_pack.ingest_inputs_pack`), not the replay-skip itself. Also
   fixed alongside: `mark_source_edges_extractor_coverage()` only trusts a
   `"complete"` coverage state when `surface.source_edges` is actually
   non-empty (a pre-C.11 `source_abi.json` can carry `"complete"` from
   `coverage["fact_family_states"]` — which predates the `source_edges`
   field itself — with no edges to back it, since the old serializer had
   nowhere to persist them); and `fold_source_edges()` now gates on
   `DEPENDENCY_EDGE_KINDS` rather than the broader `EDGE_KINDS`, so a
   forward-incompatible/malformed row can't silently fold as a decl/decl
   dependency edge. **ADR-038 C.12 closes a deeper coverage-honesty gap**
   (Codex review): `mark_source_edges_extractor_coverage()` used to alias
   *any* confirmed-complete `source_edges` rollup to full `call_graph`/
   `type_graph` trust — correct for the Python inline extractor (a genuine,
   unfiltered full-TU walk) but wrong for the clang plugin, whose
   `source_edges` only walks call/reference bodies for functions
   `classify()` accepts (public-header-declared; a private/internal helper
   defined purely in a `.cpp` is skipped entirely, its outgoing calls never
   captured) and never emits `DECL_HAS_TYPE` for a typedef's underlying type
   or a variable's type at all (only function return/parameter types).
   Aliasing the plugin's `source_edges` to full trust would hide a
   genuinely new dependency added inside a private helper's body, or a
   changed typedef/variable type, as a false negative — exactly the
   coverage-honesty failure mode this whole chain exists to prevent. The
   fix gates the alias on the rolled-up `fact_set["producer"]` being the
   Python inline extractor's id (`"abicheck-cc-clang-extractor"`); the
   plugin's own id (`"abicheck-clang-plugin"`), and a missing/disagreeing
   `fact_set` (pre-C.8 producer, mixed-producer pack), both fall back to no
   blanket trust — the same conservative default an unrecognized coverage
   state already gets. A follow-up Codex review caught that "no blanket
   trust" alone wasn't enough: leaving `call_graph`/`type_graph` entirely
   *unmarked* for a non-full-walk producer still let
   `source_graph_findings._common_dependency_edge_kinds`'s raw-edge-presence
   fallback apply — its `_pass_ran`/`_pass_trusted_kinds` checks only consult
   `extractor_passes`/`narrowed_passes`, never an *absence* of
   `degraded_passes`. A plugin baseline with even one public-surface call
   edge would then make `DECL_CALLS_DECL` look "common" against a
   full-replay candidate, surfacing a pre-existing private-helper dependency
   the plugin structurally could never have seen as a false
   `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` — the same one-directional risk
   the sixth/sixteenth Codex reviews already made `degraded_passes` guard
   against for a narrowed/crashed standalone replay. Fixed: a non-full-walk
   producer whose `source_edges` did fold real edges into the graph is now
   stamped `degraded_passes["call_graph"]`/`["type_graph"]` instead of left
   unmarked (a producer that folded *nothing* gets no stamp either — there is
   nothing to distrust). `degraded_passes` only ever restricts trust in a
   side's own *absence* of a kind, never the *other* side's presence, so
   this can only trade a missed addition for avoiding a false alarm — the
   same conservative bias the whole narrowed/degraded chain already
   commits to. A further review caught the same fallback gap one layer up:
   a third-party/hand-edited surface (or a schema older than ADR-038 C.8)
   can carry `source_edges` with no `fact_family_states` at all (missing or
   malformed) — the function used to `return` immediately in that case,
   before ever reaching the degraded-stamping check, leaving folded edges
   just as unmarked as the recognized-non-full-walk-producer case the fix
   above addressed. Now a missing/malformed `fact_family_states` is treated
   as unknown coverage (`state` stays `None`, so the full-walk-trust branch
   never fires) and falls through to the same degraded stamp instead of
   returning early.
2. ~~**Object/link provenance graph.**~~ — **done, this change.** New node
   kinds (`object_file`/`archive_member`/`static_library`/`linker_script`/
   `version_script`/`export_map`/`comdat_group`) and edges
   (`COMPILE_UNIT_EMITS_OBJECT`, `TARGET_HAS_LINK_UNIT`, `LINK_UNIT_HAS_INPUT`,
   `LINK_UNIT_USES_VERSION_SCRIPT`, `LINK_UNIT_EXPORTS_SYMBOL`) fold
   `BuildEvidence.compile_units`/`link_units` into the graph
   (`source_graph._fold_link_provenance`), so a symbol change can be
   attributed to "which object/link step" rather than only "which target."
   Every `compile_unit` with a known `output` gets an `object_file` node +
   `COMPILE_UNIT_EMITS_OBJECT` edge; every `LinkUnit` becomes a `link_unit`
   node (a kind `NODE_KINDS` reserved since ADR-031 D2 but never populated
   before this) linked to its owning `target` when known, with each input path
   classified by suffix into an `object_file` or `static_library` node
   (`CONF_REDUCED` — best-effort textual classification, no archive
   introspection) — an object a compile unit already emitted lands on the
   *same* node instead of a disconnected duplicate, so a change traced to one
   object correlates across both slices. `LINK_UNIT_EXPORTS_SYMBOL` is added
   once `_augment_with_source_abi` resolves which symbols the owning target
   actually exports. `archive_member`/`linker_script`/`export_map`/
   `comdat_group` and the `ARCHIVE_CONTAINS_OBJECT`/`OBJECT_DEFINES_SYMBOL`
   edges stay reserved (schema-only): true archive-member/per-object-symbol
   enumeration needs a real `ar`/`nm`-equivalent introspection extractor this
   increment does not add.
3. ~~**Public-entry impact closure.**~~ — **done, this change.**
   `poi.resolve_changed_paths_public_impact(changed_paths, graph)`
   is the reverse of `resolve_symbol_tus` (export delta → declaring TU): given
   a set of changed source paths, it resolves which declarations live in those
   files (via `decl_declaring_files` plus a `def_file`/`source_location`
   fallback) and returns every public entry that either declares directly in
   a changed file or reaches one through `_dependency_reachability`'s forward
   closure. Unit-tested (`tests/test_poi.py`), and now, like `resolve_symbol_tus`,
   wired into `scan_engine.py`'s replay-seed focusing
   (`_resolve_public_impact_tus`, mirroring `resolve_symbol_tus`'s own
   `SOURCE_DECLARES`-edge-or-`def_file`-attr fallback resolution) — the
   PR-scoped-deep-scan behavior this item originally described ("this PR
   touches `src/detail/cache.cpp`; only 3 public entries are reachable from
   it; replay only those") now actually happens: a `--changed-path`/`--since`
   seeded `scan` also replays any public entry whose own export/declaration
   is untouched but which transitively depends (a field/base/parameter type
   or inline body) on a changed file.
4. ~~**Explicit per-edge confidence/provenance model.**~~ — **done, this
   change.** `type_graph.py`'s `DECL_REFERENCES_DECL` edge (the one edge
   family whose resolution was a same-confidence guess regardless of how the
   reference was actually matched) now carries a dedicated
   `RESOLUTION_REF_EXACT`/`RESOLUTION_REF_UNIQUE_CANDIDATE`/
   `RESOLUTION_REF_UNRESOLVED` label — distinct from the pre-existing
   `RESOLUTION_SCOPE`/`RESOLUTION_UNIQUE_CANDIDATE`/`RESOLUTION_UNRESOLVED`
   vocabulary `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE` already
   used, since a `DeclRefExpr` resolves by a different mechanism (an id-index
   hit vs. a name-scope walk) — and `confidence` now downgrades from
   `CONF_HIGH` to `CONF_REDUCED` whenever the match isn't exact, instead of
   always emitting `CONF_HIGH`. The object/link provenance graph (P1 item 2
   above) closes the item fully: its edges emit `CONF_HIGH` (build-evidence
   direct) and its suffix-classified input nodes emit `CONF_REDUCED`
   (textual guess), so every edge family this ADR introduces now carries a
   confidence label reflecting how it was actually resolved.
5. ~~**Stable cross-clang-version identity.**~~ — **done, this change.**
   `SourceEntity.identity()`'s fallback chain is unchanged (still
   `mangled_name` → `qualified_name#signature_hash` → bare `qualified_name`,
   since folding USR into the identity string itself would change every
   caller that already keys on it across snapshot versions — too large a
   blast radius for this increment). Instead, `source_link.py`'s linker
   *detects* (never silently eliminates) the accepted collision risk: when
   two declarations route to the same `identity()` key but each carries a
   distinct clang-computed USR (`SourceEntity.names["usr"]`), that's proof
   the identity string collided two genuinely different entities, and the
   pair is recorded in a new `SourceAbiSurface.identity_collisions` list
   (`identity`/`qualified_name`/`usr_a`/`usr_b`) rather than silently merged.
   This change makes that detection *visible*: a new `identity_collision_detected`
   `ChangeKind` (RISK, D8 single-release hygiene) and matching `crosscheck.py`
   check (`_check_identity_collision`) turn each recorded collision into an
   ordinary finding, following the same skip-cleanly/coverage-honesty
   contract as `odr_type_variant`. USR-based identity replacing the fallback
   chain outright remains open.

   Landing P1 items 2/3/5 surfaced three identity-mismatch bugs between the
   AST-replay layers and `SourceEntity.identity()` (each would have silently
   broken graph-reachability BFS for the affected declarations, since a
   `SOURCE_DECLARES` node keyed one way while a replay-produced edge pointed
   at a different string for the same declaration): an `extern "C"` (or
   otherwise unmangled) **function**'s identity in `call_graph.py`/
   `type_graph.py` was the bare name instead of the scope-qualified
   `qualified_name#signature_hash` fallback; the same gap existed for an
   unmangled **variable**'s own `DECL_HAS_TYPE` edge in `type_graph.py` (no
   `signature_hash` suffix, since variables never set one); and the C++ clang
   plugin printed a pointer/reference-decorated type spelling
   (`getAsString(PP)`) verbatim instead of applying the same textual
   normalization Python's `_base_type_name()` does, so `int*`-shaped `dst`
   values diverged between the two producers. All three are fixed (a shared
   `function_decl_identity()` helper in `source_graph.py` now backs both
   Python replay modules; the plugin gained `baseTypeName()`/
   `topLevelParenIndex()` as a line-for-line port, verified byte-identical
   against Python's output via a live Clang 18 compile). A fourth,
   unrelated bug surfaced alongside: `call_graph.py`'s `_walk_calls` reused
   one `cur_file` across AST siblings without threading a discovered file
   back out of the recursion, so a later sibling's calls could be attributed
   to an earlier sibling's file — fixed by having `_walk_calls` return the
   updated `cur_file`.

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
4. ~~Kythe/CodeQL/clangd as an alternate P0/P1 edge source~~ — **done
   (partial), this change**, for the type-graph slice: `graph_backends.py`
   already ingested `/kythe/edge/ref`/`/kythe/edge/ref/call` and raw CodeQL
   call-result tuples into `DECL_REFERENCES_DECL`/`DECL_CALLS_DECL`; it now
   also ingests Kythe's `/kythe/edge/extends` (and its access-qualified
   `/public`/`/private`/`/protected` variants) and a new
   `ingest_codeql_extends_results()` entry point (same raw-tuple shape as the
   call-results ingester, but CodeQL's JSON carries no self-describing
   relation kind, so a class-hierarchy query needs its own call site) into
   `TYPE_INHERITS` edges — landed on the same `type://`/`record_type` node
   scheme `type_graph.augment_graph_with_types()` uses (not `decl://`), so a
   backend-sourced base/derived pair merges with a same-run compiler-facts
   node instead of duplicating it. Wired through `collect` as a new
   `--codeql-extends-results` flag alongside the existing `--kythe-entries`/
   `--codeql-results`. Kythe's `/kythe/edge/typed` (declaration → type) is
   deliberately still not mapped — it lacks the exact/candidate distinction
   `DECL_HAS_TYPE`'s resolution vocabulary already encodes, and mapping it as
   always-exact would misrepresent that. Virtual-dispatch/override edges (P2
   item 1) and the template-instantiation graph (P2 item 2) remain open for
   whichever backend.

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
