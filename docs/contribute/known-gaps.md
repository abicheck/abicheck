---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Known gaps — acknowledged remaining work

This page lists the gaps that are still open, the fix attempts that were
tried and should not be repeated, and the deliberate limitations that look
like bugs but are not. Per `AGENTS.md`'s "Fix the cause, not the instance"
principle, **read the entry for an area before re-attempting a fix there**:
several entries record a heuristic or a narrow patch that looked like the
obvious fix, was reviewed, and was reverted.

Closed entries, and entries whose defect lived only in deleted code (the
`scan` family removed by ADR-068 Phase 6), are kept for history in
[Known gaps — closed and moot](archive/known-gaps-closed.md). Look there
before re-reporting a gap that is no longer listed here.

## How to read this page

Every entry was re-verified against the code on 2026-10-09 at `456989f`,
and each one opens with a status line:

| Status | Meaning |
|---|---|
| OPEN | The gap is present as described. |
| PARTIAL | Part of the entry is fixed; the status line names what remains. |
| NEGATIVE_RESULT | An approach measured or reviewed as not helping. Do not re-attempt it. |
| POLICY_NOTE | A deliberate ruling or scope limit, not a defect. |

The status line overrides older wording in the entry body. When you fix a
gap, move its entry to the archive page with a CLOSED status line rather
than striking it through here. When you re-check an entry, update its
status line's date and commit.

## Page layout

Each open entry below has its status line, a risk tag and a short summary; the full write-up, with the history of what was tried, is one file per entry under `docs/contribute/known-gaps/`. Index rows are ranked: the Priority list, then `false-clean` (a run can read cleaner than its evidence), `false-positive` (a finding or failure that is not real), `perf`, and `hygiene` (tests, tooling, schema or code-ownership debt). When you fix, re-check or add an entry, edit its write-up and keep the index row's status line in sync.

## Priority

Open correctness gaps, in the order of the maintainer ruling of 2026-10-01 (closed items from that ruling are in the archive):

Next, unranked (the `--no-baseline` one-sided-detector gap, the target-platform gap and the untagged `dependency_scope` gap were closed on 2026-10-10, and the informational-outcome strict-gate gap on 2026-10-12; see the archive). Each of these can let a run read as cleaner or more comparable than its evidence supports:

### A snapshot's `build_mode` is captured only for ELF, and only from the main image

**Status (re-verified 2026-10-11 at `46c127111`): PARTIAL.** Unchanged since 2026-10-10: `extract/build_mode_capture.py` is ELF-only and its only caller is `workflows/snapshot_factory.py`. PE/Mach-O get no `build_mode`, detached debuginfo is not read, and nothing compares compiler family across snapshots.  
`false-clean` — `build_mode` comes only from an ELF main image; PE/Mach-O snapshots and detached debuginfo carry none, and compiler family is never compared across snapshots, so a toolchain switch reads as comparable. [Full write-up](known-gaps/a-snapshots-build-mode-is-captured-only-for-elf-and-only-from.md)

### A stored package's generation drift is reported only for `ProjectSnapshot` packages, and generations are bumped by hand

**Status (re-verified 2026-10-11 at `46c127111`): PARTIAL.** Unchanged since 2026-10-10. `EXTRACTOR_GENERATION`/`RESOLVER_GENERATION` now exist (`storage/versioning.py`), every package writer stamps them, `project_snapshot_store` passes them to `check_reader_compatibility`, and drift appears in `coverage_warnings` without changing verdict or exit code; an unrecorded generation is unknown. Importers stamp the generations of the build that extracted the facts: this build's only for facts it just dumped (`bundle variants` capture from binaries), a caller-supplied pair when known, else unstated (`StorageVersions.written_by_this_build`'s `generations`). Still open: the `BundleFacts` reader passes them but has no report surface; nothing enforces a bump when extraction or resolution semantics change.  
`false-clean` — Extractor/resolver generation drift is surfaced only for `ProjectSnapshot` packages (`BundleFacts` has no report surface), and nothing forces a generation bump when extraction semantics change. [Full write-up](known-gaps/a-stored-packages-generation-drift-is-reported-only-for.md)

### Scalar and release `compare` still fold the exit code in two places (2026-10-07)

**Status (re-verified 2026-10-11 at `46c127111`): OPEN, narrowed.** #1532 moved the release compatibility-axis fold into `workflows/release_exit_fold.py`, which folds each member's scalar-resolver contribution. Two precedence resolvers still exist: `policy/exit_decision.py::resolve_compare_exit_decision` and `policy/exit_decision_precedence.py::resolve_release_exit_decision`, the latter still owning the release-only axes listed below.  
`hygiene` — Scalar and release `compare` still use two precedence resolvers (`resolve_compare_exit_decision` and `resolve_release_exit_decision`), so the exit-code rules can drift between the two paths. [Full write-up](known-gaps/scalar-and-release-compare-still-fold-the-exit-code-in-two.md)

## Open and partially open gaps

Each entry's status line says what is still open. A PARTIAL entry's write-up also describes the half that is already fixed; the status line is authoritative where they disagree.

### False-clean risk

#### A `kind: bundle` check still cannot run at `depth: headers`: only the *baseline* half of per-member header staging is wired (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** actions/check-target/action.yml:1828 still routes bundle old-library to `steps.resolve.outputs.binaries-dir`, not `member-snapshots-dir`. BUNDLE_CHECK_DEPTHS is unchanged (abicheck/buildsource/bundle_member_snapshots.py:18,69). So (a) routing and (b) candidate-side per-member headers are still open.  
`false-clean` — Bundle checks can stage per-member *baseline* header snapshots, but the Action still routes the old side to binaries and the candidate side has no per-member headers, so a bundle check cannot run at `depth: headers`. [Full write-up](known-gaps/a-kind-bundle-check-still-cannot-run-at-depth-headers-only-the.md)

#### Header-pass body coverage is one blanket unit, not per body (2026-10-11)

**Status (recorded 2026-10-11 at `2a1ccb0c8`): OPEN.** source_graph_coverage gives header passes a single HEADER_INLINE_BODIES unit; no per-declaration body fact exists.  
`false-clean` — Body-dependent call/reference changes in an inline function visible on both sides are declined, because coverage cannot prove that specific body was seen. [Full write-up](known-gaps/header-body-coverage-is-not-per-body.md)

