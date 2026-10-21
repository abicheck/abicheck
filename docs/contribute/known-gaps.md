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

Next, unranked (the `--no-baseline` one-sided-detector gap, the target-platform gap and the untagged `dependency_scope` gap were closed on 2026-10-10, the informational-outcome strict-gate gap on 2026-10-12, and the ELF-only `build_mode` capture gap on 2026-10-14; see the archive). Each of these can let a run read as cleaner or more comparable than its evidence supports:

### Scalar and release `compare` still fold the exit code in two places (2026-10-07)

**Status (2026-10-12): PARTIAL, narrowed further.** The evidence-contract precedence (exit 7) is decided by one owner, `policy/exit_decision_precedence.py::resolve_evidence_over_ordinary`, for both paths; `tests/test_exit_precedence_matrix.py` pins both resolvers' exit codes over their per-axis matrices. Two resolvers remain: `policy/exit_decision.py::resolve_compare_exit_decision` (scalar) and `policy/exit_decision_precedence.py::resolve_release_exit_decision` (release), which keeps the release-only axes (`not_comparable`, the mode-dependent removed-library rank, the operational floor, the release-global fold) separate.  
`hygiene` — Scalar and release `compare` still use two precedence resolvers (`resolve_compare_exit_decision` and `resolve_release_exit_decision`), so the exit-code rules can drift between the two paths. [Full write-up](known-gaps/scalar-and-release-compare-still-fold-the-exit-code-in-two.md)

## Open and partially open gaps

Each entry's status line says what is still open. A PARTIAL entry's write-up also describes the half that is already fixed; the status line is authoritative where they disagree.

### False-clean risk

#### A binary-gate demotion does not state a coverage gap (ADR-084)

**Status (2026-10-21): OPEN.** When the ADR-084 gate demotes a binary-only source-level claim, the verdict narrows and the finding records its rule, but the run's coverage warnings do not change. `tests/test_family_f1_evidence_ablation.py` requires that a verdict which narrows from broken to clean because a fact became unknown carry a stated gap: lower confidence, a new coverage warning, a changed analysis-assurance record, or a higher contract-coverage floor. The demotion currently gives none of these. The fix is a coverage warning naming the unbacked claim, appended where `DiffResult.coverage_warnings` is assembled in `checker.py`. That file is at its no-growth ratchet (1407 lines), so adding it needs a responsibility moved out of `checker.py` first, not a raised baseline. The F1 cell `pimpl_inline_body_renamed` / `RecordType.source_header_fact` is the one that fails.

#### A refused clang AST still leaves the header type pass collected (2026-10-17)

**Status (2026-10-17): OPEN, residual.** A malformed or failed clang AST now degrades the call, override, template, callback and virtual-dispatch passes. The header type pass is still stamped from the snapshot's own declarations (the flat fallback in `header_graph.build_header_only_graph`), so its AST-only edges (`DECL_HAS_TYPE` on parameters and variables, `DECL_REFERENCES_DECL`) read as collected with role coverage unstamped. A real clang parse failure leaves the same state today. Degrading the type pass whenever `ast_failure` is set would close it, but it changes the existing clang-failure behaviour too and needs its own review.
`false-clean` — The type pass can read as complete for a header whose AST-only type edges were never read. Owner: `service_header_graph_attach._attach_header_graph`.

#### Other clang-AST readers still treat a wrong-typed `inner` as an empty list (2026-10-17)

**Status (2026-10-17): OPEN.** `validate_ast_shape` guards the header-graph projection (whole-tree and streamed) and the L5 AST fold. Readers outside those two paths still coerce a non-list `inner` to empty with `or []` or skip non-object children, so a corrupt tree there reads as "nothing to report". Known sites include `abicheck/buildsource/source_extractors/clang.py` and `clang_nodes.py`.
`false-clean` — An L4 source-ABI extraction over a corrupt AST can report an empty surface rather than failing. Owner: `buildsource/source_extractors`.

#### A `kind: bundle` check still cannot run at `depth: headers`: only the *baseline* half of per-member header staging is wired (2026-09-12)

