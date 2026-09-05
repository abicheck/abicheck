# G31 — Header-Graph Default-On: Follow-Up Phases B–D

**Origin:** ADR-041's `--header-graph`/`--header-graph-includes` flags were
opt-in from the day they shipped. G31 Phase A (2026-07-20) flipped them to
default-on: the L2 header-only semantic graph now always builds whenever
`--depth headers` or deeper evidence is available, with the two flags
demoted to hidden, deprecated no-ops. This plan scopes the phases that
follow — deeper reconciliation, backend unification, and new
detection/perf surfaces — none of which shipped in Phase A.

**Note on naming:** this plan was drafted as "G29" before the `g29` letter
was found already claimed by
[`g29-impact-analysis-layer.md`](g29-impact-analysis-layer.md) (a separate,
earlier-registered initiative on the same graph infrastructure — unified
graph-driven impact model, reachability/suppression, consumer scoping). It
is filed here as **G31** (the next free gap letter) instead. The two
initiatives share a graph substrate but are independent efforts.
Cross-reference, don't merge, the two when planning future work — a reader
arriving at either doc should check the other for adjacent context (e.g.
Phase B's canonical entity identity below overlaps with the
impact-analysis-layer plan's own graph-identity work, Phase 2 there).

**ADR:** [ADR-041](../adr/041-compiler-facts-semantic-impact-graph.md)
(introduced the flags and the header-only graph; carries the Phase A update
note). Phase B (canonical entity identity) likely needs its own ADR before
implementation, per the same bar ADR-044's "Post-merge review rounds" note
sets for graph-identity changes.
**Type:** Initiative plan (cross-cutting; spans `abicheck/buildsource/`,
`abicheck/service.py`, `abicheck/dumper*.py`, `abicheck/binder.py`/
`resolver.py`, `abicheck/change_registry*.py`, `examples/`, `docs/`).
**Effort:** Phase A — S, done (see below). Phase B — L. Phase C — L. Phase D
— M (mostly content: new kinds/examples/docs/perf gate).
**Risk:** low for Phase A (additive default-flip, both flags kept as inert
shims). Phase B is medium-risk (touches graph node/edge identity, the same
class of change ADR-044 flags as needing a recorded decision). Phase C is
medium-risk (backend AST-reuse touches the hot `dumper_clang.py`/
`dumper_castxml.py` parse path every snapshot goes through). Phase D is
low-risk (additive kinds/examples/docs, opt-in perf gate).

---

## Phase A (done) — mechanical default-on flip

**What shipped.** The L2 header-only semantic graph (and its include-file
extension) is now always built whenever headers are parsed for a
single-library `dump`/`compare` — no flag required. `--header-graph`/
`--header-graph-includes` were kept for a while as hidden (`hidden=True`,
absent from `--help`), deprecated no-op shims on `compare` and `dump`
(passing either printed a one-line deprecation note to stderr and otherwise
changed nothing) — **CLI cleanup H1 has since removed both flags outright**;
passing either is now a plain usage error. Directory/package (set-input)
`compare` still does not build the graph (the
per-library fan-out never routed through the attach step, before or after
this change); a raw `--old-sources`/`--new-sources`/`--old-build-info`/
`--new-build-info` tree on `compare` also still does not (the inline-embed
path reloads from a JSON snapshot that never attaches a graph) — both are
the same pre-existing structural gaps, just no longer flag-gated. Internally,
`service.py` gained module constants `_HEADER_GRAPH_ENABLED = True`/
`_HEADER_GRAPH_INCLUDES_ENABLED = True`; `service.resolve_input()` and
`service.run_dump()` no longer accept `header_graph`/`header_graph_includes`
keyword arguments at all — a breaking Python-API change for any direct
caller of those two functions. `service_dump_cache.py`'s whole-snapshot
cache (`cached_run_dump`) no longer disables caching for header-graph
requests, since the graph is now unconditional and deterministic given the
same already-hashed inputs, and the full snapshot (including
`build_source`) round-trips through the JSON cache.

**Files.** `abicheck/service.py` — `_HEADER_GRAPH_ENABLED`/
`_HEADER_GRAPH_INCLUDES_ENABLED` (module constants), `_attach_header_graph`
(the actual attach step, unchanged in behavior, now always invoked with
`True`/`True` except through the internal `_skip_header_graph_attach` knob
used by the buildsource-embed recursion — see the
`# TODO(header-graph-phase-D)` comment there), `run_dump`. `abicheck/cli_options.py`
used to re-export `header_graph_options` (the shared, hidden decorator) and
`warn_deprecated_header_graph_flags` (the deprecation-note helper both
`compare` and `dump` called) — both removed outright by CLI cleanup H1.
`abicheck/cli_resolve.py` —
`_EVIDENCE_SET_INPUT_FLAGS` (documents which `compare` input combinations
structurally never reach the graph-attach step, unchanged by Phase A other
than no longer needing a `UsageError` for an explicit flag that no longer
exists).

**Not fixed in this pass** (tracked as the `# TODO(header-graph-phase-D)`
comment in `service.py`): `header_graph_includes`'s per-header `clang -M`
pass has no caching of its own independent of the whole-snapshot cache. A
whole-snapshot cache *hit* restores the previously-computed `build_source`
(including the include-graph edges) without rerunning anything — the pass
only actually executes on a cache *miss* or for an uncacheable call shape
(see `service_dump_cache._dump_is_cacheable`). On a miss, though, it
re-runs unconditionally and fails soft (degrades to not_collected/partial
coverage) when clang is unavailable, with no cheaper incremental path of
its own. See Phase C's sequencing note.

## Phase B (done) — Unify graph/public-surface vocabulary + canonical entity identity

**Status: done.** Implemented via [ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md)
("Canonical Entity Identity and Graph Reconciliation") — see that ADR for
the shipped scope. The problem/scope description below is retained as the
original planning record.

**Problem.** The header-only graph and the build-integrated
(`--sources`/`--build-info`) graph currently identify the same declaration
differently depending on which pass saw it first
(`SourceGraphSummary.add_node`'s first-writer-wins merge — see ADR-046 §2).
There is no USR/mangled-name-based reconciliation step that can safely tell
"this is the same entity renamed" from "this is a genuinely new/removed
entity" across an old/new comparison, which is a prerequisite for linking a
flat, single-line finding (e.g. `struct_field_type_changed`) to a graph
*proof path* (impact closure showing which public entries actually reach
it) with confidence instead of best-effort name matching.

**Scope.**
- Canonical entity identity keyed on Itanium/MSVC mangled name where
  available, falling back to a qualified-name + kind + arity key when a
  header-only pass has no mangling to offer (mirrors the existing fallback
  already used ad hoc in `internal_leak.py`'s trigger matching — see
  ADR-044's "leak triggers were a category error" post-merge note — but
  generalized into one shared resolution path instead of a per-consumer
  workaround).
- Rename-vs-add/remove disambiguation for old/new comparisons: given the
  same canonical identity resolves on both sides, a graph-visible signature
  change should be reported as one finding, not a spurious
  remove-then-add pair.
- Linking flat findings to their graph impact/proof-paths as first-class
  structured data (not just the prose `graph explain` already produces),
  so a report consumer (SARIF/JUnit/JSON) can carry the proof path
  alongside the finding it corroborates.

**Files likely to change.** `abicheck/buildsource/header_graph.py`,
`abicheck/buildsource/source_graph.py`, `abicheck/buildsource/source_graph_findings.py`,
`abicheck/buildsource/call_graph.py`, `abicheck/buildsource/type_graph.py` —
node/edge identity and merge logic. `abicheck/binder.py`/`abicheck/resolver.py`
— existing symbol-binding infrastructure to potentially reuse rather than
building a second identity-resolution mechanism from scratch.
`abicheck/demangle.py` — canonical-name derivation for the mangled-name key.

**`declaration_moved` example coverage — closed, after one wrong attempt
(Codex review, PR #727).** ADR-048's D2 (`graph_reconcile.py`) defines three
reconciliation outcomes — `declaration_renamed`, `declaration_moved`,
`declaration_identity_reconciled` — but only `declaration_renamed` (case194)
and the deliberate ambiguous-rename counter-example (case195) had
example-catalog coverage before this pass.

A first attempt built a `declaration_moved` fixture (a private field-type
target keeping its exact qualified name but moving to a different declaring
header) the same way `tests/test_graph_reconcile.py`'s own
`test_move_reconciles_when_file_changes_but_name_does_not` unit test does:
two `GraphNode`s with matching `qualified_name` but artificially distinct
ids (`type://old`/`type://new` in the unit test; `@v1`/`@v2` suffixes in the
first attempt) so `diff_source_graph`'s node-id diff treats them as a
removed+added pair for the reconciler to re-pair via the alias tier. Review
caught that this doesn't reflect anything a **real** `dump --sources`/
`--build-info` run can produce: every graph-node-id constructor in
`abicheck/buildsource/` (`header_graph.py`'s `_decl_identity()`/`seed_decl`,
`source_graph.py`'s `_type_node_id`/`_decl_node_id`/`function_decl_identity`,
and every one of their callers in `call_graph.py`/`type_graph.py`/
`macro_graph.py`/`callback_graph.py`/`override_graph.py`/`template_graph.py`/
`graph_backends.py`, confirmed by grepping every `_type_node_id`/
`_decl_node_id`/`_debug_type_node_id` call site) keys a declaration's or
type's node id purely off its mangled or qualified name, never a file path
— so a *pure* move (unchanged signature, only the header changes) gets the
**same** node id on both sides of a real comparison and never reaches the
reconciler as a removed+added pair at all. First attempt reverted rather
than shipped with a misleading claim of reachability.

A second review round (same PR) found the corrected "unreachable" framing
had itself overclaimed: it is only a *pure* move that's unreachable.
Constructing real `SourceEntity`/`BuildEvidence` facts (a function whose
parameter type changes `int`→`long` — moving its Itanium mangled name — in
the same release its header moves) and running them through the **actual
production fold**, `source_graph.build_source_graph()`, confirmed
empirically that the qualified-name alias tier pairs the resulting two
distinct-id nodes, and the file-vs-name comparison in `_classify_outcome`
correctly reports `declaration_moved` — a compound edit (signature change +
header move, sharing a qualified name) genuinely reaches this outcome
through the real pipeline, not through a hand-invented id.

A third review round found the *second* version's own fixture design
overclaimed something else: it made the identity-perturbing edit a
**public** function `demo::f`, but a public function's mangled-name-moving
signature change is itself a real, independent BREAKING change (the old
exported symbol disappears from the export table) — cataloging that
scenario as `COMPATIBLE_WITH_RISK` contradicts `ground_truth.json`'s
one-canonical-verdict invariant (one canonical verdict applies to the
scenario a case describes, not to a hand-isolated fixture slice of it).
Fixed by moving the identity-perturbing edit onto a **private** helper
(`demo::detail::helper`, `visibility="private_header"`) reached only
through a public caller's (`demo::process`) `DECL_CALLS_DECL` dependency
edge, needed because `graph_reconcile`'s own public-reachability gate
(`_public_reachable_ids`) suppresses a reconciliation finding entirely for
a declaration with no live path from a public entry point. A fourth review
round caught that adding that same call edge to *both* the old and new
graphs (pointing at the helper's two different per-version mangled-name
node ids) made `source_graph_findings._internal_dependency_findings` — the
`public_api_internal_dependency_added` producer, `case160_public_api_
internal_dep_added`'s own subject — fire on a raw-node-id artifact rather
than a genuinely new dependency: that detector compares raw target ids
across versions, not reconciled identities, so it read the pre-existing
call relationship as newly added. Fixed by restricting the call edge to the
**new** side only, which still satisfies the reachability gate (only one
side needs the edge) without asserting a spurious new dependency; the
detector's own coverage gate then declines to credit anything as newly
reached when the old side carries no dependency-edge coverage at all — the
state as of the fourth review round, where `declaration_moved` was the
fixture's sole finding.

A fifth review round found that restricting coverage to the new side was
itself a fixture artifact, not a faithful real-world scenario: a real
`collect_inline_pack()`/`fold_call_graph()` run certifies `call_graph`
coverage identically on *both* sides of an ordinary comparison, so an
old-side coverage gap this fixture had to manufacture by hand doesn't occur
in practice, and it was suppressing the very `public_api_internal_dependency_
added` finding this case exists to demonstrate. Fixed by adding the matching
old-side call edge too — now genuinely resolvable to distinct per-version
node ids without the raw-node-id-artifact problem the fourth round found,
because both sides carry real, independently-derived coverage rather than
one side's coverage being suppressed as a workaround. A sixth review round
then found the fifth round's coverage fix was itself hand-forced (setting
`extractor_passes["call_graph"] = "full"` directly) rather than earned
through the real production path; fixed by routing both sides through
`mark_source_edges_extractor_coverage()`, the same helper a real
`collect_inline_pack()` run uses to certify coverage, so the fixture's
coverage state is produced identically to how a real run would produce it.
With the identity-perturbing edit confined to a never-exported private
declaration, and both sides' dependency-edge coverage genuinely certified,
the fixture now emits **two** findings through the real pipeline:
`declaration_moved` (RISK-tier L5, from the header move) and
`public_api_internal_dependency_added` (RISK-tier L5, from the public
caller's now-newly-visible dependency on the moved private helper).
`COMPATIBLE_WITH_RISK` — backed by both findings together, matching
`ground_truth.json`'s `expected_kinds: ["declaration_moved",
"public_api_internal_dependency_added"]` — is the genuinely correct
canonical verdict; a real end-to-end comparison of this exact scenario has
nothing BREAKING to contradict it.
`case196_header_graph_move_reconciled` now ships built exactly this way
(real dataclasses → real fold → real diff, not hand-assembled
`GraphNode`/`GraphEdge` objects), closing the example gap correctly.
`graph_reconcile.py`'s own module docstring and
`tests/test_graph_reconcile.py`'s existing unit test are unaffected — the
unit test's artificial ids are still legitimate for testing the
classifier's pure logic in isolation.

**What remains genuinely open**: the *pure*-move shape (declaration keeps
its exact signature, only its header changes) is still unreachable from any
current real producer — closing that needs a real producer-side change
(some node-id or edge/attribute signal that changes when a declaration's
file does but its identity doesn't), which is its own scoped design (which
node kinds should be file-sensitive, how that interacts with the existing
alias/structural-context tiers, whether it's a new node attribute compared
separately from the identity-based node id rather than folded into the id
itself) — not a drive-by extension of an example-catalog PR.

**A seventh, deeper finding, investigated and deliberately not attempted
this pass (Codex review, PR #727, fresh evidence).** `case196`'s own
`SourceAbiSurface`s are constructed directly by the generator script
(`_pub_caller()`/`_helper_entity()` fed straight into `SourceAbiSurface(...)`
and then `build_source_graph()`), not produced by running raw
`SourceEntity`/`BuildEvidence` facts through `link_source_abi()` — the plan
text and the generator's own comments describe this as exercising "the
actual production fold," which is true of `build_source_graph()` itself but
overstates what feeds it: `link_source_abi()`, the function that actually
turns per-TU extractor output into a `SourceAbiSurface`, is never called at
all. That gap is not cosmetic. `link_source_abi()`'s very first filter
(`_is_public(entity) or entity.qualified_name in forced`, `source_link.py`)
runs *before* any entity is routed into `reachable_declarations`/
`reachable_inline_bodies`/etc., and it keys purely on the entity's own
`visibility`/`api_relevant` — it has no notion of "reachable through a
public caller's own dependency edge." `demo::detail::helper` carries
`visibility="private_header"`, so a `link_source_abi()` call over the
equivalent raw `SourceAbiTu` facts would drop it before it ever reached a
reachable bucket, on **both** sides — the DECL_CALLS_DECL edge that would
otherwise justify keeping it (mirroring `public_api_internal_dependency_
added`'s own "already public via a dependency, not via its own header"
logic) is consulted by `graph_reconcile`/`source_graph_findings` only
*after* linking, never by `link_source_abi()` itself. The one existing
escape hatch, `forced_public`, would route it through — but `forced_public`
only ever carries a real value from ADR-049's `--contract-evaluation`
overlays (`contract_pipeline.py`'s `force_public_symbols`); a bare
`dump --sources`/`--build-info` run (`inline.collect_inline_pack()` →
`run_source_replay()`) always calls `link_source_abi()` with an empty one.
So today, a real `dump --sources` run over source code shaped exactly like
this case's own narrative comments would **not** reproduce this scenario:
the private helper would be absent from the L4 surface entirely (old side),
and the graph's `declaration_moved`/`public_api_internal_dependency_added`
findings this case demonstrates would not fire. Confirmed by reading
`link_source_abi()`, `source_replay.run_source_replay()`, and
`inline.collect_inline_pack()` directly; not attempted as a live repro
build. **Not fixed here.** A correct fix has two independent parts, each
its own scoped design: (1) `link_source_abi()` would need a second
admission path — "not directly public, but reachable through an
already-admitted public entity's own dependency edge" — which means either
running the reachability walk *before* the visibility filter (a real
ordering change to a function every other L4/L5 consumer already depends
on) or accepting a documented two-pass linking model; (2) even with that,
`case196`'s own scenario needs the DECL_CALLS_DECL fact to exist *before*
linking (today it's `source_edges`, an L5-shaped fact folded by
`build_source_graph()` *after* linking, not an L4 input `link_source_abi()`
consumes at all) — so part (1) alone is not sufficient without also
deciding how a pre-link reachability signal is supposed to reach
`link_source_abi()` in the first place. Given this session had already
produced six review rounds on this exact fixture, an eighth reactive patch
attempting both of these under continued review pressure was judged a worse
risk than documenting the gap honestly: the case still correctly exercises
`build_source_graph()`'s real fold and `graph_reconcile`'s real
classification logic against a hand-constructed-but-internally-consistent
`SourceAbiSurface` input — which is a genuine, if narrower, claim than "a
bare `dump --sources` run over equivalent source reproduces this end to
end," and the plan's and generator's own claims should be read with that
narrower scope in mind pending a dedicated follow-up.

## Phase C — CastXML schema-completeness audit + backend unification

**Problem.** Two independent gaps, related but separable:

1. **Fact completeness.** Neither header AST backend currently extracts
   bitfields, vptr placement, standard-layout/trivially-copyable traits,
   deprecation, or default-argument facts with a consistent tri-state
   provenance model (known-true / known-false / not-collected) the way
   G28's Phase 1 CastXML schema-completeness audit did for the flat
   snapshot. The header-only graph inherits whatever the underlying
   snapshot parse already knows, so this is a prerequisite for the graph
   to reason about those facts at all. **Partially done** — see the
   "Direct-clang backend fact-completeness pass" bullet below; a first
   audit pass closed several real backend-parity gaps this list assumed
   were unstarted (bitfields, default-argument facts, and deprecation were
   already wired for castxml via G28 Phase 1 — that work had simply moved
   the surviving gap to the *clang* backend specifically, not left it
   untouched everywhere).
2. **Backend duplication (done — AST reuse, not backend unification).**
   The direct-clang backend used to run a *second*, independent
   `clang -ast-dump=json` pass specifically to build the header-only graph
   (`service._attach_header_graph` → `header_graph`'s own AST walk),
   separate from the pass `dumper_clang.py` already ran to build the flat
   snapshot — exactly the gap Phase A's `# TODO(header-graph-phase-D)`
   comment flagged: since the graph is unconditional, every `--ast-frontend
   clang` dump paid a full second AST parse it didn't pay when the graph
   was opt-in and rare. **Fixed via in-process memoization, not AST
   threading**: `dumper._clang_header_dump`'s content-addressed cache key
   is now backed by an in-process memo (`dumper_cache.load_cached_ast`/
   `store_cached_ast`, bounded to 4 entries) in addition to the existing
   on-disk cache — `_attach_header_graph`'s call with the identical
   resolved headers/includes/toolchain now hits that memo and reuses the
   already-parsed dict instead of a second disk read + JSON re-parse (or a
   second subprocess, on a cold cache). This closes the *duplicated-cost*
   problem the TODO named without the deeper "thread the AST object itself
   through service.py/ElfHeaderAstResult/dumper_manifest.py across
   ELF/PE/Mach-O" plumbing a literal reuse would have needed — the memo is
   keyed the same way the disk cache already was, so it generalizes to all
   three formats and the hybrid-merge path for free, with no new
   parameters threaded through any of those call chains. The remaining,
   deliberately out-of-scope piece of "backend unification" below (a
   *single* AST object shared end-to-end, rather than a second read of an
   equivalent one) is still open — see its own bullet.

**Scope.**
- CastXML schema audit for the facts listed above, following G28 Phase 1's
  discipline (verify against real CastXML XML output before claiming a
  fact is extractable — some may turn out infeasible the way `_Atomic`
  inner-type recovery and comment-text extraction did there). **Not
  started** — the audit pass actually run (see below) went the other
  direction: real *clang* AST output, closing gaps on the clang side of
  facts G28 Phase 1 had already wired up for castxml. A from-scratch
  CastXML-schema audit for genuinely new facts (as opposed to closing an
  existing cross-backend gap) remains unstarted.
- **Direct-clang backend fact-completeness pass — done for four of the
  facts this Phase names.** Re-reading G28 Phase 1's own scope (`deprecated`
  on every surface kind, `is_scoped` on enums, bitfields, default-argument
  facts) against the *current* code found bitfields and default-argument
  facts were already populated by `dumper_clang.py` — only `deprecated` and
  `is_scoped` were still genuinely castxml-only, contrary to this plan's own
  "Neither header AST backend..." framing above. Fixed both, each verified
  against real `clang -ast-dump=json` output (Clang 18) before wiring up:
  - `Function`/`Variable`/`TypeField`/`RecordType`/`EnumType.deprecated` —
    clang emits a `DeprecatedAttr` child node under a declaration's own
    `"inner"` list (present for both the bare and messaged
    `[[deprecated]]`/`[[deprecated("msg")]]` forms, with an optional
    `message` key present only for the messaged form) — see
    `dumper_clang._clang_deprecated_message`, matching
    `dumper_castxml._deprecation_marker`'s exact three-way convention
    (message text / `""` bare / `None` not-deprecated).
  - `EnumType.is_scoped` — clang's `EnumDecl` node carries a
    `"scopedEnumTag"` key (`"class"`/`"struct"`) only for `enum class`/
    `enum struct`, absent (never present-and-false) for a plain C-style
    enum — a plain `EnumDecl` always has a definitive answer, so this is a
    concrete bool, never `None`, on this backend (unlike
    `is_standard_layout`/`is_trivially_copyable` below).

  **Producer-gating fix that came with it.** Both facts were already
  gated, per-declaration, on `fact_provenance.both_castxml_backed_fact` —
  correct while clang genuinely didn't populate them (it made a
  clang-parsed side's unconditional `None` unmistakable from a real
  removal), but now silently wrong: it would keep declining to compare a
  clang-vs-clang or clang-vs-castxml pair even though both sides now
  genuinely know the answer. Since both facts' VALUE REPRESENTATIONS are
  directly cross-comparable between backends (a plain message string / a
  plain bool, not a backend-specific encoding — unlike `Param.default`,
  where castxml keeps the real source expression and clang falls back to a
  structural placeholder for anything non-trivial), the fix is a new,
  looser gate rather than reusing the same-producer check
  `_diff_param_defaults` needs: `fact_provenance.both_known_backed_fact`
  accepts *any* combination of positively-known producers (not "both
  castxml" specifically), while still correctly declining a genuinely
  unknown/legacy producer. Wired into all six affected detectors
  (`func_deprecated`, `var_deprecated`, `field_deprecated`,
  `type_deprecated`, `enum_deprecated`, and the enum `is_scoped` gate
  inside `_diff_enums`). Five pre-existing tests in
  `test_castxml_schema_completeness.py`/`test_diff_types_deep.py` had
  asserted the *old* (now-incorrect) "castxml-vs-clang must not
  false-positive" behavior for exactly these two facts; updated to assert
  the corrected behavior (a real cross-producer transition now fires) plus
  a new "genuinely unknown producer still declines" case each, since that
  narrower condition is the real remaining false-positive-avoidance
  scenario. `is_override`/`is_abstract`/`TypeField.default` (member
  initializer) remain castxml-only — no clang-side extraction exists for
  any of the three — so their detectors, and the sibling
  producer-mismatch tests covering them, are unchanged.

  **`is_override`/`is_abstract` closed in a still-later pass** (G31 Phase C
  backend audit), once a real conda-forge castxml build (0.7.0) and clang
  18 were available in this environment to verify against.
  `Function.is_override` reads clang's `OverrideAttr` child node (whether
  the `override` keyword was written — the same semantics castxml's own
  `attributes`-string regex search already uses, not the broader
  no-keyword-required "genuinely overrides a base virtual" signal
  `dumper_clang_vtable.py` separately reconstructs); confirmed empirically
  that clang's JSON AST has no direct boolean key for it, only the child
  node, mirroring `_clang_final_attr`/`_clang_deprecated_message`'s own
  established child-node-detection pattern. `RecordType.is_abstract` reads
  `definitionData.isAbstract`, following the identical
  presence-recovers-True/absence-recovers-False convention
  `_clang_record_type_traits` already established for
  `isStandardLayout`/`isTriviallyCopyable` — confirmed this is real
  semantic computation, not a shallow "declares a pure virtual directly"
  check: a three-class hierarchy (an abstract base, a concrete override,
  and a second derived class that leaves the pure virtual unimplemented)
  correctly reports the third class as abstract too. Both detectors'
  producer gate moved from `both_castxml_backed_fact` to
  `both_known_backed_fact` (mirroring `deprecated`'s own earlier fix in
  this same pass), since both facts are now genuinely cross-producer with
  directly-comparable values (a plain bool, not a backend-specific
  encoding). `snapshot_cache._SNAPSHOT_CACHE_VERSION` bumped (v16) so an
  upgrading user's warm clang/hybrid cache entry is re-extracted instead of
  replaying a snapshot with both facts silently unset. Verified end-to-end
  against real compiled examples through the actual `abicheck dump
  --ast-frontend clang` CLI, not just at the unit level. `TypeField.default`
  (member initializer) remains the one item from this list not yet closed
  (see the separate entry earlier in this file for its own status).
  `_clang_method_is_override`/`_OVERRIDE_ELIGIBLE_KINDS`/
  `_clang_record_is_abstract`/`_clang_record_type_traits` all moved to
  `dumper_clang_qualifiers.py` in the same pass (`dumper_clang.py` sits at
  its 2000-line hard cap), re-exported from `dumper_clang.py` for
  backward-compatible import paths.
  - `RecordType.is_standard_layout`/`is_trivially_copyable` — a separate,
    already-tri-state-gated (no producer check at all, just the existing
    None-means-unknown convention) pair `dumper_castxml.py`'s own code
    comment already documented as genuinely infeasible for castxml
    ("CastXML doesn't expose the trivially-copyable trait directly, and
    'not polymorphic and no virtual bases' is not a sound standard-layout
    signal" — Codex review #345). Confirmed empirically that clang's
    `-ast-dump=json` output DOES expose both directly: a `CXXRecordDecl`'s
    `definitionData` carries `isStandardLayout`/`isTriviallyCopyable` as
    boolean keys — but only when the trait is `true` (the key is entirely
    absent, never present-and-`false`, when the trait doesn't hold — e.g. a
    class with a private member has no `isStandardLayout` key at all,
    confirmed by direct comparison against a plain-public-members struct
    which does carry it). A plain C `RecordDecl` (not `CXXRecordDecl`) has
    no `definitionData` key whatsoever, since these are C++-only concepts —
    yields `(None, None)`, matching this module's existing tri-state
    convention rather than fabricating an answer. See
    `dumper_clang._clang_record_type_traits`. This activated the
    `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST` detectors
    (`diff_layout.py`) for real for the first time on any backend — they
    were fully built (registered `ChangeKind`s, a working detector) but
    permanently dead code on every real dump before this, since neither
    backend had ever populated the fields they gate on. Verified end-to-end
    against a real compiled example (a class losing standard-layout by
    gaining a private member correctly fires `STANDARD_LAYOUT_LOST` through
    the real `dump()`/`compare()` pipeline, not just at the unit level).
    Also updated `dumper_hybrid.py`'s merge docstring/comment, which
    claimed this backfill was a no-op "without the optional
    `ABICHECK_CLANG_LAYOUT_TOOL` companion tool enabled" — no longer true
    for these two facts specifically, since the plain clang parse (no
    companion tool needed) now populates them; the merge's own
    `_LAYOUT_SCALAR_ATTRS` backfill logic required no code change (it
    already backfills any `None`-on-castxml layout attr from clang
    unconditionally), only its stale comment did.

  **Still open** for this fact-completeness pass: vptr placement remains
  only a `0`-if-polymorphic heuristic on castxml (no real multi-inheritance
  secondary-vtable placement) and is not populated by the clang backend at
  all — the one fact-completeness item from this phase's original list that
  remains genuinely unstarted. `TypeField.default` (member initializer
  *value*, not the default-argument facts above) was the other item on this
  list still marked castxml-only here; closed in a later pass — see the new
  entry below, appended rather than edited in place so this section stays
  an accurate as-of-time record of what each review round actually found.
  (The "not populated by the clang backend at all" half of this specific
  claim is also now stale — closed in a still-later pass; see the
  "closed half of the still-open vptr-placement gap" entry further below,
  same append-don't-edit convention.)

  **A later pass closed the DWARF backend's half of the vptr-placement gap
  — the L2 header-only backends (castxml/clang) remain unstarted.**
  `dwarf_snapshot.py`'s `vptr_offset_bits` used the identical
  `0`-if-polymorphic heuristic this section flags for castxml, with a real,
  verified-against-GCC-13/Clang-18 fix available: GCC/Clang both emit an
  artificial `_vptr.<Class>`/`_vptr$<Class>` `DW_TAG_member` — with a real
  `DW_AT_data_member_location` — on whichever class *introduces* a given
  vtable, already discarded (not surfaced as an ordinary field) by
  `_process_field` but never read for its offset. Now read directly instead
  of assumed. This also closed a genuine correctness gap the heuristic
  never covered at all, not just an accuracy one: a class that only
  *inherits* a vtable — adds or overrides no virtual method of its own
  (`struct N : A { int ni; };` with no override) — has an empty
  DWARF-visible `vtable` list even though it is genuinely polymorphic, so
  the old heuristic reported `vptr_offset_bits=None` (unknown) for it
  unconditionally. Resolved via a whole-binary fixed-point pass
  (`_finalize_vptr_offsets`, run after every CU is walked, not eagerly
  per-class during the walk) that looks up the one non-virtual base placed
  at absolute offset 0 — the ABI's primary base, whose vtable pointer a
  derived class always shares — since DWARF's own per-child-DIE emission
  order does not correlate with declaration order (confirmed empirically: a
  subclass with no local vtable slot can be emitted before its own base).
  Verified end-to-end against real compiled libraries (GCC and Clang alike)
  covering plain polymorphism, multiple inheritance (confirming this model
  still only tracks the *primary* vptr — a secondary base's own vtable
  pointer at a non-zero offset, e.g. `struct C : A, B` in the reasoning
  above, is the still-open "real multi-inheritance secondary-vtable
  placement" gap this note doesn't close), a virtual base (never primary,
  so the derived class gets its own local vptr at 0), a two-level
  inheritance chain, and a non-polymorphic base reordered by the ABI to lay
  out after a polymorphic one. Checked against `diff_layout.py`'s two
  `vptr_offset_bits`-consuming detectors for a stale-baseline phantom-flip
  risk (the same class of bug G31 Phase C already found once for
  `is_standard_layout`/`is_trivially_copyable` in `_has_layout_descriptor`):
  `_check_vptr_introduced` requires the *new* side's `vtable` list to be
  non-empty too, which this fix does not change, so the newly-resolved
  inherited-only case can never trip it; `_has_layout_descriptor`'s
  `old_has`/`new_has` comparison is dominated by `size_bits`, which DWARF
  always populates for any concrete (non-opaque) class regardless of this
  fix, so no realistic phantom `LAYOUT_UNVERIFIABLE` was found. No schema or
  whole-snapshot disk-cache version bump: this DWARF-only fix doesn't touch
  the header-AST cache path (`snapshot_cache.py` caches only castxml/clang
  header dumps, not binary/DWARF extraction) and never *loses* a
  previously-known non-`None` value, only turns some `None`s into a real
  offset — the precedent that required a version bump elsewhere in this
  phase (a stale `None` being misread as a reliably-known negative answer)
  doesn't apply here. Castxml's own heuristic turned out not to share the
  DWARF backend's specific bug: its `vtable` list (`_build_vtable`/
  `_collect_virtual_methods`) is already transitively inherited across
  bases by construction, unlike DWARF's per-DIE-local list, so castxml's
  `N`-shaped case was never actually broken the way DWARF's was — leaving
  the real, still-open castxml/clang gap narrower than this section
  originally scoped it: secondary-vtable placement under multiple
  inheritance (a genuine model-schema gap, needing a new field beyond the
  single `vptr_offset_bits` scalar), not the inherited-only case this pass
  closed for DWARF.

  **A review round found a real gap in the inherited-vptr resolution
  itself, once a namespace is involved.** `RecordType.bases` stores each
  base's *bare* `DW_AT_name` (unrelated, pre-existing convention this fix
  doesn't change — every other bare-name-keyed field in this codebase does
  the same), but `self.types` — and therefore the `by_name` lookup
  `_finalize_vptr_offsets` used — is keyed by *qualified* name the moment a
  namespace or enclosing class is involved. `ns::N : ns::A`'s inherited
  vptr silently failed to resolve (stayed `None`) even though `ns::A`'s own
  offset was already known, because the lookup searched for a type named
  bare `"A"` and never found `"ns::A"` — reproduced against a real
  GCC-compiled namespaced example before fixing (Codex review). Fixed by
  resolving each inheritance edge to the base type DIE's own identity
  (`(CU.cu_offset, base_die.offset)`, matching `_type_cache`'s existing
  convention) at the point the edge is processed — while the DIE is still
  directly at hand — rather than only by name: `_resolve_base_name` became
  `_resolve_base_name_and_key`, `_collect_record_type_children` threads a
  new `base_die_keys` map alongside `bases`/`base_offsets`, and
  `_process_record_type_named` registers every built `RecordType` by its
  own DIE identity (`_record_die_index`), independent of vptr resolution,
  so a later-processed derived class can look its base up unambiguously.
  `_finalize_vptr_offsets` now resolves by DIE identity first, falling back
  to the original bare-name lookup only when a base DIE didn't resolve to a
  key at all. Verified this doesn't just fix the missing case but avoids a
  worse one: a same-bare-name base in an *unrelated* namespace
  (`other::A`, non-polymorphic, sharing the bare name `"A"` with `ns::A`)
  is never mistakenly treated as `ns::N`'s base — the DIE-identity lookup
  is exact, not a name collision waiting to happen the way the pure-name
  fallback would be on its own. New regression tests compile a real
  namespaced GCC binary covering both the resolution case and the
  same-bare-name non-confusion case.

  **The next review round on the same fix found two more real gaps in the
  DIE-identity resolution itself, both reproduced against real compiled
  binaries before fixing.** (1) **Cross-CU/declaration-only stub DIEs.** A
  base class defined in one translation unit and only *inherited from* (not
  redefined) in another is referenced, from the inheriting TU's own CU, by
  a DIE that is neither the retained definition (ODR first-definition-wins
  discarded any duplicate full definition) nor previously registered
  anywhere — GCC represents an inherited-but-not-redefined base in a
  non-defining CU as either a second full-definition DIE (discarded by ODR
  dedup) or a `DW_AT_declaration: true` stub carrying no `DW_AT_byte_size`
  (hits the pre-existing "forward declaration only" early return). Both
  shapes left the inheritance edge's `base_die_key` pointing at a DIE
  `_record_die_index` never learned about, falling through to the
  bare-name lookup and failing for a namespaced base exactly like the
  first finding. Fixed by recording, at both of those two early-return
  points, which qualified name the non-retained DIE names
  (`_register_die_qualified_name` → `_die_key_to_qualified_name`) —
  deliberately *not* resolved eagerly (the real definition is not
  guaranteed to already be known at the point a forward-reference stub is
  encountered, the identical DWARF-emission-order problem
  `_finalize_vptr_offsets`'s own docstring already documents for the
  general case), only resolved back through
  `_record_by_qualified_name` once every CU is known, at
  `_finalize_vptr_offsets` time — extending its lookup to three tiers:
  the retained DIE identity, this alias tier, then the original bare-name
  fallback. (2) **Same-bare-name collision across two *direct* bases of
  one class.** `D : one::A, two::A` — two distinct bases sharing a bare
  name in different namespaces — cannot be represented by the
  bare-name-keyed dict the fix originally used for per-edge identity at
  all: the second edge's offset and DIE key silently overwrote the
  first's, corrupting exactly the offset-0 candidate the resolution needed
  (verified: `D.base_offsets` collapsed to only `two::A`'s non-zero
  offset, losing `one::A`'s real offset 0 entirely). Fixed by replacing
  the bare-name-keyed per-edge structures with `_base_edges_by_record`, an
  *ordered list* of `(base name, this edge's own offset, this edge's own
  DIE key)` tuples — one per `DW_TAG_inheritance` child, duplicates
  preserved — so two edges sharing a name can never overwrite each other.
  `RecordType.bases`/`base_offsets` themselves are untouched (their
  pre-existing bare-name-keyed shape is a separate, unrelated convention);
  only the internal structure this fix's own resolution walks changed.
  Three more regression tests added, each compiling a real GCC binary:
  cross-translation-unit inheritance-only resolution, and same-bare-name
  colliding direct bases.

  **A fourth review round on the same fix found a real regression, not a
  missing enhancement.** `struct E : virtual A { virtual void e(); };`,
  where `A` has no data members of its own ("nearly empty" for the Itanium
  ABI's virtual-primary-base rule), can be laid out so `E` and `A` SHARE
  one vptr slot at offset 0 with GCC emitting no local `_vptr.E` member for
  `E` at all — confirmed by DIE dump, and different from every other
  virtual-base case this fix already covered (`struct D : virtual A {
  virtual void d(); int di; };` DOES get its own `_vptr.D`, since a real
  data member of its own forces a non-degenerate layout). Because `E`'s
  only base is virtual, it's entirely excluded from the primary-base walk
  (mirroring `base_offsets`'s own long-standing virtual-base exclusion —
  the offset is dynamic, not static), so `E` fell through to `None` where
  the pre-fix code — which never distinguished "resolved via a real
  mechanism" from "just assumed 0 whenever `vtable` is non-empty" — used
  to give the (here, correct) answer `0`. Fixed with a final fallback pass
  in `_finalize_vptr_offsets`, after the fixed-point resolution loop
  completes: any type still `None` with a non-empty `vtable` is set to `0`,
  restoring the original blanket heuristic exactly for the residual set
  this fix's more precise mechanisms cannot explain — a change verified to
  be strictly additive over the pre-fix behavior (every case the old code
  resolved to 0 still resolves to at worst 0; several cases it could only
  guess at now resolve to a real, DWARF-derived offset instead). New
  regression test with exactly this shape.

  **The same pass's fourth regression test pushed `test_dwarf_snapshot.py`
  over the file-size hard cap** (2045 lines against the 2000-line ERROR
  threshold, `scripts/check_ai_readiness.py`'s `file-size` check) — the
  whole `TestDwarfSnapshotVptrOffset` class (all vptr-offset coverage
  added across this fix's several review rounds) moved to a new sibling
  file, `tests/test_dwarf_vptr_offset.py`, self-contained with its own
  `g++`-availability check rather than importing across test modules
  (matching this repo's existing test-file-splitting convention for a
  large module — see `AGENTS.md`'s "Files that are large" section).

  **A fifth review round on the same fix found the immediately-adjacent
  case the fourth's fallback tier didn't cover.** `struct A { virtual void
  a(); }; struct E : virtual A {};` — `E` is polymorphic ONLY through the
  virtual base `A`, adding or overriding no virtual method of its own at
  all — has neither a local vptr member NOR any entry in its own `vtable`
  list (confirmed: `E.vtable == []`), so the fourth finding's fallback
  (gated on `rec.vtable` being non-empty) never applies here, leaving `E`
  unresolved as `None`. Unlike that finding, this one is a genuine accuracy
  improvement, not a regression fix: the pre-fix heuristic (`0 if vtable
  else None`) ALSO returned `None` for this exact shape (its `vtable` was
  always empty), so there was no prior "0" answer here to lose. Fixed with
  a second final-fallback tier: a class with `virtual_bases` and no
  resolved `vptr_offset_bits` gets `0` when at least one of its own virtual
  bases is itself already known to be polymorphic (a bare-name `by_name`
  lookup — virtual bases were never given DIE-key tracking the way
  non-virtual `base_edges` are, since a virtual base's real offset is
  inherently dynamic in the general case; this tier only ever answers "0
  or unknown," matching the same virtual-primary-base-sharing rule the
  fourth finding's own tier already documents, never a real non-zero
  offset it has no way to derive). New regression test with this exact
  `A`/`E` shape, confirming both `E.vtable` and `E.bases` are empty (only
  `virtual_bases == ["A"]`) before asserting the resolved offset.

  **A sixth review round found the fifth finding's own tier still had the
  namespace-ambiguity gap the first review round already closed for the
  non-virtual walk.** `namespace ns { struct A { virtual void a(); };
  struct E : virtual A {}; }` — the fifth finding's fallback resolved a
  virtual base by bare-name `by_name` lookup, which silently fails once a
  namespace is involved for exactly the same reason the very first
  DIE-identity fix in this section exists: `rec.virtual_bases` stores the
  bare name (`"A"`), but `self.types` is keyed by the qualified name
  (`"ns::A"`) — reproduced empirically (`ns::A.vptr_offset_bits == 0`,
  `ns::E.vptr_offset_bits` stayed `None`). Fixed by giving virtual bases
  the same DIE-identity tracking non-virtual `base_edges` already has —
  a new `_virtual_base_edges_by_record` (name + DIE key per edge, no
  offset field, since a virtual base's own subobject offset is inherently
  dynamic and this tier only ever answers "0 or unknown" regardless) — and
  factoring the three-tier resolution logic (retained DIE identity →
  ODR-duplicate/declaration-stub alias → bare-name fallback) that was
  previously inline in the non-virtual fixed-point loop into a shared
  `_resolve_base_record` closure, so both the primary-base walk and the
  virtual-primary-base fallback tier resolve a namespaced base identically
  instead of drifting into two independent (and, as this finding showed,
  unequally correct) implementations. New regression test with the exact
  namespaced repro shape.

  **A review round found the earlier "no cache bump needed" conclusion
  above was wrong, and fixed it.** The very first note on this DWARF vptr
  fix (above) concluded "No schema or whole-snapshot disk-cache version
  bump: this DWARF-only fix doesn't touch the header-AST cache path
  (`snapshot_cache.py` caches only castxml/clang header dumps, not
  binary/DWARF extraction)" — that premise turned out to be false.
  `dumper_layout_backfill.backfill_dwarf_layout()` backfills a header-AST
  (castxml/clang) snapshot's `vptr_offset_bits` from real DWARF whenever
  the header-derived value is `None`, and it runs on the ordinary,
  cacheable "binary + public headers" dump shape, not only the always-
  uncacheable `--dwarf-only` path this note originally had in mind — so
  the DWARF-side fix reaches a cacheable snapshot indirectly, through the
  backfill step, even though `dwarf_snapshot.py` itself is never on the
  cache-key-computing path. A warm cache entry from before this fix would
  have kept serving the old, less-accurate backfilled value indefinitely,
  matching the same "stale `None`/heuristic misread as a reliably-known
  answer" precedent this section already required a bump for elsewhere.
  Fixed by bumping `_SNAPSHOT_CACHE_VERSION` 8 → 9, following the
  identical v7/v8 precedent already documented in `snapshot_cache.py`
  itself.

  **A review round found the virtual-only fallback tier (the fifth/sixth
  findings above) had the same single-pass-vs-fixed-point gap the
  non-virtual primary-base walk's own loop was built to avoid.** A
  multi-level virtual-inheritance chain — `struct A { virtual void a(); };
  struct E : virtual A {}; struct F : virtual E {};`, neither E nor F
  adding a virtual method of its own — needs E resolved before F can
  resolve through it, but the fallback tier was a single
  `for rec in self.types:` pass, and `self.types` iteration order follows
  DWARF emission order, not dependency order. Reproduced empirically with
  real GCC output: `self.types` came out as `[F, A, E]`, so the single
  pass checked F while `E.vptr_offset_bits` was still `None`, resolved E
  to `0` moments later in the very same pass, and left F stuck at `None`
  with no second pass to pick it back up. Fixed by converting the tier
  into the identical `while progressed and unresolved:` fixed-point loop
  shape the primary-base walk already uses just above it in the same
  function, rather than a bespoke single pass with its own, narrower
  correctness envelope. New regression test with this exact A/E/F chain.

  **A review round flagged a real, pyelftools-confirmed risk in the base
  DIE key itself, without a real-compiler repro to back it.** The DIE key
  every tier above resolves through (`_record_die_index`,
  `_base_edges_by_record`, `_virtual_base_edges_by_record`) is built in
  `_resolve_base_name_and_key` as `(CU.cu_offset, base_die.offset)` — *CU*
  being the referencing `DW_TAG_inheritance` edge's own compilation unit,
  not necessarily the resolved *base_die*'s. `DW_FORM_ref_addr` is
  section-absolute by DWARF's own definition, so it can in principle name a
  DIE genuinely owned by a *different* CU — and pyelftools's `DIE.cu`
  always records a DIE's real owning unit regardless of which CU's
  `get_DIE_from_refaddr` happened to resolve it, so a key built from the
  wrong CU would silently miss both DIE-identity resolution tiers. Unlike
  every other finding in this section, this one does NOT reproduce against
  a real compiler: GCC (plain, `-flto`, and `-fdebug-types-section`) and
  Clang were all tried against a base class defined in one TU and inherited
  in another, and every producer kept the inheritance edge CU-local —
  always emitting its own declaration-only stub for the out-of-CU base
  rather than a genuine cross-CU `DW_FORM_ref_addr`. Fixed anyway, since
  the fix is free: for every CU-relative form (the only forms actually
  observed), the referencing die's CU and the resolved DIE's own CU are
  identical by construction, so keying on `base_die.cu.cu_offset` instead
  produces the exact same result in every case tested while closing the
  theoretical gap for a producer this investigation didn't cover.

  **A later pass closed half of the still-open vptr-placement gap: the
  direct-clang header-AST backend went from populating `vtable`/
  `vptr_offset_bits` NOT AT ALL to matching castxml's own primary-vptr
  heuristic.** Before this, `dumper_clang.py`'s `_build_record` hardcoded
  `vtable=[]` unconditionally and never set `vptr_offset_bits` — not an
  imprecise heuristic the way castxml's `0`-if-polymorphic guess is, a
  total gap, silently disabling both `vtable`-consuming detectors
  (`diff_layout._check_vptr_introduced`'s `VPTR_INTRODUCED`,
  `diff_types`'s `TYPE_VTABLE_CHANGED`) for every direct-clang-only
  comparison. Closing it turned out to need more than copying castxml's
  own mechanism, because castxml/GCC-XML tags every effectively-virtual
  method `virtual="1"` in its own XML (real semantic analysis, verified
  empirically) while clang's `-ast-dump=json` output does not: a
  `CXXMethodDecl` gets `"virtual": true` only when the `virtual` keyword
  is literally written, and an `OverrideAttr` child only when `override`
  is written — a re-declaration that writes neither (compiles fine, only
  triggers clang's own `-Winconsistent-missing-override` warning) carries
  **no signal at all** in the JSON tree, confirmed with a real
  `clang++ -Xclang -ast-dump=json` run (the equivalent *textual*
  `-ast-dump` DOES print an `Overrides: [...]` annotation for the same
  input — a JSON-serializer-specific gap, not a semantic-analysis one).
  Fixed with a new leaf module, `dumper_clang_vtable.py`
  (`dumper_clang.py` was already over its own soft line-count limit),
  reconstructing virtuality via signature matching instead: a method is
  virtual if explicitly marked, OR if its (name, parameter types,
  const-qualifier) identity — deliberately excluding the return type, so
  a covariant override still matches — equals an already-known virtual
  slot inherited from a base. A destructor needed a separate mechanism
  (verified empirically both for a user-declared and a compiler-implicit
  destructor): it's virtual the moment ANY base contributes a virtual
  destructor, regardless of keywords, and `~Base`/`~Derived` never share a
  name for signature matching to key on — resolved via a fixed sentinel
  slot key instead. Verified end-to-end against a real compiled example
  (an override with neither `virtual` nor `override` written correctly
  fires `VPTR_INTRODUCED` through the live `dump()`/`compare()` pipeline,
  not just at the unit level) plus 12 unit-level cases covering multiple
  inheritance ordering, namespaced/nested base resolution, an
  unresolvable base's graceful degradation, and the const-qualifier/
  covariant-return disambiguation. **Deliberately still open, not
  attempted in this pass**: castxml's own remaining gap (no real
  multi-inheritance secondary-vtable placement — the class-level
  refinement this phase's own "still open" note below already scoped as
  needing a model-schema change, since the current `vptr_offset_bits` is
  a single scalar tracking only the class's own *primary* vptr slot at
  offset 0, never a secondary base's own separate vtable pointer at a
  non-zero offset) applies identically to the now-fixed clang backend —
  this pass gave clang PARITY with castxml's existing primary-vptr
  heuristic, not a capability neither backend has.

  **A review round found six real gaps in the clang vtable reconstruction
  above, all fixed with real-compiler verification.** In descending order
  of severity:
  1. A forward declaration (`struct A;`) followed by its complete
     definition (`struct A { virtual void f(); };`) — a real, common
     shape confirmed with clang emitting BOTH `CXXRecordDecl` nodes for
     it — made the record index's first-registration-wins policy keep
     the empty forward-decl stub whenever it preceded the definition in
     source order, silently losing every virtual method. Fixed by
     preferring a complete definition over a forward-decl stub
     regardless of walk order (`_is_record_definition`, the same guard
     `parse_types` already uses).
  2. The ordinary unqualified spelling for a base declared in the SAME
     namespace as the derived class (`namespace ns { struct C : A {}; }`
     where `A` is `ns::A`) reports `qualType: "A"` (bare), not `"ns::A"`
     — confirmed with a real clang build, along with the equivalent gap
     for a type-alias base. Both carry the fully-qualified spelling in a
     separate `desugaredQualType` field (absent, not merely identical,
     whenever nothing needs desugaring). Fixed by preferring
     `desugaredQualType` when present.
  3. Two UNRELATED bases independently declaring an identically-signed
     virtual method (`struct D : B1, B2` where both declare
     `virtual void q();`, no inheritance relationship between them) is a
     real, compiling shape with two genuinely separate vtable-group
     slots — the first version's single signature-keyed dict collapsed
     them onto one, silently discarding one slot. Fixed by splitting
     "signature" from "physical slot identity": an ordered
     `slots: dict[key, mangled]` (one key per real vtable-group entry,
     keyed on Python object identity) plus a
     `sig_index: dict[signature, list[key]]` tracking every currently-live
     key a signature resolves to, unioned across bases rather than
     overwritten. A genuine override (`D`'s own `void q() override;`)
     correctly replaces BOTH physical keys at once — verified this
     compiles and is the valid final overrider for both per
     [class.virtual], the same "non-virtual multiple inheritance" case
     castxml's own `_resolved_override_keys` already documents.
  4. The signature key's cv-qualifier field was a single `is_const`
     boolean, so a ref-qualifier (`virtual void f() &;`) or `volatile`
     mismatch was invisible to it — confirmed both compile with distinct
     manglings, so an unrelated re-declaration could have falsely
     replaced a ref-/volatile-qualified base slot. Fixed by keeping the
     full, whitespace-normalized qualifier tail string in the key instead
     of reducing it to one boolean.
  5. A virtual conversion operator (`virtual operator int() const;`) is a
     separate clang node kind, `CXXConversionDecl`, not `CXXMethodDecl`
     — confirmed with a real clang build — so it was silently excluded
     from the vtable entirely regardless of virtuality. Fixed by handling
     both kinds identically everywhere in the walk.
  6. Top-level cv-qualification on a by-value parameter doesn't
     participate in override identity in real C++ (`virtual void
     f(const int)` and a derived `void f(int)` ARE the same override) —
     confirmed both mangle to an identical parameter-encoding tail — but
     the signature key used each parameter's raw `qualType` string
     verbatim, so this case fell through as an unrelated new method.
     Fixed with `_normalize_param_type`, stripping a top-level leading
     (`"const int"`) or trailing-after-pointer (`"int *const"`) qualifier
     word while leaving a *pointee*-level one (`"const int *"`,
     confirmed this DOES survive mangling) untouched — verified against
     six real clang parameter spellings, including the nested
     pointer-to-const-pointer case (`"int *const *"`) that a naive
     strip-anywhere approach would have corrupted.
  New regression tests for all six, each traced back to a real clang
  compile before being reduced to a hand-built fixture.

  **A review round found a confirmed-real but pre-existing limitation
  (non-virtual diamond inheritance duplicates a base subobject, giving TWO
  physical vtable-group slots for one shared virtual method) that this
  module shares symmetrically with castxml's own `_collect_virtual_methods`
  — both key a base's virtual-method slot on the base's single declaring
  AST/XML node, which is reachable-once regardless of how many derived
  paths reach it, so both backends collapse the two real slots onto one.
  Documented in `dumper_clang_vtable.py`'s own docstring rather than fixed:
  a real fix needs path-local slot identity for both backends together, and
  fixing only clang would trade one asymmetry (a total gap, now closed) for
  a different one (clang more precise than castxml on this one shape).

  **A third review round found three more real gaps, all verified against
  real clang before fixing:**
  1. A variadic base method (`virtual void g(int, ...);`) and a derived,
     genuinely unrelated fixed-arity method (`void g(int);`) report the
     IDENTICAL single `ParmVarDecl` list — the `...` is visible only inside
     the outer function `qualType` string (and in the two methods' distinct
     manglings) — so the signature key missed it entirely and let the
     unrelated method incorrectly replace the variadic slot. Fixed by
     checking the text immediately before the parameter list's closing
     paren for a trailing `...` and folding that into the signature key.
  2. A parameter typed through an alias (`using I = int; virtual void
     f(I);`) reports `qualType: "I"` with the resolved `"int"` only in a
     separate `desugaredQualType` field — confirmed both mangle to an
     identical parameter encoding (typedefs are transparent to Itanium
     mangling) — but the signature key read only `qualType`, missing this
     common shape. Fixed by preferring `desugaredQualType` for parameter
     types too, the same fix already applied to base-class names.
  3. The most consequential of the three: `dumper_clang_vtable` correctly
     recognizes a no-keyword override and replaces the inherited slot in
     the reconstructed `RecordType.vtable` — but `parse_functions()`'s own
     `Function.is_virtual` still read clang's raw, keyword-only
     `node.get("virtual")`, the exact signal this whole module exists to
     work around. Since `diff_cxx_rules.vtable_slot_is_override_reuse()`
     requires both sides' `Function.is_virtual` before recognizing a vtable
     slot as reused rather than changed, this silently undercut the
     feature's own flagship scenario: confirmed end-to-end through the live
     `dump()`/`compare()` pipeline that adding a no-keyword override (with
     no other real change) produced a spurious `TYPE_VTABLE_CHANGED`
     BREAKING finding. Fixed with a new `_virtual_mangled_names()` cache —
     every mangled name occupying a slot in any record's reconstructed
     vtable across the whole TU — consulted by `parse_functions()` to widen
     `is_virtual` from `False` to `True` (never the reverse, so purely
     additive). Re-verified the end-to-end repro is now `COMPATIBLE`
     (`func_added` only, no `type_vtable_changed`).
  New regression tests for all three.

  **A fourth review round found GNU `__restrict` was excluded from the
  override signature key — and investigating it exposed a real, deeper,
  pre-existing bug the second review round's own parameter-normalization
  fix never actually closed.** Codex's finding: `virtual void a(int*
  __restrict p);` and a derived `void a(int*) override;` mangle identically
  (`__restrict` is a hint, not part of the type), but the signature key
  read the raw `qualType` unmodified, so the override appended as a new
  slot instead of replacing the inherited one. Investigating it against
  real clang turned up the actual root cause: clang's real spelling has NO
  space between `*` and the FIRST trailing qualifier word (`"int *const"`,
  never `"int * const"`), while a SECOND stacked qualifier word IS
  space-separated from the first (`"int *const volatile"`). The earlier
  `_normalize_param_type` fix assumed every trailing qualifier had a
  leading space — which meant it silently never matched ANY single-
  qualifier pointer case at all, including the plain `"int *const"`/
  `"int *volatile"` cases that fix's own docstring claimed were handled.
  That gap went undetected because the earlier round's own regression
  tests only exercised the *pointee*-const negative-control case (`"const
  int *"`, correctly untouched) and a *leading*-qualifier positive case
  (`"const int"`, handled by a different code path entirely) — never a
  genuine `"T* const"` positive-match end to end. Fixed by normalizing the
  glued-vs-separated asymmetry up front: a new `_POINTER_QUALIFIER_GLUE`
  regex inserts a space after every `*` immediately followed by a letter,
  after which one plain trailing-word-strip loop (extended to also strip
  `__restrict`) handles every stacking combination uniformly. Re-verified
  against seven real-clang-compiled cases end to end (`int* const`, `int*
  volatile`, `int* const volatile`, `int* __restrict`, `int* const
  __restrict`, plus the two pointee-qualified negative controls) — all now
  correct, including the four that were silently broken before this round
  despite not being what Codex's own finding named.

  **A fifth review round found two more real gaps, both surfaced by chasing
  a base-lookup fix to a genuine end-to-end verification instead of
  stopping once the vtable list itself looked right.** (1) A base that is a
  CONCRETE template specialization (`struct D : A<int> {...};`) was
  entirely unresolvable: clang emits the usable definition as a
  `ClassTemplateSpecializationDecl`, a different node kind from the
  `CXXRecordDecl`/`RecordDecl` pair the base-lookup index collected, so
  `A<int>`'s own vtable was invisible to `D` — an old `D` resolved to an
  empty vtable/no vptr regardless of what `A<int>` itself provides, and a
  no-keyword override in a new `D` then made the vtable appear to gain its
  FIRST entry, a false `VPTR_INTRODUCED`. Fixed with a new
  `build_specialization_index()` (`dumper_clang_vtable.py`) that
  reconstructs the specialization's own `Name<Arg1, Arg2>` spelling from
  its `TemplateArgument` children — a type argument's own `type.qualType`,
  or a non-type argument's own `value`, joined with `", "` — confirmed
  against real clang output to exactly reproduce the base-reference
  spelling for both a namespaced two-type-argument specialization
  (`"ns::A<int, double>"`) and a non-type-argument one (`"A<3>"`); an
  unindexable specialization (template-template argument, pack expansion)
  degrades to the same already-accepted "unresolvable base" false-negative
  every other unresolvable-base shape already degrades to. (2) Verifying
  fix (1) end-to-end (not just at the vtable-list level) surfaced a
  SEPARATE, previously-unreachable gap: `owner_class_of()`'s mangled-name
  fallback recovers a specialization's *raw*, un-spelled Itanium
  template-argument encoding (`"AIiE"` for `A<int>`, confirmed empirically
  — this is `itanium_scope_components`'s own documented, deliberate
  behavior for a DIFFERENT caller's purpose), which never matches
  `RecordType.bases`'s spelled form (`"A<int>"`, built from clang's own
  type printer) — so `vtable_slot_is_override_reuse()`'s owner check failed
  even after the base resolved correctly, producing a false
  `TYPE_VTABLE_CHANGED` for the exact no-keyword-override-through-a-
  specialization-base scenario fix (1) was meant to make work. This gap was
  UNREACHABLE before fix (1) (a specialization base's vtable was always
  empty, so this owner comparison never ran) — fixing (1) alone would have
  traded one false positive for a different one on the same scenario. Fixed
  by making `ClassTemplateSpecializationDecl` scope-forming in
  `_ClangAstParser._walk` (using the same `_specialization_spelling`
  reconstruction as fix (1), so both consumers agree on one spelling) and
  qualifying a specialization-owned method's `Function.name` with that
  spelled owner in `parse_functions()` — mirroring what DWARF already does
  for every member unconditionally (`owner_class_of`'s own docstring),
  scoped here to ONLY the specialization-owned case so the already-working
  plain-class path (mangled-fallback owner resolution, verified unaffected
  by a targeted before/after repro) is untouched. `_specialization_spelling`
  and the whole-AST specialization walk were moved into
  `dumper_clang_vtable.py` (as `build_specialization_index`) rather than
  grown further inside `dumper_clang.py`, which was already within 10% of
  its 2000-line hard cap. A separate, narrower fix in the SAME review round:
  the vtable module's own override-signature qualifier-tail search used
  `qualtype.rfind(")")` to find the parameter list's closing paren — correct
  when a method carries no trailing exception specification, but a C++14+
  ref-qualified declaration with one (`"void () & throw()"`) has its OWN
  trailing `()` sitting textually LAST, so the naive search matched the
  exception spec's close paren instead and both a base `& throw()` and an
  unrelated derived `&& throw()` reduced to an identical EMPTY qualifier
  tail — discarding the ref-qualifier difference and misclassifying the
  derived declaration as an override that replaces the base's slot in
  place. Fixed with a new `_top_level_param_list_close()`, mirroring
  `dumper_clang._function_qualifiers`'s own depth-aware forward scan (find
  the first top-level `(`, then walk forward counting paren depth until it
  closes) instead of searching from the end of the string.

  **A sixth review round found two more real gaps in the same
  `build_specialization_index()`, both in what makes a specialization
  reliably indexable, not in the base-resolution mechanism itself.** (1) An
  explicit specialization can be forward-declared
  (`template<> struct A<int>;`) before its complete definition
  (`template<> struct A<int> { ... };`) — both emit their own
  `ClassTemplateSpecializationDecl` node sharing the IDENTICAL `"A<int>"`
  spelling, confirmed with a real clang build. The index's
  first-registration-wins insertion permanently kept the empty
  forward-decl stub whenever it was walked first — the same
  forward-decl-shadows-definition shape `_record_index()` already guards
  for an ordinary record, reproduced one level up for a specialization.
  Fixed by applying the identical "a complete definition always wins"
  tie-break, via the shared `_is_record_definition` (moved into
  `dumper_clang_vtable.py` so both indexes can use it). (2) A non-type
  template argument's raw JSON `value` does not always print the same way
  a base reference spells it: `template <bool B> struct A; ... A<true>`
  reports `value: -1` (confirmed empirically — not even `1`), while the
  base reference still spells it `"A<true>"`, so the previous
  reconstruction fabricated an `"A<-1>"` key that could never match — an
  old `D : A<true>` stayed unresolvable while a new side adding a
  no-keyword override made it appear to gain its first vptr, the exact
  false `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED` shape this whole index was
  built to prevent. Fixed conservatively rather than attempting a general
  non-type-argument printer: a new whole-AST pass
  (`_index_template_param_kinds`) records, per `ClassTemplateDecl`, which
  positional non-type parameters are declared with one of a small,
  CONFIRMED-safe set of plain builtin integer types (`int`, `unsigned
  int`, `long`, ... — verified these DO round-trip: `A<3>`'s `value: 3`
  matches the base reference's `"A<3>"` exactly); `_specialization_spelling`
  now consults this by position and returns `None` for the WHOLE
  specialization the moment any non-type argument's parameter isn't
  confirmed safe (`bool`, an enum, a pointer, floating-point, structural
  C++20 arguments — none of these have any reason to share the plain-int
  round-trip property, and none were verified to). Both fixes verified
  end-to-end through the live `dump()`/`compare()` pipeline in addition to
  unit fixtures: the forward-decl case now correctly resolves and reports
  no findings; the `bool` case now correctly degrades to `D.vtable == []`
  on both sides (unresolvable, matching every other unresolvable-base
  shape) rather than fabricating a false break.

  **A seventh review round found two more real gaps, plus a memoization
  fix, all confirmed against real clang output.** (1) A dynamic exception
  specification (`throw(int)`, `throw()`) participated in the override
  qualifier tail unstripped — only `noexcept` was — so a base `virtual
  void f() throw(int);` overridden by a derived `void f() throw()
  override;` (a legal C++14-and-earlier narrowing, confirmed compiling
  with real clang) compared as a different signature and appended a
  spurious second slot instead of replacing the inherited one. Fixed by
  cutting the qualifier tail at whichever of `noexcept`/`throw` appears
  first, alongside the pre-existing `noexcept` handling. (2) A
  specialization always carries a `TemplateArgument` for EVERY parameter,
  including one a base reference omitted because it equals its own
  default — `template <class T, class U = int> struct A; struct D :
  A<double> {...};` reports arguments for BOTH `T` and `U`, confirmed with
  a real clang build — so joining all of them unconditionally produced
  `"A<double, int>"`, which never matches the referring site's own
  `"A<double>"`. Fixed with a new whole-AST pass
  (`_index_template_param_defaults`, mirroring `_index_template_param_kinds`'s
  own shape) recording each TYPE parameter's own default spelling
  (`defaultArg.type.qualType`); `_specialization_spelling` now pops
  trailing arguments that exactly equal their own parameter's default
  before building the key. Verified this reproduces clang's own canonical
  spelling for BOTH an omitted default and one explicitly repeated with
  the identical value: `struct D : A<double, int> {...}` reports
  `type.qualType == "A<double, int>"` (as literally written) but
  `type.desugaredQualType == "A<double>"` (defaults collapsed) —
  `_base_qualnames` already prefers `desugaredQualType`, so both shapes
  resolve to the identical trimmed spelling this collapses to. Fixing (2)
  surfaced a THIRD, narrower gap in the specialization-owner-qualification
  fix from the fifth review round: `dumper_clang.py`'s own `_walk` call
  site passed no `param_kinds`/`param_defaults` context to
  `_specialization_spelling` at all (only the base-lookup index's own call
  did), so a confirmed-safe non-type specialization (`A<3>`) or a
  defaulted-argument one still left `Function.name` bare — reintroducing
  the exact owner-mismatch false `TYPE_VTABLE_CHANGED` that fix was built
  to prevent, just via a different argument shape. Fixed by computing both
  indices once, eagerly, in `_ClangAstParser.__init__` (before `_walk`
  runs, since `_walk` itself is what needs them) and threading them into
  both call sites; `build_specialization_index()` now accepts these as
  optional parameters so the eager computation isn't paid for twice.
  Separately, `_base_lookup_index()` was rebuilding its merged dict on
  every call — `_build_record` calls it once per record in the TU, so this
  was an O(records × index size) cost; now memoized the same way the other
  per-parse indices already are.

  **An eighth review round found two more real gaps, both in the
  unresolvable-base degradation path itself rather than in resolving a
  new argument shape.** (1) A class deriving from an unresolvable
  specialization (e.g. the `bool` non-type case above) is correctly
  invisible to the reconstruction, but an own member carrying an
  EXPLICIT `virtual`/`override` keyword was still unconditionally added
  as a brand-new slot, since nothing recognized it as a possible override
  of something in the invisible base. Confirmed end-to-end: an old `D :
  A<true> {}` (empty vtable) and a new `D` adding only `void f()
  override;` produced a real `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED`
  false positive — the module docstring's own "known limitation" section
  had claimed this shape was "never a false positive," which was true
  only for an IMPLICIT (no-keyword) override; an explicit one was
  reachable as a genuine false positive the whole time. Fixed by tracking
  whether ANY of a record's own bases failed to resolve to a node at all,
  and suppressing any new (non-candidate-matching) own slot -- explicit
  or not -- when so: ambiguous whether such a member is a genuine
  addition or an invisible override, and this module's established
  posture is to prefer that accepted false negative over a false
  positive. Deliberately coarse (per-record, not per-method) — a
  genuinely unrelated new virtual on the same class is silently
  suppressed too, since there's no way to tell the two cases apart from
  available data; documented with its own regression test rather than
  left implicit. (2) A template default that depends on an EARLIER
  parameter (`template <class T, class U = T> struct A;`) reports the
  literal, unsubstituted default spelling (`"T"`), which never equals a
  real resolved argument (`"double"`) by plain string comparison — the
  identical false-positive shape the plain-default-collapse fix closed
  one review round earlier, just for a dependent default instead of a
  literal one. Fixed with a new `_index_template_param_names`
  (mirroring `_index_template_param_kinds`/`_index_template_param_defaults`'s
  own shape) recording each parameter's own bare name; when a trailing
  argument's default spelling exactly matches an EARLIER parameter's
  name, it's substituted with that parameter's own already-resolved
  argument before comparing — always safe, since a dependent default can
  only name an earlier parameter, never itself or a later one. Anything
  more complex (a default only partially referencing an earlier
  parameter, e.g. `U = Wrapper<T>`) is conservatively left unsubstituted
  rather than guessed at. Both fixes verified end-to-end through
  `dump()`/`compare()` in addition to unit and integration tests.

  **A later pass closed the last of this phase's four originally-listed
  facts** (`deprecated`/`is_scoped`/bitfields/default-argument facts were
  the other three, already covered above): direct-clang now populates
  `TypeField.default` too, via `dumper_clang_expr._field_initializer_value`,
  verified against real Clang 18 `-ast-dump=json` output before wiring it
  up. The one real trap found and fixed while verifying: a `FieldDecl`'s
  `inner` list is overloaded — a **bitfield width** is nested there as a
  `ConstantExpr` too (`int bf : 3;` has no initializer at all, but nests
  exactly one `ConstantExpr` child, structurally identical to what an
  initializer expression looks like). Presence is taken from clang's own
  `hasInClassInitializer` flag (present-only-when-true, matching
  `scopedEnumTag`'s convention) rather than "any non-attribute child" the
  way `_param_has_default` reads a `ParmVarDecl` — that heuristic would
  have fabricated an initializer of `3` for `bf` above. Unlike
  `deprecated`/`is_scoped` (directly cross-comparable values — a plain
  message string / bool), `TypeField.default`'s two backends are
  cross-*producer* without being cross-*comparable*: castxml keeps the
  verbatim source expression, clang falls back to a literal or a
  structural fingerprint for anything non-trivial — the same shape
  `Param.default` already has. So its detector
  (`_diff_field_default_initializer`) needed the SAME-producer gate
  `_diff_param_defaults` already uses (`fact_provenance.
  same_producer_backed_fact_qualified`, `diff_helpers.
  fact_same_producer_qualified`), not the any-known-producer gate
  (`both_known_backed_fact`) `deprecated`/`is_scoped` correctly use — using
  the wrong one here would compare castxml's source text against clang's
  fingerprint and read every initializer as changed. Two follow-on
  bookkeeping gaps closed in the same pass, both exactly the shape prior
  rounds on this same fact-completeness effort already found for
  `deprecated`/`is_scoped`: (1) schema bumped to **v20**
  (`AbiSnapshot.clang_field_initializer_facts_reliable`, mirroring
  `clang_deprecation_facts_reliable`'s v19 shape one version and one fact
  later — tracked as its own flag since a v19 snapshot has reliable
  deprecated/is_scoped but unreliable field defaults) and the whole-snapshot
  disk cache bumped to **v8**; (2) the hybrid merge's field `default`
  provenance key is now namespace-qualified (previously deliberately kept
  bare, since it never got a clang-only-append write — that exemption no
  longer holds now that a clang-only field's `default` is stamped too), with
  the existing bare-key fallback covering a pre-fix persisted hybrid
  baseline. Verified end-to-end against a real compiled library through the
  actual `dump()`/`compare()` pipeline
  (`test_clang_header_backend_integration.py`), not just at the
  parser-unit or hand-built-snapshot detector level.

  **Two more real gaps found and fixed in the same pass** (Codex review,
  fresh evidence, both confirmed against real code before fixing): (1) the
  producer-gating fix above only stamps `fact_provenance` for a declaration
  actually routed through `_merge_function`/`_merge_record_type`/
  `_merge_enum_type`/`_merge_variable` — a declaration present on BOTH
  snapshot sides but ONLY ever via the clang leg (absent from castxml
  entirely) is appended to the merged result verbatim, without going
  through those merge functions, so its `deprecated`/`is_scoped` fact keys
  were never recorded at all; `fact_producer()` then returned `None` for a
  fact that genuinely IS known (clang-sourced), and
  `both_known_backed_fact` incorrectly declined to compare a real
  transition on it. Fixed by stamping `provenance[...] = "clang"` for every
  clang-only function/type/field/enum/variable appended in
  `merge_snapshots()`, verified end-to-end (a clang-only function gaining
  `[[deprecated]]` between old and new now correctly fires
  `FUNC_DEPRECATED_ADDED` through the real `compare()` pipeline, not just
  provenance-map inspection). (2) `merge_snapshots()` itself matched
  castxml/clang record types and enums by bare `name` (`{t.name: t for t in
  clang_snap.types}`), same collision class as `diff_layout._index()`
  above — two distinct types/enums sharing only a bare leaf name in
  different namespaces could merge against the wrong counterpart, or a
  genuinely clang-only type get silently dropped as "already present."
  Fixed by keying on `diff_helpers.type_map_key` (namespace-qualified
  identity) for the MATCH, while keeping `fact_provenance`'s own keys
  bare-name (unchanged — that's what the detectors themselves query by).
  `diff_layout._index()` itself was rewritten to build a real
  `diff_helpers.TypeMap` (via `build_type_map`/`lookup_matched_type`)
  instead of a plain `{rec.name: rec}` dict, for the identical reason —
  this pre-existing collision already affected `base_offsets`/
  `vptr_offset_bits` (live via castxml for a while), it just had no visible
  consequence until `is_standard_layout`/`is_trivially_copyable` started
  being populated for real and made the collision reachable through a
  realistic scenario a reviewer could construct.

  **A narrower, related gap found while testing the fix above, deliberately
  NOT fixed in this pass**: two *distinct*, correctly-matched records that
  independently undergo the identical boolean-trait transition (e.g. both
  `a::Foo` and `b::Foo` separately lose `is_trivially_copyable` between old
  and new) can still have their findings collapse from two to one --
  `_check_trivially_copyable_lost`/`_check_standard_layout_lost` build
  `Change.symbol`/description from the bare `name` alone (matching every
  other bare-name-keyed detector's own `symbol=` convention, e.g.
  `diff_types.py`'s "Bare, not the qualified matching key" comment), so two
  distinct records sharing a bare name produce byte-identical `(kind,
  description)` pairs, and `diff_filtering._dedup_exact` correctly (by its
  own existing rule) treats that as one duplicate finding. This is real —
  confirmed with a same-transition test reproducing the collapse — but is a
  different, narrower failure mode than the matching bug above (a genuinely
  *mis-attributed* finding vs. a *legitimately-deduped-looking* one that
  happens to be wrong here), it is not new in this pass (every existing
  bare-name-keyed `Change.symbol` in the codebase has the identical
  exposure whenever two distinct declarations share a bare name and
  produce identical description text), and a real fix (threading
  qualified identity into `Change.description`/a new disambiguating field,
  or reworking `_dedup_exact`'s key) has a blast radius across every
  bare-name-keyed detector in `diff_types.py`/`diff_symbols.py`/
  `diff_layout.py` alike — its own scoped follow-up, not a drive-by
  extension of the matching fix. Re-flagged by Codex in a later review
  round on the same PR; no new code fix applied (the reasoning above still
  holds — a real fix needs the codebase-wide `Change.symbol` convention
  change, not a local patch), but `diff_layout._diff_layout_descriptor`'s
  own `name = new_rec.name` line now carries an inline comment stating this
  deferral directly at the source, so a future reader doesn't have to find
  this plan doc to rediscover it.

  **Two more real gaps found in the very next review round on the same
  fix** (Codex review, fresh evidence): both are cross-producer schema
  bookkeeping gaps, not detector logic bugs. (1) `SCHEMA_VERSION` was not
  bumped alongside wiring real clang-side `deprecated`/`is_scoped`
  extraction — following the same precedent `header_cv_facts_reliable`
  (schema v9) already established for the mirror-image castxml case: a
  snapshot serialized under an *older* schema version, on the clang header
  path, has `deprecated`/`is_scoped` values that were never actually
  extracted (always `None`/absent from before this fix), and reloading
  that JSON without a version gate would let `fact_producer()` claim those
  stale `None`s are a reliably-known "not deprecated"/"not scoped" answer
  — a real cross-producer detector could then fire a false
  `FUNC_DEPRECATED_ADDED`/-`REMOVED` purely from re-loading old data, not a
  genuine transition. Fixed by bumping `SCHEMA_VERSION` to **19** and
  adding a new `AbiSnapshot.clang_deprecation_facts_reliable: bool` field
  (default `True`, mirroring `header_cv_facts_reliable`'s exact shape) plus
  a `_MIN_SCHEMA_VERSION_FOR_CLANG_DEPRECATION_FACTS = 19` gate in
  `serialization.snapshot_from_dict` — a legacy clang-header snapshot
  predating v19 loads as unreliable (prefers the explicit dict key on
  round-trip, same "don't silently heal an already-known-unreliable
  reserialized snapshot" precedent). `fact_provenance.fact_producer()`
  gates on it narrowly, by key suffix (`:deprecated`/`:is_scoped` only,
  not `:param_defaults` or any other fact), so the two flags stay
  independent. Deliberately *not* needed for castxml or hybrid: castxml's
  own extraction has been reliable since G28 Phase 1 (well before this
  PR), and `merge_snapshots()`'s pre-fix backfill always stamped these two
  facts' `fact_provenance` as `"castxml"` even under the old code, so no
  legacy hybrid snapshot can carry a stale clang-attributed answer for
  them. (2) `tests/test_castxml_clang_parity_gate.py`'s
  `test_deprecated_attribute_is_expected_producer_difference` still
  asserted the *pre*-Phase-C behavior (`Parity.UNSUPPORTED_ON_ONE_PRODUCER`
  from `unsupported_on_clang=True`) — a real, `integration`-marked CI
  failure only surfaced once the `integration-tests` job actually ran real
  castxml+clang+gcc (not reproducible in a sandbox lacking those tools;
  found by reading the CI job's own log output). Fixed by renaming it to
  `test_deprecated_attribute_now_agrees_across_producers` and asserting
  `Parity.EQUAL` on a plain `classify()` call with no `unsupported_on_clang`
  flag, plus updating `classify()`'s own docstring (which still listed
  `Function.deprecated` in its "clang structurally cannot populate this"
  example list) to note the capability gap closed and callers should no
  longer pass the flag for this fact.

  **A third review round found the qualification fix (finding 2 of this
  round's follow-ups) was one layer too shallow.** The `type_map_key`-based
  matching fix makes old/new MATCHING namespace-aware, but
  `dumper_hybrid.py`'s `fact_provenance` dict — a single flat
  `dict[str, str]` shared across every merged/appended declaration — still
  keyed its `deprecated`/`is_scoped` entries by bare declaration name
  (Codex review, fresh evidence). Two distinct types sharing only a bare
  leaf name in different namespaces (e.g. a castxml+clang-matched `a::Foo`
  and a genuinely clang-only `b::Foo`) write to the exact same provenance
  dict key, so one writer's entry silently overwrites the other's —
  independent of the matching fix, since this collision is in the shared
  write-side dict, not in old/new lookup. Confirmed real: this class of
  write only became reachable once the clang-only-declaration
  provenance-stamping fix (finding 2 above) started writing "clang"
  entries for facts a bare-name-colliding, castxml+clang-matched sibling
  might also be writing "castxml" entries for — before that fix, a
  clang-only declaration wrote no provenance entry for these facts at
  all, so there was nothing to collide with. Fixed by qualifying exactly
  the affected keys (`type`/`field`/`enum` `deprecated`, and `enum`
  `is_scoped` — the four facts Phase C's `both_known_backed_fact` gate and
  clang-only-append writes actually touch) with
  `diff_helpers.type_map_key()` in both `dumper_hybrid.py`'s writers
  (`_merge_record_type`/`_merge_field`/`_merge_enum_type`, and the three
  clang-only append loops) and `diff_types.py`'s four reader call sites —
  while deliberately leaving `RecordType.is_abstract` and
  `TypeField.default` (the two pre-existing, castxml-only facts
  `_merge_record_type`/`_merge_field` also handle) on their original bare
  keys, since neither gets a clang-only-append write and qualifying them
  would be an unrelated, unverified change outside this finding's scope.
  `Change.symbol`/description stay bare throughout — only the internal
  provenance-dict key changed.

  **A fourth review round found the qualification fix itself created a
  backward-compatibility regression.** A `--ast-frontend hybrid` baseline
  persisted *before* this qualification landed has real
  `deprecated`/`is_scoped` provenance recorded under the *former* bare
  key — `_backfill_fact` always records provenance for a matched
  declaration regardless of the fact's actual value, and castxml has
  populated `deprecated`/`is_scoped` since long before G31 Phase C, so
  this isn't a hypothetical: any existing hybrid baseline with a
  namespaced type has real `type:Foo:deprecated -> "castxml"`-shaped
  entries (Codex review, fresh evidence). Once `diff_types.py`'s readers
  started requesting the qualified key, that legacy data stopped
  matching — `fact_producer()` returns `None` for it, silently
  suppressing a genuine transition (a conservative false negative, not a
  false positive, but a real regression in comparison coverage for any
  existing persisted hybrid baseline). Fixed with the same shape
  `lookup_matched_type`'s own bare-name retry already uses for old/new
  type matching (PR #608): a new `diff_helpers.fact_known_qualified()`
  tries the qualified key first, falling back to the bare key only when
  the caller's own `TypeMap.bare_name_is_unambiguous(name)` confirms no
  *other* distinct qualified identity in that side's snapshot shares the
  bare name — otherwise the fallback would reopen the exact collision the
  qualification was introduced to close. `fact_provenance.
  both_known_backed_fact_qualified()` is the underlying old/new pair
  check; `fact_known_qualified()` (home: `diff_helpers.py`, alongside
  `lookup_matched_type` — the identical bare-name-retry pattern applied to
  a fact-provenance key instead of an old/new type match, and outside
  `diff_types.py`'s own 2000-line hard cap) derives the two
  `TypeMap`-backed ambiguity flags so the four call sites in
  `diff_types.py` (type/field/enum `deprecated`, enum `is_scoped`) stay a
  single line each.

  **A fifth review round found the bare-key-fallback fix itself was
  probing both sides with only ONE side's qualified key.** The fallback
  fix's first cut built a single `qualified_key` from `t_old`/`e_old`
  alone and reused it for the NEW-side lookup too — correct only when
  both sides happen to share the same qualified identity, which isn't
  guaranteed for a matched pair: a genuinely legacy `old` snapshot that
  predates `qualified_name` entirely has `type_map_key(t_old) ==
  t_old.name` (bare), while a freshly-merged `new` snapshot for the same
  namespaced declaration has a real qualified `type_map_key(t_new)` —
  probing `new`'s provenance dict with `old`'s bare-shaped key can never
  find `new`'s real, qualified-keyed entry (Codex review, fresh evidence,
  third round). Fixed by threading `old_qualified_key`/`new_qualified_key`
  through separately (`fact_provenance.both_known_backed_fact_qualified`,
  `diff_helpers.fact_known_qualified`), each side's own `type_map_key()`
  derived from its own matched declaration (`t_old`/`t_new`,
  `e_old`/`e_new`) rather than one shared string — the shared `bare_key`
  fallback stays a single value, since a matched pair's bare declaration
  name is the same on both sides by construction (that's how the pair
  matched in the first place).

  **A sixth review round found a real, wide-blast-radius false positive
  independent of the fact_provenance qualification chain above — this one
  in `diff_layout.py`'s `LAYOUT_UNVERIFIABLE` heuristic.** Since G31 Phase
  C the direct-clang backend populates `is_standard_layout`/
  `is_trivially_copyable` (semantic traits) independent of any real
  layout pass — `dumper_clang.py` never sets `size_bits`/`data_size_bits`/
  `vptr_offset_bits`/`base_offsets` without the optional
  `ABICHECK_CLANG_LAYOUT_TOOL` companion (confirmed:
  `dumper_clang.py`'s own module docstring states this explicitly, and
  its `RecordType` construction hardcodes `size_bits=None`). But
  `diff_layout._has_layout_descriptor()` counted these two semantic
  traits as "layout descriptor evidence," so a persisted pre-v19
  direct-clang snapshot compared against a fresh dump of UNCHANGED
  headers had the traits flip from `None` to a real value on the new
  side alone — that flip alone made `_check_layout_unverifiable()`'s
  `descriptor_in_play`/`old_has != new_has` gate trip and fire a phantom
  `LAYOUT_UNVERIFIABLE` RISK finding for *every record*, purely from a
  tool/schema upgrade, not a real change (Codex review, fresh evidence).
  Confirmed `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST` themselves
  were NOT affected — they already self-gate correctly on this exact
  asymmetry (`old_rec.is_X is True` requires a real old value, so `None`
  on the old side stays silent); only the cruder "did any layout evidence
  appear/disappear" heuristic was affected, since it conflated "we now
  know a semantic trait" with "we now know the type's actual
  size/offsets" — two different kinds of evidence. Fixed by excluding
  `is_standard_layout`/`is_trivially_copyable` from
  `_has_layout_descriptor()`'s definition entirely — it now answers only
  "does `rec` carry real size/offset layout evidence," which is what
  `LAYOUT_UNVERIFIABLE` is actually about. One existing test
  (`test_layout_unverifiable_on_asymmetric_evidence`) had accidentally
  relied on the now-corrected coupling (using `is_standard_layout=True`
  as its stand-in for "a layout descriptor is present"); updated to use
  `data_size_bits` (genuine layout-pass evidence) instead, preserving the
  scenario it was actually testing.

  **The same review round found two more, independent gaps.** (1)
  `snapshot_cache.py`'s whole-snapshot disk cache
  (`_SNAPSHOT_CACHE_VERSION`, separate from `AbiSnapshot.SCHEMA_VERSION`
  — see that constant's own docstring) was not bumped alongside this PR's
  new direct-clang extraction: an upgrading user's warm clang/hybrid
  cache entry, keyed on the same headers/includes/version/lang/`extra`
  inputs a pre-upgrade dump already covers, would keep replaying the old
  snapshot (missing `deprecated`/`is_scoped`/`is_standard_layout`/
  `is_trivially_copyable`, or — for a hybrid entry — retaining stale
  bare-keyed `fact_provenance`) until the entry happened to expire or was
  manually cleared, silently suppressing every detector this PR wires up
  (Codex review, fresh evidence). Bumped to v7, following the same
  documented precedent as v2/v3/v4/v6's identical "behavior changed
  without changing the cache key" bumps. (2) `diff_layout._index()`'s
  stdlib exclusion filtered on `rec.name` (bare) rather than
  `rec.qualified_name or rec.name` (the same `identity` split
  `diff_types._is_abi_surface_type` already uses) — castxml/clang keep
  `RecordType.name` bare (e.g. `"vector"`) and carry the real namespace
  in `qualified_name` (e.g. `"std::vector"`), so the bare-name filter
  never actually matched the `std::`/`__gnu_cxx::`/etc. prefix. A
  retained dependency-header stdlib record could therefore leak into
  `diff_layout.py`'s public surface and fire `STANDARD_LAYOUT_LOST`/
  `TRIVIALLY_COPYABLE_LOST` for a toolchain-owned type once those two
  traits started being populated for real (G31 Phase C) — the exact
  toolchain-noise-vs-real-break distinction the stdlib filter exists to
  draw. Fixed to match `_is_abi_surface_type`'s identity split exactly.

  **A ninth review round found the exact same class of legacy-baseline
  false positive one more time, this time for the clang vtable
  reconstruction feature itself (Codex review, fresh evidence, real
  end-to-end repro against a persisted schema-v20 direct-clang
  snapshot).** The vtable/vptr reconstruction (`dumper_clang_vtable.py`)
  made `RecordType.vtable`/`vptr_offset_bits` real for the first time on
  the direct-clang backend — before it, EVERY record read `vtable=[]`/
  `vptr_offset_bits=None`, unconditionally, regardless of the class's real
  polymorphism. Exactly the same shape as the `is_standard_layout`/
  `is_trivially_copyable` false positive above and the `deprecated`/
  `is_scoped`/`TypeField.default` false positives before it: a persisted,
  pre-fix clang snapshot's blanket-empty vtable is real but WRONG data for
  an already-polymorphic class, indistinguishable by value alone from a
  genuine non-polymorphic class — so comparing it against a fresh dump of
  the SAME, unchanged headers read as every polymorphic class gaining its
  first vptr (confirmed: `struct A { virtual void f(); };` unchanged,
  `VPTR_INTRODUCED` fired). Worse than the semantic-trait case: the same
  legacy-vs-fresh asymmetry also reaches `TYPE_VTABLE_CHANGED` (a vtable
  differing in slot count between the two sides) and
  `_has_layout_descriptor()`'s `vptr_offset_bits is not None` check — the
  identical `LAYOUT_UNVERIFIABLE` phantom the sixth review round fixed for
  the two semantic traits, now reachable through the vptr field this
  reconstruction feature newly populates. Fixed with the same
  established pattern: schema bumped to **v21**, a new
  `AbiSnapshot.clang_vtable_facts_reliable` marker (mirroring
  `clang_deprecation_facts_reliable`'s shape — clang-producer-only, not
  hybrid, since the reconstruction lives entirely in
  `dumper_clang_vtable.py`, a module the hybrid merge path never invokes),
  threaded into `_diff_type_vtable` (declines `TYPE_VTABLE_CHANGED`
  entirely when unreliable), `_check_vptr_introduced` (declines
  `VPTR_INTRODUCED` entirely), and `_has_layout_descriptor` (excludes
  `vptr_offset_bits` from the evidence check when unreliable, mirroring
  the sixth round's semantic-trait exclusion). Verified end-to-end through
  `compare()` for all three findings, plus a positive-control test
  confirming the bug reproduces when both sides claim reliable facts (so
  the suppression isn't just masking an already-inert case).

  **The same round found a second, independent gap in the reconstruction
  itself: an uninstantiated template method's bare fallback name collided
  with an unrelated `extern "C"` free function's real mangled name.** A
  template method with no `mangledName` at all falls back to its bare
  `name` (e.g. `"f"`) as the vtable slot's identity in
  `_collect_virtual_slots`; a free `extern "C"` function sharing that same
  bare name mangles to the identical string by C-linkage design. Since
  `parse_functions()`'s inferred-virtuality recovery
  (`_virtual_mangled_names()`) is a plain string-membership test with no
  owner/kind context, the free function was incorrectly widened to
  `is_virtual=True` purely from the name collision — confirmed with a real
  clang dump of `template<class T> struct A { virtual void f(); };
  extern "C" void f();`, where both `f`s share the identical unmangled
  fallback string. Fixed by restricting the widening to actual
  member-function declaration kinds (`kind != "FunctionDecl"`) — only a
  class member can be virtual in C++ at all, and `_collect_virtual_slots`
  only ever walks member kinds when building the set this check consults,
  so a bare `FunctionDecl` can never legitimately appear in it.

  **A tenth review round (post-merge, on the follow-up branch) found two
  more real gaps in `build_specialization_index()`'s own indexing, both
  confirmed with real Clang 17 output.** (1) When EVERY template argument
  of a specialization equals its own default, the trailing-default-pop
  loop in `_specialization_spelling` emptied `args` entirely and returned
  `None` (unresolvable) — but clang always prints an explicit, empty
  angle-bracket pair (`"A<>"`) on the base reference, never a bare `"A"`
  (confirmed: `template<class T=int> struct A {...}; struct D : A<>
  {...};` gives `bases[0].type.qualType == "A<>"`). Unlike a genuine
  no-arguments case (a template-template argument, pack expansion, or a
  zero-parameter template — where returning `None` is correct), every
  argument here IS safely known; only the resulting joined spelling
  happens to be empty. Fixed by returning `f"{name}<>"` specifically for
  the popped-to-empty case, leaving the earlier, genuinely-no-arguments
  check untouched. (2) `build_specialization_index()`'s own whole-AST walk
  descended into a specialization's children using its bare `name`, not
  its reconstructed spelling — since `ClassTemplateSpecializationDecl` is
  deliberately not in `_SCOPE_NODE_KINDS` (it isn't an ordinary namespace/
  class/linkage-spec scope), a NESTED specialization inside it
  (`struct D : Outer<int>::A<double>`) indexed as bare `"A<double>"`
  instead of `"Outer<int>::A<double>"`, leaving the base's vtable
  completely invisible. This is the exact same shape `dumper_clang.py`'s
  own `_walk` already fixed for scoping a specialization's OWN MEMBERS
  (see the earlier P1 finding on this file) — that fix never reached this
  sibling walk, which builds the base-lookup index rather than member
  scopes. Fixed identically: `child_scope = (*scope, spelling) if
  spelling else scope` for a `ClassTemplateSpecializationDecl` node, ahead
  of the generic `_SCOPE_NODE_KINDS` branch. Both verified end-to-end
  through `dump()`/`compare()` against real compiled GCC binaries, plus
  hand-built unit fixtures.

  **An eleventh review round found two more real gaps in the SAME
  bare-qualname lookup convention, plus one confirmed-safe recurrence of
  the same accepted limitation reached through a different AST shape.**
  (1) When TWO DIFFERENT explicit outer specializations each define their
  OWN same-named nested member template with DIFFERING defaults
  (`Outer<int>`'s `template<class U=int> struct A` vs. `Outer<double>`'s
  `template<class U=double> struct A`), both register under the identical
  bare qualname `"A"` in `_index_template_param_kinds`/`_defaults`/
  `_names`, since none of those three functions ever extend scope through
  a `ClassTemplateSpecializationDecl`. First-registration-wins previously
  trusted whichever was visited first, silently borrowing the WRONG
  default for the other — confirmed end-to-end this left the base
  unresolvable and a real virtual-method addition completely undetected
  (`NO_CHANGE`). Fixed by treating a conflicting second registration as
  genuinely ambiguous and dropping it entirely (in all three index
  functions, via a new shared `_register_template_param_metadata()`
  helper), rather than trusting either candidate — degrading to this
  module's usual unresolvable-base false negative (safe) instead of
  fabricating a wrong resolution (unsafe). (2) That fix was immediately
  too broad: an ordinary, LEGAL C++ redeclaration of the SAME template
  with renamed parameters (`template<class T, class U=T> struct A;`
  followed by `template<class X, class Y> struct A {...};`) is one
  entity, not two — but the two declarations' own parameter NAME lists
  legitimately differ (`["T","U"]` vs `["X","Y"]`), which finding (1)'s
  guard treated as a genuine conflict and dropped, breaking dependent-
  default substitution for this ordinary shape (confirmed end-to-end:
  masked a real virtual-method addition as `NO_CHANGE` too). Distinguished
  the two using clang's own `previousDecl` link, confirmed empirically to
  be present on every legal redeclaration and absent between two
  genuinely unrelated declarations that coincidentally share a bare
  qualname. A secondary gap surfaced while implementing this: comparing
  `None == None` when neither node carries a real `id`/`previousDecl`
  (reachable for a hand-built test fixture without them) spuriously
  matched — guarded with an explicit truthiness check.

  **The same round's third finding is real but confirmed to be the
  IDENTICAL accepted degradation as (1), not a new false positive.** When
  one member template `Outer<T>::A<U=T>` is IMPLICITLY instantiated for
  both `Outer<int>` and `Outer<double>` (rather than explicitly
  specialized), clang emits cloned `ClassTemplateDecl A` nodes with
  substituted defaults (`int`/`double`) but — confirmed empirically — NO
  `previousDecl` link between them (they are independent implicit
  instantiations of the same primary pattern, not redeclarations of each
  other). This reaches the exact same bare-qualname collision as finding
  (1), and (1)'s fix correctly treats it as ambiguous and drops it. Traced
  end-to-end through `dump()`/`compare()` with the reviewer's exact repro:
  both `Outer<int>::A<>` and `Outer<double>::A<>` stay symmetrically
  unresolvable (empty vtable on old AND new sides), producing zero
  findings — not a false positive, the same false negative this whole
  degradation path already accepts. The reviewer's suggested fix (key
  metadata by the instantiated outer scope) is exactly what a prior round
  already declined as its own larger, separate change: it would need
  `_index_template_param_kinds`/`_defaults`/`_names` to key entries by
  (bare qualname, enclosing specialization identity) rather than bare
  qualname alone, threaded consistently through BOTH consumers
  (`build_specialization_index`'s own base-lookup walk AND
  `dumper_clang.py`'s `_walk` owner-qualification path) — real,
  bounded work, but a genuine architectural change to the shared index
  functions' key shape, not a drive-by extension of the ambiguity guard.
  Left as a documented, tracked limitation rather than attempted here.

  **A later pass closed one more fact-completeness gap — `Param.is_restrict`
  — and it was found by building Phase D's capability matrix rather than by
  reading this list.** The matrix's own drift check (an `ast` pass over both
  parsers, see Phase D below) reported a divergence this plan had never
  recorded: castxml populated `Param.is_restrict` from the day it shipped
  (`_resolve_cv_restrict`), the clang backend never did, and unlike
  `deprecated`/`is_scoped` before this phase, `_diff_param_restrict` had
  **no producer gate at all** — it compares the two bools directly. So a
  castxml-vs-clang comparison of unchanged headers reported
  `param_restrict_changed` for every `restrict`-qualified parameter, and had
  since the fact was introduced. Closed the same way this phase closed
  `deprecated`/`is_scoped`: extract it on the clang side rather than gate it,
  since the value representation is a plain bool that is directly
  cross-comparable (`dumper_clang_qualifiers._clang_param_is_restrict`,
  verified against real clang 18 output for the C `restrict`, the C++
  `__restrict`/`__restrict__` spellings clang normalizes to one form, a
  typedef whose qualification is only visible in `desugaredQualType`, and
  the `int *restrict *` vs `int **restrict` split where the qualifier
  belongs to the pointee rather than the parameter). Reuses
  `_desugared_qualtype`/`_last_top_level_ptr_end`, the typedef-unwrapping and
  top-level-qualifier helpers the cv-qualifier work already needed, and adds
  `_declarator_group` for the parenthesized-declarator rule the review rounds
  below forced. (An early cut reused `_field_own_cv_source` as well; that is
  the discarded approach finding (1) below describes, not what shipped.)

  Two gates came with it, both following precedent already set in this
  phase rather than inventing a third pattern. (1) A **header-tier gate**
  (`_both_header_aware`, as `param_defaults` uses): DWARF, PDB and the
  symbol-table paths never populate this fact, so a non-header side's
  `False` means "not collected" — comparing it against a header-parsed side
  manufactured the same finding from an evidence-tier difference alone.
  This gate can only ever remove findings that were false, since a
  non-header side is unconditionally `False`. (2) A **legacy-baseline
  flag**, schema **v22**'s `clang_restrict_facts_reliable` — the fourth
  instance of the shape v19/v20/v21 established, and covering `"hybrid"`
  alongside `"clang"` for the same reason v20 did (a merge keeps castxml's
  `params` verbatim for a matched function, but appends a clang-ONLY
  function's parameters as they are). Snapshot disk cache bumped to v10 on
  the v7/v8 precedent. Both `dumper_clang.py` and `diff_symbols.py` were
  within 30 lines of the 2000-line hard cap, so this landed with two
  splits: `dumper_clang_qualifiers.py` (the top-level-qualifier
  helpers, re-exported so existing import paths resolve) and
  `diff_param_qualifiers.py` (the `param_restrict` + `param_va_list`
  detectors, registered through `checker.py`'s import block so no cycle is
  introduced).

  **Two review findings on that change, both worth not rediscovering.**
  (1) *A callback parameter's argument qualifier is not the parameter's.*
  The first cut reused `_field_own_cv_source`, whose no-pointer fallback
  returns the whole type spelling — right for a const/volatile field, wrong
  here. clang spells a callback parameter as `void (*)(int *restrict)`, its
  own `*` inside parentheses, so there is no depth-0 pointer and the
  fallback handed the whole spelling to the regex, matching the
  *callback argument's* `restrict` as though it qualified the parameter.
  castxml's type-chain walk stops at the outer `PointerType` and correctly
  answers False — so this would have introduced a fresh cross-backend false
  positive of exactly the kind the change exists to remove (Codex review,
  reproduced against real clang 18 output). The first fix — require a
  depth-0 pointer before searching at all — was **too strong, and a second
  review round caught it**: `int (*restrict p)[3]` (a pointer to an array)
  is parenthesized for the same declarator-precedence reason, but is a
  perfectly legal restrict-qualified *object* pointer that castxml does
  see, so requiring depth 0 turned the false positive into a false
  negative. The rule that holds in both directions is that a
  **parenthesized declarator group wins over depth 0**: when C forces the
  parameter's own `*` into parentheses, everything at depth 0 belongs to
  something else — the pointee (`int *(*restrict)[3]`, whose leading `*` is
  the array element's) or the callback's parameter list. `_declarator_group`
  finds the group that actually holds the parameter's pointer, identified
  as the first depth-0 parenthesized group whose contents begin with `*` —
  which distinguishes it from a function pointer's trailing parameter list
  (contents begin with a type name) and from an array extent (not
  parenthesized). Twelve spellings verified end to end against real clang
  18, including one **neither review round named** and which the depth-0
  rule got wrong in the original direction: `int *(*)(int *restrict)`, a
  function pointer with a pointer return type, has a depth-0 `*` from
  `int *`, so searching after it scans the callback's parameter list and
  matches its `restrict`. Function-pointer declarators need no special case
  despite looking identical in shape: C11 6.7.3p2 allows `restrict` only on
  a pointer to an *object* type, so such a group can never legally carry
  the qualifier. Verified rather than assumed — both
  `void (*restrict cb)(int)` and `int *(*restrict cb)(void)` are rejected
  outright by clang in C and C++ alike, which also retired an "accepted
  false negative" note an earlier draft had written for that shape.
  A third round found the mirror-image case one level out: `int *__restrict &`
  (a *reference* to a restrict-qualified pointer) is legal C++ whose
  qualifier clang still prints, but castxml's walk follows only
  `CvQualifiedType`/`Typedef`/`ElaboratedType` and stops dead at the outer
  `ReferenceType`, reporting False. Matching the visible qualifier would
  therefore have re-created the cross-backend disagreement from the other
  side, so a top-level `&` after the parameter's own pointer answers False —
  matching castxml deliberately rather than the spelling. Confirmed against
  real clang for the lvalue-reference, rvalue-reference, plain-reference and
  reference-to-array-of-pointers spellings.

  A fourth round found two more, and the method matters more than either
  fix. Hand-picking spellings had by then failed three times running, so
  this round built a *systematic* corpus instead: every declarator shape,
  each compiled twice — once with `restrict` on the parameter's own pointer
  and once without — with the expected answer encoded in the function's own
  name (`_r`/`_n`). That corpus isolated exactly one wrong case out of
  fifteen, and in doing so revealed the actual rule rather than another
  patch: **the parameter's own declarator is the INNERMOST parenthesized
  `(*…)` group**. `int (*(*restrict p)[3])[2]` (p IS restrict) and
  `int (*restrict (*p)[3])[2]` (p is plain; the qualifier is on the array's
  element type) print as `int (*(*restrict)[3])[2]` and
  `int (*restrict (*)[3])[2]` — taking the outer group answers both alike
  and gets the second wrong. Descending to the innermost group is correct
  for all fifteen. The second finding was narrower: `decltype(*gp + 0)
  *__restrict` opens a depth-0 `(` whose contents begin with `*` — a
  dereference, not a declarator — so the group selection now rejects a `(`
  that directly follows an identifier character, which separates a
  call-like operand (`decltype(`, `typeof(`) from a declarator exactly. The
  corpus itself is now a live-clang test, so a future shape has to be added
  with its expected answer rather than argued about.

  A fifth round found that second fix half-right, and the reason it was
  half-right is the useful part. `typeof (*gp) *__restrict` answered False:
  clang prints `typeof` **spaced** whatever the source wrote — even
  `typeof(*gp)` comes back as `typeof (*gp)`, confirmed against clang 18 —
  so "directly follows an identifier character" sees a space and calls
  `(*gp)` the declarator group, searching `*gp` and missing the parameter's
  own qualifier. `decltype(` passed only because clang happens to print it
  unspaced. The obvious repair — skip whitespace, then require an
  identifier — was checked before being applied and is wrong in the other
  direction: `int (*restrict)[3]` and `void (*)(int *restrict)` also read
  as `<identifier><space>(`, so it would dismiss both as expression
  operands and regress rounds 1 and 2. What actually separates the two
  families is the *specific keyword*, so the rule is now a closed set
  (`decltype`, `typeof`, `__typeof`, `__typeof__`, `typeof_unqual`) in
  `_follows_type_operator_keyword` — these being the only constructs clang
  prints in a type spelling whose parenthesized operand is an expression.
  Re-verified against real clang: all three source spellings plus the
  unqualified control, the `decltype` pair, the fifteen-parameter corpus,
  and the reference wrappers, 25 cases, no regressions.
  (2) *Detector registration order is user-visible, and a split must not
  change it.* `registry.detector()` stamps an incrementing counter and
  `run_all()` executes in that order, so registration order fixes the order
  findings appear in every JSON/text report. Registering the new module from
  `checker.py`'s top import block broke this **twice over**, and the two
  halves were found by different means. First, it pulled `diff_symbols` (and
  its `diff_symbols_renames` sibling, which owns `fingerprint_renames`)
  forward from its own import much further down `checker.py`, moving those
  detectors ahead of `diff_elf_layout`'s and reordering the coverage-gap rows
  — caught by five golden-output tests, a marker the everyday fast command
  excludes, which is the case for running `verify.py --profile pr` before
  opening a PR. Making the shared-helper imports function-local fixed that
  half. It did **not** fix the second half, which no test covered: the two
  *moved* detectors themselves went from indices 16 and 20 to 5 and 6,
  because they now registered with the new module at the top of `checker.py`
  rather than from `diff_symbols` in the middle (Codex review; measured by
  dumping `registry.detector_names` against a worktree at the base commit —
  note that comparison is only valid with `PYTHONPATH` pointed at the
  worktree, since the editable install otherwise shadows it and reports a
  false "identical").

  The shape that preserves order exactly: the **registrations stay in
  `diff_symbols.py`** at their original source positions, and only the loop
  bodies moved. Each detector applies its snapshot-level gates and hands
  `diff_param_qualifiers` the already-selected public-function maps, so the
  new module takes `dict[str, Function]` and imports nothing from
  `diff_symbols` at all. That last point is a constraint, not a preference:
  once `diff_symbols` imports the new module, a back-import would be a real
  cycle, and the AI-readiness import-cycle gate walks *every* AST import —
  a function-local one included — so the "function-local import" escape
  hatch is not available in that direction. `checker.py` is left untouched;
  `ensure_loaded`'s prefix discovery imports the new `diff_*` module anyway,
  and it registers nothing. Verified by re-measuring: all 65 detectors, same
  order as base, with `param_restrict` back at 16 and `param_va_list` at 20.

  (3) *A documentation claim that was not verified.* The capability page's
  graph-edge table asserted that a `hybrid` dump pays a second `clang`
  invocation for the graph attach, "same as castxml". It does not, normally:
  `service._run_dump_uncached` runs the clang sub-dump inside
  `dumper_cache.ast_memoize_scope()`, and the memo slot outlives that scope
  (it is cleared only on failure), so the `_attach_header_graph` that follows
  consumes the same parsed AST whenever its own resolved headers/includes/
  toolchain hash to the same key (Codex review). Corrected to describe the
  handoff and the one condition it depends on. Worth noting as a category:
  this was the only finding in four rounds that landed on *prose* rather than
  code — the generated half of that page is machine-checked against the
  parsers, and the hand-authored half is not.

- ~~Single-AST reuse for the direct-clang backend~~ **Done** (see above) —
  via in-process memoization of `_clang_header_dump`'s result, not by
  threading the parser's already-consumed AST object through
  `service.py`/`ElfHeaderAstResult`/`dumper_manifest.py`. A literal
  single-object reuse (no second `json.loads` of the *cache file*, but also
  no second dict *construction* from disk at all, and no dependence on the
  cache key matching) is still open if the memoization's residual cost
  (cache-key recomputation, one dict lookup) ever turns out to matter.
- **Hybrid-backend provenance-tagged merging** (finalized scope, not
  attempted): extend G28 Phase 3's `--ast-frontend hybrid` per-field
  provenance model (`fact_provenance.py`'s `func_fact_key`/`type_fact_key`/
  `enum_fact_key`/`field_fact_key` — `"func:<mangled>:<fact>"`-shaped keys
  mapping to `"castxml"`/`"clang"`) to graph nodes/edges, not just snapshot
  facts. **Where the gap actually is, confirmed by reading the hybrid path**:
  under `service.run_dump(header_backend="hybrid")`, the L2 graph is *not*
  independently built per backend and then merged the way the flat snapshot
  is — `_attach_header_graph`'s `_skip_header_graph_attach=True` on both
  recursive castxml/clang sub-dumps means neither one gets its own graph,
  and the graph is attached exactly once, after the merge, from
  `header_graph.build_header_only_graph(merged_snapshot, clang_ast_root,
  ...)`. Two different halves of the resulting graph therefore have two
  different, currently-untracked provenance stories: (1) `seed_decl()`'s
  per-node `attrs["visibility"]` comes from `entity.origin` on the *merged*
  snapshot's `Function`/`Variable` — which is itself the output of G28 Phase
  3's castxml-primary/clang-backfill merge, i.e. already a per-field
  provenance decision `fact_provenance.py` recorded, but that record is
  never carried onto the graph node built from it; (2) every structural
  edge (`TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE`/
  `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`) comes from `ast_root`, which
  under hybrid is *always* the clang sub-dump's AST (castxml never builds
  graph edges at all, hybrid or not — confirmed: no code path calls
  `build_header_only_graph` with a castxml-sourced AST), so every edge in a
  hybrid graph is unconditionally clang-only regardless of provenance and
  needs no new tagging — it's already unambiguous. **The real, scoped fix**
  is therefore narrower than "provenance-tag the whole graph": only (1)
  needs new plumbing — `header_graph.seed_decl()`/its type-node sibling
  would need to read the merged snapshot's own `AbiSnapshot.fact_provenance`
  map (`model.py`'s `kw_only` field, already populated by
  `dumper_hybrid.merge_snapshots` and already present on the `merged`
  snapshot object `_attach_header_graph`'s hybrid call site already passes
  through — not discarded, just never consulted by `build_header_only_graph`
  today) and copy the relevant `func_fact_key(mangled, "visibility")`-shaped entry onto
  `GraphNode.attrs["visibility_provenance"]` (a new, additive attr — never
  changes an existing attr's meaning) when that node's identity resolves to
  a fact `fact_provenance` actually tracked. **Consumers, and why this
  hasn't blocked anything so far**: no current L5 detector reads a
  per-attr node provenance at all (`is_public_dependency_node`/
  `is_consumer_compiled_public_entry` key off `visibility`/
  `consumer_compiled_body` directly, never off where either value came
  from), so this gap has produced no known incorrect finding — it would
  only start mattering if a future detector needed to discount/flag a
  finding resting on a clang-backfilled (vs. castxml-primary) visibility
  determination specifically, e.g. to surface "this RISK finding's
  reachability classification rests on a fact clang had to backfill, verify
  it if that matters for your review" the way `LAYOUT_UNVERIFIABLE`
  annotates an analogous evidence-absence gap for layout facts (see this
  same plan's "Findings emitted from absent evidence" AGENTS.md entry for
  the general pattern). **Files that would change**: `header_graph.py`
  (`seed_decl`/type-node builders gain an optional `fact_provenance`
  parameter), `service.py`'s hybrid branch (thread `merge_snapshots`'
  provenance dict through to the `_attach_header_graph` call instead of
  discarding it), `graph_facts.py` (no schema change needed — `attrs` is
  already an open dict). No `SOURCE_GRAPH_VERSION` bump needed unless a new
  detector starts depending on the new attr's presence for correctness
  rather than reading it as optional enrichment.

  **Closed in a later pass, exactly matching the scoped design above.**
  `dumper_hybrid.merge_snapshots` now stamps a `"visibility"`-named
  `fact_provenance` entry for every merged function/variable — `"castxml"`
  for a castxml-primary entry (including a synthetic ctor/dtor key
  reconciled to a real clang mangled name — the *declaration* is still
  castxml's, mirroring the identical reasoning `"param_defaults"` already
  uses for the same case), `"clang"` for a clang-only-appended one.
  `header_graph.build_header_only_graph` gained the exact optional
  `fact_provenance` parameter the design sketch named; `seed_decl()` reads
  it back (trying the function key first, falling back to the variable key
  — a mangled symbol name is never genuinely both, so no runtime
  `isinstance`/import is needed for the `Function`/`Variable` pair, which
  stayed `TYPE_CHECKING`-only) and stamps the additive
  `attrs["visibility_provenance"]` when found. `service._attach_header_graph`
  threads `snap.fact_provenance` through — already present and unused, not
  discarded, exactly as the design sketch found — with no other call site
  needing a change, since `snap` at that call site is already the
  post-merge object for a hybrid dump. Verified end-to-end against a real
  compiled library through the actual `abicheck dump --ast-frontend hybrid`
  CLI (castxml 0.7.0 + clang 18 + g++, conda-forge), not just at the unit
  level: every `source_decl` node in the resulting `source_graph.nodes`
  correctly carries `visibility_provenance: "castxml"` for a header both
  backends parsed identically. No `SOURCE_GRAPH_VERSION` bump, matching the
  design sketch's own prediction — the new attr is additive enrichment no
  existing reader depends on. No detector consumes it yet, same as before;
  this closes only the plumbing gap the design sketch scoped, not a new
  detection capability.
- **Header-defined body fingerprints** (finalized scope, not attempted):
  detect a behavior-preserving vs. behavior-changing inline/template body
  edit in a header, distinct from a signature change — today invisible to
  every evidence tier below L4 (a signature-unchanged body edit changes no
  flat snapshot fact and moves no graph node id, so L0-L2 report nothing,
  and the L2-only header graph carries no body-derived fact at all to
  report a change in). **This already exists at L4, for the build-integrated
  path only**: `source_extractors/clang.py`'s per-TU replay computes
  `SourceEntity.body_hash` via `clang_nodes._subtree_hash()` — an
  alpha-equivalence-normalized structural fingerprint of a clang AST
  subtree, already parameter-aware (`_param_ids`) so a body referencing its
  own parameters normalizes correctly — and `source_diff._diff_inline_bodies()`
  compares it old-vs-new to emit `inline_body_changed`
  (never `BREAKING`, per this directory's D3 rule). **The scoped gap**:
  `header_graph.py`'s L2-only AST walk parses the *identical shape* of
  `clang -ast-dump=json` tree `source_extractors/clang.py` does (both are
  `clang -ast-dump=json` over public headers, structurally the same input
  `_subtree_hash()` already consumes) but never computes or stores a body
  hash for the inline/template function bodies it already visits when
  seeding `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL` edges — so the *same*
  fingerprinting `_subtree_hash()` already provides for L4 could, in
  principle, be reused verbatim for L2, closing the gap for the common
  no-build-integration case (any `dump`/`compare` with headers, not just
  `--sources`/`--build-info`). **Design sketch, not implemented**: (1)
  `header_graph.seed_decl()` gains a body-hash computation for a
  `FunctionDecl`/`CXXMethodDecl` node with a real `CompoundStmt` body child
  (skipping declaration-only/pure-virtual nodes, mirroring
  `_subtree_hash`'s own `_param_ids`-aware call convention exactly), stored
  as a new `GraphNode.attrs["body_hash"]`; (2) a new `graph_reconcile`-
  adjacent or `source_graph_findings`-sibling diff function compares two
  matched `source_decl` nodes' `body_hash` (matched the same way
  `diff_source_graph_findings` already matches other node facts — by node
  id when unchanged, or via `graph_reconcile`'s alias tier when a compound
  edit also moved the id) and emits a new RISK-tier finding when they
  differ but the node's own signature-derived facts (mangled name/params)
  did not — reusing the *existing* `inline_body_changed` `ChangeKind` is
  the natural choice over minting a new one, provided its
  `default_verdict`/severity/`min_evidence` in `change_registry.py`
  tolerate an `L2`-sourced instance alongside its current L4-only
  producer (needs checking against that entry's own assumptions before
  reuse — the CLAUDE.md "Adding a new ChangeKind" 4-step procedure applies
  either way, whether reusing or minting). (3) needs new example-catalog
  coverage (an L2-only fixture demonstrating the finding) and a
  `min_evidence` reclassification check (`scripts/evidence_tiers.py`) if
  `inline_body_changed` is reused, since that registry currently derives
  its tier from the kind alone, not per-producer. **Deliberately not
  attempted in this pass**: this is a genuinely new detection capability
  (new node attr + new diff function + new/reused `ChangeKind` wiring +
  catalog coverage), not a fix to existing code, and — per this same
  phase's own repeated lesson from the vptr-offset and case196 fixture
  work above — a change touching a shared, heavily-reviewed AST-walk
  function (`header_graph.seed_decl`) deserves its own dedicated
  implementation-and-review pass rather than a documentation-pass
  drive-by.
- Preprocessor/build-context reconciliation: macros, `#ifdef` conditionals,
  and compile-DB flags flowing into the header parse consistently between
  the flat-snapshot pass and the graph pass (today each independently
  resolves its own compiler flags/include roots — see
  `service._attach_header_graph`'s own `-isystem` deferred-root handling,
  which G28 Phase 4's hardening already had to fix once for a cache-key gap).
  **Audited (this pass): all three named inputs turn out to already be
  reconciled, not independently resolved — this bullet is stale and the
  item is closed.** (1) *Manual flags* (`--gcc-options`/`--gcc-option`) —
  `_attach_header_graph` and the primary snapshot pass both receive the
  identical `CompileContext` object (`compile: CompileContext | None`), a
  frozen dataclass whose `gcc_options`/`gcc_option_tokens` fields are where
  a manual `-D`/`-U` define lives; there is no second, independently
  resolved copy for the graph pass to diverge from. (2) *`-isystem`/
  include-root inference* — `_attach_header_graph` re-invokes the same
  pure functions the primary pass already ran (`expand_header_inputs`,
  `resolve_inferred_header_roots`) over the identical raw `headers`/
  `includes`/`gcc_options`/`gcc_option_tokens` inputs; since both are
  deterministic pure functions of those inputs, the two calls cannot
  disagree — this is redundant computation (already tracked above as the
  memoization's "residual cost"), not a reconciliation gap. (3)
  *Compile-DB flags* — this one WAS a real, exactly-this-shape divergence,
  and reading `cli_dump_helpers.py`'s ELF `dump` path found it already
  fixed, under a prior Codex review, before this pass started: the main
  header-AST parse consumes `effective_gcc_options` (`_merge_gcc_options`),
  which folds in the `-p`/`--compile-db`-derived `-D`/`-I`/`-std` flags on
  top of the plain `--gcc-options` value — and, per that same function's
  own code comment ("effective_gcc_options folds in the -p/
  --compile-db-derived ... flags that compile_context itself does not
  carry ... Without this, a header that only parses successfully with
  those compile-DB flags would produce a valid main snapshot while a
  second clang pass parses it without them and silently degrades"), the
  identical `effective_gcc_options` value is folded into the
  `CompileContext` passed to `_attach_header_graph` too
  (`dataclasses.replace(compile_context, gcc_options=effective_gcc_options)`
  when they differ), specifically so the header-graph attach and the
  clang-layout attach (G28 Phase 4) both see what the primary pass saw.
  Separately, the fully-integrated `--sources`/`--build-info` path
  (`inline.py`) doesn't call `_attach_header_graph` at all — its own L5
  graph is built by real per-TU clang replay against the compile DB's
  actual flags (`call_graph.py`/`type_graph.py`, `inline_graph_fold.py`),
  a structurally separate mechanism from the header-only L2 graph this
  bullet is about; `cli_buildsource.embed_build_source` only backfills the
  L2 graph as a fallback when that build-integrated fold produced none of
  its own, never runs both against divergent flag sets for the same
  snapshot. No code change made — the fix already exists and is verified
  present; this pass is a documentation correction only, closing an item
  this plan had carried as open past the point a prior review round
  already closed it.

**Files likely to change.** `abicheck/dumper_castxml.py`,
`abicheck/dumper_clang.py`, `abicheck/dumper.py` (`_header_ast_parser`),
`abicheck/dumper_hybrid.py`, `abicheck/buildsource/header_graph.py`,
`abicheck/buildsource/include_graph.py`.

## Phase D — New ChangeKinds, examples, docs, perf

**New/expanded `ChangeKind`s** for identity-rename/move/impact findings that
Phase B's canonical identity makes possible. Illustrative, not final —
actual names need to go through the usual `change_registry.py`
categorization step:

- `PUBLIC_API_INTERNAL_TYPE_RENAMED` — a public entry's dependency target
  was renamed (Phase B identity resolves it as the same entity, not an
  add+remove pair).
- `PUBLIC_API_INTERNAL_DEPENDENCY_MOVED` — a dependency target moved
  between internal headers/translation units without changing shape.
- `PUBLIC_API_IMPACT_PROOF_PATH_CHANGED` — an existing dependency's proof
  path (the specific edge chain a `graph explain` would show) changed
  shape even though the finding it supports did not.

**~16 new runnable examples with ground truth**, following
`examples/README.md`'s case-numbering convention. Check the current
highest case number before assigning new ones:

```bash
ls examples/ | grep case | sort -V | tail -5
```

Propose the next contiguous block (e.g. if the highest existing case is
193, claim 194–209) covering: rename-vs-add/remove disambiguation (Phase B),
each new fact family from Phase C's schema audit, at least one case per new
`ChangeKind` above, and a header-only-vs-build-integrated collector-upgrade
case exercising the reconciliation path end-to-end.

**Full documentation rewrite — done.** All three bullets below shipped as
`docs/reference/header-backend-capabilities.md`:
- A backend capability matrix (CastXML vs. direct-clang vs. hybrid, which
  facts/edges each can and cannot see).
- "Why CastXML can't do all graph edges" (the schema-limit findings from
  Phase C's audit, in the same spirit as G28 Phase 1's "explicitly declined
  as infeasible" section).
- "How to extend clang parsing" roadmap comparing JSON AST dump vs.
  `clang.cindex` vs. LibTooling vs. preprocessor callbacks vs.
  `VTableContext` — when each is the right tool, referencing G28 Phase 4's
  own LibTooling companion-tool experience (`tools/clang-layout-tool/`) as
  a worked example of the LibTooling option's cost/benefit.

**What shipped, and the one design decision worth recording.** The matrix is
**not prose**: `scripts/backend_capabilities.py` carries one `FactRow` per
field of the six declaration dataclasses the two backends build (95 rows),
`scripts/gen_backend_capability_matrix.py` renders them into the page's
generated section (the `gen_platform_matrix.py`/`platform_capabilities.py`
splice pattern, one sibling over), and the hybrid column is **derived** from
`dumper_hybrid.py`'s real backfill lists rather than hand-typed. The reason
for that shape is this plan's own history: several rounds recorded here
found a fact's real backend coverage had moved on while a document still
described the old world, so `tests/test_backend_capability_matrix.py`
re-derives every published claim by reading `dumper_castxml.py`/
`dumper_clang.py` with `ast` — asking not "is this keyword passed?" but
"is it passed a real expression, or a placeholder like `size_bits=None`
/`is_opaque=False`?" — and fails when the claim and the code disagree. That
check is what found the `Param.is_restrict` gap recorded under Phase C
above; it was written to prevent drift and immediately paid for itself by
catching a live false positive.

**Gaps the matrix surfaced and documented rather than fixed** (each needs
its own verification, and castxml was not installable in the environment
this pass ran in — the same "verify before claiming" bar the rest of this
phase held to):
- `EnumType.underlying_type` is clang-only; a castxml (and therefore
  hybrid) enum keeps the model default `int` whatever the header declared.
  No diff detector reads it — `enum_underlying_size_changed` comes from
  DWARF — but `tu_merge.py`'s ODR-conflict check does.

  **Closed in a still-later pass** — see the matching entry under
  "Gaps the matrix surfaced" below for the fix and its verification.
- A hybrid merge is castxml-based, so a clang-only fact it does not
  explicitly backfill (`is_template_pattern`,
  `has_anonymous_aggregate_fields`, `underlying_type`) is dropped for any
  declaration both backends saw. Both of the first two look benign on
  inspection (castxml never emits an uninstantiated pattern, and supplies
  the real offsets the anonymous-aggregate flag exists to help find), but
  "looks benign" is the claim that needs a castxml host to check.

  **Closed for `is_template_pattern`/`has_anonymous_aggregate_fields` in a
  still-later pass** (PR #719 follow-up), once a real castxml+clang host was
  available to check "looks benign" against for real. Both are plain `bool =
  False` — not an Optional tri-state like every other backfilled fact in
  `dumper_hybrid.py` — so castxml's own `False` is never itself a "castxml
  doesn't know" placeholder the way `None` is elsewhere; `_merge_record_type`
  therefore OR-merges these two instead of using the existing
  `_backfill_fact` null-check helper. Verified end to end against a real
  compiled class template + anonymous-union header (`clang++`/`g++` +
  castxml 0.6.3 + direct-clang 18): `is_template_pattern`'s backfill is
  empirically **inert** for the current producer pair — a clang-recognized
  template *pattern* (e.g. bare `Box`) never shares a `type_map_key` with
  any castxml-visible *concrete instantiation* (`Box<int>`), so `clang_t` is
  never itself the pattern for a castxml-matched `t`; the pattern already
  reaches a hybrid snapshot correctly via the pre-existing clang-only-append
  path. Kept anyway (not asserted unreachable) as a defense-in-depth/honesty
  measure, the same precedent this module already sets for
  `RecordType.is_abstract`. `has_anonymous_aggregate_fields` is genuinely
  live, not inert: dumping a real all-anonymous-union record
  (`struct AllAnon { union { int i; float f; }; };`) confirmed castxml
  itself reports `False` (it already has real per-field offsets and doesn't
  need the structural signal) while clang reports `True` for the *same*
  matched type — and, before this fix, the hybrid-merged result silently
  kept castxml's `False`, discarding a real, accurate fact. New coverage:
  `tests/test_dumper_hybrid.py`'s `TestTypeAndFieldFactBackfill` OR-merge
  tests, plus `scripts/backend_capabilities.py`'s two rows moving to
  `hybrid_backfilled=True` (`gen_backend_capability_matrix.py` regenerated).
  `tests/test_backend_capability_matrix.py`'s "hybrid drops an unbackfilled
  clang-only fact" fixture moved again, to `Param.is_va_list` (see that
  fact's own entry below — deliberately excluded from hybrid's backfill even
  at the diff-detector level, so it keeps this shape for the foreseeable
  future). `snapshot_cache._SNAPSHOT_CACHE_VERSION` bumped (v14) so an
  upgrading user's warm hybrid cache entry is re-extracted instead of
  replaying the pre-fix merge indefinitely.
- An opaque handle type is **absent** from a clang snapshot rather than
  opaque: `parse_types` skips every non-definition, so `is_opaque=False` is
  correct by construction while the type itself never appears.

  **Closed in a still-later pass** (PR #719 follow-up), verified against a
  real compiled `struct Handle;` (forward-declared, never defined) header.
  `parse_types` now groups `self._records` by identity (scope + resolved
  name) before building `RecordType`s: an identity with a definition
  anywhere among its candidates collapses to that definition (never
  opaque, same as before); an identity with ONLY forward-declaration
  candidates now emits one opaque stub instead of nothing at all. Confirmed
  end to end (`abicheck dump --ast-frontend clang`) that `Handle` now reads
  `is_opaque=True` with empty fields, matching castxml's own
  `incomplete="1"` handling. `_build_record` grew an `is_opaque` parameter
  and an early-return branch mirroring `dumper_castxml.py`'s exact
  opaque-record shape — every derived fact (fields/bases/vtable/
  is_standard_layout/is_trivially_copyable/has_anonymous_aggregate_fields)
  stays at its neutral/unknown default rather than a computed value, since
  a forward-decl-only node has no member list to have judged any of them
  from. The dedup logic also closed a second, adjacent gap along the way: a
  type BOTH forward-declared and defined in the same TU (`struct Foo;
  struct Foo { ... };`) — confirmed with real clang output that both land
  as separate `CXXRecordDecl` nodes sharing one identity — previously
  relied on `parse_types`' per-entry loop processing each node
  independently (the definition entry building a correct `RecordType`, the
  forward-decl entry being skipped outright by the old guard); the new
  identity-grouped pass makes that collapse explicit and order-independent
  (a definition wins regardless of which declaration came first in source
  order), the same tie-break `_record_index()` already documents for its
  own, unrelated vtable-base-lookup purpose. `scripts/backend_capabilities.py`'s
  `RecordType.is_opaque` row moved from clang `NONE` to `FULL`
  (`gen_backend_capability_matrix.py` regenerated); its own AST-evidence
  scanner needed `_build_record`'s two `is_opaque=True`/`is_opaque=False`
  literals rewritten to `is_opaque=is_opaque` (a variable reference to the
  new parameter) to be correctly read as a real extraction rather than a
  hardcoded placeholder — `tests/test_backend_capability_matrix.py`'s own
  placeholder-vs-extraction regression test moved `is_opaque` to the
  EXTRACTED side accordingly. `snapshot_cache._SNAPSHOT_CACHE_VERSION`
  bumped (v15): a clang-only opaque handle type used to be silently absent
  from the snapshot entirely, not just wrong-valued, so a warm cache from
  before this fix is invalidated rather than replayed.
- `Variable.value`, `Variable.access` and `Param.is_va_list` have no
  producer on any layer, which makes `param_became_va_list`/
  `param_lost_va_list` unreachable on real input. Recorded in
  `diff_param_qualifiers.py`'s own docstring too, since a backend that
  later populates `is_va_list` inherits `param_restrict`'s exact problem.

  **Closed for `Param.is_va_list` in a later pass, exactly inheriting
  `param_restrict`'s gate shape as predicted above.** The direct-clang
  backend now extracts it (`dumper_clang_qualifiers._clang_param_is_va_list`,
  schema v23), verified against real clang 18 output for the x86-64 System V
  spelling — the one ABI verified in this environment; an unrecognized
  target's real `va_list` still reads `False` (a conservative false
  negative, not a guessed spelling, matching this whole phase's "verify
  before claiming" discipline). Unlike `is_restrict`, this fact is **not**
  symmetric across producers: castxml has never populated it and always
  reports `False`, so `diff_symbols._diff_param_va_list` requires both
  sides' `ast_producer` to be reliable clang facts.

  **A Codex review round found the first version of this fix over-included
  `"hybrid"`, mirroring `param_restrict`'s own gate without checking
  whether the analogy actually held.** It doesn't: `param_restrict`'s
  hybrid inclusion is safe because castxml genuinely IS a real `is_restrict`
  producer, so a hybrid merge's castxml-verbatim params for a MATCHED
  function still carry a real answer either way. `is_va_list` has no such
  fallback — castxml has never populated it — so a matched function's
  param in a hybrid snapshot reads a permanent, version-independent
  `False`, not a legacy-baseline artifact any reliability flag could
  describe. The dangerous case: the SAME function's parser coverage
  differing between the old and new snapshot (clang-only-appended, real
  value, in one; matched-by-both-and-blind in the other) would read a
  real, unchanged `va_list` parameter as added/removed purely from that
  coverage shift. Fixed by excluding `"hybrid"` from the producer gate
  entirely — `diff_symbols._diff_param_va_list` now requires
  `ast_producer == "clang"` on both sides, full stop. See
  `diff_param_qualifiers.param_va_list_changes`'s own docstring for the
  complete reasoning and `tests/test_clang_param_va_list.py`'s
  `TestParamVaListHybridExcluded` for the regression coverage, including
  the exact coverage-shift repro.

  **Closed for `Variable.value`/`Variable.access` in a still-later pass**
  (schema v24), once a real pinned castxml build became available to
  verify against in this environment (`action/install-castxml.sh`).
  castxml already emits the identical structured `access` attribute on a
  static class member's `<Variable>` element that `_access_level` already
  reads for `Function`/`TypeField` (confirmed against real castxml 0.6
  output), and the identical verbatim, unevaluated `init` attribute
  `TypeField.default`/`Param.default` already read (also confirmed a
  non-const global's initializer can be an arbitrary runtime expression —
  `init="f()"` — which is why `value` extraction is restricted to
  `is_const`, mirroring `_iter_public_constants`'s own filter one method
  below). `Variable.value` needed no reliability flag at all:
  `diff_types_abicc_parity._diff_var_values` already declines per-pair
  unless BOTH sides are non-`None`, so a legacy blanket-`None` side is
  silently skipped rather than misread — the same protection that made
  `TypeField.default`'s *value representation* safe without a whole-
  snapshot gate. `Variable.access` has no such "unknown" state (a plain
  enum, `PUBLIC` by construction), so it needed the identical
  real-but-WRONG-data treatment as `is_restrict`/`is_va_list`:
  `AbiSnapshot.castxml_var_access_facts_reliable`, and — learning directly
  from the Codex finding immediately above — `"hybrid"` excluded from
  `diff_symbols._diff_var_access`'s producer gate from the start, not
  added as a second review round's correction.

  **Closed for `EnumType.underlying_type` in a still-later pass**, once a
  pinned castxml build was available to verify against. castxml's
  `<Enumeration type=...>` attribute names the compiler-resolved underlying
  integer type — fixed (`enum E : short`) or implementation-chosen from the
  member value range for an unfixed enum — verified against real castxml
  0.6.3 output for both cases (`enum class Color : short` reading `short
  int`, a plain `enum Status { OK, FAIL }` reading `unsigned int`). No
  reliability flag was needed: unlike `is_va_list`/`Variable.access`, this
  is a straightforward "was it read at all" gap, not a "reads a real value
  that happens to be systematically wrong" one, so there is no legacy
  baseline to distinguish from a genuine absence. `dumper_castxml.py`'s
  `parse_enums` now resolves the `type` id through the same
  `_underlying_type_name` helper `parse_typedefs` already used (following a
  typedef chain to its concrete base type, for the rare `enum E : my_int_t`
  spelling). `dumper_hybrid.py`'s `_merge_enum_type` still does not
  explicitly backfill this field (unchanged, and still recorded as an open
  gap immediately below), but since castxml is now a real producer rather
  than a placeholder default, a hybrid snapshot inherits a genuine answer
  either way — the backfill gap stopped mattering for this specific field
  without needing to be closed itself. `scripts/backend_capabilities.py`'s
  `EnumType.underlying_type` row moved from castxml `NONE` to `FULL`
  accordingly (`gen_backend_capability_matrix.py` regenerated), and
  `tests/test_backend_capability_matrix.py`'s "hybrid drops an
  unbackfilled clang-only fact" fixture moved to
  `RecordType.is_template_pattern`, which still has that shape.

**Performance benchmarks + regression gate — done.** Now that the
header-only graph is always-on rather than opt-in, its per-dump cost is paid
on every run, not just when a user explicitly asked for it.
`scripts/check_header_graph_perf.py` (new dedicated script, following
`check_fp_rate.py`/`check_tier_accuracy.py`/`benchmark_scaling.py`'s
conventions) isolates `service._attach_header_graph`'s own marginal cost
from the `dumper.dump()` call it's layered on top of, across a synthetic
header-declaration-count sweep and both header backends (`clang`'s real
Phase C in-process memo handoff, and `castxml`'s genuine second `clang`
invocation), verifying each attach actually completed a real AST-backed
pass (not a silent degraded fallback) rather than trusting a possibly-fast-
because-broken sample. Self-skips without a real `clang`/`clang++`/`g++`
install or off Linux/ELF; `castxml` is optional (its points are just
omitted, never fatal, unless it's genuinely present and broken). Mirrored
in `tests/test_header_graph_perf_gate.py` (pure-logic tests run
unconditionally; the live-measurement tests self-skip the same way the
script does, carrying the `integration` marker so the fast lane never
compiles fixtures or invokes a compiler).

**Wired into `.github/workflows/performance.yml`** (Codex review, P1 —
declaring this done while no CI workflow ever ran it left every standard
PR/scheduled run silently skipping the gate). Two jobs, mirroring the
existing `scaling`/`regression` pair's own split rather than inventing a
third pattern:
- `header-graph-perf` — report-only trend data on schedule/dispatch/PR
  (path-filtered to the graph-attach/perf-gate files themselves), uploading
  a JSON+text artifact. Deliberately *not* gated against a committed
  baseline number: a fixed value recorded on one runner/toolchain would go
  stale the moment either changes, the same class of drift
  `check_mutation_score.py`'s own `SURVIVOR_BASELINE` bootstrap (`None`
  until a real run establishes it) avoids — this job's artifacts are how
  that trend gets established across scheduled runs instead.
- `header-graph-regression` — real, *gating* PR-vs-base regression
  checking, following the existing `regression` job's own pattern exactly:
  measures the base branch and the PR head on the identical runner/
  toolchain in the same job (via the script's own `--json-out`/`--baseline`
  flags, exactly as `benchmark_scaling.py` already does), so there is no
  stale-baseline problem to bootstrap past in the first place — gates from
  day one. Falls back to report-only when the base branch predates this
  gate entirely (a PR introducing it, or one from before it existed).

Both new jobs install the pinned CastXML build (`action/install-castxml.sh`,
the same helper `ci.yml`'s `unit-tests` job already uses) so the default
backend's genuine second-`clang`-invocation cost — the more common
real-world case, since most `dump`/`compare` calls use the default castxml
frontend, not `--ast-frontend clang` — is actually part of the comparison,
not silently narrowed to clang-only by an absent/out-of-policy castxml
(Codex review: the bare PyPI `castxml` package a stock runner would
otherwise see is exactly the out-of-policy build the script's own version
gate rejects). The workflow's PR path filter also covers the attach path's
transitive dependencies, not just the two files the gate itself touches
directly (`dumper_ast_config.py`'s AST cache-key computation,
`dumper_sysinc.py`'s system-include-dir probing, `buildsource/include_graph.py`'s
depfile parsing for the include-graph pass, `header_utils.py`'s inferred-root
resolution the script itself also calls).

`scripts/verify.py`'s `full` profile also gained a `header-graph-perf` step
(report-only, `--sizes 25 100`, gated on `clang`/`clang++`/`g++` all being
present via the new `_need_all_bins` precondition helper) — closing the gap
where `python scripts/verify.py --profile full` could never even run this
script at all (Codex review). Deliberately *not* the base-vs-head
`header-graph-regression` half: that job spans two checkouts/venvs in one
workflow run, which is not expressible as a single `verify.py` `Step` — the
same structural reason `benchmark_scaling.py`'s own sibling `regression` job
was never routed through `verify.py` either.

**Synthetic-consumer compile-probe layer — deferred via ADR, not dropped
silently.** A compile-probe layer (actually compiling a synthetic consumer
against old/new headers to observe real compiler diagnostics as
corroborating evidence, distinct from the existing runtime `app.c`/`app.cpp`
fixtures in `examples/`, from `probe_harness.py`'s header-only-library
snapshot-extraction probe, from `contrib/abicheck-clang-plugin`'s
compile-time facts extraction, and — closest of the four —
`abicheck/source_smoke.py`'s hand-authored two-sided consumer compile/link
oracle) turned out to be out of scope for this initiative as a *general*
mechanism. A driving case already exists —
`case111_enumerable_thread_specific_lambda_ambiguity`'s `source_smoke` spec
proves a real `API_BREAK` no L0–L5 evidence tier reaches
(`known_detector_gap: "constructor_overload_ambiguity"`) — but that case is
one hand-authored probe, not a procedure for synthesizing the right consumer
automatically per finding; the open blocker is that synthesis-strategy
design (plus evidence-model placement, verdict mapping, trust/sandboxing —
see the ADR for detail), not the absence of a motivating case. Recorded as
[ADR-060](../adr/060-synthetic-consumer-compile-probe-deferral.md), the
same discipline G28 Phase 5 used when deferring concepts/`requires`
handling to [G4](g4-header-ast-extractor.md) instead of quietly not doing
it. Revisit once a scoped synthesis-strategy design exists, per the ADR's
own "Revisiting this decision" criteria.

**Files likely to change.** `abicheck/change_registry.py` (or a sibling
`change_registry_<topic>.py`), the relevant `diff_*.py` detector module(s),
`examples/caseNNN_*/`, `docs/reference/`, `scripts/check_tier_accuracy.py`/
`scripts/check_fp_rate.py` (or a new perf-gate script).

## Sequencing note

Phase C's AST-reuse fix (in-process memoization, see above) has landed, so
Phase D's perf-regression gate is now meaningful to add: the always-on
header graph no longer structurally pays a second disk read/JSON re-parse
(or a second subprocess) on every `--ast-frontend clang` dump the way Phase
A's `# TODO(header-graph-phase-D)` comment originally flagged. A gate added
before this fix would just have baselined that known, already-diagnosed
inefficiency rather than catching a new one; that's no longer the case.
Phase C's *other* half (the CastXML fact-completeness audit) remains
unstarted and is not a blocker for Phase D's perf gate. Phase B
(canonical identity) is largely independent of Phase C and can proceed in
parallel, but Phase D's new `ChangeKind`s depend on Phase B's identity
resolution being in place first (a rename-detection finding needs the
identity layer to exist before it can be defined precisely).

## Cross-references

- [ADR-041](../adr/041-compiler-facts-semantic-impact-graph.md) — introduced
  the header-only graph and the flags Phase A retired.
- [ADR-044](../adr/044-reachability-aware-suppression.md) — reachability-aware
  suppression; documents the identity-mismatch category of bug (mangled-key
  vs. qualified-name fallback) Phase B generalizes into a shared resolution
  path.
- [ADR-046](../adr/046-source-graph-identity-v2-and-evidence-merge.md) —
  first-writer-wins merge semantics Phase B's canonical identity work
  directly supersedes for the header-graph/build-integrated overlap case.
- [g28-castxml-clang-l2-parity-hardening.md](g28-castxml-clang-l2-parity-hardening.md)
  — the sibling initiative this plan's Phase C schema-completeness audit and
  backend-unification work directly parallels (same discipline, applied to
  the graph-construction path instead of the flat-snapshot path).
- [g29-impact-analysis-layer.md](g29-impact-analysis-layer.md) — the
  unrelated, earlier-registered initiative sharing the "G29" label; see the
  naming note at the top of this document.

## Out of scope

- Re-litigating Phase A's already-shipped default-on flip or the two flags'
  deprecation-and-removal timeline — that is a settled decision recorded in
  ADR-041's Phase A update note.
- `scan`'s multi-binary fan-out gaining the header-only graph — out of
  scope for this plan the same way it was out of scope for ADR-041's
  original addendum; a separate design decision, not a mechanical
  extension of Phases B–D.
- Directory/package `compare` and the raw `--old-sources`/`--new-sources`
  inline-embed path gaining the graph — both remain structural gaps
  Phase A explicitly did not close; closing them would need routing those
  paths through `service.run_dump`'s attach step (or an equivalent), which
  is its own scoped follow-up, not implied by anything in Phases B–D above.
