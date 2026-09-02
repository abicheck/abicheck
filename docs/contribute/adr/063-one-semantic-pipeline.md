# ADR-063: One Semantic Pipeline — Unifying Application, Fact, Identity, and Outcome Models

**Date:** 2026-08-27
**Status:** Proposed — roadmap ADR, partially implemented.

- **Phase 0** (`Fact[T]`/`FactStatus` infrastructure) is **complete**: the
  five converted `RecordType`/`Param` fields, producer-side construction,
  serialization round-trip, the `fact-detector-misuse`/`fact-field-readers`
  AI-readiness gates, and — closing the gap this status paragraph
  previously recorded — **every known reader of the five converted fields
  has migrated off the retained legacy attribute onto its `Fact[...]`
  sibling.** `scripts/fact_field_readers.py`'s `KNOWN_UNMIGRATED_READERS`
  baseline, which stood at 104 recorded sites across 20 modules (the
  allowlist-and-shrink tracking mechanism the previous revision of this
  paragraph described as "not a completed migration"), is now the empty
  set: the scan itself is unchanged and stays permanently live (a genuinely
  new direct read of `RecordType.bases`/`virtual_bases`/`vtable`/
  `vptr_offset_bits` or `Param.is_va_list` anywhere under `abicheck/` still
  fails the gate), but there is nothing left in the baseline for it to
  exempt. `abicheck/model/fact.py`'s new `resolved_fact_value(fact, default)`
  helper — the one, audited, mypy-safe narrowing every migrated site calls
  through (a `<field>_fact` field is statically `Fact[T] | None` only
  because of the direct-construction compatibility bridge; `__post_init__`
  guarantees it is never `None` on a real instance, but mypy cannot see
  that invariant across instances) — is what every migrated call site now
  reads through instead of the bare attribute, re-exported from
  `abicheck.model` alongside `Fact`/`replace_with_fact_sync`.
  **This migration is representation-only, exactly as Phase 0's own
  acceptance bar requires ("this phase changes representation, not
  detector logic")**: `bridge_legacy_and_fact`'s own construction invariant
  (`rec.bases == (rec.bases_fact.value if rec.bases_fact.is_present else
  [])` for every constructed instance, and identically for the other four
  fields) makes `resolved_fact_value(rec.bases_fact, [])` an exact,
  provable re-spelling of what `rec.bases` already returned — not a new
  collapse of availability into a default, since the `Fact[...]` sibling's
  own `.status` remains fully inspectable at every migrated site for a
  future caller that wants it. No detector's emitted findings changed:
  confirmed by the full test suite (mypy 0 errors, ruff clean, the
  complete non-integration/non-slow/non-golden suite green) and by the
  FP-rate/tier-accuracy gates staying at their existing baselines. A
  detector actually *branching* on `FactStatus` — treating
  `NOT_COLLECTED`/`UNSUPPORTED`/`FAILED` differently from `PRESENT` rather
  than resolving to the same default the legacy field already held — is
  still Phase 5's job, exactly as this paragraph's previous revision
  scoped it; Phase 0 closes the representation gap Phase 5 needs standing
  ready, it does not pre-empt Phase 5's own behavior change. Two large
  detector modules (`diff_types.py`, `dwarf_snapshot.py`) crossed the
  AI-readiness `file-size` hard cap purely from this migration's added
  local-variable lines; both were split the same way this codebase already
  splits an oversized module — `diff_types_vtable.py` (the
  `TYPE_VTABLE_CHANGED` evidence-gating cluster:
  `_vtable_transition_is_evidenced`/`_vtable_transition_rests_on_
  unresolved_evidence`/`_layout_evidence_is_unverifiable`/
  `_owned_virtual_signatures`/`_owned_virtual_signatures_for_record`/
  `_diff_type_vtable`, re-imported back into `diff_types.py`) and
  `dwarf_snapshot_datasources.py` (`show_data_sources` and its private
  helpers, re-exported from `dwarf_snapshot.py` so every existing
  `from abicheck.dwarf_snapshot import show_data_sources` call site is
  unaffected) — neither split changes any behavior, only which module owns
  the code. Six further modules separately tracked in ADR-061's
  `architecture/debt.yaml` no-growth ledger (a stricter, independent
  per-file budget from the `file-size` cap above) needed a narrower fix:
  `RecordType` gained two short, import-free convenience methods,
  `resolved_bases()`/`resolved_virtual_bases()` (each a one-line call to
  the same `resolved_fact_value` primitive), so a caller with an
  already-typed `RecordType` in hand can write `rec.resolved_bases()`
  instead of paying a new `from .model import resolved_fact_value` line —
  not a second representation (both spellings resolve through the one
  primitive), a narrower avoidance of an import cost. All six land exactly
  at their recorded adoption baseline with this migration's reads
  included; see the implementation plan's own Phase 0 section for the
  full accounting.
- **Phase 1** ("finish the `dump`/`scan` typed-API convergence," closing
  AGENTS.md "PR C") has landed its real `dump` execution routing onto
  `execute_dump_request` (`frontends/cli/dump_execute.py`) for **both**
  binary formats it supports, on top of the earlier `--dry-run` slice: ELF
  first (commit `0b69fc3` plus three review follow-ups), then PE/Mach-O
  the identical way once the shared pipeline's own format-generic design
  meant no second structural investigation was needed — see
  `docs/contribute/known-gaps.md`'s "PR C" entry for the exact mechanism
  and the two structural blockers the ELF slice closed first.
  `perform_elf_dump`/`handle_non_elf_dump` are both retired for this call
  site (each stays defined, unchanged, for its own direct unit tests). The
  PE/Mach-O half is verified only via mock-based CLI/unit tests, not a real
  PE/Mach-O toolchain — none was available in this environment for a
  byte-for-bit parity check the way the ELF slice's own
  `tests/test_dump_cli_typed_api_parity.py` corpus does. **One permanent,
  named exception remains out of scope**: `cli_buildsource.
  dump_source_only()`, the binary-less `dump --sources`/`--build-info`
  path, has no `execute_dump_request()` call to route through at all and
  stays its own separate pipeline — this is a deliberate scope boundary
  from Phase 1's own Design section, not a residual gap.
- **Phase 2** ("`EntityId`/`ScopePath` as the one identity primitive") has
  landed seven slices: the `ScopePath`/`EntityId` primitive itself
  (`abicheck/model/identity.py`); both header-AST backends (`dumper_clang.py`/
  `dumper_castxml.py`) tracking scope as typed segments at parse time;
  `RecordType`/`EnumType`/`Function`/`Variable` gaining a parse-time-resolved
  `entity_id` carrier, populated by both backends (the plan's open
  carrier-field question resolved as option (a)); a `storage/entity_ids.py`
  wire-schema-v2 bridge (`domain_entity_id_to_dto`/`_from_dto`) that encodes
  `ScopePath` losslessly (a rendered `qualified_name` string cannot — two
  distinct `ScopePath`s can render identically); that carrier now
  persisting through `serialization.py` (`SCHEMA_VERSION` 28); a first,
  bounded piece of the `finding_identity.py` algorithm migration (c2) —
  `finding_identity.resolve_function_identity` now canonicalizes each
  parameter through the same
  `model.signature_normalization.canonicalize_function_signature_param_type`
  primitive `entity_id_for_function`'s own signature-fallback branch uses,
  rather than a second, independently-maintained
  `canonicalize_type_name`-only pass — a real behavior fix (a top-level
  by-value cv-qualifier no longer fragments identity, matching the C++
  standard's own linkage rules), not only a dedup; and, as the seventh
  slice, `Change` (`checker_types.py`) gaining its own `entity_id` carrier
  field, wired at every `diff_symbols.py` function-diff call site
  (`_check_removed_function`/`_check_return_type_change`/
  `_check_params_change`/`_check_ref_qualifier_change`/
  `_check_linkage_change`/the `bool_transition`-backed noexcept/virtual/
  explicit/variadic checks/`_check_contract_attributes_change`/
  `_check_exception_spec_change`/`_check_vtable_index_change`/
  `_check_inline_transitions`/the `FUNC_ADDED` site/
  `_detect_newly_deleted_functions`) — the old side's `Function.entity_id`
  when it exists, else the new side's, mirroring `Change.symbol_binding`'s
  own old-side convention; an eighth slice extended that wiring to every
  remaining function-backed site (`diff_hidden_friends.py`,
  `diff_param_qualifiers.py`, `_diff_ctor_overload_ambiguity`, and the
  auxiliary param/deprecated/override-specifier detectors), and gave
  `EntityId` its own `.key` property (`model/identity.py`, a flat,
  collision-safe string) so `finding_identity.resolve_change_identity`
  could fold `Change.entity_id` in as a new `entity:` alias — the first
  real consumer read, additive only, never promoted to `primary_id`/tier
  (existing suppression-rule/canonical-id compatibility is unaffected).
  **Still not landed: the mangled-name-is-genuine determination**
  (`finding_identity.is_real_mangled_name`/`normalize_mangled_name` stay
  finding_identity's own — `model/identity.py`'s own docstring records why
  moving them would reverse the required `compare -> model` import
  direction). **Exhaustive population beyond the function-diff path is now
  substantially landed** (ninth through eleventh slices): every
  `RecordType`/`EnumType`-backed `make_change()`/`bool_transition()` call
  site across `diff_types.py`, `diff_types_field_facts.py`,
  `diff_types_vtable.py` (the base-class/vtable evidence-gating group,
  independently split out of `diff_types.py` by the concurrent Phase 0
  detector migration — this slice's own separately-named copy was folded
  into it on merge; no behavior change), `diff_layout.py`,
  `diff_vtable_layout.py`, the remaining
  `diff_symbols.py`/`diff_symbols_variables.py` variable/field sites, and
  the anonymous-field group (`diff_symbols_anon_fields.py`, split out of
  `diff_symbols.py` for the identical file-size reason) now carries
  `entity_id=`, using the old-side-preferred pairing convention `Change.
  symbol_binding` already established, with a field/base-level finding
  attributed to its *containing* record's `entity_id` (no field-level
  `EntityId` exists — `EntityKind.FIELD`/`BASE` are declared in
  `model/identity.py` but have no constructor, an accepted, named gap, not
  a silent one). A twelfth slice closed the typedef/constant gap this
  paragraph previously listed first: `entity_id_for_typedef`/
  `entity_id_for_constant` now have production callers on both header-AST
  backends (`dumper_clang.py::parse_typedef_entity_ids`/
  `parse_constant_entity_ids`, `dumper_castxml.py`'s same-named pair),
  feeding two additive `AbiSnapshot` sidecars — `typedef_entity_ids`/
  `constant_entity_ids`, keyed exactly like `typedefs_qualified`/
  `constants` respectively, since neither collection has a parsed
  declaration object to carry an `entity_id` on the way
  `RecordType`/`EnumType`/`Function`/`Variable` do. They persist through
  `storage/entity_id_codec.py` (`SCHEMA_VERSION` 30→31; absent on a
  pre-v31 snapshot, which loads as `{}` — the same value a v31 snapshot
  with no header-resolved typedef/constant identity carries, so no
  migration adapter is needed) and `snapshot_cache.py` (cache version
  22→23), merge through `dumper_hybrid.py`/`tu_merge.py` in the same
  direction as the dicts they annotate, and are read by
  `diff_types.py::_diff_typedefs` (`TYPEDEF_REMOVED`/
  `TYPEDEF_BASE_CHANGED`/`TYPEDEF_VERSION_SENTINEL`) and
  `diff_symbols.py::_diff_constants` (`CONSTANT_REMOVED`/`CONSTANT_CHANGED`/
  `CONSTANT_ADDED`) under the same old-side-preferred convention every
  slice above uses. `typedef_entity_ids` joins
  `qualified_name_segments._LAMBDA_IDENTITY_FIELDS` because
  `typedefs_qualified`'s keys are renumbered there and the sidecar's must
  move with them; `constant_entity_ids` deliberately does not, because
  `constants` itself is excluded from that walk (its values are payload
  literals) and renumbering only the sidecar is what would desynchronize
  the pair. DWARF-only snapshots leave both sidecars empty, exactly as they
  already leave `typedefs_qualified`. **Two classes of site remain
  deliberately unwired, for reasons specific to each, not oversight:**
  (1) every DWARF/PE/Mach-O/ELF-symbol-table-only detector
  (`diff_platform.py`'s DWARF-tier struct/enum/field functions,
  `diff_elf_layout.py`, `diff_platform_elf_dynamic.py`,
  `diff_platform_elf_symbols.py`, `diff_versioning.py`, `diff_sycl.py`) —
  these operate on raw platform/DWARF facts with no `EntityId`-carrying
  object behind them at all, since only the header-AST (L2) backends
  populate `entity_id` today; closing this needs `EntityId` extended to the
  DWARF backend first (build a `ScopePath` from a DIE's parent chain), a
  real, separate extraction-side project, not a diff-site wiring pass; (2)
  `buildsource/*.py`'s L5 source-graph `Change` construction sites, which
  already have their own identity primitive (`buildsource/
  entity_identity.py`'s `CanonicalIdentity`) and are out of this decision's
  scope by design (D3 generalizes flat/diff/graph identity, not the L5
  source-graph's already-separate scheme). **Every post-parse consumer
  migration remains not landed**: `diff_filtering.py`'s `_enum_canonical_
  names`/`record_canonical_names` string-bridging is blocked on (1) above
  (it exists specifically to reconcile header-tier and DWARF-tier spellings,
  so it cannot move to `EntityId` comparison until the DWARF side has one);
  a `type_reachability.py` hardening (anchoring `_record_identity` on
  `EntityId.key` when available) was investigated and found not
  independently landable — every call site feeds directly into the
  module's signature-spelling substring-matching machinery, which has no
  `EntityId` to anchor on regardless (see that module's own docstring);
  and promoting the `entity:` alias into a real alias-match reconciliation
  tier stays blocked on `EntityId.key`'s cross-release stability, which is
  not established (`Anonymous`/`LocalToFunction` ordinals are only
  stable within one process's lifetime, and two prior attempts at ordinal
  stability were each reverted — see `model/identity.py`'s own docstring).
  Those are the remaining items before Phase 2 is complete.
- **Phase 3** ("public surface as a graph query over one evidence graph,"
  D5) has landed its plumbing across thirteen slices: `model/occurrence.py`
  (`OccurrenceId`/`canonical_key`); a `SurfaceGraphLike` structural
  `Protocol` (`model/graph_facts.py`); `AbiSnapshot.surface_graph:
  SurfaceGraphLike | None` (unconditional, not nested under
  `build_source`) and its `storage/surface_graph_codec.py` encode/decode
  pair (`SCHEMA_VERSION` 28→29); `compare/surface_graph.py` (a new,
  `compare/`-layer builder populating `declaration`/`type`/`header`/
  `symbol` nodes and `declares`/`references`/`exports` edges from L0-L2
  snapshot facts alone — `model.graph_facts.GraphNode`/`GraphEdge`
  directly, never a second dataclass hierarchy); `policy/public_surface.py`
  (`PublicSurfaceQuery`, whose `.resolve()` returns a
  `frozenset[EntityId]` of both public function/variable roots and
  reachable record/enum ids); `surface_graph.py`'s (root module)
  `build_surface_graph()`/`compute_surface_metrics()` each gaining one
  optional `public_entity_ids` parameter, narrowing (never expanding)
  the legacy `Visibility.PUBLIC`-derived root set when given;
  `pattern_verdicts.py`/`diff_surface_metrics.py` each gaining an
  `old_public_entity_ids`/`new_public_entity_ids` pair threaded to their
  own matching single-snapshot call; `checker.compare()` gaining the
  identical optional pair (forwarded, never resolved — `compare()` stays
  free of any `policy/` import) and `service.compare_snapshots()`
  resolving each side's own set via `PublicSurfaceQuery().resolve()`
  before forwarding into `compare()`, which `service_compare_pipeline.
  classify_compare_pair` inherits for free since it already routes
  through `compare_snapshots()` rather than `checker.compare()` directly;
  and `service_header_graph_attach._attach_header_graph()` assigning the
  *same* `SourceGraphSummary` instance `build_header_only_graph()` already
  produces to both `AbiSnapshot.surface_graph` and `AbiSnapshot.
  build_source.source_graph` — deliberately without also populating this
  phase's own `compare/surface_graph.py` facts onto it there: that
  builder runs unconditionally on essentially every real dump (G31 Phase
  A), and doing the per-declaration walk speculatively, for a feature
  nothing in this phase's own wiring reads back yet, regressed the
  header-graph attach-cost perf gate by 47-96% at realistic sizes (caught
  by CI on this phase's own PR). `build_public_surface_facts()` stays
  available for a caller that does need those facts to populate onto the
  same shared instance explicitly.
  **Phase 3's infrastructure and its planned follow-up migration have both
  landed, but D5's own literal premise — `compute_public_surface()` becomes
  a traversal *through the graph* — is not what the shipped closure walk
  does, and that is not a loose end left for later; it is what a
  three-review-round investigation concluded the correct design must not
  do (full account: `docs/contribute/known-gaps.md`, this section's own
  single owner for that history — summarized, not repeated, below).
  Calling the phase "complete" would hide that from a status reader, so
  this entry states it plainly instead: `compute_public_surface()`/
  `PublicSurfaceQuery` never reads `AbiSnapshot.surface_graph`/
  `GraphNode.attrs` for the one traversal D5 names as its defining case —
  is-this-declaration-public — at all, and neither does anything else in
  production. `compare/surface_graph.py`'s `build_public_surface_facts()`,
  the function that would stamp this phase's own node facts onto that
  graph, has **no production caller** (confirmed by a repo-wide call-site
  check); `_attach_header_graph()` deliberately never calls it either (see
  the perf note above). What *does* run in production —
  `PublicSurfaceQuery().resolve()` computing a plain `frozenset[EntityId]`,
  threaded through `checker.compare()` as `public_entity_ids` to narrow the
  *legacy, unrelated* `surface_graph.py` (root module) `SurfaceGraph`'s
  root set — is a set membership check, not a graph query, and never
  touches `AbiSnapshot.surface_graph` either. The Phase 3 evidence graph
  exists and is persisted (via the shared `SourceGraphSummary` instance
  `_attach_header_graph()` assigns to it), but nothing in this phase's own
  production wiring queries it.** A first
  landing (thirteen slices, 2026-08-31)
  shipped the plumbing above without migrating `surface.py`'s own
  closure-walk traversal — `PublicSurfaceQuery` delegated to it
  unchanged, a scoped, documented risk decision given the demonstrated
  fragility of that exact algorithm (see `docs/contribute/known-gaps.md`).
  A second round (2026-09-01) did migrate it: `surface.py`'s
  `_index_surface_types`/`_seed_public_roots`/`_walk_type_closure`/
  `_walk_exact_type_closure`/`_record_exact_identities`/
  `_record_nested_in_known_record`/`_record_is_confirmed_public_seed` and
  the `PublicSurface` type moved to `policy/public_surface.py` (the
  dataclass + indexing) and `policy/public_surface_closure.py` (the walk
  itself, plus `resolve_public_surface()`); `surface.py`'s own copies were
  **deleted**, not kept alongside, and `surface.compute_public_surface()`
  is now a thin re-exporting wrapper. `export_surface.py`'s own
  root-seeding (the export-table-matching logic) was left unchanged, but
  its final type-closure step calls the same migrated `_walk_type_closure`
  the header domain uses, so that domain became graph-native for free.
  `PublicSurfaceQuery` itself moved again, to `policy/public_surface_query.py`,
  since it is the one module depending on both the closure module and
  `export_surface.py` at once and keeping it in either would have closed
  a real, `check_architecture.py`-detected import cycle.

  That migration went through three review rounds before landing on this
  design — `docs/contribute/known-gaps.md` owns the full account (why the
  first two designs read the graph and each turned out unsafe: a
  never-enriched-in-production graph silently misread as "references
  nothing," then a fix for that introducing both a measured performance
  regression and a confidence-precedence security hazard). The shipped
  fix stopped reading the graph for this decision at all:
  `compare/surface_graph.py`'s `referenced_identifiers_by_node()` — a pure
  function of the snapshot's own declarations, computed before any
  `GraphNode` is built — is what `policy/public_surface_closure.py` and
  `export_surface.py`'s closure-walk entry points call directly.

  **Against D5's own text, three things do not match "one authoritative
  graph, queried by traversal," and only two of them are this phase's
  deliberate scope boundaries — the third is the closure-walk deviation
  named above, which is neither a scope boundary nor an open item, but a
  considered decision to not do what D5 originally specified:**
  `export_surface.py`'s own root-seeding logic was never reimplemented as a
  graph traversal (only the type-closure step it shares with the header
  domain became graph-native); `type_reachability.
  directly_referenced_stdlib_types()` was not migrated into
  `policy/public_surface.py` — doing so would require reclassifying
  `type_reachability.py` into the `policy` layer, which would introduce a
  genuine new `policy -> extract` architecture violation (that module
  imports two already-`extract`-classified siblings); and the closure walk
  itself resolves declaration/type reachability from `referenced_
  identifiers_by_node()` — a pure function of the snapshot's own
  declarations — rather than from the graph object, for the correctness and
  security reasons the review-round history above gives. **One item remains
  genuinely open, not deliberately deferred:** `compare/surface_graph.py`'s
  own node ids (`canonical_key(occurrence_id)`/`approx::`/`typedef::`
  fallbacks) and `buildsource/header_graph.py`'s pre-existing L5 node ids
  (`decl://<identity>`/`type://<identity>`) remain two independent
  namespaces — sharing one `SourceGraphSummary` instance is real and
  tested, but the two schemes do not dedup onto one node for a declaration
  both builders see (see `compare/surface_graph.py`'s own module
  docstring). This is real, separate follow-up work, not
  silently-abandoned scope. FP-rate gate and per-tier accuracy gate both
  show zero regression from any part of this phase — but a gate showing no
  regression is not evidence the graph traversal itself shipped, since the
  shipped design does not perform one for the closure walk.

  **Bottom line for a status reader:** this phase's infrastructure landed
  and its own planned `surface.py`/`export_surface.py` migration is done,
  but neither means D5 shipped as written. `public_entity_ids` — the one
  piece of this phase's output that does run in production — is a plain
  `frozenset[EntityId]` narrowing the *legacy* `surface_graph.py`
  `SurfaceGraph`'s root set, not a query against `AbiSnapshot.
  surface_graph`; that Phase 3 evidence graph is built and persisted but
  has no production reader at all (`build_public_surface_facts()` has no
  caller outside its own tests). D5's central claim — that
  `compute_public_surface()` becomes a graph traversal — is not what
  shipped for the one traversal D5 names as its motivating case. That is a
  considered, reviewed departure from the decision as written, not an
  oversight — recorded here rather than papered over with "complete," per
  this same document's own governing invariant against one concept getting
  two disagreeing descriptions. If D5's authoritative-graph requirement is
  still wanted for the closure walk specifically, it is unimplemented scope
  for a future phase to pick back up, informed by why three rounds already
  concluded the graph-reading version is unsafe as designed.

  Two corrections to the implementation plan's own text, found during this
  phase's implementation rather than assumed from the design prose: the
  plan's Phase 3 section describes `surface.py`/`export_surface.py`/
  `pattern_verdicts.py` as "still flat, top-level modules" for the
  `policy -> compare` layering analysis — checked against the real
  `architecture/modules.yaml` rather than trusted, all three (plus
  several siblings) are already classified into the `policy` layer via
  `legacy_paths`, an unrelated prior classification pass the plan's own
  text predates; and `service_compare_pipeline.classify_compare_pair`
  already calls `service.compare_snapshots()` (not `checker.compare()`
  directly, as an earlier plan draft assumed), which is what let slice 11
  wire both Tier-2 production paths from a single change.
- **Phase 4** (`AnalysisPlan`: pre-flight resolution, not mid-run discovery)
  has landed `abicheck/workflows/plan.py`'s `AnalysisPlan`/`SidePlan`/
  `AnalysisPlanner`, and a new `PlanningError` (`abicheck/errors.py`).
  `AnalysisPlanner.resolve()` runs inside `resolve_compare_request`
  (`service_compare_pipeline.py`) and `resolve_dump_request`
  (`service_dump_pipeline.py`) — the one chokepoint every front end already
  resolves a request through — before either function invokes a header-AST
  backend or a build-info adapter. **One of this decision's two named
  scenarios is closed; the other is named explicitly as out of scope for
  this phase, not silently dropped.** The `--build-target` + pre-captured
  Bazel `aquery`/`cquery` gap (`docs/contribute/known-gaps.md`) is real,
  isolated, and previously undiagnosed at the request level — it now raises
  `PlanningError` with the documented workaround in the message ("option 2"
  from that known-gap entry). The known-gap entry names both `dump` and
  `scan`; `scan --against`'s own candidate resolution
  (`scan_engine._build_new_snapshot`) has no `CompareRequest`/`DumpRequest`
  of its own to resolve through `AnalysisPlanner`, so it calls the
  underlying check directly (`workflows.plan.bazel_target_scoping_failure`,
  the free function `_check_bazel_target_scoping` wraps for the
  `AnalysisPlanner` path) rather than being left as the one place this gap
  stayed open — both halves of the named gap are closed, not only the
  `resolve_compare_request`/`resolve_dump_request` half. This decision's second illustrative scenario,
  "a `-H` flag accepted by a collect mode that cannot use it," does not
  correspond to any isolated, currently-open known-gap entry once checked
  against the real code: the one combination that literally matches it
  (`--depth binary` combined with an explicit header list, which silently
  clears the headers) is intentional, already-shipped, reviewed behavior
  with its own dedicated regression tests — turning it into a hard
  `PlanningError` would be an unreviewed behavior change to already-tested
  surface, not a same-phase fix for a documented silent failure. See
  `abicheck/workflows/plan.py`'s own module docstring for the full
  reasoning and what a genuine second check would need. `AnalysisPlan`
  itself is scoped exactly as D4 states: it carries each side's *requested*
  toolchain/compile-context inputs, never the resolved P0.3 L3→L2
  compile-context fold's output, and no resolved policy/pack/contract
  state. The `scripts/check_ai_readiness.py` `cli-contract`/
  `engine-cli-boundary` gates were not widened in this slice — every
  `resolve_compare_request`/`resolve_dump_request` caller already reaches
  `AnalysisPlanner.resolve()` through the two functions this phase changed
  directly, so there is no second call site for those gates to newly
  police yet; a future phase adding one should widen them then.
  **Second slice: the `.abicheck.yml`-only dry-run/execution parity gap this
  phase's first slice named as open is now closed for `dump`/`compare`/
  `scan`.** `workflows.plan.bazel_target_scoping_failure`/
  `scan_bazel_scoping_failure` now accept an optional `sources`/
  `build_config`, and fall back to auto-discovering (or, for `scan`, an
  explicit `ScanRequest.build_config` override honoring) an
  `.abicheck.yml`'s `build.targets:` at that source tree whenever no
  explicit `build_targets` was requested — mirroring `embed_build_source`'s
  own `targets=list(build_targets) if build_targets else cfg.targets`
  precedence exactly, never running the fold itself (a pure, deterministic
  config read, not the P0.3 compile-context resolution this phase's own
  design excludes). Because `dump --dry-run`/`compare --dry-run` already
  resolve through the same `AnalysisPlanner`-wired chokepoint, widening the
  check there closes their dry-run parity gap for free, with no change to
  either renderer; three of `scan`'s four pre-flight call sites
  (`scan_engine.run_scan_core`, and both of `cli_scan.py`'s single-binary
  and `--artifact-set` pre-flight checks, real-run and dry-run alike) were
  updated to forward their own already-in-scope `sources`/`build_config`
  values. `dump`/`compare` still have no request-level seam for an
  *explicit* `--config <path>` override (no `build_config` field exists on
  `InputSpec`/`DumpRequest`/`CompareRequest`) — only the auto-discovery half
  closes for them; `scan`'s own `ScanRequest.build_config` field already
  provided that seam, so both halves close for the three updated call
  sites. **The fourth, `service_scan.run_scan_set`, was deliberately left
  unwidened**: that file sits exactly at the AI-readiness 2000-line hard
  cap, the widened call doesn't fit `ruff format`'s column budget on one
  line, and a hard-cap file is not a `LARGE_FILE_ALLOWLIST` candidate for a
  fix this narrow (that allowlist's own comment reserves it for pre-existing
  `scripts/`/`tests/` debt, not a new production-file exemption). A direct
  `run_scan_set(ScanRequest(...))` typed-API call with no CLI in front of it
  is the one shape this residual leaves open — `scan --artifact-set`'s own
  CLI pre-flight in `cli_scan._run_artifact_set` already forwards both
  values ahead of this call, so the CLI path is unaffected. Named explicitly
  as out of scope for this slice, per this ADR's own governing "acknowledged
  gap over risky reactive patch" convention, rather than forced in under a
  file-size constraint; see `docs/contribute/known-gaps.md`'s matching
  entry.
- **Phase 5** (the fact/capability registry, generalizing
  `change_registry.py`'s `ChangeKindMeta` pattern from change *kinds* to
  *facts*) is **complete**: both the registry infrastructure and the full
  field-by-field population the plan's own Scope section describes,
  landed as the sequence of small, independently-reviewable commits that
  section calls for (schema v30 through v40) rather than one PR.
  `abicheck/model/fact_registry.py` (new) defines
  `FactDefinition`/`FactLifecycle`/`FactRegistry`, the case-(a)
  `REFERENCE_FLAG_COVERAGE` inventory (every `*_facts_reliable` flag on
  `AbiSnapshot` and the exact fields it gates — built against the real
  `fact_provenance.py`/detector call sites, not derived from a flag's own
  name), and `FACT_REGISTRY` itself, populated with the five fields Phase 0
  already converted (`RecordType.bases`/`virtual_bases`/`vtable`/
  `vptr_offset_bits`, `Param.is_va_list`) plus this phase's own first new
  conversion, `RecordType.is_final` — chosen because, unlike Phase 0's own
  five fields, it needs no private omission sentinel and no reliability
  flag (`is_final`'s own `None` already unambiguously means "not
  captured"), demonstrating the plan's stated six-item touch list: model
  field + registry entry + serialization encode/decode (`storage/
  fact_codec.py`, `SCHEMA_VERSION` 30) + parser (both header-AST backends
  — `extract/headers/castxml/records.py`, `extract/headers/clang/
  records.py` — construct `is_final_fact` directly as an explicit kwarg,
  the same convention Phase 0's own `bases_fact`/`vtable_fact` already
  established, rather than relying on `RecordType.__post_init__`'s
  generic legacy-value bridge; a Codex review round on this PR caught the
  first draft's claim that no parser change was needed, which left
  `scripts/backend_capabilities.py`'s AST-verified capability matrix
  correctly failing since neither backend's source literally names
  `is_final_fact` without it) + detector (none needed — Phase 0's own
  precedent: no detector migrates to read a `Fact[...]` sibling in this
  phase either) + test. `scripts/fact_registry_
  completeness.py` (new, mirroring `fact_field_readers.py`'s split-leaf-
  module pattern) is the `fact-registry-completeness` AI-readiness check
  D7 calls for: every `Fact[T]`-typed field has exactly one registry entry
  and vice versa, and a real AST scan finds every field eligible for
  conversion (case (a) via `REFERENCE_FLAG_COVERAGE`, case (b) via a
  tri-state-annotation-plus-documented-ambiguity heuristic) that isn't yet
  converted — each such field must be named in `fact_registry.
  KNOWN_UNCONVERTED_ELIGIBLE_FACTS`, an allowlist-and-shrink baseline
  mirroring `fact_field_readers.KNOWN_UNMIGRATED_READERS`'s
  own convention exactly, rather than a silently-passing check (it stood
  at ~30 fields when the infrastructure landed and is empty today — see
  below). A stale
  allowlist entry (naming a field that's since been converted, or no
  longer exists) fails the same way. `scripts/gen_fact_capability_
  matrix.py` (new) renders the registry into `docs/reference/
  fact-registry.md`, a fully-generated page (`gen_detector_spec.py`'s
  pattern, not `gen_backend_capability_matrix.py`'s splice-into-hand-
  authored-page one — this page has no hand-authored narrative to
  preserve). **Where the population landed.** The registry
  covers 45 facts across ten owners. Phase 0's five fields and
  `RecordType.is_final` came first; batches two through seven converted
  the case-(b) population (`RecordType`'s remaining six, `EnumType`'s two,
  `Variable`'s three, `Function`'s ten, `AbiSnapshot.
  ast_resolved_standard`, and the five binary-format fields on
  `ElfMetadata`/`PeMetadata`/`MachoMetadata`, schema v31-v37); batches
  eight through ten converted the **case-(a)** population — the harder
  half, where the field's own resting value (`False`, `None` meaning "not
  deprecated", `AccessLevel.PUBLIC`) is a legitimate answer and only a
  snapshot-level `*_facts_reliable` flag can say whether anyone looked:
  `TypeField`'s five fields (v38), the `deprecated` family across
  `Function`/`Variable`/`RecordType`/`EnumType` plus `EnumType.is_scoped`
  (v39), and `Param.is_restrict`/`Variable.access` (v40).
  `KNOWN_UNCONVERTED_ELIGIBLE_FACTS` is now the **empty set** — the
  allowlist stays declared (both halves) because `fact_registry_
  completeness.py` checks it in both directions, so a newly-added eligible
  field fails the gate outright rather than joining a silent baseline.

  Two pieces of shared machinery came out of the case-(a) half rather than
  being written per field. `storage/fact_codec.apply_case_a_fact_backfill`
  replaced the three loops ADR-063 Phase 0 open-coded for
  `vtable`/`vptr_offset_bits`/`is_va_list` with one navigator plus a
  `CaseAFactRule` tuple (`apply_legacy_fact_backfill` is now a thin
  wrapper stating Phase 0's own three rules through it), so a converted
  field's "this legacy document's value is a placeholder its flag marks
  untrustworthy" correction is a table row; and
  `decode_fact_with_legacy_presence` closes the matching decode-side gap —
  for a case-(a) field, a document omitting the *legacy* key means "no
  evidence", which the reader's own `.get(key, False)` default would
  otherwise launder into a confirmed value. The conversions also surfaced
  five real mutation traps in production merge paths
  (`dumper_hybrid._merge_field`/`_merge_variable`, `tu_merge`'s
  `_more_public_of`/`_merge_variables`, `tu_merge_provenance`), each a
  bare `dataclasses.replace()` that the bridge's "explicit Fact wins" rule
  turns into a silently-reverted write; all now use
  `replace_with_fact_sync()`, and `_blank_provenance` derives its
  fact-blanking from its own blanked-field list so a future conversion
  cannot reintroduce it.

  **Deliberately not attempted in this phase, named explicitly
  rather than left as a silent gap**: a detector actually *branching* on
  `FactStatus` (no fact reaches `CONSUMED`; every registered entry sits at
  `PERSISTED`, exactly as Phase 0's own status note scoped it);
  generating the
  model field, the serialization encode/decode pair, or a suppression/
  report-schema wiring from the registry (the plan's own Design/Acceptance
  sections name all three as separate, out-of-scope codegen designs); and
  widening the registry to the full "every persisted, detected, or
  reported fact" population D7's own amendment scopes as a future,
  separately-justified extension beyond the availability-bearing subset
  this phase (and D7's initial realization) actually covers.
- **Phase 6** (one canonical `SemanticIR` between the backends and the
  checker) has landed its **first slice — the IR itself, its persistence,
  and the hybrid merge's reconciliation of it — and no parser narrowing.**
  That ordering is the plan's own: `SemanticIR` is defined and tested
  before any backend is narrowed onto it, so the per-backend migrations
  converge on one shape rather than on each backend's own reading of
  "canonical" behind a shared name. `abicheck/model/semantic_ir.py` (new)
  defines `SemanticIR.occurrences: Mapping[OccurrenceId, CanonicalEntity]`
  — keyed by occurrence, never collapsed per `EntityId`, so an
  ODR-duplicate/incomplete-declaration pair keeps both sides' availability
  — plus `canonical_entities()` as the explicit, order-independent
  reduction (most-resolved occurrence wins, ties broken on
  `canonical_key`), and `CanonicalEntity`, which carries only the
  non-identity payload (`Fact`-wrapped canonical spelling, template
  arguments, CV-qualification, and a `producer` tag), never a second copy
  of the identity its key already states. `AbiSnapshot.semantic_ir` and
  `AbiSnapshot.semantic_ir_conflicts` persist through
  `storage/semantic_ir_codec.py` (`SCHEMA_VERSION` 37→38) as a **list of
  entries** rather than a string-keyed map, for the two reasons the plan
  names: `dataclasses.asdict()` raises outright on a dataclass dict key,
  and a rendered string key cannot carry back the typed `ScopePath` inside
  an `OccurrenceId`. `dumper_hybrid.merge_snapshots()` gains the fifth
  reconciliation step (`extract/semantic_ir_merge.py`): base-plus-backfill
  with castxml as the base, a clang-only entity unioned in, a two-sided
  disagreement keeping castxml's value and recording the discarded one
  per *occurrence* (`fact_provenance`'s declaration-only key cannot
  separate two matched pairs sharing one `EntityId`), and a fail-closed
  matcher whose one refusal is a *two-sided* disagreement: two occurrences
  pair when at most one side supplies a disambiguator (castxml routinely
  supplies none, so this is the ordinary case, and the two keys then
  legitimately differ), and never when both supply one and they differ. A
  group with no unique complete matching keeps every occurrence from both
  sides; an occurrence the two sides key *identically* still merges there,
  since one `OccurrenceId` names one occurrence and discarding the
  overlay's copy would lose the evidence this step exists to preserve. Each of the plan's four review-falsified matching rules is
  pinned as a property test (`tests/test_semantic_ir_merge.py`); one of
  them — two occurrences on one side sharing a non-empty disambiguator —
  turns out to be structurally unreachable from a real `SemanticIR` (that
  pair *is* one `OccurrenceId`, hence one dict key), so the guard is
  tested at the matcher's own list-taking entry point and the reachability
  limit is stated rather than left implied by a test that only appears to
  cover it. **Deliberately not in this slice, and therefore not yet
  observable to any user:** no backend produces an IR yet
  (`dumper_castxml.py`/`dumper_clang.py`/`dwarf_snapshot.py`/
  `pdb_metadata.py`/`btf_metadata.py`/`ctf_metadata.py` are unchanged),
  `extract/semantic_normalizer.py` does not exist, and none of the five
  assembly call sites projects through it — so `semantic_ir` is `None` on
  every snapshot a real `dump` produces, every v38 document is
  identical to the v37 one it would have been apart from the version stamp
  itself (the `semantic_ir_conflicts` map is written only when a hybrid merge
  recorded one, never as an empty object), and the snapshot cache
  version is deliberately *not* bumped (no dumping-pipeline output
  changed). The parser narrowing, the normalizer, and the per-call-site
  parity tests are the remainder of this phase.
- **Phases 7–10** are still unimplemented design text.

See the [implementation plan](../plans/one-semantic-pipeline.md) for the
full phase-by-phase state, including every slice's own "Landed"/"What this
slice deliberately does not attempt" notes and review-found corrections.
Several of the still-unimplemented phases' decisions are already partially
satisfied by work this ADR consolidates rather than replaces (see
"Relationship to existing ADRs" below).
**Decision maker:** abicheck maintainers
**Relates to / builds on:** [ADR-024](024-public-abi-surface-resolution.md),
[ADR-031](031-source-implementation-graph-augmentation.md),
[ADR-036](036-report-view-model.md),
[ADR-037](037-cli-interface-contract.md),
[ADR-042](042-compatibility-and-gate-decision-separation.md),
[ADR-043](043-cli-pre-1.0-surface-reset.md),
[ADR-044](044-reachability-aware-suppression.md),
[ADR-045](045-identity-based-old-new-entity-matching.md),
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md),
[ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md),
[ADR-049](049-contract-relevance-and-compatibility-configuration.md),
[ADR-050](050-comparability-contract-and-multi-tu-manifest.md),
[ADR-053](053-tu-link-unit-dso-attribution.md),
[ADR-054](054-cli-project-integration-surface-consolidation.md),
[ADR-055](055-typed-request-result-completeness-and-schema-registry.md),
[ADR-057](057-consumer-graph-and-impact-join.md),
[ADR-061](061-responsibility-package-architecture.md),
[ADR-062](062-project-snapshot-storage-v2.md)
**Implementation plan:** [One Semantic Pipeline](../plans/one-semantic-pipeline.md)

## Context

A second pass over abicheck's merged-PR history — not just `fix:` commits,
but feature/refactor/perf/hardening PRs and, in particular, chains of the
shape *feature → follow-up → corrective fix → generalized fix* — shows a
recurring root cause behind the codebase's most expensive defects. It is
**not** localized algorithm bugs. It is **disagreement between multiple
representations of the same concept** that are each updated independently,
by different PRs, at different times:

| Concept | Representations that can disagree today |
|---|---|
| Input resolution | `cli.py`/`cli_dump_helpers.py`, `service.py`, `scan_engine.py`, the composite Action, bundle/project paths — each independently resolving headers, includes, compile context |
| Configuration | Click options, `.abicheck.yml`, Action inputs, `CompareRequest`/`DumpRequest` kwargs, environment variables |
| Identity | dict key, `name`, `qualified_name`, `mangled`, castxml synthetic ctor/dtor key, `finding_identity`'s resolved identity, source-graph node id |
| Availability | `None`, `[]`, a boolean reliability flag, a coverage row, a provenance map entry |
| Public surface | header provenance, exported-symbol reachability, path-prefix rules, source-graph reachability |
| Semantic result | findings (`Change`), `Verdict`, severity-scheme exit code, `AnalysisAssurance`, the raw process exit code |
| Report | `DiffResult`, JSON construction, Markdown, SARIF, JUnit, the PR comment |
| Fact support | a model field, a parser, a serializer, a detector, a provenance entry, a capability-matrix row |
| Persistence | per-library snapshot, baseline set, `BundleFacts`, `BuildSourcePack` |
| Compiler context | CLI flags, a compile database, a captured evidence pack, L4 replay argv, the resolved `CompileContext` |

AGENTS.md's own "Known gaps" section is the best evidence for this claim,
not an assertion made here: dozens of numbered findings there are the same
shape — a fact is folded into one of these representations but not a
sibling, or two independently-maintained implementations of the same
decision quietly diverge. One of those findings is cited below by name;
the other two examples are independently verifiable in the repository's
own merged-commit history (`git log --grep`) rather than in AGENTS.md —
stated that way deliberately, so a reader checking a citation against the
wrong source doesn't read it as unsupported. All three are real, not
illustrative:

- **ELF binding** (commit `e5fabd403` / PR #734, `feat(model): expose ELF
  symbol binding as a Function/Variable field + suppression selector`):
  adding one symbol-binding fact required coordinated edits to
  `model.py`, `dumper_elf_symbols.py`, `serialization.py`, `Change` in
  `checker_types.py`, `diff_symbols.py`, `suppression.py`,
  `scripts/backend_capabilities.py` and its generated docs, a suppression
  doc, and two regenerated example fixtures — nine files for one
  additive field. (AGENTS.md separately documents a *different*, later
  defect on this same field — `elf_binding`/`elf_visibility` collapsing
  mixed bindings across symbol-versioned aliases — which is not this
  example's point and is not the source of this citation.)
- **L3→L2 compile-context fold** (the AGENTS.md "Known gaps" entry by that
  name): eighteen-plus numbered follow-on findings, each a *different*
  place the same resolved compile context needed to be threaded but
  wasn't — `perform_elf_dump`, `handle_non_elf_dump`, `scan_engine.
  _build_new_snapshot`, the header-graph second pass, three independent
  AST cache keys, and the legacy `-p`/`--compile-db` auto-match
  overlapping the new fold instead of composing with it.
- **`scan` severity folding** (commit `73b5576c5` / PR #700, titled
  `feat: honor severity/exit-code-scheme in scan --against`): teaching `scan
  --against` to respect a configured severity scheme required widening
  the exit-code space, which broke `aggregate.py`'s own assumption that a
  gated target reading non-blocking under the old scheme meant it was
  safe to drop from `blocking_targets` — fixed in the same PR, but only
  because the regression was caught before merge, not because the
  dependency was made impossible. The exit code was being used *inside
  the system* as semantic data, not only as an external contract.

None of these are bugs in the PR that shipped the fix. They are evidence
that **the integration surface for one new fact, one new config value, or
one new policy decision is too large**, because the same concept exists in
more than one place and nothing makes those places agree by construction.

abicheck's current `main` already contains the correct building blocks for
fixing this the right way: typed `CompareRequest`/`CompareResult`
(ADR-055), a shared resolve/classify compare pipeline
(`service_compare_pipeline.py`), `AnalysisAssurance` as an axis independent
of `Verdict` (introduced alongside ADR-049/055-era work), the
`compatibility_decision`/gate separation (ADR-042), canonical entity
identity and graph reconciliation (ADR-046/048), the responsibility-package
migration (ADR-061), and the storage-v2 foundation — `FactStatus`/
`FactAvailability`, occurrence-preserving identity, canonical encoding,
separated version axes (ADR-062, Phase 0 already implemented in
`abicheck/storage/`).

**This ADR does not propose a rewrite.** It proposes finishing the
consolidation these ADRs already started, generalizing three of their
primitives (typed request/result, fact availability, canonical identity)
from the specific subsystem each first applied to (the compare path, the
storage layer, source-graph matching) to the whole analysis model, and then
**removing the legacy parallel paths** each of those ADRs left in place
rather than letting a fourth, fifth, or sixth representation accumulate
next to them.

## Governing invariant

> One concept, one representation, everywhere it is used. Never two.

This is the one rule every decision and every phase below exists to
enforce, and it is deliberately stated as absolute, not as a preference
to be weighed against convenience:

- **A second implementation of an already-consolidated concept is a
  defect, full stop — not an acceptable transitional state, not a pragmatic
  shortcut, and not something a later cleanup gets to find "eventually."**
  If a phase's own PR leaves the representation it was meant to replace
  still reachable by any caller, that PR is incomplete, regardless of how
  much of the new representation it built. This is why every phase in the
  implementation plan carries its own explicit deletion step and
  acceptance criterion, and why the plan's final phase (delete the
  superseded representations) is not optional cleanup — it is the point at
  which each earlier phase's consolidation is actually true rather than
  merely begun.
- **"Generalize and finish," never "add a parallel design."** Every
  decision in this ADR explicitly names the existing, narrower primitive
  it generalizes (ADR-042/046/048/049/055/061/062's own work) rather than
  proposing a new one from scratch. A future contributor extending this
  architecture inherits the same rule: before introducing a new type or
  module for a concept this ADR already names (availability, identity,
  public surface, outcome, configuration, fact), check whether it already
  has a representation here first. It does not get a second one.
- **This is enforced mechanically, not by convention alone.** Each phase
  that consolidates a representation adds (or extends) an AI-readiness or
  architecture-gate check that makes the *old* representation's reappearance
  a build failure, not a matter of code review catching it — the `Fact[...]`
  truthiness/availability-handling check in Phase 0, the identity
  ambiguity-tracker deletion in Phase 2, the `no-inline-gate-computation`
  check in Phase 7, and so on. A rule that only holds "as long as reviewers
  remember to enforce it" is not the rule this ADR commits to.
- **Reviewers and future ADRs should read a proposal that adds a second way
  to represent something this ADR already covers as a rejection trigger,**
  not as a variation to reconcile later. The corrective action is to extend
  the existing representation (or file a narrowly-scoped amendment to this
  ADR explaining why it cannot be extended), not to let two stand side by
  side.

## Decision drivers

- A large share of abicheck's worst-documented incidents are cross-layer
  disagreement, not single-function logic errors — fixing the class means
  changing where disagreement is *possible*, not adding another guard.
- Several ADRs (042, 046, 048, 049, 055, 061, 062) already introduce the
  right primitive for one subsystem. The fix is to generalize and finish
  wiring those, not to invent a seventh parallel design.
- Migration must be incremental and behavior-preserving per phase — ADR-061
  and ADR-062 already establish this discipline (vertical slices, explicit
  "nothing consumes this yet" states) and this ADR keeps it.
- Every phase that consolidates a representation must end by **deleting**
  the representation(s) it replaces. A consolidation that leaves the old
  path "for compatibility" creates a third implementation, which is the
  exact failure mode this ADR exists to stop (AGENTS.md's own closing
  principle: "After each migration phase, delete the old. This is
  critical.").
- Generalized regression tests (AGENTS.md's "bug-class regression testing"
  convention, and `tests/regressions/manifest.py`) become smaller and
  stronger once an invariant is enforced by the type/module structure
  instead of by a test that has to re-check it at N call sites.
- Nothing here should weaken the AI-readiness gates, the architecture gate
  (`scripts/check_architecture.py`, ADR-061), or the FP-rate/tier-accuracy/
  mutation-score gates — each new primitive below must ship with the
  verification those gates already require for a behavior change of this
  size.

## Decision

Adopt ten decisions (D1–D10), each generalizing an existing, narrower
primitive into a repository-wide one, executed as the sequence in the
[implementation plan](../plans/one-semantic-pipeline.md). Collectively they
are referred to as **One Semantic Pipeline**.

### D1 — One application pipeline; adapters never call the engine directly

All entry points (`cli.py`/`cli_*.py`, the Python API, the composite Action,
`cli_project.py`, bundle/release fan-out) construct a typed request and
hand it to one application-layer pipeline — `resolve → plan → execute →
classify → outcome` — instead of each independently orchestrating
`dumper.dump`/`checker.compare`/policy loading/evidence collection.

This generalizes ADR-055's D1 (CLI and the typed API already share one
input-resolution path for `compare`) and ADR-061's `workflows` ring to
*every* operation, not only `compare`. Concretely: `dump`'s ELF/PE/Mach-O
execution paths (`perform_elf_dump`/`handle_non_elf_dump`) are meant to
converge on the `resolve_dump_request`/`execute_dump_request` split already
added to `service_dump_pipeline.py`, but still run their own legacy route
today. `scan`'s candidate resolution (`scan_engine._build_new_snapshot`)
already converges on the shared `workflows.artifact.execute.
_resolve_side_snapshot_impl` primitive (`service_input_resolution` is only
a delegating facade over this same module; import the real owner in new
code, per that facade's own docstring) — the same one `execute_dump_request`
itself calls internally (`resolve_dump_request` does not; it only validates
evidence and builds a `ResolvedDumpRequest`) — not on
`resolve_dump_request`/`execute_dump_request` verbatim, since `scan` has no
`DumpRequest`-shaped input for that pair's own signature to accept (per
AGENTS.md's own record, this half is already landed; only `dump`'s real
execution path remains on the legacy route, still needing to be migrated
onto `execute_dump_request` itself, not merely onto the shared primitive it
wraps). See AGENTS.md's "PR C" note for the concrete, already-identified
blockers to finishing the `dump` half, and the Action/`cli_project.py`/
bundle fan-out stop doing their own policy interpretation or compare setup.
The `cli-contract`/`engine-cli-boundary` AI-readiness gates are widened to
check this directly rather than only the currently-allowlisted legacy
exceptions.

**No new root entry point is introduced.** ADR-043/054's CLI surface is
unchanged; this decision is about what happens *behind* each existing
entry point.

**One dump shape is explicitly excluded from this convergence, by name,
rather than silently implied as covered — an earlier draft of this
decision left it unstated, and a repository-wide search of the
implementation plan found no phase that ever migrates or retires it.**
The binary-less `dump --sources`/`--build-info` path (no `SO_PATH`), which
executes through `cli_buildsource.dump_source_only()`, stays a third,
independent assembler after this plan's Phase 1 ships — that phase's own
text names it as a tracked residual (the executor has no concept of a
snapshot with no binary-derived L0-L2 facts at all, a real, separate
design question), but tracking it inside one phase's own prose does not
satisfy this decision's "all entry points" wording, and no later phase
picks it back up. This decision's own scope is therefore: every dump
execution path *that produces a binary-derived snapshot* converges on
`resolve_dump_request`/`execute_dump_request`; the source-only path is a
named, intentionally out-of-scope exception until a future, separately-
justified phase gives the executor a real answer for a binary-less
snapshot — not a gap this decision claims to close.

**A second, narrower exception, found the same way (checked against real
code, not assumed covered by "bundle/release fan-out" above): two branches
inside `cli_compare_release.py` bypass the pipeline and are not migrated by
this plan either.** `_collect_matrix_result()` (the `--probe-matrix-*`
release-global build-configuration feature) calls `service.
compare_snapshots()` directly over a pair of empty snapshots with
`extra_changes` — the sanctioned Tier-2 chokepoint, not the disallowed
Tier-1 `checker.compare()` core, so this does not itself trip the
`cli-contract` gate — but it still never constructs a request or a plan,
which is what this decision's own convergence is about. `_resolve_stranded_library()` (the
`--bundle-facts-out` path's fallback for a library missing from the normal
per-pair comparison) calls `cli_resolve._resolve_input()` directly — the
same Tier-2 resolution `resolve_compare_request` itself calls, but reached
independently, with its own bespoke fallback on top. The release fan-out's
*main* per-pair path (`_run_compare_pair`) does route through `service.
run_compare`, so "bundle/release fan-out" above is not uniformly
unconverged — only these two narrower branches are. Migrating either is a
real, separate design question (an `AnalysisPlan` pre-flight check for a
probe-matrix build-config diff, or for a deliberately-degrading
stranded-library fallback, is not the same shape of check this decision
specifies for an ordinary comparison) and is named here, explicitly, as
staying outside this decision's convergence rather than left to be
discovered as a silent gap in an implementation phase's own accounting.

### D2 — `Fact[T]`: one representation of "do we know this, and how"

Generalize ADR-062 Phase 0's `FactStatus`/`FactAvailability` (currently
storage-scoped, in `abicheck/storage/`) into the in-memory domain
representation used by the comparison/detector layer, not only the
on-disk one. A fact-bearing model field becomes a tagged value distinguishing:

- "we never attempted to collect this" (`NotCollected`) from
- "this backend cannot produce this fact" (`Unsupported`) from
- "collection ran and failed" (`Failed[Reason]`) from
- "the fact does not apply to this kind of entity" (`NotApplicable`) from
- a genuine present value, **including a confirmed absence** (`Present[T]`,
  `T` legitimately `None`/empty; optionally `Partial[T]`) —
  `FactStatus.PRESENT`'s own existing definition already covers "the
  producer ran... including establishing that a collection is legitimately
  empty," so confirmed absence is not a seventh status; it is `Present`
  carrying an empty payload, and `Fact[T]` does not introduce a status
  `FactStatus` itself does not have.

This replaces the current overloaded use of `None`/`False`/`[]` to mean
several of the above simultaneously — documented repeatedly in AGENTS.md's
"Known gaps" (the `type_vtable_changed`/`type_base_changed` evidence-gap
entries, the `Param.is_va_list` reliability-flag entry, the per-finding
evidence-provider gap) as a root cause of fabricated or suppressed
findings. A detector cannot write `if old.default != new.default` without
first unwrapping availability — `Fact.__bool__` is defined to raise
rather than merely left undefined (an object with no `__bool__` is still
truthy in Python), so `if fact:` itself is a type error, not an untested
branch. **`Fact.__eq__`/`__ne__` are deliberately *not* given the same
raising treatment — an earlier draft of this decision proposed exactly
that, and review correctly rejected it.** `Fact[T]` is a field on
`RecordType`/`Function`/every other fact-bearing model dataclass, and a
raising `__eq__` on a field poisons the *containing* dataclass's own
generated `__eq__` the moment it reaches that field — comparing two
otherwise-identical `RecordType` instances (ordinary test assertions,
list/snapshot comparisons, `SemanticIR`'s own `CanonicalEntity` equality)
would raise `TypeError` too, which is a far more disruptive failure than
the one this decision is trying to prevent. `Fact.__eq__` instead keeps
the plain dataclass-generated structural comparison (`status`, `value`,
and `diagnostics` together) — correct and unsurprising for a
containing object's own equality, and exactly what a detector comparing
`old.default != new.default` would get instead of the raise this
decision's own earlier text claimed. Guarding *that* specific
misuse — comparing two `Fact[...]` values directly inside detector logic,
rather than unwrapping first — is enforced the same way the `.value_or()`
misuse already is: a static AST check (the implementation plan's
`check_ai_readiness.py` rule) flags a bare `Fact[...]`-typed field on
either side of `==`/`!=` inside a detector module, the identical
mechanism and the identical file scope as the existing bare-attribute-read
rule, rather than a second runtime mechanism layered underneath the first.
`abicheck/storage/availability.py`'s existing
`FactAvailability`/`FactStatus` vocabulary is reused verbatim as the wire
encoding `Fact[T]` serializes to — but because ADR-061 fixes the
dependency direction as `storage -> model`, the vocabulary itself
(`FactStatus`/`Confidence`, not the storage-specific `FactAvailability`
ledger record) relocates to `abicheck/model/` as part of this
generalization, with `storage/` re-exporting it rather than `model/`
importing from `storage/`; this decision does not introduce a second
vocabulary, and the implementation plan states the exact relocation.

### D3 — `EntityId`/`OccurrenceId` as the one identity primitive

Generalize ADR-062's occurrence-preserving identity model and ADR-046/048's
canonical-entity/source-graph identity into the identity primitive used
everywhere an entity needs to be referenced: diff matching (`diff_helpers.
TypeMap`, `finding_identity.py`), the source
graph (`graph_facts.py`), and persisted snapshots.

**Suppression/policy selectors are deliberately not on this list — an
earlier draft of this decision included them, and implementation-plan
review correctly found no phase actually migrates them (Phase 2 never
touches `suppression.py`/`reclassify.py`, and Phase 9's own selector-
consolidation is a string-grammar merge between those two modules, not an
`EntityId` projection).** Unlike the other listed consumers, a selector's
`symbol`/`namespace`/`entity_namespace`/`cause_namespace` fields are
regex/glob **patterns a user writes into a public YAML policy file**, not
an internal reference to one specific entity — there is no single
`EntityId` a pattern like `namespace: "ns::.*"` could be rewritten to
point at, and changing what these fields accept would be a breaking
change to the public suppression-file schema, a different and much larger
problem than the identity-consumer migration this decision is about.
Collapsing a pattern-matching surface into a point-identity primitive is
not the same kind of generalization as the other three consumers, so it
is named here as explicitly out of scope rather than silently implied by
"everywhere an entity needs to be referenced."

**This does leave a narrower, real residual the suppression/reclassify
grammar still carries, named rather than fixed here**: a `namespace`/
`entity_namespace`/`cause_namespace` pattern matches against a *rendered*
qualified-name string, the same flattening documented further below in
this section as lossy — two entities whose typed `ScopePath`s differ
only in segment kind can render to the identical string, so a namespace
pattern could match (or fail to match) both identically even though a
`ScopePath`-aware match would tell them apart. This is an accepted
limitation of the string-pattern selector surface as it exists today, not
introduced or worsened by this decision; closing it would mean designing
a `ScopePath`-aware pattern-matching semantics for a public, user-authored
config format, which is its own scoped follow-up, not a consequence of
migrating internal identity consumers in Phases 2/9. A structural `ScopePath`
(a sequence of typed segments — namespace, record, inline-namespace,
anonymous/local markers) replaces string-concatenated qualified names
wherever identity, not display, is being computed, which closes the family
of bare-name/qualified-name collision bugs AGENTS.md's "Known gaps"
documents repeatedly for opaque-type suppression, typedef dedup, and
`type_reachability.py`'s stdlib-reference detection — each currently
patched locally, each sharing the identical root cause.

Backend-internal implementation keys (castxml's synthetic ctor/dtor key
being the canonical example — PR #582 introduced the key format, and the
follow-up fix, PR #761 (titled `fix: reconcile castxml synthetic
ctor/dtor key format drift (PR #582)`), is the real incident: the
key-generation algorithm changed and a persisted snapshot's old-format
key stopped matching its own unchanged
constructor) are explicitly **not** persisted identity; `EntityId` is
produced once, downstream of backend-specific extraction, not re-derived
from a backend-specific string at comparison time.

### D4 — `AnalysisPlan` resolved before any extraction runs

Before a single collector or backend is invoked, an immutable `AnalysisPlan`
states: the requested operation, per-side evidence requirements, and the
requested toolchain/compile-context inputs — each evidence requirement as a
`requested`/`resolved`/`unsupported`/`ambiguous` tuple. A request that
cannot be satisfied
(AGENTS.md's recorded `--build-target` + pre-captured `aquery` silent
no-op, or a `-H` flag accepted by a collect mode that cannot use it) is
rejected by the planner, before execution, instead of discovered mid-run or
not at all.

**The toolchain/compile-context field is the *requested* inputs, never the
resolved output of the P0.3 L3→L2 compile-context fold — an earlier draft
of this decision said "resolved," and implementation-plan review correctly
found that contradicts an already-landed design decision.**
`service_dump_pipeline.py`'s own `ResolvedDumpRequest` (landed ahead of
this ADR) is explicit that the fold cannot be determined without invoking
it, and the fold can raise `HeaderCompileContextAmbiguousError` on
genuinely ambiguous build evidence — which is exactly why that object
deliberately excludes the fold's result and the fold stays inside
`execute_dump_request`, never the side-effect-free resolve step: running it
there would change `--dry-run`'s existing never-raises-but-a-usage-error
contract, not merely extend it. `AnalysisPlan` is bound by the identical
constraint, being built at the same resolve-time point. It therefore
carries the same *requested* inputs `ResolvedDumpRequest` itself carries
(explicit `--gcc-path`/`--ast-frontend`/language, whatever `--build-info`/
`--sources` path was given) — not the fold's resolved compile context, and
not a path for `HeaderCompileContextAmbiguousError` to surface as a
`PlanningError`. This costs nothing this decision's own named scenarios
need: both are about build-info/depth/collect-mode compatibility,
resolvable from the request's own inputs before any compile-unit matching
runs.

**`AnalysisPlan` deliberately excludes resolved policy and the
public-surface contract — an earlier draft of this decision included
both, and implementation-plan review found neither belongs here.** This
decision's own scope is extraction-feasibility pre-flight; policy/pack
resolution and contract-mode selection answer a later question (how an
already-extracted comparison is classified and scored), and for the
native `compare`/`scan` CLIs specifically that question is not yet
answerable at the point a plan would be built — `cli_compare_receipt.
resolve_and_apply()` (ADR-049 Phase 5) is a separate, Click-dependent step
that runs strictly *after* snapshot resolution, not before it, since it
depends on CLI-specific inputs (`--policy`/`--pack`/`--exit-code-scheme`/a
discovered `.abicheck.yml`) a plan built from a bare request has no seam
for. A plan that carried "resolved policy" as of its own construction
would therefore be stale or incomplete for exactly that front end — worse
than not recording the field at all. ADR-049's D7 precedence resolver
keeps its own existing timing, wherever each front end's configuration
seam for it already sits; this decision does not move that earlier or
claim a canonical pre-extraction point for it that does not exist for
every front end.

This generalizes ADR-050's comparability contract (a `profile_fingerprint`/
`scope_fingerprint` computed and checked, today, mostly *after* both sides
are extracted) into a pre-flight step for evidence/extraction
satisfiability specifically — not for policy, which stays exactly where it
already ran.

### D5 — Public surface as a query over one evidence graph

Generalize the public-surface computation (`surface.py`,
`dumper_scoping.py`, `type_reachability.py`, ADR-024/044) into a query over
one authoritative graph with typed nodes (`Header`, `TranslationUnit`,
`Declaration`, `Type`, `Symbol`, `Target`) and typed edges (`Includes`,
`Declares`, `References`, `Instantiates`, `Exports`, `OwnedByTarget`, ...).
`compute_public_surface()` becomes a traversal from explicit public roots
through this graph rather than a second, independently-maintained
reconstruction of the same relationships from the flat snapshot — closing
the class of bugs AGENTS.md documents under the namespace-collision and
partial-qualification findings in `type_reachability.py`.

**This graph is not a new primitive.** `GraphNode`/`GraphEdge`/
`merge_graph_facts` (ADR-031 D2, ADR-046 D1/D2) — relocated by an unrelated
ADR-061 Phase 5 migration to `model.graph_facts`/`graph_identity`/
`graph_vocabulary`, with `buildsource/graph_facts.py` now a back-compat
re-export shim (Status note above) — already is exactly the
producer-agnostic node/edge/evidence-merge primitive this decision needs,
today used to build the optional L5 source/build-evidence graph and,
independently, `impact/consumer_graph.py`'s/`impact/use_cases.py`'s own
consumer-impact graph (CodeRabbit review, PR #958). D5
reuses that already-relocated primitive directly (ADR-061's own
task-routing table already names `model/` as owning "an ABI entity/value
shared across stages") and builds the public-surface graph as
a second set of node/edge *kinds* over the same primitive, available
unconditionally rather than gated on L3-L5 evidence — not a second
dataclass hierarchy. A first draft of this decision proposed exactly such
a second hierarchy in `compare/`; that draft was rejected during review
for violating this ADR's own Governing Invariant, and the corrected design
is what's stated here.

Building the graph and deciding relevance from it are two different
responsibilities under ADR-061's own task-routing table ("match... a raw
change" vs. "decide relevance"): the public-surface graph *builder* —
the code that walks a snapshot and populates node/edge instances of the
shared `model.graph_facts`/`graph_identity`/`graph_vocabulary` primitive
D5 reuses above — lands in `compare/` (a reconciliation of raw facts, not
a policy decision; **not the shared `GraphNode`/`GraphEdge`/
`merge_graph_facts` primitive itself, which stays under `model.graph_facts`
per the relocation two paragraphs above — a first draft of this sentence
said "the graph substrate lands in `compare/`," contradicting that same
relocation within this decision's own text**), and the relevance
query itself — what `compute_public_surface()` actually decides — lands in
`policy/`, which may import from `compare/` under ADR-061's fixed
dependency direction. D5 does not move a relevance decision into
`compare/`; see the implementation plan's Phase 3 for the exact package
split.

Sharing one node identity (Phase 2's `EntityId`) alone does **not** merge
two independently-built graph objects — `merge_graph_facts` only folds the
facts already attached to *one* node within one `SourceGraphSummary`
instance; it is not itself what combines two builders' separate outputs.
What actually closes ADR-053's TU→link-unit→DSO attribution and ADR-057's
consumer graph risk of permanently disagreeing with the public-surface
graph about the same declaration is a real assembly step: when L3-L5
evidence is present, both the public-surface builder and the L5 source-
graph builder are handed the *same* `SourceGraphSummary` instance — by the
orchestrating workflow code, since this is exactly where two independent
builder packages can be made to share state without either importing the
other — and both call its real `add_node`/`add_edge`, which is what
invokes the merge. Migrating ADR-053/057's own query logic onto this
shared graph directly is still explicitly **not** part of this decision's
first implementation phase — see the implementation plan's Phase 3 for
the exact assembly mechanism, the full file list, and why sharing one
instance is sufficient to close the disagreement risk even before those
consumers' own query logic moves.

**"One authoritative graph" is this decision's target state, not a claim
this decision's own first implementation phase fully achieves — a review
round correctly asked where that gap is, and the honest answer is
narrower than "closed."** The public-surface builder and the pre-existing
L5 builder sharing one `SourceGraphSummary` instance only reconciles a
declaration/type node when both builders mint the *same* node id for it.
Implementation-plan review found, and this decision's own first
implementation phase does not close, a real case where they don't: the
twelve pre-existing L5 producer call sites mint ids from a flattened,
already-normalized identity *string* with no `ScopePath` segment-kind
information behind it, while the collision-free id this decision requires
(Phase 2's `EntityId`/`OccurrenceId` identity) is deliberately *not*
injective on a flattened string — two `EntityId`s whose `ScopePath`s
differ only in segment kind can render to the same string, which is
exactly the collision this decision's own graph-key design avoids by
keying on the segments' own identity fields instead. Migrating those
twelve L5 call sites to carry real structured identity through is a
genuine, cross-cutting rewrite of eight already-complex modules, not a
same-phase fix — so for a snapshot with L3-L5 evidence where a real
segment-kind collision occurs, the public-surface node and the L5 node
for that one declaration remain two separate, unreconciled nodes even
after this decision's first implementation phase ships, an accepted,
named limitation rather than a silently-assumed-closed one. See the
implementation plan's Phase 3 for the full accounting and what a real fix
would require.

### D6 — `RunOutcome` as independent axes; no `exit_code` inside the domain

Generalize ADR-042's `CompatibilityDecision`/gate-decision split and the
existing `AnalysisAssurance` axis into one `RunOutcome` with independent,
non-encoding fields:

```text
RunOutcome(
    compatibility: CompatibilityVerdict | None,
    assurance: AnalysisAssurance,
    gate: PolicyGateDecision,
    operational: OperationalStatus,
    lifecycle: TargetLifecycle,
)
```

**`compatibility` is optional, not required, and a review round correctly
found the first draft's bare `CompatibilityVerdict` couldn't represent
every report this ADR itself names.** The implementation plan's Phase 7
is explicit that `buildsource/check_report.py`'s `build_operational_
error_report()`/`build_bootstrap_report()`/`build_new_target_report()`
each build a report for exactly the non-`EXISTING` cases `operational`/
`lifecycle` exist to represent — a pipeline failure before any comparison
ran, a target with no baseline to compare against yet, a target new to
this baseline-set — none of which ever computed a real compatibility
verdict. A required field leaves an implementer with no honest value to
put there: inventing a verdict (`COMPATIBLE` as a default, say) would
silently claim a comparison happened when none did, exactly the kind of
encoded-meaning collapse this decision exists to get away from. `None`
means "no compatibility axis was computed for this report," distinct from
any real `CompatibilityVerdict` member — not a sixth verdict value folded
into the enum (which would force every existing compatibility-axis
consumer to handle a case that isn't actually a verdict), but the absence
of the axis itself, the same shape `Fact[T]`'s `NotApplicable` uses one
layer down for a fact that doesn't apply to a given entity. Every ordinary
single-pair `compare`, `scan --against`, and release/bundle comparison
populates a real, non-`None` value; only the three synthetic report
builders above, and any future report genuinely representing "no
comparison ran," leave it `None`.

**`TargetLifecycle` is attributed to ADR-053 in an earlier draft of this
decision without checking — ADR-053 is TU-to-link-unit-to-DSO attribution
and defines no lifecycle vocabulary at all, and no `TargetLifecycle` type
exists anywhere in the repository today.** Defined here instead, grounded
in vocabulary this codebase's `aggregate` domain already distinguishes
rather than invented from nothing: `workflows/aggregate/contracts.py`'s
own `_BOOTSTRAP_VERDICT`/`_NEW_TARGET_VERDICT` sentinels separate exactly
the cases a target's own lifecycle state needs to distinguish —
`TargetLifecycle = EXISTING | BOOTSTRAP | NEW_TARGET`, where `EXISTING` is
the ordinary case (a target this baseline-set already knows, with a real
prior baseline to compare against), `BOOTSTRAP` is `load.py`'s own "no
baseline published yet" synthesis (`raw_verdict == _BOOTSTRAP_VERDICT`),
and `NEW_TARGET` is its "target new to this baseline-set" synthesis
(`raw_verdict == _NEW_TARGET_VERDICT`). This axis is meaningful only where
a "target" exists at all — a single-pair `compare` invocation, with no
baseline-set or expected-target concept, always reports `EXISTING`, the
same fixed default every non-`aggregate` `RunOutcome` construction uses;
`aggregate`'s own `_load_report_file` is where the real derivation
(reading `raw_verdict` against the two sentinels above — both genuinely
part of a report's *own* persisted content) happens, not a new mechanism
this decision invents.

**`TargetReport.unexpected` is deliberately *not* folded into this axis —
an earlier draft of this section included it as a fourth member, and
review correctly traced why that doesn't work.** Reading the real code
(`workflows/aggregate/execute.py`), `unexpected` is not part of a report's
own content the way `verdict` is — it is assigned by the *aggregator*,
per invocation, from comparing the set of reports actually found against
a specific expected-target manifest (`ExpectedTargets`) that invocation
was given. The identical report file, aggregated against two different
manifests, can be `unexpected` under one and not the other — so it cannot
be a property `_load_report_file` derives once and bakes into a
per-report `RunOutcome`, the way `EXISTING`/`BOOTSTRAP`/`NEW_TARGET`
genuinely can be. It is also not mutually exclusive with the other three
members the way a single enum requires: a target can be both `NEW_TARGET`
(or mid-bootstrap) *and* absent from a given manifest's expected set at
the same time, two orthogonal facts a four-way enum has no way to
represent together. `unexpected` therefore stays exactly where it already
correctly lives — a separate, aggregation-context-only field
(`TargetReport.unexpected`, set by `execute.py` at aggregation time, after
`RunOutcome.lifecycle` has already been read off the loaded report) — and
this decision does not move it, fold it into `RunOutcome`, or ask any
report writer to emit it.
`PolicyGateDecision` is a **new, code-free type this decision defines** —
not a reuse of `severity.GateDecision`, the existing type that already has
this name's natural meaning in the codebase today. The existing
`severity.GateDecision` dataclass carries exactly what D6 exists to keep
out of domain/workflow data: an `exit_code: int`, a `blocking: bool`
derived from it, and `blocking_categories`, all scheme-dependent (legacy
vs. severity-aware) and correct for what that type is — the CLI's and
aggregate's own *exit-code encoder* output, not something `RunOutcome`
should hold. `PolicyGateDecision` is instead an ordered, exit-code-free
enum/dataclass (e.g. `NONE < ADDITION_QUALITY < POTENTIAL_BREAKING <
ABI_BREAKING`, or the equivalent `IssueCategory`-shaped ordering
`compute_exit_code`'s own severity scheme already uses) carrying enough to
*derive* an exit code, never one itself. **`PolicyGateDecision` alone is
not the whole gate, though — it only orders *compatibility* categories,
and `scan`'s own legacy exit
codes (5 for budget overflow, 6 for not-comparable) are real, independent
blocking conditions neither category covers.** `RunOutcome.operational`
carries exactly this.

**`OperationalStatus` itself is named throughout this decision with no
defined members, ordering, or front-end mapping anywhere — an earlier
draft used it as if it were already specified, and review correctly
found nothing backing that.** Defined here, grounded in the real,
already-distinct conditions this codebase's report writers/readers
already encode rather than invented from nothing: `OperationalStatus =
NONE | BUDGET_OVERFLOW | NOT_COMPARABLE | EVIDENCE_CONTRACT_ERROR |
EXTRACTION_ERROR`, ordered `NONE < BUDGET_OVERFLOW = NOT_COMPARABLE =
EVIDENCE_CONTRACT_ERROR = EXTRACTION_ERROR` for fold
purposes — the four non-`NONE` members are equally blocking and mutually
exclusive per report (a `scan` run that hit budget overflow didn't also
fail extraction), so there is no further internal ordering among them to
state, only "blocking vs. not." `BUDGET_OVERFLOW`/`NOT_COMPARABLE` are
`scan`'s own legacy exit 5/6 (`gate.py::from_scan_report`'s existing
raw-code branch, read the same way for a fresh, structured report).
**`EVIDENCE_CONTRACT_ERROR` is a fourth, genuinely distinct member, not a
renaming of `EXTRACTION_ERROR` — an earlier draft of this definition
conflated the two, and review correctly found `service_scan.py` returning
a real, separate `ScanResult(verdict="EVIDENCE_CONTRACT_ERROR",
exit_code=1)` at more than one call site (ADR-037 D5's evidence-contract
check), never mapping to `EXTRACTION_ERROR`'s own exit 4.**
`EXTRACTION_ERROR` is `compare-release`'s own `verdict: "ERROR"` sentinel
(`load.py`'s `_OPERATIONAL_ERROR_VERDICT` — "a library failed to
dump/extract/compare," ranked above `BREAKING` and floored to exit 4
today). Each front end maps its own real failure modes onto this set at
the point it already computes them (`ScanOutcome`/`ScanResult`/
`ScanSetResult`'s own to_dict() for `scan`'s three members, the release
fan-out for the fourth) — this decision does not invent a new front-end
computation for it, only the one shared vocabulary the existing writers
already need to agree on. A front end with no operational failure of its
own to report (a plain single-pair `compare`, a `scan` invocation that
completed cleanly) always reports `NONE`.

**Which layer folds the two axes together — `gate.py`'s own per-target
readers, or `fold.py`'s cross-target aggregation — is stated once here,
and an earlier revision of this section said it twice, inconsistently:
"`fold.py`'s aggregation orders and `max()`s `PolicyGateDecision` values
directly, with no integer in the comparison" in this paragraph, while the
implementation plan's own corrected Phase 7 design (the one actually
built and verified) has `gate.py`'s readers fold both axes into the one
`GateInfo` they already return, leaving `fold.py` itself unchanged.** The
plan's design is the normative one, restated here rather than contradicted:
`gate.py`'s `GateInfo.from_report_data`/`from_scan_report` fold
`PolicyGateDecision`'s ordering and `OperationalStatus`'s blocking set
together, by `max()` over the shared exit-code scheme both already share,
into the single `GateInfo` each reader returns per target — two
independent axes, neither masking the other, the same orthogonal-fold
shape ADR-049 Phase 7's contract-coverage axis already uses elsewhere in
this codebase, resolved once per target rather than threaded as two
separate values for `fold.py` to remember to fold itself.
`fold.py`'s own cross-target aggregation (`max(t.gate.exit_code for t in
gated...)`) is **unchanged by this decision** — it was already, and stays,
a `max()` over each target's own `GateInfo.exit_code`, which by the time
it reaches `fold.py` is the *output* of `gate.py`'s semantic fold, not a
raw integer read back off a persisted report a second time; "no integer
in the comparison" describes `gate.py`'s own fold of the two typed axes,
not `fold.py`'s aggregation across targets, which has always been (and
remains) an integer `max()` by design. Converting a `PolicyGateDecision` to `severity.GateDecision.
exit_code` is confined to the same boundary encoders D6 already names —
**a first draft of this list omitted `gate.py` itself, contradicting the
fold this same decision just described two sentences earlier: folding
`PolicyGateDecision` and `OperationalStatus` together "by `max()` over the
shared exit-code scheme" is exactly this conversion, performed inside
`gate.py`, not deferred past it.** The real, complete list is four
encoders, not three: `gate.py` (folding both typed axes into one
`GateInfo.exit_code`, once per target, the read-time boundary), the CLI's
`_exit_with_severity_or_verdict`, the Action's encoder, and `aggregate`'s
own `exit_code()` method (each converting an already-folded `GateInfo`/
`RunOutcome` into the final process exit code or JSON integer, the
write-time boundary) — every one of them already exists today and already
owns exactly this conversion for the legacy-scheme case, so this is a new
input type for an existing function, not a new encoder. The two boundaries
stay genuinely distinct rather than one encoder duplicating the other's
work: `gate.py` is the only place a `RunOutcome` axis is *decoded from raw
fields* at all (structured-first, with the legacy `exit_code` decode as
the named fallback); the other three only ever consume `gate.py`'s already
-folded `GateInfo`/`fold.py`'s own cross-target aggregation of it, never
re-deriving the same fold from `RunOutcome` a second time.

No domain or workflow code computes a *new* semantic decision by
branching on an integer exit code. **The one stated exception is a
compatibility adapter, not a gap in this rule**: `gate.py`'s decode of a
legacy report's raw `exit_code` — a report that predates this decision and
carries no structured `RunOutcome` fields at all — is reading a *persisted
external wire value* back into the semantics this decision defines, the
same "read once, decode for legacy, never for fresh" backfill shape D0's
`Fact[T]` bridge already establishes against the pre-existing reliability
flags. It is scoped narrowly (named fallback path only, never reached for
a report carrying the new fields) and is the mechanism that keeps every
already-published report decodable rather than orphaned by this decision.
Exactly one function per front end (the CLI's `_exit_with_severity_or_
verdict`, the Action's own encoder, and `aggregate`'s own `fold.py::
exit_code()` — restated here explicitly rather than only in the paragraph
above, so this sentence's own list doesn't read as narrower than it is)
maps `RunOutcome` to that front end's
exit-code scheme. **"The Action's own encoder" names the role this
decision assigns to `action/run.sh`, not a claim that Phase 7 of the
implementation plan migrates it there — implementation-plan review found
that phase's Files list never touches the script, so its several hundred
lines of `case $ABICHECK_EXIT in ...)` branching stay exactly as
unmigrated after that phase as before it, pending a future,
separately-scoped rewrite; see that plan's own Phase 7 text for the full
accounting.** For the aggregate path specifically: `gate.py` reads a
report's structured `RunOutcome.gate`/`.operational` fields and folds both
into the one `GateInfo` it returns, by `max()` over the shared exit-code
scheme — `PolicyGateDecision`'s own ordering for the compatibility
contribution, `OperationalStatus`'s blocking set for the operational one —
falling back to legacy `exit_code` decoding only for a report that
predates this change; `fold.py`'s own aggregation (unchanged by this
decision) `max()`s the resulting per-target `GateInfo.exit_code` values the
way it already does today, and `fold.py::exit_code()` is the
one place that final aggregated value is converted back to the integer
`aggregate`'s own JSON output and process exit code need — two steps
(structured read-and-fold at `gate.py`, one final integer encode at
`fold.py::exit_code()`), not three, since folding both axes together at
the read boundary is what lets `fold.py`'s own pre-existing aggregation
stay exactly as it is. This
directly targets the PR #700 failure mode (a
downstream consumer decoding semantic meaning from an exit-code integer)
and finishes what ADR-042 started: `mcp_server.py`'s removal already
deleted one of the remaining inline exit-code computations AGENTS.md
flagged; `workflows/aggregate/gate.py`/`fold.py`'s
own `max()`-over-raw-integer aggregation is folded into this decision's
scope too — it was missed by an earlier draft of this ADR's
own implementation plan, caught in review, and is exactly the shape of
gap D6 exists to close: decoding and aggregating exit-code semantics
*inside* `aggregate`'s own workflow code, not only at its final encoder.

`RunOutcome` is a **report-level** aggregate; it is not a substitute for
a per-finding decision, and this decision does not ask it to be one, nor
does it touch `junit_report.py`'s own per-test-case pass/fail —
`_is_failure` already decides that per-render, from `(Change,
SeverityConfig, relevant_ids)`, and an earlier draft of this decision
tried repeatedly to move that answer onto a stored `Change` field
(first reusing `compatibility_decision`, deliberately `None`/
`NOT_EVALUATED` by design and therefore the wrong signal; then a new,
always-resolved field under several candidate stamping layers). Review
correctly rejected the whole direction, not just each individual layer:
the identical `DiffResult` can legitimately be rendered twice under two
different `SeverityConfig`s/`relevant_ids`, requiring opposite `_is_failure`
answers for the same `Change` — a single field stamped once on a shared
object cannot represent that, regardless of which layer stamps it. D6
therefore touches **no** per-finding field at all; `_is_failure` stays the
unchanged per-render function it already is. See the implementation
plan's Phase 7 for the full correction and for exactly what this decision
does and does not change.

### D7 — A declarative fact/capability registry

Every persisted, detected, or reported fact is declared once —
value type, producing backends, persistence, identity-relevance,
comparability, suppressibility — in a registry (`abicheck/model/
fact_registry.py` or equivalent), generalizing `change_registry.py`'s
existing `ChangeKindMeta` pattern (already required for every `ChangeKind`,
per AGENTS.md's "Adding a new ChangeKind" steps) from change *kinds* to
*facts*. Serialization, the backend capability matrix
(`docs/reference/*capability*`), coverage computation, and the AI-readiness
`changekind-*` checks are **generated or validated from this registry**
instead of hand-maintained in parallel, closing the "add a field, touch
nine files" cost the ELF-binding incident (PR #734) is the canonical
example of. Detector logic stays hand-written — this only removes the
*mechanical* plumbing, mirroring the distinction AGENTS.md's own decision
already draws between "the registry entry" and "implement detection in the
appropriate diff module."

**This decision's initial realization is narrower than "every fact,"
and a review round correctly found the implementation plan's own Phase 5
scope (availability-ambiguous fields guarded by a backend-reliability
flag — the ELF-binding incident's own shape) does not cover the full
population this paragraph names, without that gap being stated
anywhere.** An ordinary, always-present fact with no availability
ambiguity at all — an entity's name, a type's size, a symbol's binding,
a report-only derived value — has no "unavailable vs. genuinely absent"
question for `Fact[T]`/a reliability flag to resolve, so registering it
carries none of D7's stated benefit (closing exactly that ambiguity) and
would, at the scale of this codebase's model, mean an inventory entry for
essentially every dataclass field that exists — a different, much larger
project than the one Phase 5 actually ships. D7's own scope is therefore
the availability-bearing subset Phase 5 implements; registering the
remaining, unambiguous fact population is a real, legitimate directional
extension of this same registry, not ruled out by this ADR, but it is an
explicit, separately-justified future amendment — per this ADR's own
"don't attempt a change with no real caller" discipline (D1) — not a
claim this decision already covers.

A fact also carries an explicit lifecycle state (`MODELLED → PRODUCED →
NORMALIZED → PERSISTED → CONSUMED → REPORTED → PUBLIC`). A capability is
documented or exposed as a CLI option only once it reaches `PUBLIC` —
closing the repeated "shape shipped, wiring followed later" pattern
AGENTS.md records for the L3→L2 fold (`compare`'s implicit-dump path wired first,
`dump`'s CLI path following later, `scan`'s candidate resolution later
still).

### D8 — Finish storage v2 as domain/wire separation, nothing else

ADR-062 already states the correct target for persistence: content-
addressed sections, explicit fact availability, occurrence-preserving
identity, separated version axes, semantic-vs-operational payload
separation. This ADR adds one explicit rule to that target: **no phase of
storage v2 serializes a runtime/domain object directly** (no `asdict(
AbiSnapshot)`, no single large mirror deserializer the shape
`serialization.py`'s `snapshot_from_dict` has today — PR #696, `refactor:
cut CodeFactor complexity across the five reporting entry points`,
already had to de-duplicate part of that same function's structure once,
which is evidence the shape recurs rather than evidence it was fixed).
Each layer is `Domain SemanticIR → DTO vN → canonical wire
encoding`, and back, with a migration adapter per DTO version — so an
internal refactor (a synthetic key rename, a reordered field) is never, by
construction, a persisted-schema change. This decision does not change
ADR-062's phasing; it is an added constraint on Phase 1/2's implementation.

### D9 — A canonical semantic IR between raw backend output and the checker

CastXML, direct-clang, DWARF, PDB, and BTF/CTF (the Linux kernel debug
formats — each has its own type representation, `BtfType`/`CtfType`, and
its own `_TypeResolver`, genuinely exposed to the identical class of
scope/spelling-normalization disagreement the header-AST/DWARF backends
already have, even though no specific AGENTS.md incident has been filed
against them yet) each produce `RawXFacts`; a single `SemanticNormalizer`
(not duplicated per backend) turns each into one `SemanticIR` that the
comparison layer consumes. Canonicalization of type spelling, scope,
template arguments, anonymous/lambda naming, CV-qualification, and
identity happens exactly once, downstream of the backend, rather than once
per backend — closing the repeated-fix pattern AGENTS.md documents across
more than a dozen numbered findings in the `type_reachability.py`/
`dumper_clang.py`/`dumper_castxml.py` entries, each an instance of one
backend normalizing a construct the other backend handles differently (or
not at all). A backend adapter's contract narrows to "what did the tool
actually say" — it no longer decides how abicheck identifies a C++ entity.

**ELF/PE/Mach-O binary-symbol extraction is deliberately excluded from
this normalizer, not an oversight of scope.** A first draft of this
decision named it alongside the type-declaration-producing backends above;
review correctly pointed out the implementation plan never actually
migrated it, and on inspection there is nothing there *to* migrate in the
same sense: `elf_metadata.py`/`pe_metadata.py`/`macho_metadata.py` extract
symbol-table facts (a name string, a binding, a section/offset) with no
AST-level type declaration, template argument, or anonymous/lambda-naming
concern of the kind `SemanticNormalizer` exists to canonicalize — there is
no cross-backend type-spelling disagreement to close for a binary symbol
table, because there is no type spelling there at all. Scoped out
explicitly here rather than left as a silently-unmigrated item the
implementation plan's own completeness check would otherwise have to
explain away.

**"The comparison layer consumes" above names the target architecture,
not this decision's own phase's delivery bar — a review round correctly
asked where the checker actually migrates onto `SemanticIR`, and the
honest answer is: not in the phase that builds it.** The implementation
plan's Phase 6 assembles a real `SemanticIR` alongside every existing
backend (populating `AbiSnapshot.semantic_ir`, verified end to end against
every assembly call site) and makes it available to a future detector, but
every detector that ships with that phase still reads the legacy
`functions`/`types`/... projection, exactly as before — `checker.compare()`
needs no change there, and is given none. That is a deliberate, named
sequencing rather than an oversight: migrating the checker's own detectors
onto `SemanticIR` is a separate, much larger change (every `diff_*.py`
module, not one normalizer), and attempting it inside the phase that first
makes `SemanticIR` exist would mean validating both "is the IR correct"
and "does every detector still behave identically once reading from it" in
one unreviewable pass. The two representations can therefore disagree after
construction (Phase 6's own text says so), and neither is retired by this
ADR — that happens in whichever future, separately-scoped phase first has a
real `SemanticIR`-only detector population large enough that the legacy
projection has no remaining reader. This decision's own commitment is
narrower than "the checker consumes `SemanticIR`" read in isolation would
suggest: it is that a canonical IR exists, is assembled once per backend
instead of per detector, and is reachable — not that this ADR's own phases
complete the checker migration onto it.

### D10 — Selector/identity/availability as dependency-free leaf packages

`selectors/` (glob/regex/match grammar shared by suppression,
reclassification, and policy filtering), the `Fact[T]`/identity primitives
from D2/D3, and the fact registry from D7 are leaf packages with zero
dependency on `checker_types`, `reporter`, or any CLI module — generalizing
the fix direction PR #733 already took for one local import cycle
(`reclassify.py`'s `importlib.import_module` workaround to avoid importing
`suppression.py`) into a standing rule enforced by `scripts/
check_architecture.py`'s import-direction gate (ADR-061), not solved ad hoc
per occurrence. See the implementation plan's Phase 9 for the selector
half specifically: extracting the shared matching grammar into
`policy/selectors.py` is what actually lets `reclassify.py` drop its
`importlib.import_module` workaround, rather than this decision stating
the generalization as an aspiration with no phase that closes it.

## Relationship to existing ADRs

This ADR does not supersede any of the ADRs it builds on. It states how
their already-accepted decisions generalize and finish converging:

- **ADR-055** (typed request/result): D1 extends its CLI/API convergence
  from `compare` to every operation; D7's registry extends its
  schema-version-registry idea from wire formats to facts generally.
- **ADR-061** (responsibility packages): D1/D9/D10 are instances of the
  `extract/model/compare/policy/workflows/storage/report/frontends` ring
  structure it already defines; this ADR does not add new packages beyond
  what ADR-061 already names, and the `model` package is D2/D3/D7's home.
- **ADR-062** (storage v2): D8 is an added constraint on its existing
  phasing, and D2/D3 generalize its Phase 0 primitives (`FactAvailability`,
  occurrence-preserving identity) from storage-only to domain-wide. Nothing
  here changes ADR-062's own phase boundaries.
- **ADR-042** (compatibility/gate separation) and the `AnalysisAssurance`
  axis: D6 is their completion, not a new design — it closes the two
  remaining inline exit-code computations ADR-042 already flagged as
  unfinished.
- **ADR-046/048** (canonical entity identity, source-graph identity v2):
  D3 generalizes their identity model from source-graph matching to every
  identity-bearing subsystem (diff matching, graph identity, persistence)
  — **not** suppression selectors, which D3 itself now explicitly excludes
  (a first draft of this summary still listed them, contradicting that
  correction; fixed here to match).
- **ADR-049** (contract relevance/compatibility configuration): D4's
  `AnalysisPlan` deliberately does not relocate its D7 precedence
  resolver's own call site — per D4's own corrected scope, policy/pack
  resolution stays exactly where each front end's configuration seam for
  it already sits, since `AnalysisPlan` carries no policy field to give it
  a second one. D6's `RunOutcome` is where its `compatibility_decision`
  axis already lives and stays.
- **ADR-050** (comparability contract): D4 promotes its fingerprint checks
  from a post-extraction gate to a pre-extraction planning input where
  possible; the fingerprint mechanism itself is unchanged.
- **ADR-036** (report view-model): D6's `RunOutcome` is exactly the
  upstream object ADR-036's `ReportModel` should render — this ADR extends
  ADR-036's "renderer does not compute, only displays" rule to cover the
  gate/assurance/lifecycle axes ADR-036 predates.

## Consequences

**Benefits**

- A large, recurring class of defect (cross-representation disagreement)
  becomes structurally harder to introduce, not merely better-tested.
- New facts, new config fields, and new policy decisions have one
  integration point instead of N, shrinking both the review surface and
  the AGENTS.md "Known gaps" entries this exact pattern keeps producing.
- Generalized regression tests shrink and strengthen: a test proves one
  shared primitive's contract once, rather than re-proving "this config
  value reached every consumer" at each consumer.
- Performance improves as a side effect of correctness (D9's single
  normalization pass, a run-scoped semantic index replacing repeated
  re-derivation — see the CastXML XML-root-scanned-nine-times finding in
  AGENTS.md for a concrete case this already reproduces).

**Costs / risks**

- This is a multi-quarter, many-PR effort touching nearly every package.
  Each phase must be independently shippable and behavior-preserving, or
  the project accumulates exactly the kind of half-migrated parallel path
  (ADR-061's own "Phase 5 begun... the rest remains incremental" note is
  the honest precedent) this ADR is meant to prevent.
- Several of the backing ADRs (049, 050, 055, 061, 062) are themselves
  partially implemented. Sequencing matters — D1 depends on finishing
  specific, already-identified blockers in `service_dump_pipeline.py`
  (see AGENTS.md's "PR C" entry) before it can close; D8 depends on
  ADR-062 Phase 1 landing an actual writer/reader, not only the Phase 0
  primitives that exist today.
- A registry-generated capability matrix (D7) can mask a real gap as
  "modelled" if lifecycle-state discipline isn't enforced in code review,
  not only in the registry's own schema — this is the same vigilance
  AGENTS.md's "change the kind of tests that get written" section already
  asks for `ChangeKind` registration.
- D6 does **not** remove or change `junit_report.py`'s own inline
  `_is_failure` computation — its answer is a per-render function of each
  call's `SeverityConfig`/`relevant_ids`, not a property a finding carries,
  so there is nothing to migrate away from it, and an earlier draft of
  this section overstated D6's scope to include it. The behavior-risk-
  bearing change D6 actually makes is the aggregate report reader/encoder
  migration (`workflows/aggregate/gate.py`'s readers folding structured
  `RunOutcome.gate`/`.operational` fields, `fold.py::exit_code()` staying
  the unchanged external encoder) — that is what needs its own parity
  tests before and after, per the repository's "toolchain/wire format
  changes need a round-trip test at production scale" convention, run
  against both a legacy-`exit_code`-only fixture and a freshly-regenerated
  structured-field fixture.

**Explicitly not done by this ADR**

- No new CLI root command, no new Python public API class is promised by
  this decision alone — `RunOutcome`, `AnalysisPlan`, `Fact[T]`, `EntityId`
  are internal domain types unless and until a specific phase's plan
  explicitly promotes one to public API (and updates ADR-055's typed
  surface accordingly).
- No existing persisted schema (snapshot v25, `BundleFacts` v1, baseline
  set manifests) changes as a result of *accepting this ADR alone* —
  before any of its phases land, nothing about today's schemas is
  different. Once the implementation plan's phases do land, several
  schema migrations are explicit and intentional parts of specific
  phases, stated there and nowhere hidden: Phase 0 bumps
  `serialization.SCHEMA_VERSION` for the new `Fact[...]` fields (the
  same counter every prior reliability-flag addition already bumped),
  Phase 7 adds structured `RunOutcome` fields to the report JSON
  alongside the unchanged `exit_code`, and Phase 8 is ADR-062's own
  `ProjectSnapshot`/DTO schema migration, on ADR-062's own phasing,
  unchanged by this ADR. This bullet is about the decision to *adopt*
  ADR-063, not a blanket freeze on every schema touched by its own
  implementation.
- No existing exit code, JSON field, or CLI flag changes meaning as a
  result of accepting this ADR alone; D6/D7's changes are internal until a
  phase's own plan states an external contract change and the usual
  docs/test/changelog-fragment discipline applies.

See the [implementation plan](../plans/one-semantic-pipeline.md) for
phasing, sequencing against the blockers named above, file-level targets,
and acceptance criteria per phase.