**Status (re-verified 2026-10-12): OPEN, partly narrowed.** actions/check-target/action.yml:1828 still routes bundle old-library to `steps.resolve.outputs.binaries-dir`, not `member-snapshots-dir`. BUNDLE_CHECK_DEPTHS is unchanged (abicheck/buildsource/project_targets.py:151). The fail-early requirement for `depth: headers` on a bundle is met at input validation, with a corrected reason. So (a) routing and (b) candidate-side per-member headers are still open.  
`false-clean` — Bundle checks can stage per-member *baseline* header snapshots, but the Action still routes the old side to binaries and the candidate side has no per-member headers, so a bundle check cannot run at `depth: headers`. [Full write-up](known-gaps/a-kind-bundle-check-still-cannot-run-at-depth-headers-only-the.md)

#### Header-pass body coverage is one blanket unit, not per body (2026-10-11)

**Status (updated 2026-10-14): PARTIAL, narrowed.** The header pass now marks each declaration whose body it parsed in full (`header_body` node attribute), and `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` judges call/reference edges per body seen on both sides. Still open: the edge-query coverage records (`pass_coverage_records`) still give a header pass the blanket `HEADER_INLINE_BODIES` unit, and `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED` still needs whole-project call coverage on both sides.  
`false-clean` — Edge queries and the call-reachability finding still decline body-dependent edges from a header-only pass, even for a body seen on both sides. [Full write-up](known-gaps/header-body-coverage-is-not-per-body.md)

#### A virtual call through an out-of-line definition is classified as direct (2026-10-21)

**Status (2026-10-21): OPEN.** `call_graph.py` reads a callee's `virtual` flag from the resolved node, and an out-of-line definition carries no flag, so the call is recorded `direct`/`exact` and no `VIRTUAL_CALL_MAY_DISPATCH_TO` edge is produced. The dispatch-set detector cannot fire for such bases. `false-clean` — The call reads as exact when its targets can change. [Full write-up](known-gaps/virtual-call-through-out-of-line-definition-is-classified-direct.md)

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

**Status (2026-10-12): PARTIAL.** The in-memory half is fixed: `AdvancedDwarfMetadata.extraction` is set by the extraction (`completed`, `failed`, `not_run`), `advanced_facts_collected` reads it, and the `advanced_dwarf` detector names a failed or not-run side in its not-evaluated reason. Still open: the status is not persisted, so a stored snapshot reads `unrecorded` and falls back to the inference. Persisting it is a schema change; [ADR-080](adr/080-persisting-the-advanced-dwarf-extraction-status.md) is drafted and Proposed.  
`false-clean` — The extraction records its own outcome, so a completed empty parse is no longer read as not run. A stored snapshot still carries no outcome and uses the old inference until ADR-080 is decided. [Full write-up](known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md)

#### L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/adapter_contract.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.  
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

**Status (2026-10-12): PARTIAL.** Fixed: FAILED import reads; tests/unit/workflows/test_failed_consumer_read.py exists. Open: a stored snapshot cannot record a partial export-table read. [ADR-081](adr/079-partial-export-table-read-in-the-stored-snapshot.md) is drafted and Proposed; the schema field waits on its decision, so the schema stays at v58.  
`false-clean` — FAILED import reads are fixed. A stored snapshot still cannot record that its export-table read was partial; ADR-081 is drafted (Proposed) and needs a schema decision. [Full write-up](known-gaps/a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read.md)

#### `debug = 1` DWARF has no function types, so a return-type break reads `NO_CHANGE` with `assurance: complete` (2026-10-15)

**Status (2026-10-15): OPEN.** The DWARF section is present, but exported subprograms carry no `DW_AT_type` or parameters, and assurance still reads `complete`.
`false-clean` — A Rust cdylib built with `debug = 1` misses a return-type change and exits 0 with a complete-assurance label. [Full write-up](known-gaps/debug-1-dwarf-drops-function-types-with-complete-assurance.md)

#### A function-pointer parameter's signature is not compared, so a callback arity change reads `NO_CHANGE` on the header path (2026-10-15)

