# ADR-048: Canonical Entity Identity and Graph Reconciliation (G31 Phase B)

**Date:** 2026-07-20
**Status:** Accepted — implemented.
**Decision maker:** (pending — recorded per repository convention;
implemented under the G31 Phase B scope doc's own "needs its own ADR" gate,
the same bar ADR-044 D1 set.)

---

## Context

[G31](../plans/g31-header-graph-default-on-followup.md) Phase A made the L2
header-only semantic graph default-on. Its Phase B scope names the
prerequisite gap: the header-only graph and the build-integrated graph
identify the same declaration differently depending on which pass saw it
first (`SourceGraphSummary.add_node`'s first-writer-wins merge), and there is
no reconciliation step that can safely tell "this is the same entity
renamed" from "this is a genuinely new/removed entity" across an old/new
comparison — a prerequisite for linking a flat finding to a graph proof path
with confidence instead of best-effort name matching.

This is explicitly the same class of problem two existing ADRs already
addressed for adjacent parts of the codebase, and this ADR must not
contradict or silently reopen either:

- **ADR-045** fixed the identical bare-name-collision bug for *flat* old/new
  `RecordType`/`EnumType` matching (`diff_types.py`/`diff_symbols.py`),
  generalizing it into `diff_helpers.TypeMap`/`lookup_matched_type`. Its
  principle — prefer the most specific available identity, fall back only
  when the fallback is ambiguity-safe *on both sides*, never resolve on a
  bare name alone — is the one this ADR applies to **L5 graph nodes**
  instead of flat `AbiSnapshot` types. This ADR does not touch `TypeMap`;
  `entity_identity.py` is `TypeMap`'s graph-node analogue, not a
  replacement.
- **ADR-046** (Proposed, not yet implemented) designed a much larger v2
  graph schema: `relation_key`/`occurrence_id` edge-identity splitting,
  evidence-preserving `facts`/`resolved`/`conflicts` node/edge merge
  (replacing first-writer-wins), a per-(kind,role) coverage matrix, a
  clang-USR-based `EntityResolver` with `SOURCE_GRAPH_VERSION = 2`, a
  `TraversalPolicy` type, and a proof-path preference order. This ADR
  implements a deliberately **narrower, additive** slice of that same
  problem space — see "Relationship to ADR-046" below for exactly what
  overlaps and what does not.

## Decision

### D1. `entity_identity.py` — canonical identity, five-tier preference order

New `abicheck/buildsource/entity_identity.py`. `CanonicalIdentity` carries a
`primary_id`, an `IDENTITY_TIER_{CANONICAL,NORMALIZED,REDUCED}` tier, a tuple
of `aliases`, and the qualified name / normalized signature / source-relative
identity computed along the way (all always populated, even when they are
not the tier that won — an alias source for D2 below). Resolution order,
matching the G31 Phase B scope doc exactly:

1. **Canonical** — a compiler-provided stable identity (`usr`, when a
   producer supplies one — no current in-tree producer does yet; the field
   exists so a future clang-USR-emitting extractor needs no schema change),
   else a **real** Itanium/MSVC mangled name. "Real" reuses
   `source_graph.function_decl_identity`'s own check (`mangled_name !=
   name`) plus `abicheck.demangle.demangle()` to verify an Itanium name
   actually demangles to something else — never a second demangling
   implementation.
2. **Normalized** — a normalized fully-qualified semantic signature
   (qualified name + kind + arity + parameter types) when no real mangling
   is available.
3. **Reduced (alias, not primary)** — source-relative declaration identity
   (file + enclosing scope + name), always computed as an alias regardless
   of which tier wins, matching the scope doc's "additional alias, not a
   primary key" instruction.
4. **Reduced (primary)** — the source-relative identity is promoted to
   `primary_id` only when tiers 1–2 have nothing (no mangled name, no
   qualified name at all).
5. **Synthetic fallback** — a `synthetic:sha256:...`-prefixed hash of
   whatever facts exist, tier `IDENTITY_TIER_REDUCED`, used only when
   nothing else is available. Clearly marked (the `synthetic:` prefix) so a
   consumer can distinguish "genuinely reduced-but-real" from "there was
   nothing at all to key on."

No tier is ever guessed: `resolve_canonical_identity` only claims a tier
when the corresponding input field is actually non-empty — a CastXML-sourced
node with no mangled name never receives a fabricated one.

`candidate_lookup_keys()` generalizes the ad hoc `{dname, qualified_name,
...}` set literal `internal_leak.py`'s call-graph-leak-path lookup used
before this module existed, into one shared helper — `internal_leak.py` is
updated to call it (one line change; the lookup semantics are unchanged).
This is the "generalize into one shared resolution path" instruction from
the scope doc, applied minimally: it does not rewrite `internal_leak.py`'s
broader reachability machinery, only its one hand-rolled key-set literal.

### D2. `graph_reconcile.py` — safe old/new reconciliation

New `abicheck/buildsource/graph_reconcile.py`. `reconcile_added_removed`
takes a `GraphSummaryDiff`'s `removed_nodes`/`added_nodes` (already computed
by the existing, **unmodified** `source_graph.diff_source_graph`) plus both
full graphs, and classifies each removed/added declaration/type node
(`source_decl`/`record_type`/`enum_type`/`typedef` — the same kind set
`DECL_NODE_KINDS` already uses) into:

- **canonical-id match** — the two nodes' D1 identities share the same
  `primary_id` (a real USR/mangled match).
- **alias match** — the two nodes' D1 alias sets intersect, and the
  intersection is unambiguous **in both directions** (exactly one candidate
  on each side) — the direct graph-node analogue of ADR-045's
  `lookup_matched_type` bidirectional ambiguity-safety.
- **structural-context match** (weakest tier) — used only when a rename
  changed the qualified name itself, so D1's alias set differs on both
  sides too. Computes each node's "position" (the set of
  `(direction, edge_kind:role, neighbor_kind)` tuples it participates in)
  and matches only when that position is unique among same-kind candidates
  on **both** sides — if two removed nodes (or two added nodes) share the
  identical position, neither is resolved; both stay ambiguous. This is
  deliberately the same "ambiguous fallback key resolves to no match, never
  an arbitrarily-chosen candidate" principle ADR-045 states, generalized
  from a bare-name key to a structural-position key.
- **ambiguous** — recorded (`ambiguous_old`/`ambiguous_new`), no match
  produced.
- **true add / true remove** — no candidate at all; passes through
  unchanged.

A matched pair is classified into one of three outcomes by comparing the two
identities' qualified name and declaring-file prefix:
`declaration_renamed` (qualified name changed, file did not),
`declaration_moved` (file changed, qualified name did not), or
`declaration_identity_reconciled` (both changed, or the match came from
canonical-id/alias evidence with no clean rename/move split).

**Concrete example that correctly stays unreconciled** (mirrors
`examples/case195_header_graph_ambiguous_rename_not_reconciled/`): a public
struct has two internal-type-typed pointer fields,
`detail::RawA* a; detail::RawB* b;`. Both internal types are simultaneously
renamed in the next version. Neither rename can resolve via alias (the
qualified names all changed) nor via structural context, because
`TYPE_HAS_FIELD_TYPE:field` edges carry no per-field discriminator — both old
nodes present the *identical* structural position (the sole field-type
target of the same parent, role `"field"`), and so do both new nodes. The
reconciler correctly refuses to guess which old name maps to which new one;
both pairs stay a true add + true remove, at no loss of soundness (the
alternative — picking a pairing anyway — would be exactly the "swap when
ambiguous" false positive ADR-045's own Context section describes).

### D3. New `ChangeKind`s (RISK-tier, non-authoritative)

`declaration_renamed`, `declaration_moved`, `declaration_identity_reconciled`
— added via the standard four-step procedure (`checker_policy.ChangeKind`,
one `ChangeKindMeta` entry each in `change_registry_buildsource.py`, the
detector in `graph_reconcile.diff_graph_reconciliation_findings` wired into
`source_graph_findings.diff_source_graph_findings`, unit tests in
`tests/test_graph_reconcile.py`). All default to `COMPATIBLE_WITH_RISK` —
pure enrichment/classification metadata, never `BREAKING`/`API_BREAK`, per
the scope doc's explicit instruction and ADR-028 D3's authority rule.

**The authority rule is structurally unaffected, not just documented**:
`diff_graph_reconciliation_findings` only *appends* new `Change` objects to
the list `diff_source_graph_findings` already returns; it never inspects,
filters, or mutates any other finding (from this function or any other
detector). `tests/test_graph_reconcile.py::
test_reconciliation_never_deletes_or_downgrades_artifact_finding` builds an
old/new snapshot pair with a genuine artifact-proven `func_removed` finding
*and* a graph-reconcilable rename in the same comparison, runs the full
`checker.compare` pipeline, and asserts the `func_removed` finding is still
present with verdict `BREAKING` — reconciliation firing does not touch it.

### D4. Structured impact/proof-path data (B3)

New `abicheck/buildsource/graph_impact.py`. `structured_proof_path(graph,
path)` renders a `list[GraphEdge]` (the same type
`source_graph_findings._dependency_path` already returns) into an ordered
`node, edge, node, edge, node, ...` list of typed reference dicts —
`{"type": "node", "id", "kind", "label"}` / `{"type": "edge", "kind", "role",
"confidence"}` — instead of only the existing formatted-string rendering.
`attach_impact_metadata` sets three new `Change` fields in place:
`affected_public_roots` (the public entry label(s) the walk started from),
`impact_proof_path` (the structured list), `impact_is_direct` (single-hop vs.
transitive).

Wired into `source_graph_findings._internal_dependency_findings` (the
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` producer): the shortest per-target
`_dependency_path` result it already computes for the prose "Proof path(s)"
sentence is now *also* attached structuredly to the same `Change` object —
no duplicate finding, matching the module's own "explain, don't duplicate"
convention.