#### Opaque-type suppression still falls back to bare RecordType.name when stable identities are incomplete

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** compare/opaque_types.py:74-130 now uses OpaqueTypeIndex carrying stable EntityIds plus a bare-name 'local' tier. contains(strict=True) narrows the bare-name collision only when intersect() proves the pairing is complete, and otherwise falls back to the old bare RecordType.name matching. diff_filtering.py:854 _root_type_name now prefers Change.qualified_name.  
`false-clean` — Opaque-type suppression keys on stable `EntityId`s only when the pairing is proven complete; otherwise it falls back to bare `RecordType.name`, so same-named types in different scopes can suppress each other. [Full write-up](known-gaps/opaque-type-suppression-still-falls-back-to-bare-recordtypename.md)

#### Type identity: typedef and tag names keyed by bare spelling

`false-clean` — Two entries on one shape: a typedef's identity is its bare spelling, so a tag-namespace record and a same-spelled typedef collide in the clang parser, and typedef diffing still reads the bare-keyed `AbiSnapshot.typedefs` instead of the qualified twin.

- [`dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace identity with an ordinary-namespace typedef identity that happens to share the same spelling — pre-existing, not introduced by PR #719's own changes, but investigated and confirmed reachable in this pass (Codex review, investigated, not fixed)](known-gaps/dumper-clangpys-parse-types-conflates-a-cc-tag-namespace.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** The code moved to extract/headers/clang/records.py:145, where identity = "::".join([*entry.scope, name]) still uses the anon-typedef fallback name. Tag and ordinary namespaces are not distinguished, so `struct Foo; typedef struct{} Foo;` still collides.
- [Qualified typedef twin exists but diff_types/surface still read bare-keyed AbiSnapshot.typedefs](known-gaps/qualified-typedef-twin-exists-but-diff-typessurface-still-read.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** AbiSnapshot.typedefs_qualified now exists (model/snapshot.py:466; dumper_castxml_typedefs.py:105) and type_reachability reads it. diff_types.py typedef diffing and surface.py typedef-following are still not wired to it.

#### The castxml L4 source-ABI extractor does not fold the resolved EMULATED compiler's identity into either `fact_set.compiler_version` or the D8 TU cache key — attempted once for the persisted half, and REVERTED after a follow-up review caught it as a real regression, not a fix (Codex review, PR #719, three follow-up rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** buildsource/source_extractors/castxml.py:700-738 still resolves cc_bin. compiler_version is _castxml_identity_with_stat_fallback(self.castxml_bin), and the comment at line 734 says a `cc_bin --version` probe is still future work.  
`false-clean` — The castxml L4 extractor's persisted `compiler_version` and TU cache key omit the emulated compiler's identity, so changing that compiler can reuse stale facts. One attempted fix was a regression and was reverted. [Full write-up](known-gaps/the-castxml-l4-source-abi-extractor-does-not-fold-the-resolved.md)

#### `advanced_facts_collected` infers "advanced DWARF extraction ran" from its output rather than recording it

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/dwarf_facts.py:181 advanced_facts_collected still infers that extraction ran. The comment at line 229 says an explicit 'advanced extraction completed' status is still future work.  
`false-clean` — Whether advanced DWARF extraction ran is inferred from its output, so a run that produced nothing is indistinguishable from one that never ran. [Full write-up](known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md)

#### `Function.elf_binding`/`Variable.elf_binding` (and the pre-existing `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned aliases sharing one bare name — investigated, not fixed (Codex review, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/elf_facts.py:238-244 symbol_map is still `{s.name: s for s in self.symbols}`, last entry wins, which collapses versioned aliases.  
`false-clean` — Symbol-versioned aliases sharing one bare name collapse into one binding/visibility (last entry wins), so a binding change on one alias can be hidden. [Full write-up](known-gaps/functionelf-bindingvariableelf-binding-and-the-pre-existing-elf.md)

#### L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/base.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.  
`false-clean` — An `icpx`/`dpcpp` override now gets the right `-fsycl` flags and no longer crashes, but L4 replay still runs one context; host+device dual-context replay is not implemented. [Full write-up](known-gaps/l4-sycl-replay-via-a-resolved-gcc-path-icpxdpcpp-override-flag.md)

#### Include-graph dependency files are never captured in production

`false-clean` — `depfile_args_from_argv`'s `trusted_root` has no production caller, so `@file` tokens are dropped, and the L2 header parse never writes a dependency file, so `depfile_resolved_paths` is never fed.

- [`depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail vulnerability is closed, real production wiring not implemented](known-gaps/depfile-args-from-argvs-trusted-root-parameter-the-self-jail.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep finds no `trusted_root=` argument at any production call site; only buildsource/include_graph.py defines the parameter (lines 119-216), so production callers still drop the @file token.
- [The L2 header parse never captures a dependency file (2026-10-03)](known-gaps/the-l2-header-parse-never-captures-a-dependency-file-2026-10-03.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** depfile_resolved_paths is referenced only inside abicheck/comparability.py:563, and no production caller passes it.

#### `Param.is_va_list` (G31 Phase C continued) inherits the toolchain- identity-probe gap above, rather than being a new problem

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** snapshot_reliability.py:182 and fact_registry_schema.py:273 have clang_va_list_facts_reliable as a plain flag without target identity, and no toolchain-identity probe exists yet (see entry 9).  
`false-clean` — `clang_va_list_facts_reliable` is a plain flag with no target identity, inheriting the toolchain-identity-probe gap rather than being a new defect. [Full write-up](known-gaps/paramis-va-list-g31-phase-c-continued-inherits-the-toolchain.md)

#### A using-declaration re-exporting a namespace-scope constant produces two `AbiSnapshot.constants` entries for one declaration on the castxml backend — reported against real oneDAL headers. A per-snapshot, name-shape-based dedup was implemented, reviewed, found unsound in two independent ways, and reverted rather than shipped broken (Codex review, three rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The entry's own later verification (known-gaps.md ~4030-4068) found both backends now drop the alias spelling (a false negative), so the alias-identity gap remains open. There is no UsingShadowDecl threading in the code.  
`false-clean` — A using-declaration re-exporting a namespace-scope constant: the reverted dedup was unsound; both backends now drop the alias spelling (a false negative) and no `UsingShadowDecl` identity is threaded. [Full write-up](known-gaps/a-using-declaration-re-exporting-a-namespace-scope-constant.md)

#### EntityId scope collisions in template declarations

`false-clean` — `requires`-constrained overloads with the same signature share one `EntityId`, and an out-of-line member template definition gets a different `EntityId` scope from its in-class declaration in the header parser.

- [Two function/method template overloads distinguished only by a `requires`-clause still collide under ADR-063 Phase 2's `EntityId` discriminator (Codex review, PR #943, fresh evidence)](known-gaps/two-functionmethod-template-overloads-distinguished-only-by-a.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/buildsource/template_graph.py:102-122 still defers CONSTRAINT_DEPENDS_ON_DECL and says requires-constrained overloads with the same signature collide. abicheck/extract/headers/clang has no ConceptSpecializationExpr handling.
- [Out-of-line member template definition gets a different EntityId scope than its in-class declaration (header parser; template_graph fixed)](known-gaps/out-of-line-member-template-definition-gets-a-different.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The template_graph half is fixed: abicheck/buildsource/template_graph.py:1207-1211 uses parentDeclContextId. No parentDeclContextId use exists in dumper_clang.py (_ClangAstParser at :743) or in extract/headers/clang, so the EntityId scope split is still present.

#### Dependency static/dynamic linking-mode change has no ChangeKind

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** docs/contribute/abi-taxonomy-coverage.md:177 still lists dependency-abi.linking-mode-change as NOT_IMPLEMENTED, and no ChangeKind containing linking_mode exists in abicheck/model/change_catalog.  
`false-clean` — A dependency switching between static and dynamic linking has no `ChangeKind` (`dependency-abi.linking-mode-change` is NOT_IMPLEMENTED). [Full write-up](known-gaps/dependency-staticdynamic-linking-mode-change-has-no-changekind.md)

#### AArch64 AAPCS64 by-value aggregate passing is not modeled

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/dwarf_advanced.py:885-893 models only SysV AMD64 (_SYSV_AMD64_RETURN_ARCHES), and its comment says an AArch64 HFA flip is just a generic value-ABI change.  
`false-clean` — By-value aggregate passing is modelled only for SysV AMD64; an AArch64 HFA/HVA flip reads as a generic value-ABI change or not at all. [Full write-up](known-gaps/aarch64-aapcs64-by-value-aggregate-passing-is-not-modeled.md)

#### A resolved PDB/DWP/dSYM artifact reaches no dump path

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/dumper.py:1089 _dump_pe has no debug_roots parameter, and dwp_path/dsym_path are referenced only in extract/debug_artifact.py (no dump consumer). The directory form still resolves PDB/DWP/dSYM artifacts that are then dropped.  
`false-clean` — The directory form resolves PDB/DWP/dSYM artifacts, but no dump path consumes them, so the debug evidence is dropped. [Full write-up](known-gaps/a-resolved-pdbdwpdsym-artifact-reaches-no-dump-path.md)

#### A labelled side-scoped `--include` appeared to suppress unlabelled global include roots

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: the entry itself has no minimal reproduction. The labelled/unlabelled --include merge in frontends/cli/options/params.py:187+ was not exercised with castxml here.  
`false-clean` — Unverified, no minimal reproduction: a labelled side-scoped `--include` appeared to drop unlabelled global include roots. [Full write-up](known-gaps/a-labelled-side-scoped-include-appeared-to-suppress-unlabelled.md)

#### A stored snapshot keeps no record of a partial export-table read

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The entry says closed, but it also says the partial read is still open. Fixed: FAILED import reads; tests/unit/workflows/test_failed_consumer_read.py exists. Open: a stored snapshot cannot record a partial export-table read (needs a schema field and ADR).  
`false-clean` — FAILED import reads are fixed, but a stored snapshot cannot record that its export-table read was partial (needs a schema field and ADR). [Full write-up](known-gaps/a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read.md)

### False-positive risk

#### `--lang c++` explicitness not threaded to remaining fan-out callers (l0_export_delta/appcompat); dump/compare fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed for dump/compare CLI and the DumpRequest/CompareRequest API. `lang_explicit` is threaded through frontends/cli/commands/dump.py, compare.py, workflows/dump/{native,pe,macho}.py and workflows/artifact/*, and release_member_request.py now carries it too. The scan follow-up is moot (scan was deleted). appcompat and l0_export_delta are unverified. l0_export_delta.py takes a plain `lang: str` with no lang_explicit, so that follow-up looks still open.  
`false-positive` — An explicit `--lang c++` reaches dump/compare, but `l0_export_delta` (plain `lang: str`) and appcompat still guess the language themselves. [Full write-up](known-gaps/lang-c-explicitness-not-threaded-to-remaining-fan-out-callers.md)

#### A compatible-but-ambiguous opaque redeclaration set (`class H; struct H;`) that later gains a same-key COMPLETE definition still produces a false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex review, PR #719, fourth follow-up round on this same area)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for kind_ambiguous in abicheck/ finds nothing, so RecordType has no provenance for an ambiguous forward-declaration kind.  
`false-positive` — `class H; struct H;` followed by a complete definition produces a false `SOURCE_LEVEL_KIND_CHANGED`, because `RecordType` keeps no provenance for an ambiguous forward-declaration kind. [Full write-up](known-gaps/a-compatible-but-ambiguous-opaque-redeclaration-set-class-h.md)

#### A pre-fix clang-backend baseline compares against a post-fix candidate as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards `Function.is_inline`

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for inline_facts_reliable in abicheck/ finds nothing. diff_symbols.py:477 _check_inline_transitions has no reliability or producer gate.  
`false-positive` — A baseline from the pre-fix clang backend compared against a post-fix candidate reports a wave of `FUNC_BECAME_INLINE`; no reliability flag gates `Function.is_inline`. [Full write-up](known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md)

#### type_base_changed declines on incomplete bases facts but still fires on a PRESENT-but-empty capture gap

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The vtable half is fixed. compare/base_class_diff.py:36-71 diff_bases now gates bases_fact/virtual_bases_fact through compare_facts and declines when a side is NOT_COLLECTED/FAILED/PARTIAL. A PRESENT-but-empty capture gap can still fabricate type_base_changed, as the entry describes.  
`false-positive` — `type_base_changed` now declines on NOT_COLLECTED/FAILED/PARTIAL bases facts, but a PRESENT-but-empty capture gap still fabricates the finding. [Full write-up](known-gaps/type-base-changed-declines-on-incomplete-bases-facts-but-still.md)

#### Type reachability (direct vs. transitive stdlib references) — computed and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef paths remain unwired

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** type_reachability.py is wired into the RecordType detectors (diff_types_surface.py and others), but the diff_types.py:1113-1118 enum paths still call _is_non_abi_surface_type(e.name) directly, as the entry says.  
`false-positive` — Direct-vs-transitive stdlib reachability is wired into the `RecordType` detectors only; enum and typedef paths still use the bare-name non-ABI-surface check. [Full write-up](known-gaps/type-reachability-direct-vs-transitive-stdlib-references.md)

#### Closure-parameterized non-ctor functions still churn (func_params_changed) on lambda line shifts; '?' ctor param unexplained

**Status (re-verified 2026-10-12 at `b694ccc2f`): PARTIAL.** Type-level marker fix, ctor/dtor binary-evidence fix and L5 declaration_renamed fix (model.graph_identity.closure_location_free_identity) are recorded as landed. Non-ctor func_params/return churn on an inserted unrelated closure is fixed for exported symbols (`compare/closure_respelling.py`). Still open: the same churn on unexported/inline declarations (`make_guard`, template members, synthetic ctor keys; fails closed, decision proposed in ADR-079 D1), `exported_not_public` noise on identical builds (ADR-079 D2), the mangled-only surface filter gap, and residual (2), the literal '?' ctor parameter (not reproduced on any dump in this work).  
`false-positive` — Non-ctor functions parameterized over a lambda churn when an unrelated closure is inserted earlier in the header; fixed for exported symbols. Still open: inline/unexported declarations and identical-build `exported_not_public` noise (both decisions proposed in ADR-079), the literal `?` ctor parameter (not reproduced), and the mangled-only surface filter gap. [Full write-up](known-gaps/closure-parameterized-non-ctor-functions-still-churn-func.md)

#### The L5 source graph's own node identities are never renumbered alongside the flat snapshot's closure markers (Codex review on PR #868, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/snapshot_load_normalization.py:123-138 docstring still says surface_graph node/edge identities are not renumbered. renumber_anonymous_closure_identities (storage/closure_identity.py) has no graph handling.  
`false-positive` — Closure markers in the flat snapshot are renumbered on load, but the L5 `surface_graph` node and edge identities are not, so the two disagree. [Full write-up](known-gaps/the-l5-source-graphs-own-node-identities-are-never-renumbered.md)

#### Declared-header order is still load-bearing because the driver TU is sequential

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The scope-confirmed header growth waiver landed (comparability.py:845,1298 reference _header_sequence_is_scope_confirmed_growth). Still open: the sequential aggregate driver TU itself, so reordering an existing header still refuses.  
`false-positive` — The scope-confirmed header-growth waiver landed, but the aggregate driver TU is still sequential, so reordering an existing header refuses the comparison. [Full write-up](known-gaps/declared-header-order-is-still-load-bearing-because-the-driver.md)

#### Export-loss ownership: inline-copy proxy and template-specialization owner remain open (see export-loss-ownership-root-cause.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** This is a pointer stub to export-loss-ownership-root-cause.md. It still lists two open items: is_inline is used as a proxy, and there is no Itanium type decoder for specialization owners. Not re-verified in code beyond that.  
`false-positive` — Export-loss ownership still uses `is_inline` as a proxy for an inline copy and has no Itanium decoder for template-specialization owners; see `export-loss-ownership-root-cause.md`. [Full write-up](known-gaps/export-loss-ownership-inline-copy-proxy-and-template.md)

#### MinGW toolchain headers are not classified as dependencies (2026-09-23)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/provenance.py:270 _is_toolchain_compiler_include_dir still recognises only lib/gcc/<triple>/<ver>/include, lib/clang, and castxml layouts. provenance.py has no handling for MinGW's <prefix>/<triple>/include.  
`false-positive` — Toolchain include detection knows GCC/clang/castxml layouts but not MinGW's `<prefix>/<triple>/include`, so MinGW headers are not classified as dependencies. [Full write-up](known-gaps/mingw-toolchain-headers-are-not-classified-as-dependencies-2026.md)

#### The clang JSON header backend records no resolved type identities, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public` (2026-10-01)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for type_identities under abicheck/extract/headers/clang/ finds nothing, so the clang backend still records no identities.  
`false-positive` — The clang JSON backend records no `type_identities`, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public`. [Full write-up](known-gaps/the-clang-json-header-backend-records-no-resolved-type.md)

#### A library's own `_Float128` API has no correct mangled name from castxml

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: castxml mangling was not run. The entry records the defect as an unresolved castxml limitation: _Float128 functions read as not exported.  
`false-positive` — Unverified: castxml gives a library's own `_Float128` functions no correct mangled name, so they read as not exported. [Full write-up](known-gaps/a-librarys-own-float128-api-has-no-correct-mangled-name-from.md)

### Performance

#### Bundle and multi-library memory

`perf` — Three retention costs on the multi-library path: `read_bundle_facts_package` holds raw JSON and decoded snapshots together, every member snapshot stores its own copy of the shared header evidence, and `--bundle-facts-out` pins OLD snapshots per member.

- [`bundle_facts_store.read_bundle_facts_package` holds a bundle's raw JSON and its decoded `AbiSnapshot`s concurrently at peak](known-gaps/bundle-facts-storeread-bundle-facts-package-holds-a-bundles-raw.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/import_bundle_facts.py:597 export_bundle_facts still returns one whole dict[str, Any] document (no streaming form). It is bounded by DEFAULT_MAX_BUNDLE_DECODED_BYTES, as the entry notes.
- [The release public surface is acquired once, but each member snapshot still stores its own copy of the header evidence](known-gaps/the-release-public-surface-is-acquired-once-but-each-member.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** The per-member header evidence is still stored once per member. abicheck/storage/import_bundle_facts.py:107 still has _BUNDLE_FACTS_SCHEMA_VERSION = 3, so it still refuses schema 4.
- [Multi-library compare memory: --bundle-facts-out still pins OLD snapshots per member](known-gaps/multi-library-compare-memory-bundle-facts-out-still-pins-old.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed: JUnit uses SymbolInventory, compressed baseline streaming, and the attrs aliasing. Still open: OLD-side retention for --bundle-facts-out (no spooling handle). Graph sharing and Fact pooling were rejected as negative results, and the surface_graph ordering question is unverified.

#### Interpreter startup dominates a small-input CLI run

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Measurement only. The entry says import cost was never investigated, and no lazy-import work is recorded; the Click registration-by-import pattern is unchanged. unverified: wall time was not re-measured.  
`perf` — On a small input the run is dominated by interpreter startup and import cost, which was never investigated. [Full write-up](known-gaps/interpreter-startup-dominates-a-small-input-cli-run.md)

#### The `clang -M` include-graph pass

`perf` — The include pass is memoized per process only: it is never persisted across runs, never batched, and a warm AST cache does not shorten it.

- [The `clang -M` include-graph pass is per-header and cached only in-process, never across runs](known-gaps/the-clang-m-include-graph-pass-is-per-header-and-cached-only-in.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** abicheck/buildsource/header_include_memo.py now holds a process-wide memo, keyed in header_graph.py:635-650, so release members and sides no longer re-run the same clang -M set. Still open: the memo lives only in-process (never persisted across runs), and nothing batches per-header invocations.
- [The `clang -M` include pass is unaffected by a warm AST cache — confirmed at `--repeat 3`](known-gaps/the-clang-m-include-pass-is-unaffected-by-a-warm-ast-cache.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** header_include_memo.py is a process-wide memo with no persistent/AST-cache tier, so a new run still recomputes the include graph whatever the warm AST cache holds.

#### A directory `compare`'s per-member header narrowing is ELF-only

**Status (2026-10-11): OPEN.** A PE or Mach-O member of a directory/package `compare` still parses the union `-H` set, because `workflows/release_member_headers.py` reads exports only from ELF. A second, ELF-side limit: an export spelled only through macro token pasting is not matched to its header (see the write-up).
`perf` — The narrowing reads ELF export tables only (`elf_symbol_filter.exported_symbol_names`). For a PE or Mach-O member it finds no export tokens and falls back to the union operand. That fallback is safe (it over-reports and never hides a finding), but it leaves the per-member union walk and the duplicated type findings in place on those platforms. The parent entry is closed (see the [archive](archive/known-gaps-closed.md)). [Full write-up](known-gaps/a-directory-compare-s-per-member-header-narrowing-is-elf-only.md)

#### Multi-library L2 compare scales quadratically with library count

`perf` — Memoization landed (scaling budget 1.7), and step 2 (2026-10-11) means an ELF member no longer walks the union header set; the scaling budget has not been re-measured since.

- [Multi-library L2 compare scales quadratically with library count](known-gaps/multi-library-l2-compare-scales-quadratically-with-library-count.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The memoization landed: the libraries budget is 1.7 in scripts/check_l2_scaling_perf.py:103 and header_scan_memo/include memo exist. Still open: every member walks the union header set and runs the per-member field parse (step 2 of entry 73).

#### Spelling alternation matcher scales linearly with vocabulary (nested same-offset bug fixed)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The under-report half is fixed and pinned by tests/test_spelling_nested_same_offset.py. The entry itself says the alternation-scaling half (linear in vocabulary size) remains open.  
`perf` — The nested same-offset under-report is fixed; the alternation matcher still scales linearly with vocabulary size. [Full write-up](known-gaps/spelling-alternation-matcher-scales-linearly-with-vocabulary.md)

#### A member's peak memory is the clang JSON AST, not the header graph (2026-09-19)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The 'currently ungated' clause is closed: scripts/check_header_graph_perf.py:267 defines MEMORY_METRICS attach_peak_rss_mib/attach_end_rss_mib. The peak from materialising the clang JSON AST is still open, and the empty-list reduction is a recorded negative result.  
`perf` — A member's attach peak is the clang JSON document plus its parsed tree, not the header graph; the memory metrics are now gated, the peak itself is not reduced. [Full write-up](known-gaps/a-members-peak-memory-is-the-clang-json-ast-not-the-header.md)

#### Python-level AST walks, the side boundary and the snapshot cache dominate large runs (2026-10-11)

**Status (2026-10-11): OPEN.** Location-pass, cache trace and cache write switch landed; threads-on-free-threaded, a faster `json_compact` and pickle protocol 5 were measured on oneDAL and rejected; walk fusion is smaller than profiled (cProfile ~7x); closure renumbering after scoping and the cold GIL member-parallelism A/B remain.  
`perf` — The clang JSON tree is walked at least eight times in Python, snapshots are pickled across the side boundary, and populating the snapshot cache costs ~30 % wall on a 28-member release. [Full write-up](known-gaps/python-level-ast-walks-side-boundary-and-cache-costs.md)

### Hygiene

#### Release report: live and stored operands render different documents

**Status (recorded 2026-10-11): OPEN.** Both operands go through `report.release_document.build_release_report` and agree on every shared semantic field (`tests/unit/report/test_release_document.py`), but each yields its own JSON/Markdown shape and the stored operand has no JUnit, HTML, `oneline` or `--view show=` projection.
`hygiene` — Converging the shapes changes a published JSON document, so it needs a release-schema decision. [Full write-up](known-gaps/release-report-live-and-stored-operands-render-different-documents.md)

#### Public-surface graph: surface_graph vs L5 header_graph node-id namespaces not unified; type_reachability stdlib query unmigrated

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The traversal half is superseded: the closure walk moved to policy/public_surface_closure.py. Still true: abicheck/type_reachability.py is unmigrated, and the compare/surface_graph.py and header_graph node-id namespaces are not unified.  
`hygiene` — The closure walk moved to `policy/public_surface_closure.py`, but `type_reachability.py` is unmigrated and the surface-graph and header-graph node-id namespaces are still separate. [Full write-up](known-gaps/public-surface-graph-surface-graph-vs-l5-header-graph-node-id.md)

#### `action/run.sh`'s `extra-args` parsing performs pathname expansion (globbing), not just word-splitting, and no site disables it — investigated, deliberately not fixed (CodeRabbit review, PR #998, ADR-064's effective-format-override fix)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** action/run.sh:2235 `set -- ${INPUT_EXTRA_ARGS:-}` and :3893 `CMD+=($INPUT_EXTRA_ARGS)` are unquoted, with no set -f around them. set -f appears only at :189 in _split_legacy_value.  
`hygiene` — `extra-args` is split unquoted with no `set -f`, so glob characters in it expand against the runner's working directory. Deliberately not fixed. [Full write-up](known-gaps/actionrunshs-extra-args-parsing-performs-pathname-expansion.md)

#### `BundleFacts` (and its G40 archive container) has no published JSON Schema, in either `abicheck/schemas/` or `docs/reference/schemas/`

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/model/bundle_facts.py:53 BUNDLE_FACTS_SCHEMA_VERSION = 4, but abicheck/schemas/ and docs/reference/schemas/v1/ hold no bundle_facts schema (only aggregate/audit/audit_set/build_evidence/build_source_pack/compare). The module path in the entry is stale (bundle_facts.py is now model/bundle_facts.py).  
`hygiene` — `BundleFacts` (schema version 4) and its archive container have no published JSON Schema in `abicheck/schemas/` or `docs/reference/schemas/`. [Full write-up](known-gaps/bundlefacts-and-its-g40-archive-container-has-no-published-json.md)

#### The weekly `Mutation testing` scheduled lane (`.github/workflows/mutation.yml`, job `mutmut (detector core)`) can outgrow its own job timeout before producing a receipt — investigated, partially mitigated, not fully fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** .github/workflows/mutation.yml:327 timeout-minutes: 355 (mitigation landed). The entry itself says there is no measurement that a full run completes, and only_mutate scoping/splitting is not done.  
`hygiene` — The weekly `mutmut (detector core)` job's timeout was raised to 355 minutes, but no run is measured to finish inside it and `only_mutate` scoping/splitting is not done. [Full write-up](known-gaps/the-weekly-mutation-testing-scheduled-lane.md)

#### Retired `--crosscheck` promotion axis still serialized in ExitDecision and read by action/run.sh

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The engine half is gone: scan_engine.py/cli_scan*.py are deleted, policy/exit_decision.py:147-154 marks PROMOTED_CROSSCHECK a read-only retired producer, and resolve_exit_decision no longer takes it. Residue: ExitDecision still serializes crosscheck_promotion_contribution (exit_decision.py:322,435; compare_report.schema.json:2058) and action/run.sh:5125,5183-5191 still reads promoted_crosscheck.  
`hygiene` — The `--crosscheck` engine is gone, but `ExitDecision` still serializes `crosscheck_promotion_contribution` and `action/run.sh` still reads `promoted_crosscheck`. [Full write-up](known-gaps/retired-crosscheck-promotion-axis-still-serialized-in.md)

#### SARIF suppression justification carries only the display label, not the full disposition record

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The cli_scan_baseline.py half is moot because the file was deleted. The sarif.py half is still open: abicheck/sarif.py:963 still interpolates only change.suppression_rule ('suppressed by --suppress rule: ...') and never reads DispositionLedger.rule_for.  
`hygiene` — SARIF suppression justifications carry only the `--suppress` rule label, never the full `DispositionLedger` record. [Full write-up](known-gaps/sarif-suppression-justification-carries-only-the-display-label.md)

#### `compare`'s migrated cross-source checks drop `--since`'s changed-path confidence boost

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** workflows/cross_source_evolution.py:360 is still `def compute_cross_source_evolution(old, new)` with no changed_paths parameter, and checker.py has no changed_paths.  
`hygiene` — `compute_cross_source_evolution` takes no changed paths, so `compare --since` loses the changed-path confidence boost the old scan path applied. [Full write-up](known-gaps/compares-migrated-cross-source-checks-drop-sinces-changed-path.md)

#### `compare --depth binary` skipping matrix-wide `--sources`/`--build-info` is tested only on snapshot operands, which cannot prove it

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** tests/test_scenarios.py:653 test_sc_scan_binary_depth_matrix_args was revived, but per docs/contribute/usecase-registry.yaml:1040-1055 it runs on stored JSON snapshots (tests/scenarios/ci_gating.yaml:120), so it cannot tell 'binary depth skipped collection' apart from 'snapshot inputs always skip it'.  
`hygiene` — The only test runs on stored JSON snapshots, which always skip collection, so it cannot prove that `--depth binary` skips matrix-wide `--sources`/`--build-info`. [Full write-up](known-gaps/compare-depth-binary-skipping-matrix-wide-sources-build-info-is.md)

#### ADR-061 gap F: nested `buildsource`/`impact` modules still carry no disposition

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The compat/* modules named in the entry are gone (compat front end removed). Several named nested modules still exist with no architecture/*.yaml mention, e.g. buildsource/build_evidence.py, build_output.py, evidence_policy.py, fact_set.py, merge_support.py, source_graph.py and impact/use_case_impact.py (0 hits).  
`hygiene` — Several nested `buildsource/` and `impact/` modules (e.g. `build_evidence.py`, `fact_set.py`, `impact/use_case_impact.py`) have no ADR-061 disposition in `architecture/*.yaml`. [Full write-up](known-gaps/adr-061-gap-f-nested-buildsourceimpact-modules-still-carry-no.md)

#### A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** action/validate-inputs.sh:327-338 still checks only the release allowlist (json|markdown|junit|oneline|html) and has no stored-BundleFacts narrowing. No --probe-formats capability exists.  
`hygiene` — `action/validate-inputs.sh` checks only the release format allowlist, so a format the stored-`BundleFacts` baseline cannot render fails late, not at input validation. [Full write-up](known-gaps/a-stored-bundlefacts-baselines-narrower-format-set-is-not-pre.md)

#### `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** --bundle-facts-out is still a compare option (frontends/cli/options/bundle_facts.py, commands/compare.py), and no dump command offers a bundle-facts format/fan-out. The blocker is removed, but the dump fan-out is unwritten.  
`hygiene` — `--bundle-facts-out` remains a `compare` flag because `dump` has no release fan-out; the blocker is gone but the fan-out is unwritten. [Full write-up](known-gaps/compare-bundle-facts-out-stays-a-compare-flag-because-dump-has.md)

#### A directory/package `compare` cannot render sarif/review; `bundles:` cannot state scope policy

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** frontends/cli/commands/compare_routing.py:45 `_RELEASE_FORMATS = {json, markdown, junit, oneline, html}`, so sarif/review are still rejected for a release, and BundleSpec still has no scope-policy keys (not re-checked in detail).  
`hygiene` — A release `compare` rejects `sarif`/`review` output, and `bundles:` has no scope-policy keys. [Full write-up](known-gaps/a-directorypackage-compare-cannot-render-sarifreview-bundles.md)

#### Release-fan-out CLI tests JSON-parse `CliRunner.output`, which the memory-clamp note can join

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The clamp note is still a stderr line: `workflows/release_members.compare_release_members` emits it through the reporter's `notice`, which the CLI echoes with `err=True`. tests/test_release_package_inventory_cli.py:92-99 still does `json.loads(CliRunner().invoke(...).output)`, as do test_release_evaluation_config.py:712,730.  
`hygiene` — The memory-clamp note goes to stderr, and several release CLI tests `json.loads(CliRunner(...).output)`, which can mix the two streams. [Full write-up](known-gaps/release-fan-out-cli-tests-json-parse-clirunneroutput-which-the.md)

#### Source-read licence: enforced at the one consumer, not repo-wide (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Both deliberate residuals are still open: scripts/check_ai_readiness.py has no source-read licence gate (grep 'licen' finds only license-header checks), and there are no per-input content digests. The third residual is recorded as closed.  
`hygiene` — The source-read licence is enforced at its one consumer; there is no repo-wide gate and no per-input content digest. [Full write-up](known-gaps/source-read-licence-enforced-at-the-one-consumer-not-repo-wide.md)

#### A release/bundle report carries no authoritative entity-by-operation counts

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** report/pr_comment_aggregate.py:575-588 still marks the release rollup exact=False when targets cannot be itemized. The release JSON has an `uncapped` findings path (cli_compare_release_matrix.py:634), but no authoritative entity-by-operation count field was found; pr_comment._release_change_summary named in the entry no longer exists under that name.  
`hygiene` — The release rollup is marked `exact=False` when targets cannot be itemized, and the release JSON has no authoritative entity-by-operation count field. [Full write-up](known-gaps/a-releasebundle-report-carries-no-authoritative-entity-by.md)

#### Five SVS-scan report findings not addressed by the change-vs-inventory/depth/exclusion fixes (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: none of the five SVS report items was checked in code (header accounting does not lower status, the not-applicable detector vocabulary, the 294 KB Markdown default, partial telemetry, the identical-binary channel). Nothing marks any of them fixed.  
`hygiene` — Unverified: five SVS report items (header accounting, not-applicable detector vocabulary, 294 KB Markdown default, partial telemetry, identical-binary channel) are not marked fixed. [Full write-up](known-gaps/five-svs-scan-report-findings-not-addressed-by-the-change-vs.md)

#### A snapshot is not byte-reproducible across processes

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: no cross-process determinism check was run. The entry says the producer of the surface_graph.nodes ordering drift was never identified.  
`hygiene` — Unverified: a snapshot is not byte-reproducible across processes; the producer of the `surface_graph.nodes` ordering drift was never identified. [Full write-up](known-gaps/a-snapshot-is-not-byte-reproducible-across-processes.md)

#### Destructive test-helper resets are guarded one helper at a time (2026-09-30)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** No gate guards against rmtree of a caller-supplied path: 40 rmtree call sites remain under tests/ and scripts/, and the entry says the /tmp canary detector is not wired into CI. Not further verified.  
`hygiene` — About 40 `rmtree` sites under `tests/` and `scripts/` are guarded one helper at a time; the `/tmp` canary detector is not wired into CI. [Full write-up](known-gaps/destructive-test-helper-resets-are-guarded-one-helper-at-a-time.md)

#### `compare --no-baseline` has no typed Python request, scalar or directory (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for 'NoBaseline.*Request' in abicheck/ finds nothing, so there is still no typed no-baseline request.  
`hygiene` — There is no typed Python request for `compare --no-baseline`, scalar or directory; only the CLI drives it. [Full write-up](known-gaps/compare-no-baseline-has-no-typed-python-request-scalar-or.md)

#### The clang backend's guard retry still maps an aggregate line to a header (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/headers/clang/error_header_retry.py:69-79 still maps the aggregate line index to the header. The entry notes it is latent: not broken unless a preamble is added. The writers are now in extract/headers/clang/backend.py:304 and clang_layout_tool.py:203.  
`hygiene` — The clang backend's guard retry maps an aggregate-TU line index straight to a header; latent until a preamble is added to the aggregate. [Full write-up](known-gaps/the-clang-backends-guard-retry-still-maps-an-aggregate-line-to.md)

## Do not re-attempt

Approaches that were tried, measured or reviewed and did not help. Their value is stopping a re-attempt; read the write-up before trying anything similar.

| Approach | Measured result | Evidence caveat | Write-up |
|---|---|---|---|
| Demote a vanished weak/vague-linkage export (`Function.is_inline`, then COMDAT membership) | Both proved a fact about the library's build, not that consumers own a copy; `extern template` breaks the second. Reverted twice. | — | [Linkage-blind removal — attempted twice, reverted twice. The evidence keeps proving something adjacent to the invariant](known-gaps/linkage-blind-removal-attempted-twice-reverted-twice-the.md) |
| Demote stdlib closure instantiations as "unnameable" | Source nameability is not ABI compatibility; the fix was also dead code for its ELF-only case. Reverted. | — | [A third instance of the same shape (code-review report item 3): demoting a stdlib closure instantiation as "unnameable" — attempted, reverted](known-gaps/a-third-instance-of-the-same-shape-code-review-report-item-3.md) |
| Per-finding evidence-provider downgrade for header-only layout findings | The suspected misattribution does not occur on either header backend; no fix applied. | Premise did not reproduce | [Evidence-provider model — investigated, found not to reproduce as described; no fix applied](known-gaps/evidence-provider-model-investigated-found-not-to-reproduce-as.md) |
| L2 performance bottlenecks found by the full-CLI harness | Frame for the measurement-only perf rows below (`scripts/check_l2_cli_perf.py`). | — | [L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)](known-gaps/l2-performance-bottlenecks-found-by-the-full-cli-harness.md) |
| Share one dependency-header context across a set of libraries | 20 extractions either way; the count is driven by top-level headers. | Original fixture was flawed (shared arm shared nothing); conclusion re-measured on a corrected fixture | [A set of libraries pays full cost per library, and a shared header context does not reduce it](known-gaps/a-set-of-libraries-pays-full-cost-per-library-and-a-shared.md) |
| Four digest/save reductions (`iterencode`, nested hashing, `InMemoryObjectStore.detach`, split `update()`) | Peak unchanged at 474/576 MiB; three reverted, one kept as waste removal. | An 18% "win" for attempt 3 was a measurement artifact; corrected in the entry | [Snapshot digest/save amplification is in the sectioning layer, and four attempts to reduce it bought nothing (2026-09-17)](known-gaps/snapshot-digestsave-amplification-is-in-the-sectioning-layer.md) |
| Drop the release fan-out's `futures` map as results complete | Saves only `Future` wrappers; `results_by_key` holds the same result objects. | — | [The release fan-out's future map is not a last owner, so dropping it saves nothing (2026-09-17)](known-gaps/the-release-fan-outs-future-map-is-not-a-last-owner-so-dropping.md) |
| Release the clang AST before the header-graph build | Peak unchanged (2215 MiB, 0.05%); steady state ~8-12 MiB worse. Kept for mid-attach residency only. | — | [Releasing the clang AST before the graph build does not reduce the peak (2026-09-19)](known-gaps/releasing-the-clang-ast-before-the-graph-build-does-not-reduce.md) |
| m1360 asks not taken (shared stdlib scan, union vocabulary, `loc` strip, derived primary-pass form, forked child) | Each needs a rewrite or an ADR, not a cache. | — | [m1360 performance round: the asks not taken, and why (2026-09-24)](known-gaps/m1360-performance-round-the-asks-not-taken-and-why-2026-09-24.md) |

## Deliberate limitations

Deliberate rulings and scope limits, not defects. Changing one needs the decision its write-up names (usually an ADR or maintainer ruling).

| Limitation | Write-up |
|---|---|
| `dump -H <dir>` passes directories as `public_header_dirs` (provenance tagging), matching `compare`; deliberate. | [`dump -H <dir>` changed which channel carries the directory when the ELF `dump` CLI migrated onto the shared typed executor — recorded, deliberately not reverted (ADR-063 Track 1, 2026-09-05)](known-gaps/dump-h-dir-changed-which-channel-carries-the-directory-when-the.md) |
| Only elevated workflows and `action.yml` pin action SHAs; the rest use tags. | [Action pinning is deliberately partial, not a full sweep](known-gaps/action-pinning-is-deliberately-partial-not-a-full-sweep.md) |
| Every CODEOWNERS risk tier resolves to one maintainer. | [CODEOWNERS risk tiers currently all resolve to one person](known-gaps/codeowners-risk-tiers-currently-all-resolve-to-one-person.md) |
| Toolchain-profile `args` trust boundary hardened; the `-stdlib=`/`--target=` change was wrong and reverted. | [Toolchain-profile compiler-family rendering — audited, `args` trust boundary hardened; the `-stdlib=`/`--target=` "fix" itself was wrong and has been reverted](known-gaps/toolchain-profile-compiler-family-rendering-audited-args-trust.md) |
| Scope deferred from that pass (e.g. no `.devcontainer/`). | [Deferred entirely, not attempted this pass](known-gaps/deferred-entirely-not-attempted-this-pass.md) |
| Old `run-plan.json` readers cannot be retrofitted to honour the `gate` block; structurally infeasible. | [`run-plan.json`'s `gate` block is unprotected against an already-shipped, version-skewed reader — investigated end-to-end, confirmed structurally infeasible to close within the document's own shape, not fixed (Codex review, PR #779)](known-gaps/run-planjsons-gate-block-is-unprotected-against-an-already.md) |
| `DEFAULT_SYSTEM_PROVIDERS` is a hand-maintained soname allow-list, not a topology model. | [`DEFAULT_SYSTEM_PROVIDERS` (`bundle_models.py`) is a hand-maintained soname allow-list, not a real topology model for bundle-level system-provider classification -- a tactical fix that has grown, not a designed feature (Codex review on #791, fresh evidence)](known-gaps/default-system-providers-bundle-modelspy-is-a-hand-maintained.md) |
| With one `--write` slot, a non-JSON report can miss the coverage/assurance/severity floors; accepted in `action/AGENTS.md`. | [The composite Action's single `--write` slot can leave its unconditional coverage/assurance/severity floors without a structured report](known-gaps/the-composite-actions-single-write-slot-can-leave-its.md) |
| A behavioural change behind an unchanged declaration is out of scope for declaration detectors (case208). | [Behavioral regressions behind an unchanged declaration](known-gaps/behavioral-regressions-behind-an-unchanged-declaration.md) |
| Extraction failure exits 4 from a directory `compare`, 1 from a single pair; maintainer ruling "keep both". | [A member that fails to extract exits `4` from a directory `compare` but `1` from a single-pair one](known-gaps/a-member-that-fails-to-extract-exits-4-from-a-directory-compare.md) |
| ADR-071's fold excludes per-library overrides, per-member blocks and fold convergence; items 4-5 are residual defects. | [ADR-071's release assurance fold: what it deliberately does not do](known-gaps/adr-071s-release-assurance-fold-what-it-deliberately-does-not-do.md) |
| The compatibility percentage divides findings by symbols (ABICC heuristic, public contract); kept. | [The compatibility percentage counts findings against symbols](known-gaps/the-compatibility-percentage-counts-findings-against-symbols.md) |
| Local-exec TLS in an AArch64 shared object leaves no binary evidence; toolchain limit. | [Local-exec TLS in an AArch64 shared object leaves no binary evidence](known-gaps/local-exec-tls-in-an-aarch64-shared-object-leaves-no-binary.md) |
