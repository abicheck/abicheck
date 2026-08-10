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
`--header-graph-includes` remain as hidden (`hidden=True`, absent from
`--help`), deprecated no-op shims on `compare` and `dump`: passing either
prints a one-line deprecation note to stderr and otherwise changes nothing.
Directory/package (set-input) `compare` still does not build the graph (the
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
— `header_graph_options` (the shared, now-hidden decorator) and
`warn_deprecated_header_graph_flags` (the deprecation-note helper both
`compare` and `dump` call). `abicheck/cli_resolve.py` —
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
- ~~Single-AST reuse for the direct-clang backend~~ **Done** (see above) —
  via in-process memoization of `_clang_header_dump`'s result, not by
  threading the parser's already-consumed AST object through
  `service.py`/`ElfHeaderAstResult`/`dumper_manifest.py`. A literal
  single-object reuse (no second `json.loads` of the *cache file*, but also
  no second dict *construction* from disk at all, and no dependence on the
  cache key matching) is still open if the memoization's residual cost
  (cache-key recomputation, one dict lookup) ever turns out to matter.
- Hybrid-backend provenance-tagged merging: extend G28 Phase 3's
  `--ast-frontend hybrid` per-field provenance model to graph nodes/edges,
  not just snapshot facts.
- Header-defined body fingerprints (for detecting a behavior-preserving
  vs. behavior-changing inline/template body edit, distinct from a
  signature change).
- Preprocessor/build-context reconciliation: macros, `#ifdef` conditionals,
  and compile-DB flags flowing into the header parse consistently between
  the flat-snapshot pass and the graph pass (today each independently
  resolves its own compiler flags/include roots — see
  `service._attach_header_graph`'s own `-isystem` deferred-root handling,
  which G28 Phase 4's hardening already had to fix once for a cache-key gap).

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

**Full documentation rewrite** covering:
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