**Surfacing.** JSON (`reporter.py`) and SARIF (`sarif.py`) both gained the
three fields as ordinary properties (`affected_public_roots`/
`affectedPublicRoots`, etc.), following the exact pattern the existing
`reachability_proof_path` field already used in both formats. **JUnit is
deliberately not touched**: `junit_report.py` does not surface
`reachability_proof_path`/`correlated_change_kind` either today (JUnit XML's
structured-data surface is `<properties>` text values, a poor fit for a
node/edge list) — this ADR keeps that existing precedent rather than
introducing a first structured field there. SARIF's `relatedLocations`/
`codeFlows` model *source-file* locations (URI + region), not abstract graph
node/edge references with no file/line of their own for most L2 header-only
nodes — forcing the proof path into that shape would need synthesizing fake
locations. Given the L5 graph proof path is evidence *about the graph*, not
about a specific source range, this ADR surfaces it as a typed `properties`
value (consistent with every other graph-derived field already on `Change`)
rather than `relatedLocations`/`codeFlows` — a deliberate scope decision, not
an oversight.

## Relationship to ADR-045

Not superseded, not contradicted — extended into a new domain. ADR-045's
principle (prefer specific identity, ambiguity-safe fallback, never resolve
on a bare name) is realized here for L5 graph nodes via D1/D2, the same way
`TypeMap` realizes it for flat `RecordType`/`EnumType`. `TypeMap` itself is
untouched by this ADR — a future consolidation (making `TypeMap` and
`entity_identity.CanonicalIdentity` share more machinery) is out of scope
here and not implied by anything in this decision.