**Status (2026-10-15): OPEN.** Reproduced in plain C. The parameter record keeps only the pointer's type name, and the pointee's signature is not compared on either path.
`false-clean` — A callback that gains a parameter is reported as no change with headers, and on DWARF unless the type name happens to carry the signature. [Full write-up](known-gaps/function-pointer-signatures-are-not-compared-in-header-mode.md)

#### A default release-profile Rust run without headers reports `NO_CHANGE ... ACCEPTED` while its own warnings say the result is unconfirmed (2026-10-15)

**Status (2026-10-15): OPEN.** The headline label, the gate and the `analysis_assurance: partial` field disagree with each other, and the label is the clean one.
`false-clean` — A real return-type change in a header-less, no-debug run is labelled `NO_CHANGE` and accepted at exit 0. [Full write-up](known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md)

#### The root Action's baseline provenance is not recorded in the JSON report (2026-10-16)

**Status (2026-10-16): OPEN.** The root Action now says which baseline it compared against, in its `baseline-source`, `baseline-asset-name`, `baseline-project-ref`, `baseline-generation` and `baseline-profile` outputs and in a "Baseline source" step-summary block. The JSON report, which a downstream consumer reads on its own, does not carry that provenance, so it cannot tell a legacy asset from a baseline-set, or which `project_ref` was checked. The nearest existing owner is ADR-065's acquisition record (`abicheck/model/scope_acquisition.py`). It records a per-member *state* (`available`, `failed`, `expected_not_produced`, and so on), not the artifact or revision a member was read from, so it does not already carry this. Adding the fields there is a report-schema change with its own decision and golden updates, so it is not made here.

### False-positive risk

#### Two BINARY_GATED detectors still assert public reachability from the export population (ADR-084)

**Status (2026-10-21): OPEN.** ADR-084 made the verdict of every `BINARY_GATED` kind depend on observed header or source evidence, and removed the hard-coded `public_reachable=True` from `std_reexport_removed` and `tag_type_renamed`. Two detectors in the same bucket still set the tags from the binary export population: `unspecified_return_now_named` (`diff_templates.py`, the `public_reachable=True` set on the finding built around line 1160, where the comment says `_index()` filters to `Visibility.PUBLIC`) and `experimental_removed_without_replacement` (`diff_namespaces.py`, `_emit_experimental_change`, `public_reachable=subject_is_public`, with `subject_is_public` derived from export at about lines 977 and 996). Their verdict is correct, because the gate ignores tags, but a consumer reading `public_reachable`/`reachability_kind` on these two kinds is told the subject is proven public when only the export table shows it. Fix by building them with `public_reachability(has_observed_source_contract_evidence(...))` as `compare/namespace_shape_detectors.py` now does.

#### DWARF-level BINARY_GATED kinds are classified by reading the producers, not probed

**Status (2026-10-21): OPEN.** `policy/binary_gated_contract.py` `BINARY_GATED` includes 19 DWARF-level kinds (`type_became_abstract`, `field_renamed`, `ctor_explicit_added`, `func_became_inline`, ...) whose producers read DWARF-level type facts. Only the five kinds the ADR-084 probes reached (`mandatory_template_param_added`, `inline_function_removed`, `type_removed`, `experimental_removed_without_replacement`, `unspecified_return_now_named`) were reproduced at binary depth. The gate is safe for the rest in one direction: a kind placed in `BINARY_GATED` by mistake is only demoted in a binary-only run, and a header-only kind could not have been produced there anyway. The gap runs the other way. A kind placed in `HEADER_OR_SOURCE_GATED` that a binary-only run can in fact produce keeps a false `API_BREAK`. The `NON_BINARY_REASONS` rows for `param_default_value_removed`, `constant_changed` and `constant_removed` are the ones to probe first. Probe each DWARF-level kind with a binary-only fixture, and move any that is header-only.

#### `--lang c++` explicitness not threaded to remaining fan-out callers (l0_export_delta/appcompat); dump/compare fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed for dump/compare CLI and the DumpRequest/CompareRequest API. `lang_explicit` is threaded through frontends/cli/commands/dump.py, compare.py, workflows/dump/{native,pe,macho}.py and workflows/artifact/*, and release_member_request.py now carries it too. The scan follow-up is moot (scan was deleted). appcompat and l0_export_delta are unverified. l0_export_delta.py takes a plain `lang: str` with no lang_explicit, so that follow-up looks still open.  
`false-positive` — An explicit `--lang c++` reaches dump/compare, but `l0_export_delta` (plain `lang: str`) and appcompat still guess the language themselves. [Full write-up](known-gaps/lang-c-explicitness-not-threaded-to-remaining-fan-out-callers.md)