## Relationship to ADR-046

At the time this ADR (G31 Phase B) was written, ADR-046 was Proposed and
unimplemented; ADR-046's D2 (the evidence-preserving `facts`/`resolved`/
`conflicts` node/edge merge, replacing `add_node`'s first-writer-wins) has
since landed as a later, independent slice (G29 Phase 2). This ADR does not
implement ADR-046's D1/D3-D6, supersede its Decision, or change its status
beyond that D2 note. What this ADR *does* share with ADR-046 D4's
`EntityResolver` proposal (still unimplemented):

- Both use "the most specific identity available, aliases for everything
  else" as the organizing principle, and both fall back to a v1-style hash
  when no clang USR is available.
- **This ADR's `entity_identity.CanonicalIdentity` is intentionally smaller
  in scope than ADR-046 D4's `EntityResolver`.** It resolves identity for
  reconciliation (D2) and impact-linking (D4 above) — it does not touch
  `GraphNode.id`, does not bump `SOURCE_GRAPH_VERSION`, and predates (is
  independent of) ADR-046 D2's evidence-preserving merge; it does not split
  edge identity into `relation_key`/`occurrence_id` (D1), does not add the
  per-(kind,role) coverage matrix (D3), and does not formalize
  `TraversalPolicy` (D5) or the six-tier proof-path preference order (D6).
- Should ADR-046 D4 later be implemented, `entity_identity.py`'s
  `CanonicalIdentity` is the natural first alias `EntityResolver.aliases`
  would fold in (not a competing identity scheme to reconcile away) — this
  ADR does not create a second, conflicting identity model for a future
  `EntityResolver` implementation to have to unify; it is a strict subset of
  what `EntityResolver` would eventually need, shipped now because Phase B
  needed *a* working identity model before ADR-046 D4 existed.

## Non-goals

- Not a change to `GraphNode.id`/`_decl_node_id` et al., `add_node`'s
  first-writer-wins merge behavior, or `SOURCE_GRAPH_VERSION` (stays `1`).
- Not CastXML schema-completeness extraction, single-AST reuse for the
  clang backend, hybrid-backend provenance merging, body fingerprints, or
  preprocessor/build-context reconciliation — all explicitly Phase C
  (`# TODO(header-graph-phase-C)` comments mark the deferred spots).
- Not a performance benchmark/regression gate — Phase D, and explicitly
  sequenced after Phase C per the G31 plan's own sequencing note.
- Not a new CLI flag or command; reconciliation findings flow through the
  existing `diff_source_graph_findings` → `compare` pipeline exactly like
  every other L5 finding.

## Consequences

**Positive:** a genuine internal-dependency rename (case194 in `examples/`)
now reports one `declaration_renamed` finding with clear before/after
identity instead of leaving a reader to notice an unrelated add+remove pair
in the graph diff and manually infer they were the same declaration. An
ambiguous rename (case195) correctly stays unresolved rather than guessing —
soundness preferred over recall, matching every other graph-evidence
decision in this codebase (ADR-028 D3, ADR-031 D6). Structured proof-path
data lets a SARIF/JSON consumer walk graph evidence programmatically instead
of parsing prose.

**Costs:** the structural-context match tier is graph-shape-dependent and
will not resolve a rename that also happens to change the node's structural
position (e.g. a rename combined with becoming a base class instead of a
field) — it correctly falls through to "ambiguous" or "true add/remove" in
that case, which is conservative-safe but means some real renames go
unreconciled rather than mis-reconciled. `reconcile_graph_diff` runs its own
`diff_source_graph` pass internally (in addition to the one
`diff_source_graph_findings`'s other helpers already use indirectly via
their own set-based comparisons) — a second O(n log n) sort over the same
node/edge sets; not measured as a regression risk at current graph sizes,
but noted for Phase D's eventual perf-gate work.

## References

- `abicheck/buildsource/entity_identity.py`, `abicheck/buildsource/graph_reconcile.py`,
  `abicheck/buildsource/graph_impact.py`
- `abicheck/buildsource/source_graph_findings.py` — `_reconciliation_findings`,
  `_internal_dependency_findings`'s impact-metadata attachment
- `abicheck/checker_policy.py` — `DECLARATION_RENAMED`/`DECLARATION_MOVED`/
  `DECLARATION_IDENTITY_RECONCILED`
- `abicheck/change_registry_buildsource.py`
- `tests/test_entity_identity.py`, `tests/test_graph_reconcile.py`,
  `tests/test_graph_impact.py`
- `examples/case194_header_graph_rename_reconciled/`,
  `examples/case195_header_graph_ambiguous_rename_not_reconciled/`
- [ADR-028](028-source-build-evidence-pack.md) D3 (authority rule),
  [ADR-031](031-source-implementation-graph-augmentation.md) D6,
  [ADR-041](041-compiler-facts-semantic-impact-graph.md),
  [ADR-044](044-reachability-aware-suppression.md),
  [ADR-045](045-identity-based-old-new-entity-matching.md),
  [ADR-046](046-source-graph-identity-v2-and-evidence-merge.md)
- [g31-header-graph-default-on-followup.md](../plans/g31-header-graph-default-on-followup.md)
  — Phase B's scope doc