#### A compatible-but-ambiguous opaque redeclaration set (`class H; struct H;`) that later gains a same-key COMPLETE definition still produces a false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex review, PR #719, fourth follow-up round on this same area)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for kind_ambiguous in abicheck/ finds nothing, so RecordType has no provenance for an ambiguous forward-declaration kind.  
`false-positive` — `class H; struct H;` followed by a complete definition produces a false `SOURCE_LEVEL_KIND_CHANGED`, because `RecordType` keeps no provenance for an ambiguous forward-declaration kind. [Full write-up](known-gaps/a-compatible-but-ambiguous-opaque-redeclaration-set-class-h.md)

#### A pre-fix clang-backend baseline compares against a post-fix candidate as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards `Function.is_inline`

**Status (re-verified 2026-10-12): PARTLY FIXED — see [ADR-081](adr/081-inline-fact-reliability-and-producer-generation.md).** Stale header-backend `is_inline` values now decline the transition, recorded as `inline_facts_reliable`. A baseline re-saved by a pre-change build still reads as reliable until re-dumped.  
`false-positive` — A baseline from the pre-fix clang backend compared against a post-fix candidate reports a wave of `FUNC_BECAME_INLINE`; no reliability flag gates `Function.is_inline`. [Full write-up](known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md)

#### type_base_changed declines on incomplete bases facts but still fires on a PRESENT-but-empty capture gap

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The vtable half is fixed. compare/base_class_diff.py:36-71 diff_bases now gates bases_fact/virtual_bases_fact through compare_facts and declines when a side is NOT_COLLECTED/FAILED/PARTIAL. A PRESENT-but-empty capture gap can still fabricate type_base_changed, as the entry describes.  
`false-positive` — `type_base_changed` now declines on NOT_COLLECTED/FAILED/PARTIAL bases facts, but a PRESENT-but-empty capture gap still fabricates the finding. [Full write-up](known-gaps/type-base-changed-declines-on-incomplete-bases-facts-but-still.md)

#### Type reachability (direct vs. transitive stdlib references) — computed and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef paths remain unwired

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** type_reachability.py is wired into the RecordType detectors (diff_types_surface.py and others), but the diff_types.py:1113-1118 enum paths still call _is_non_abi_surface_type(e.name) directly, as the entry says.  
`false-positive` — Direct-vs-transitive stdlib reachability is wired into the `RecordType` detectors only; enum and typedef paths still use the bare-name non-ABI-surface check. [Full write-up](known-gaps/type-reachability-direct-vs-transitive-stdlib-references.md)

#### Closure-parameterized non-ctor functions still churn (func_params_changed) on lambda line shifts; '?' ctor param unexplained

**Status (re-verified 2026-10-12 at `b694ccc2f`): PARTIAL.** Type-level marker fix, ctor/dtor binary-evidence fix and L5 declaration_renamed fix (model.graph_identity.closure_location_free_identity) are recorded as landed. Non-ctor func_params/return churn on an inserted unrelated closure is fixed for exported symbols (`compare/closure_respelling.py`). Still open: the same churn on unexported/inline declarations (`make_guard`, template members, synthetic ctor keys; fails closed, decision proposed in ADR-083 D1), `exported_not_public` noise on identical builds (ADR-083 D2), the mangled-only surface filter gap, and residual (2), the literal '?' ctor parameter (not reproduced on any dump in this work).  
`false-positive` — Non-ctor functions parameterized over a lambda churn when an unrelated closure is inserted earlier in the header; fixed for exported symbols. Still open: inline/unexported declarations and identical-build `exported_not_public` noise (both decisions proposed in ADR-083), the literal `?` ctor parameter (not reproduced), and the mangled-only surface filter gap. [Full write-up](known-gaps/closure-parameterized-non-ctor-functions-still-churn-func.md)

#### The L5 source graph's own node identities are never renumbered alongside the flat snapshot's closure markers (Codex review on PR #868, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/snapshot_load_normalization.py:123-138 docstring still says surface_graph node/edge identities are not renumbered. renumber_anonymous_closure_identities (storage/closure_identity.py) has no graph handling.  
`false-positive` — Closure markers in the flat snapshot are renumbered on load, but the L5 `surface_graph` node and edge identities are not, so the two disagree. [Full write-up](known-gaps/the-l5-source-graphs-own-node-identities-are-never-renumbered.md)

#### Declared-header order is still load-bearing because the driver TU is sequential

**Status (2026-10-12): PARTIAL.** A reorder of a header set is now comparable when both dumps prove the canonical order (ADR-082). Still refused: an unproven declared order, and a macro/pragma leak that changes no declaration (not visible to the witness). The scope-confirmed header growth waiver landed (comparability.py:845,1298 reference _header_sequence_is_scope_confirmed_growth). Still open: the sequential aggregate driver TU itself, so reordering an existing header still refuses. Canonical sorting of the driver TU is measured unsound; the remaining options are in ADR-082.  
`false-positive` — The scope-confirmed header-growth waiver landed, but the aggregate driver TU is still sequential, so reordering an existing header refuses the comparison. Sorting the driver TU is measured unsound (it breaks a valid declared order); removing the refusal alone reports a false BREAKING. [Full write-up](known-gaps/declared-header-order-is-still-load-bearing-because-the-driver.md)

#### Export-loss ownership: inline-copy proxy and template-specialization owner remain open (see export-loss-ownership-root-cause.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** This is a pointer stub to export-loss-ownership-root-cause.md. It still lists two open items: is_inline is used as a proxy, and there is no Itanium type decoder for specialization owners. Not re-verified in code beyond that.  
`false-positive` — Export-loss ownership still uses `is_inline` as a proxy for an inline copy and has no Itanium decoder for template-specialization owners; see `export-loss-ownership-root-cause.md`. [Full write-up](known-gaps/export-loss-ownership-inline-copy-proxy-and-template.md)

#### Class-template specialization members a new header stops enumerating keep `inline_function_removed` and `type_removed` (partly fixed)

**Status (2026-10-20): PARTIAL.** A template instance whose primary template is still declared once in the new header graph is no longer reported as `func_removed`/`inline_function_removed`; its export change is `func_visibility_changed`, or nothing (`compare/template_instance_absence.py`). Still open: the implicit members of a specialization the new header no longer instantiates, and the specialization's own `type_removed`.  
`false-positive` — A class-template specialization that the new header stops instantiating still reports its implicit members as `inline_function_removed` and the specialization as `type_removed`, although the template is still declared. [Full write-up](known-gaps/class-template-specialization-members-a-new-header-stops-enumerating.md)

#### MinGW toolchain headers are not classified as dependencies (2026-09-23)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/declaration_provenance.py:270 _is_toolchain_compiler_include_dir still recognises only lib/gcc/<triple>/<ver>/include, lib/clang, and castxml layouts. provenance.py has no handling for MinGW's <prefix>/<triple>/include.  
`false-positive` — Toolchain include detection knows GCC/clang/castxml layouts but not MinGW's `<prefix>/<triple>/include`, so MinGW headers are not classified as dependencies. [Full write-up](known-gaps/mingw-toolchain-headers-are-not-classified-as-dependencies-2026.md)

#### The clang JSON header backend records no resolved type identities, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public` (2026-10-01)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for type_identities under abicheck/extract/headers/clang/ finds nothing, so the clang backend still records no identities.  
`false-positive` — The clang JSON backend records no `type_identities`, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public`. [Full write-up](known-gaps/the-clang-json-header-backend-records-no-resolved-type.md)

#### A library's own `_Float128` API has no correct mangled name from castxml

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: castxml mangling was not run. The entry records the defect as an unresolved castxml limitation: _Float128 functions read as not exported.  
`false-positive` — Unverified: castxml gives a library's own `_Float128` functions no correct mangled name, so they read as not exported. [Full write-up](known-gaps/a-librarys-own-float128-api-has-no-correct-mangled-name-from.md)

#### Renaming a Rust crate changes every crate-qualified type name, so an unchanged C surface reads `API_BREAK` on the DWARF path (2026-10-15)

**Status (2026-10-15): OPEN.** Header mode is clean; DWARF mode reports each type as removed and re-added under the new crate name.
`false-positive` — A crate rename with the same C ABI gives `API_BREAK` on DWARF-only compares, because DWARF type names are crate-qualified. [Full write-up](known-gaps/crate-rename-changes-rust-type-identity.md)

#### Header-less runs flag `exported_object_alignment_reduced` on an unrelated exported static after an additive change (2026-10-15)

**Status (2026-10-15): OPEN.** The alignment is derived from link-time address, so a neighbour's addition can move it; headers suppress the finding.
`false-positive` — An added exported function gives `COMPATIBLE_WITH_RISK` through an alignment finding on a static it does not touch, when no headers are passed. [Full write-up](known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md)

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

#### A directory `compare` feeds the union `-H` set to every member again: per-member header narrowing was reverted (2026-10-12)

**Status (2026-10-12): OPEN, reopened.** The narrowing from #1554 (`882482c47`) is reverted. It gave each release member the headers its exported names appeared in, decided per side. On a real oneDAL release (2026.0.0 to 2026.1.0, four members) three members stopped being compared: one got a header subset that did not parse (castxml exit 1), and two were refused as NOT_COMPARABLE because each side's assignment followed that side's exports and header set (scope_fingerprint mismatch). The release exit moved from 4 to 16. The union operand is back, and the release gate is again the one that was proven.
`perf` — Every member still parses the whole union `-H` set, so release header work grows with members x headers, and a type from one member's header is parsed by its siblings too. The verdict half is closed: a type that a member's own exports prove unreachable is recorded out of that member's contract, and it gates only through a sibling that reaches it (see the [archive](archive/known-gaps-closed.md)).

Do not re-attempt token-claiming. A header's export tokens cannot tell whether it is self-contained: the umbrella header that supplies a prelude is an includer, not an include, so an include-closure does not recover it. Per-side assignment is also not symmetric, so the two sides parse different surfaces. A retry needs include-graph evidence, one assignment shared by both sides, and a castxml failure that is recorded as a FAILED fact, never a silent fall-back. [Full write-up](known-gaps/a-directory-compare-s-per-member-header-narrowing-is-elf-only.md)

#### Multi-library L2 compare scales quadratically with library count

`perf` — Memoization landed (scaling budget 1.7). Step 2 was reverted on 2026-10-12, so every member walks the union header set again; the scaling budget has not been re-measured since the revert.

- [Multi-library L2 compare scales quadratically with library count](known-gaps/multi-library-l2-compare-scales-quadratically-with-library-count.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The memoization landed: the libraries budget is 1.7 in scripts/check_l2_scaling_perf.py:103 and header_scan_memo/include memo exist. Still open: every member walks the union header set and runs the per-member field parse (step 2 of entry 73).

#### Spelling alternation matcher scales linearly with vocabulary (nested same-offset bug fixed)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The under-report half is fixed and pinned by tests/test_spelling_nested_same_offset.py. The entry itself says the alternation-scaling half (linear in vocabulary size) remains open.  
`perf` — The nested same-offset under-report is fixed; the alternation matcher still scales linearly with vocabulary size. [Full write-up](known-gaps/spelling-alternation-matcher-scales-linearly-with-vocabulary.md)

#### A member's peak memory is the clang JSON AST, not the header graph (2026-09-19)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The 'currently ungated' clause is closed: scripts/check_header_graph_perf.py:267 defines MEMORY_METRICS attach_peak_rss_mib/attach_end_rss_mib. The peak from materialising the clang JSON AST is still open, and the empty-list reduction is a recorded negative result.  
`perf` — A member's attach peak is the clang JSON document plus its parsed tree, not the header graph; the memory metrics are now gated, the peak itself is not reduced. [Full write-up](known-gaps/a-members-peak-memory-is-the-clang-json-ast-not-the-header.md)

#### The cache keys do not hash system include directories, and a file passed as an include is not content-hashed (2026-10-12)

**Status (2026-10-12): OPEN.** Found by reading `workflows/dump/cache/key.py`'s `_cache_key` and `extract/headers/ast_config._cache_key`, not by a toolchain-change probe. The whole-snapshot key hashes the caller's `headers`/`includes`, inferred roots and compile-context inputs. It does not hash auto-probed system include directories that are absent from those inputs. The header-AST key folds those resolved `system_includes` as path strings only, not their contents. A header update inside one of those unchanged directories can therefore reuse both caches. Compiler-option `-isystem` roots are outside this gap: their paths are content-hashed through `compile_context_header_inputs` and `extra_hash_dirs`. Separately, the snapshot key's include walk has no file branch: a file passed as an `includes` entry contributes its path but not its contents, because the directory scan returns no entries for a file. Reachable only through a direct `includes=[file]` call; `-include`/`-imacros` files use the content-hashed `files` channel.
`false-clean` — A warm snapshot or AST entry can survive an edit to a system header, or to a file passed as an include, under an unchanged path.

#### The cache keys walk include trees only over `CACHE_HEADER_SUFFIXES`, so transitive headers with other suffixes never bust them

**Status (2026-10-12): OPEN, by decision.** `header_utils.py` documents the suffix filter as a deliberate choice (#454). Explicit inputs are hashed whatever their suffix; transitive includes under an `-I` directory with an unlisted suffix (`.def`, `.in`, `.cfg`) are not. Widening the walk re-keys every existing entry and adds walk cost on large include trees, so it needs a measured change rather than a one-line edit.
`false-clean` — An edit to a transitively included `.def`/`.in` under an include root leaves both cache keys unchanged.

#### The whole-snapshot key mixes mtime into identity, which ADR-062 D11 (Proposed) rules out

**Status (2026-10-12): OPEN, blocked on D11.** `workflows/dump/cache/key.py`'s `_cache_key` hashes each header's `st_mtime_ns` alongside its content, so a touched but unchanged header discards the entry. ADR-062 D11 says caches key on content and use size and mtime only to avoid recomputing a digest. D11 is Proposed, so the key is unchanged until D11 is accepted or amended. The header-AST key is the other way round: mtime only, no content. The two caches therefore disagree on the same filesystem facts.
`perf` — Unchanged headers with fresh mtimes miss the whole-snapshot cache. Keeping content but dropping mtime is safe for correctness because content is hashed.

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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** workflows/cross_source_hygiene.py:360 is still `def compute_cross_source_evolution(old, new)` with no changed_paths parameter, and checker.py has no changed_paths.  
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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** --bundle-facts-out is still a compare option (frontends/cli/options/bundle_facts_library_manifest.py, commands/compare.py), and no dump command offers a bundle-facts format/fan-out. The blocker is removed, but the dump fan-out is unwritten.  
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

#### The agent-eval `base_commit` test checks object presence, not ancestry from the default branch (2026-10-12)

**Status (2026-10-12): OPEN.** `test_manifest_base_commit_exists_in_history` runs `git cat-file -e` only; a manifest pinned to a fetchable but unmerged commit passes.  
`hygiene` — The test named "exists in history" does not check that a task's `base_commit` is reachable from the default branch. [Full write-up](known-gaps/agent-eval-base-commit-test-checks-presence-not-ancestry.md)

## Do not re-attempt

Approaches that were tried, measured or reviewed and did not help. Their value is stopping a re-attempt; read the write-up before trying anything similar.

| Approach | Measured result | Evidence caveat | Write-up |
|---|---|---|---|
| Demote a vanished weak/vague-linkage export (`Function.is_inline`, then COMDAT membership) | Both proved a fact about the library's build, not that consumers own a copy; `extern template` breaks the second. Reverted twice. | — | [Linkage-blind removal — attempted twice, reverted twice. The evidence keeps proving something adjacent to the invariant](known-gaps/linkage-blind-removal-attempted-twice-reverted-twice-the.md) |
| Treat a symbol the new binary still exports as not removed, with no declaration gate | Silences a plain function the header dropped while the binary still exports it (`ns::bar`), a real API removal that reports `func_removed` today. The gate on the primary template's declaration is what keeps it. | — | [Class-template specialization members a new header stops enumerating (partly fixed)](known-gaps/class-template-specialization-members-a-new-header-stops-enumerating.md) |
| Demote stdlib closure instantiations as "unnameable" | Source nameability is not ABI compatibility; the fix was also dead code for its ELF-only case. Reverted. | — | [A third instance of the same shape (code-review report item 3): demoting a stdlib closure instantiation as "unnameable" — attempted, reverted](known-gaps/a-third-instance-of-the-same-shape-code-review-report-item-3.md) |
| Per-finding evidence-provider downgrade for header-only layout findings | The suspected misattribution does not occur on either header backend; no fix applied. | Premise did not reproduce | [Evidence-provider model — investigated, found not to reproduce as described; no fix applied](known-gaps/evidence-provider-model-investigated-found-not-to-reproduce-as.md) |
| L2 performance bottlenecks found by the full-CLI harness | Frame for the measurement-only perf rows below (`scripts/check_l2_cli_perf.py`). | — | [L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)](known-gaps/l2-performance-bottlenecks-found-by-the-full-cli-harness.md) |
| Share one dependency-header context across a set of libraries | 20 extractions either way; the count is driven by top-level headers. | Original fixture was flawed (shared arm shared nothing); conclusion re-measured on a corrected fixture | [A set of libraries pays full cost per library, and a shared header context does not reduce it](known-gaps/a-set-of-libraries-pays-full-cost-per-library-and-a-shared.md) |
| Four digest/save reductions (`iterencode`, nested hashing, `InMemoryObjectStore.detach`, split `update()`) | Peak unchanged at 474/576 MiB; three reverted, one kept as waste removal. | An 18% "win" for attempt 3 was a measurement artifact; corrected in the entry | [Snapshot digest/save amplification is in the sectioning layer, and four attempts to reduce it bought nothing (2026-09-17)](known-gaps/snapshot-digestsave-amplification-is-in-the-sectioning-layer.md) |
| Drop the release fan-out's `futures` map as results complete | Saves only `Future` wrappers; `results_by_key` holds the same result objects. | — | [The release fan-out's future map is not a last owner, so dropping it saves nothing (2026-09-17)](known-gaps/the-release-fan-outs-future-map-is-not-a-last-owner-so-dropping.md) |
| Release the clang AST before the header-graph build | Peak unchanged (2215 MiB, 0.05%); steady state ~8-12 MiB worse. Kept for mid-attach residency only. | — | [Releasing the clang AST before the graph build does not reduce the peak (2026-09-19)](known-gaps/releasing-the-clang-ast-before-the-graph-build-does-not-reduce.md) |
| m1360 asks not taken (shared stdlib scan, union vocabulary, `loc` strip, derived primary-pass form, forked child) | Each needs a rewrite or an ADR, not a cache. | — | [m1360 performance round: the asks not taken, and why (2026-09-24)](known-gaps/m1360-performance-round-the-asks-not-taken-and-why-2026-09-24.md) |
| Canonicalise the declared header order (sort the aggregate driver TU) so a reorder of existing headers stops refusing | Sorted order is the failing order for any header that depends on an earlier one: castxml returns zero declarations, clang raises. Removing the refusal alone reports a false BREAKING on an unchanged library. | — | [Declared-header order is still load-bearing because the driver TU is sequential](known-gaps/declared-header-order-is-still-load-bearing-because-the-driver.md) |

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
| DWARF member-location evaluation covers `DW_OP_plus`/`plus_uconst`/`constu`/`consts`/`lit0..31` only; other ops give unknown offsets, not values. | [DWARF member-location evaluator implements only a subset of DW_OP](known-gaps/dwarf-member-location-evaluator-implements-only-a-subset-of-dw-op.md) |
| Local-exec TLS in an AArch64 shared object leaves no binary evidence; toolchain limit. | [Local-exec TLS in an AArch64 shared object leaves no binary evidence](known-gaps/local-exec-tls-in-an-aarch64-shared-object-leaves-no-binary.md) |
