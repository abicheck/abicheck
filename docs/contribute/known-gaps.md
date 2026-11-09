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
| NARROWED | The gap's reach was cut down; the status line names the residual. |
| POLICY_NOTE | A deliberate ruling or scope limit, not a defect. |

The status line overrides older wording in the entry body. When you fix a
gap, move its entry to the archive page with a CLOSED status line rather
than striking it through here.

### Canaries

Every OPEN, PARTIAL or NARROWED entry is linked to a **canary**: a test
marked `@pytest.mark.known_gap_canary` that asserts the gap's *current*
answer (for example, case98 reads `NO_CHANGE` with `partial`
assurance at debug-headers depth on both header backends). The entry names it on its own line inside the entry,
`<!-- canary: tests/test_x.py::test_name -->` (one or more), or carries one
reasoned exemption, `<!-- no-canary: needs MSVC cl.exe and a PDB -->`, when
no canary is cheap. A canary fails when the gap closes *or* widens. When
yours fails because you fixed the gap, move the entry to the archive, turn
the canary into a regression test of the correct answer (drop the marker),
and remove the entry's `canary:` line; never re-pin the new answer as the
gap. The `known-gap-canaries` check (`scripts/known_gap_canaries.py`, run by
`check_ai_readiness.py`) fails an unlinked open entry, a missing, unmarked,
skipped or non-strict-xfail canary, a marked test no entry names, and any
CLOSED or FIXED entry left on this page. When you re-check an entry, update its
status line's date and commit.

## Page layout

Each open entry below has its status line, a risk tag and a short summary; the full write-up, with the history of what was tried, is one file per entry under `docs/contribute/known-gaps/`. Index rows are ranked: the Priority list, then `false-clean` (a run can read cleaner than its evidence), `false-positive` (a finding or failure that is not real), `perf`, and `hygiene` (tests, tooling, schema or code-ownership debt). `route-divergence` marks two equivalent routes (CLI, typed API, package) that report different findings for one input without a cleaner verdict. When you fix, re-check or add an entry, edit its write-up and keep the index row's status line in sync.

## Priority

Open correctness gaps, in the order of the maintainer ruling of 2026-10-01 (closed items from that ruling are in the archive):

Next, unranked (the `--no-baseline` one-sided-detector gap, the target-platform gap and the untagged `dependency_scope` gap were closed on 2026-10-10, the informational-outcome strict-gate gap on 2026-10-12, and the ELF-only `build_mode` capture gap on 2026-10-14; see the archive). Each of these can let a run read as cleaner or more comparable than its evidence supports:

### Scalar and release `compare` still fold the exit code in two places (2026-10-07)

**Status (2026-11-01): PARTIAL, one cell family awaiting a ruling.** Both ladders (`resolve_compare_exit_decision_with_abort_axes`, scalar; `resolve_release_exit_decision`, release) now delegate dominant-axis precedence to one rank table, `policy/exit_rank.py::COMPARE_EXIT_RANK`, tested on its own (`tests/test_exit_rank_table.py`). All 512 N=1 cells were enumerated; the ladders agree field for field except the 128 cells where a budget overflow (5) decides the scalar exit and the evidence contract does not fire: the release ladder has no budget input. That family is pinned by `tests/test_exit_precedence_matrix.py::TestOneMemberCellDiff` and awaits a maintainer ruling under ADR-064 before the scalar path can call the release ladder directly.  
`hygiene` — Scalar and release `compare` still have two ladder entry points; they share one rank table, and at N=1 differ only on the budget-overflow axis the release ladder cannot express. [Full write-up](known-gaps/scalar-and-release-compare-still-fold-the-exit-code-in-two.md)
<!-- canary: tests/test_exit_precedence_matrix.py::TestOneMemberCellDiff::test_cell -->

## Open and partially open gaps

### Option vocabulary (ADR-091 follow-ups)

The option-vocabulary migration is complete (ADR-091, Accepted and
implemented). Its follow-ups were closed on 2026-10-31 (see the archive):
the `--debug-info` and `deps.*` config homes were added, `--sources` stays a
flag only, by design, and a directory/package `compare` now applies the
dependency walk and debug roots to every member. No open gap remains.

### Report and detector coverage

#### The typed `compare()` route omits `exported_not_public` that the CLI reports for the same catalog case (2026-11-08)

**Status (2026-11-08): OPEN.** Found by the typed-API route check in `tests/test_invariant_oracles_catalog.py`.
[Full write-up](known-gaps/the-typed-compare-route-omits-exported-not-public-that-the-cli-reports-for-the-same-catalog-case.md)
`route-divergence` — the typed route reports a different kind set from the CLI for one catalog case (one hygiene finding missing). The verdict agrees, so no run reads cleaner than its evidence.
<!-- canary: tests/test_invariant_oracles_catalog.py::test_typed_api_omits_exported_not_public_on_case09 -->

#### The clang record-layout pass leaves unnameable records and alias-template arrays to DWARF (2026-11-04)

**Status (2026-11-05): PARTIAL.** Residuals of the closed clang-layout gap ([archive](archive/known-gaps-closed.md)). Since extractor generation 39 the built-in pass (`extract/headers/clang/record_layout_dump.py`) probes C headers too (as `struct N`, `union N` and a typedef name), sizes empty records, and gives unnamed member aggregates' fields their offsets; the catalog-wide castxml-oracle harness (`tests/test_family_f8_catalog_backend_parity.py`) pins every case. It still only lays out records a `sizeof` can name, so a private nested record or an unnamed record still depends on DWARF for its size and offsets. A field declared as an array of an alias-template specialization (`pimpl<Impl> m[2]`) keeps its written spelling: clang's field spelling carries no desugared element type, so a retargeted alias is not reported as `type_field_type_changed` (`records.alias_template_target`). Under castxml, case85 reports `func_visibility_changed` plus `internal_template_leaks_via_public_api` where the catalog lists `func_removed`: `compare/template_instance_absence.py` reads an old instance as removed only when `explicit_instantiation_fact` proves the header spelled it (`extern template`), and castxml (0.7.0) cannot supply that fact — its `<Function>` element for an `extern template` instance, an explicit instantiation definition and an implicit instance carries the same attributes (name, context, mangled, and the primary template's `location`), and castxml rejects clang's `-ast-dump` action, so the instance reads as implicit and its export loss as `func_visibility_changed`. Closing it needs instantiation-kind evidence castxml does not emit (only an explicit specialization is distinguishable, by its own `location`). Owner: `extract/headers/castxml/` (upstream castxml).
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_clang_alias_template_array_field_retarget_is_not_a_field_type_change -->
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_castxml_case85_reports_visibility_change_not_removal -->

#### castxml elides defaulted template arguments and inline namespaces from record names (2026-11-05)

**Status (2026-11-05): OPEN.** castxml names a class-template specialization with its defaulted arguments left out (`descriptor<double>`, `knn_descriptor<>`), where the clang backend spells every argument (`descriptor<double, minkowski_distance<double>>`), and drops an inline namespace from a record's qualified name (`crypto::Context`, clang: `crypto::v1::Context`). The same record therefore has a different name per frontend, so a castxml baseline compared with a clang dump reports it removed and added, and under castxml an inline-namespace move (case71) is visible only through the mangled symbols. Declared, per case and record, in `BACKEND_EXCEPTIONS` of `tests/test_family_f8_catalog_backend_parity.py` (case71, case77, case87), which fails when a row goes stale. Owner: `extract/headers/castxml/`.
<!-- canary: tests/test_family_f8_catalog_backend_parity.py::test_clang_record_layout_matches_castxml_across_the_catalog -->

#### The clang header backend records only explicitly instantiated class-template specializations (2026-11-05)

**Status (2026-11-05): OPEN.** castxml records every class-template specialization the headers instantiate, including one only used as a member's or base's type (`detail::P<int> p;`); the clang backend records a specialization as its own `X<Args>` record only when the header instantiates or specializes it explicitly (`extern template`, an explicit specialization). An implicitly instantiated specialization is absent from a clang snapshot, so a layout change inside it is seen only through the containing record's size and offsets. Declared in `BACKEND_EXCEPTIONS` of `tests/test_family_f8_catalog_backend_parity.py` (case87, case109, case217, case218). Owner: `extract/headers/clang/`.
<!-- canary: tests/test_family_f8_catalog_backend_parity.py::test_clang_record_layout_matches_castxml_across_the_catalog -->

#### `dumper.dump()` never runs the clang record-layout pass (2026-11-05)

**Status (2026-11-05): OPEN.** `attach_clang_layout` is called from `workflows/dump/native.py`, which the CLI and `abicheck.service.run_dump` reach; the legacy in-process `abicheck.dumper.dump()` does not call it, so a clang-frontend dump through it carries no record size or offset unless DWARF supplies one (observed on catalog case07/case09 with debug info stripped). The catalog harness dumps through `run_dump`. The in-tree callers of `dumper.dump()` are tests (`test_example_autodiscovery.py`, `test_depth_metamorphic_examples.py`), so a clang-frontend run of those reads less layout than the CLI would. Owner: `dumper.py` (route through the workflow, or retire it).
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_legacy_dumper_dump_leaves_a_clang_record_unsized -->

#### Catalog known-gap rows read `NO_CHANGE` at debug-headers depth: case105 on castxml (concept) and case98 (`-std` floor) (2026-11-05)

**Status (2026-11-06): OPEN (narrowed: case105 is fixed on clang).** case98's truth (`COMPATIBLE_WITH_RISK` for a raised `-std=c++20` floor) needs L3 build evidence; at debug-headers depth it reads `NO_CHANGE` with `partial` assurance on both backends ("extraction contexts were not provably identical"). case105 (a tightened C++20 concept, truth `API_BREAK`) now splits by backend. The clang header backend models concept definitions and the constraints of function and class templates (`extract/headers/clang/concept_constraints.py`, persisted as `AbiSnapshot.cxx_concept_constraints`, extractor generation 42), and `compare/concept_constraints.py` reports `concept_tightened`/`concept_constraint_changed` with the L4 source lane's direction rule (`model/concept_constraint.py`); no concept gap is named for a side that modelled them. castxml emits no element for a concept, so a castxml run still reads `NO_CHANGE` with `partial` assurance and the dimension "C++20 concepts and requires-clauses" named (`AbiSnapshot.cxx_concept_header_scan`); the catalog row declares `known_gap_frontends: [castxml]`. The clang table is also not modelled (gap still named) after a multi-TU manifest merge or without a declared public header set, and a concept or template present on only one side is not reported (no existing kind states a concept removal).
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_case105_concept_tightening_debug_headers_per_backend -->
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_case98_standard_floor_reads_partial_no_change_at_debug_headers -->
`false-clean` (narrowed) — A C++20 concept tightening compared from castxml headers reads `NO_CHANGE`, with `partial` assurance and the concept dimension named. Owner: `extract/headers/castxml/` (no concept element to read; use the clang backend or L4 `--sources`).

#### The clang frontend's builtin-ABI probe links and runs a program, so a cross or linker-less dump records no builtin `type_abi_fact` (2026-11-05)

**Status (2026-11-05): OPEN.** `extract/builtin_abi.probe_builtin_abi` compiles, links and runs a `printf(sizeof, _Alignof)` program with the frontend's own driver. When the driver cannot link (no `ld` reachable: clang resolves it from `PATH`) or the host cannot run the target (a `--target=` cross dump, a Windows/Mach-O ABI target), the probe returns `None` and every builtin slot, and the whole `typedef_abi_facts` map, reads `NOT_COLLECTED`. Nothing is fabricated, so an ABI-equivalent retype simply stays a `type_field_type_changed`/`func_params_changed` instead of `abi_equivalent_type_changed`. Record layout, enums, typedefs and vtables are unaffected (the record-layout pass is compile-only). Pinned by `tests/test_default_install_fact_invariants.py::test_clang_builtin_abi_facts_are_marked_without_a_linker`. The fix is a compile-only probe (clang's `-fdump-record-layouts` over one wrapper record per builtin gives size and in-struct alignment for any target); not done here because it changes the measured alignment from `_Alignof` to member alignment on i386-like targets and needs its own oracle. Owner: `extract/builtin_abi.py`.

`false-positive` — an ABI-equivalent builtin retype stays a type change instead of `abi_equivalent_type_changed`; nothing is fabricated. [Full write-up](known-gaps/clang-builtin-abi-probe-needs-a-linker-and-a-runnable-target.md)
<!-- canary: tests/test_default_install_fact_invariants.py::test_clang_builtin_abi_facts_are_marked_without_a_linker -->

#### Clang leaves enum- and typedef-typed slots without a `type_abi_fact` that castxml records (2026-11-05)

**Status (2026-11-05): OPEN.** For `handle_t make(Point, Color, Mode, count_t)` castxml records `type_abi_fact` PRESENT for the `Color`/`Mode` parameters and the `handle_t` return; the clang frontend records `NOT_COLLECTED` for them (it does record `count_t`, a typedef of a builtin spelled without a namespace, and every plain builtin). The gap is marked, not empty, so it narrows the ABI-equivalence path rather than manufacturing a break. Found by the default-install invariant suite while comparing frontends; independent of optional tools (the same with every tool present). Cause not traced; `builtin_type_fact` is called on the desugared spelling, and has no enum underlying-type input. Owner: `extract/headers/clang/functions.py`.

`false-positive` — a clang-side enum or typedef retype between ABI-identical types is not downgraded; castxml snapshots are unaffected. [Full write-up](known-gaps/clang-leaves-enum-and-typedef-slots-without-a-type-abi-fact.md)
<!-- canary: tests/test_default_install_fact_invariants.py::test_clang_leaves_enum_and_typedef_slots_without_an_abi_fact -->

#### The standing-inventory headline folds observed compatible findings into one clause (2026-10-28)

**Status (2026-10-28): OPEN.** A one-line summary with standing cross-source inventory states the observed COMPATIBLE verdict as a single `compatible` clause, so an informational or quality finding is counted there, not named. The `summary.change_inventory` block has no additions/quality/informational split and its JSON shape is fixed, so the split needs an additive field and its schema ruling. The parts still add up to the total. Owner: `report/change_inventory.py`, `report/render_text.py`.
<!-- no-canary: report-shape decision: the split needs an additive summary field and its schema ruling; today's single clause is the deliberate interim, not a wrong value -->

#### Contract-unscored findings are not named by the JSON summary or the release line (2026-10-28)

**Status (2026-10-28): OPEN.** A finding the compatibility policy left `NOT_EVALUATED` is in `summary.total_changes` and in no bucket, and the JSON names it nowhere except as that surplus; its per-change status is emitted only under a stamped contract relevance. The one-line summary states it as `N not evaluated`. The release line's total is the sum of member buckets, so unscored release findings are absent from it. Owner: `report/release_oneline.py`, `report/json_sections.py`.
<!-- no-canary: report-shape decision: naming unscored findings needs a JSON/release-line field and its schema ruling; the counts reconcile today -->

#### An exported instantiation of a public class template is not in the public contract (2026-11-03)

**Status (2026-11-03): OPEN.** case180's `symbol_binding_lost_unique` is excluded under the default public contract: its owner `Registry<Widget>::instance()` is an instantiation of a public-header class template that no header frontend emits, so it reads `exported_not_public`. The local-static-to-owner link is fixed; the instantiation membership needs an ADR-049 ruling. [Full write-up](known-gaps/an-exported-instantiation-of-a-public-class-template-is-not-in-the-public-contract.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_case180_binding_loss_is_filtered_as_exported_not_public -->

#### The `#ifdef` guard census is runtime-only, so stored snapshots fall back to the `sycl::` name rule (2026-10-25)

**Status (2026-10-29): PARTIAL — fixed for persistence (schema v67); the ELF-only, name-only census matching below remains open.** The census of exports declared only under an excluded `#ifdef` (`extract/headers/preprocessor_branches.py`, reported per macro in `coverage_warnings` and `undeclared_export_census.by_guard`, report schema 6.7) is computed from the castxml run's own `-E -dD` output and is now persisted on `AbiSnapshot.live_preprocessor_hidden` as the document key of the same name. A compare of stored snapshots attributes a generic `#ifdef FOO` export to `FOO` exactly as a live compare does (`tests/test_live_stored_census_parity.py`, `tests/test_preprocessor_hidden_census_integration.py`). A pre-v67 snapshot carries no scan and uses the `sycl::` name rule only, and a clang-frontend parse and a multi-TU manifest still carry no scan. No `EXTRACTOR_GENERATION` bump: the scan's content is unchanged, only its persistence, which the `SCHEMA_VERSION` bump to 67 covers. Also open: the census reads only the ELF export table, and matches an export to a hidden declaration by function name only (no variables, no signature check).
`hygiene` — A stored-snapshot compare under-reports why exports are undeclared; it never fabricates a finding. Owner: `confidence.undeclared_export_censuses`.
<!-- no-canary: residual is under-reporting of a coverage census (ELF-only, name-only matching), never a finding; a pin would need a PE/Mach-O guarded-export fixture -->

#### The Markdown "Additions" section is one row per finding, so it grows linearly (2026-10-22)

**Status (2026-10-22): OPEN.** The review-group section is bounded; the per-finding Additions rows are not. [Full write-up](known-gaps/the-additions-markdown-section-is-one-row-per-finding.md)
<!-- no-canary: output-size growth, linear by design today; a canary would only restate the row-per-finding renderer -->

#### The legacy gate counts a redundant finding the review groups omit (2026-10-23)

**Status (2026-10-26): OPEN — gate-semantics decision needed.** A finding folded out as redundant but kept for the verdict is `gating` under the legacy scheme and `non_gating` under a severity config. `result_counts.gating_retained` now partitions the retained findings and the summary names the folded one, so the counts no longer contradict each other; the scheme disagreement remains. [Full write-up](known-gaps/legacy-gate-counts-a-redundant-finding-the-review-groups-omit.md)
<!-- no-canary: awaits a gate-semantics decision between two schemes that are each internally consistent; no wrong value to pin until the ruling -->

### Source-replay twin pairing (L4/L2 Phase 8)

**Status (2026-10-23): OPEN, narrowed.** Two observations are reported by both the header tier and the L4 replay: a header-only inline removal (`INLINE_FUNCTION_REMOVED`) and a C++ `constexpr` value change (`CONSTEXPR_VALUE_CHANGED` / `CONSTANT_CHANGED`). `compare.source_replay_twins.REPLAY_TWINS` keeps the header-tier finding and drops the replay copy, matched by name, one for one. For constants, the match is on the qualified name only: a replay copy whose value disagrees with the header copy is still dropped, and the header copy's value is what is reported. The "L2 tier not run, replay copy kept" case is covered only at unit level, because the replay alone does not produce the copy end to end (a no-header run matches no symbols). For inline removals the match is on the bare function name. Two limits remain there. (a) A header-tier symbol that does not demangle (no `demangled_symbol`, no demangler result) keeps its replay twin visible; this is conservative, not a silent loss. (b) Two overloads of one name pair by count, not by signature, because the replay `Change` carries no parameter list, so which of the two overloads is the twin is not recorded. Both replay copies are still counted in the ledger. Closing (b) needs the replay finding to carry its signature hash.
<!-- no-canary: needs an L4 source-replay run with clang source extraction on both sides plus header tier; the residual overload pairing is covered at unit level in tests/test_source_replay_twins*.py -->

### Source-replay typedef pairing (L4/L2 Phase 8)

**Status (2026-10-23): OPEN, narrowed.** A removed or retargeted typedef is reported by the header tier (`TYPEDEF_REMOVED`, `TYPEDEF_BASE_CHANGED`) and by the L4 replay (`PUBLIC_TYPEDEF_REMOVED`, `PUBLIC_TYPEDEF_TARGET_CHANGED`). `compare.source_replay_twins.REPLAY_TWINS` (the typedef pairs) keeps the header-tier finding and drops the replay copy, matched by qualified name (the header entity id rendered through `render_display_name_or_leaf`), one for one, and recorded as `deduplicated`. Two limits remain. (a) The value spellings are not compared, so a pair whose two tiers disagree on the type text (`long int` vs `long`) still pairs; this is deliberate, since a value check would miss every real pair, but it means a replay finding is dropped even when the tiers name different types. (b) A header-tier finding with no entity id pairs by bare symbol, so in C++ a namespaced typedef without an entity id keeps its replay twin visible (conservative).
<!-- no-canary: needs an L4 source-replay run; limit (a) is a deliberate pairing rule, limit (b) is conservative (keeps a duplicate visible) -->

### Clang projection (ADR-063 Phase 4 prerequisites)

The entries below come from the Phase 4 measurement spike
(`scripts/measure_l4_clang_projection.py`, 253 catalog TUs, 2026-10-22; numbers
in the plan's "Status — Phase 4 measurement spike" and "Status — Phase 4 golden and
pinned set" notes; the b1 golden test reproduces the same counts, and none of the
statuses below changed). They are gaps in
`dumper_clang._ClangAstParser`'s output as an L4 projection source, not in L4's
current emitter, which is unchanged.

#### The clang parser leaves `origin` unset, so a projected entity's visibility and `api_relevant` are not derived (2026-10-22)
**Status (2026-10-22): OPEN.** `_ClangAstParser.parse_functions` and `parse_types` leave `Function.origin` and `RecordType.origin` at `UNKNOWN`, with `source_header` and `source_location` unset (checked on a corpus TU), so `base.entity_from_*` maps every projected entity to `visibility="unknown"` and, for functions and records, `api_relevant=False`. On the 253-TU corpus, `visibility` and `api_relevant` differ on 1531 of 1531 paired functions and 290 of 290 paired records. The fix belongs in the parser's origin classification (the same provenance matcher `_decl_is_public` uses), before a projection can carry these fields.
<!-- canary: tests/test_l4_clang_projection_golden.py::test_pinned_set_matches_the_measured_projection_equivalence -->

#### Clang-projected member and namespace names are bare, so `qualified_name` differs from the current L4 name (2026-10-22)
**Status (2026-10-22): OPEN.** A member function is projected as `execute` where L4 emits `Processor::execute`; a namespaced record as `task_arena` where L4 emits `mylib::task_arena`; an enum likewise. `parse_typedefs_qualified` exists, but no qualified form is provided for functions, records or enums. On the corpus, `qualified_name` differs on 1045 of 1531 paired functions (205 TUs), 130 of 290 records (75 TUs) and 5 of 5 enums. A qualified name must be built from the declaration scope before a projection can use it as an identity input.
<!-- canary: tests/test_l4_clang_projection_golden.py::test_pinned_set_matches_the_measured_projection_equivalence -->

#### A C++ declaration with no clang `mangledName` falls back to its bare name and loses its mangled identity in the projection (2026-10-22)
**Status (2026-10-22): OPEN; cause not traced.** For `distance(Point, Point)` in `case69_trivial_to_nontrivial` (`v1.cpp` and `v2.cpp`) clang's AST carries no usable `mangledName`, so `Function.mangled` falls back to the bare name `distance`. `base.entity_from_function` then blanks `mangled_name`, and `SourceEntity.identity()` keys the declaration on `qualified#signature_hash` where L4 keys it on `_Z8distance5PointS_`. This is the fabricated-identity case ADR-063 A1 names. Pairing-free counters show 502 projected function entities with a blank mangled name against 404 on the current side, so the blanking is wider than these two; the cause of the 404 and 502 split has not been traced.
<!-- canary: tests/test_l4_clang_projection_golden.py::test_pinned_set_matches_the_measured_projection_equivalence -->

#### Hash fields and the default-argument string are spelled differently by the two producers, so cross-producer equality is not a semantic check (2026-10-22)
**Status (2026-10-22): OPEN.** `signature_hash` differs on 1374 of 1531 paired functions, record `type_hash` on 290 of 290, and typedef `type_hash` on 22 of 22. The inputs differ (L4 hashes its own `_signature(node)` string, `base.py` hashes `return(params)`), so the hashes cannot show the underlying types are equal. The default-argument `value` differs on 18 pairs: L4 spells `p0=sha256:…` and the projection spells `max_threads=expr:…`. A pinned set cannot include these fields until both producers use one canonical spelling.
<!-- canary: tests/test_l4_clang_projection_golden.py::test_pinned_set_matches_the_measured_projection_equivalence -->

### False-clean risk

#### A stripped library compared without headers reads `NO_CHANGE` with `partial` assurance and exit 0 (2026-11-08)

**Status (2026-11-08): OPEN.** Found while building the invariant oracles (`tests/test_invariant_oracles_reduced_evidence.py`).
[Full write-up](known-gaps/a-stripped-library-compared-without-headers-reads-no-change-with-partial-assurance-and-exit-0.md)
`false-clean` — a symbols-only compare of a stripped library reads `NO_CHANGE` with exit 0 and an accepted headline.
<!-- canary: tests/test_invariant_oracles_reduced_evidence.py::test_stripped_pair_gap_is_still_the_current_answer -->

#### One-sided DWARF or an unmet `--depth headers` request reads a clean verdict with exit 0 (2026-11-08)

**Status (2026-11-08): OPEN.** Found by the evidence-shape oracles (`tests/test_invariant_oracles_evidence_shapes.py`). Sibling shapes of the stripped-pair entry above: an old library with DWARF against a stripped new one reads `COMPATIBLE` (`partial`, `version_bump: patch`), and a stripped pair compared with `--depth headers` and no headers reads `NO_CHANGE` (`failed`). Both exit 0, and every renderer states the clean result. [ADR-090](adr/090-unresolved-surface-is-not-a-clean-verdict.md) (proposed) covers both.
[Full write-up](known-gaps/one-sided-dwarf-or-an-unmet-depth-headers-request-reads-a-clean-verdict-with-exit-0.md)
`false-clean` — a compare with type-level evidence on one side only, or with its requested depth unmet, reads clean with exit 0.
<!-- canary: tests/test_invariant_oracles_evidence_shapes.py::test_one_sided_and_unmet_depth_gap_is_still_the_current_answer -->

#### A castxml base whose bare leaf two records share is not resolved by the leak walk (2026-11-05)

**Status (2026-11-05): OPEN, narrow.** castxml writes a base class as its bare leaf (`Base`) and, unlike a field or signature slot, records no `type_identities_fact` for it. When two records share that leaf (`lib::detail::Base` and `other::Base`) and C++ name lookup from the derived class's scope does not find the one meant (the base lives in a sibling namespace), the leak walk (`model/type_spelling_resolve.py`) keeps the spelling unresolved rather than guess, so a layout change to that internal base reports no `internal_type_leaks_via_public_api`. The clang backend writes the base as the header did and is unaffected. Closing it needs castxml base identities in the snapshot. `tests/test_detector_frontend_spelling.py` pins the resolved shapes.
<!-- canary: tests/test_detector_frontend_spelling.py::test_castxml_ambiguous_bare_base_leak_is_not_reported -->

`false-clean` — a castxml dump can miss the leak finding (the raw size change still gates) for an internal base whose bare name is ambiguous.


#### A reloaded binary snapshot written since ADR-079 cannot be flattened with `snapshot_to_dict` (2026-11-04)

**Status (2026-11-04): OPEN.** Since ADR-079 (#1914) every binary dump writes the `binary` section's `abicheck.export_read` extension fact (`{}` when every export table was read completely). Absent means `unrecorded`, so the writer cannot leave it out. A snapshot loaded from disk keeps the fact in `WireExtensions.header_extras`, and `storage/wire_extensions.flattening_loses` counts any header extra as lossy, so `snapshot_to_dict` refuses every reloaded binary snapshot written since ADR-079 (an older one without another carrier still flattens). The frozen gen35 extractor corpus is the first fixture to carry the fact. Its flat-document differentials (`test_snapshot_section_decode.py`, `test_snapshot_lazy_decode.py`) assert that refusal and compare every other observed value, until the flat document can carry an understood extension fact, or `flattening_loses` exempts one the flat path can round-trip. Owner: `storage/wire_extensions.py`, `storage/extension_facts.py`.
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_reloaded_binary_snapshot_refuses_to_flatten -->

#### A cached header parse does not see a newly reachable system header that its depfile never named (2026-11-01)

**Status (2026-11-01): OPEN, narrow.** The header-AST and whole-snapshot caches fold the compiler's system include directories by spelling and validate an entry by the files the parse read (`extract/cache_dep_manifest.py`, ADR-062 D11 implementation note). Three changes are not seen. (a) A file created where a `__has_include` probe or a failed `#include_next` search would now find it: the depfile lists only files that were opened, so a probe that answered "absent" records nothing. (b) Shadowing is checked lexically: a recorded header's relative name is checked absent in every *earlier* search directory by path spelling, so a directory reached through a symlink under a different spelling is not covered. (c) A frontend run that writes no depfile (a DPC++ multi-pass `-fsycl` run, or an injected test runner) records an empty manifest: its system headers are keyed by directory spelling only, as before ADR-062 D11, so an in-place system-header edit is not seen for that run. Hashing every file under the system directories instead walked whole SDK trees on each lookup (the Windows SDK and MSVC include dirs), which timed out Windows dumps. ccache's direct mode has the same (a) and (b). Each case is a stale hit only after a system-header installation that adds files without changing any file the parse read.
<!-- no-canary: needs a staged system-include tree with __has_include probes and symlinked search dirs across two cached runs; no cheap fixture yet -->

`false-clean` — after such an installation a warm dump can serve the snapshot of the previous system-header set.

#### On Windows an in-process file digest can miss a rewrite that kept size and mtime (2026-11-03)

**Status (2026-11-03): OPEN, narrow.** `extract/cache_input_digest.py` reuses a memoized file digest while the stat signature (device, inode, size, mtime, ctime) is unchanged. On Windows `st_ctime` is the creation time, so a rewrite that restores both size and mtime leaves the signature unchanged. The cross-process store is therefore off on Windows (`_STORE_SIGNATURES_TRUSTED`). The memo inside one process still applies there: it is protected only by the racy-mtime window, which does not cover a file whose old mtime is restored on purpose. Closing it needs a change time that Windows `os.stat` does not expose (CPython plans to make `st_ctime` the change time on Windows in a later release). [Full write-up](known-gaps/windows-in-process-file-digest-misses-size-and-mtime-preserving-rewrite.md)
<!-- no-canary: Windows-only: needs st_ctime as creation time, which no Linux CI lane has -->

`false-clean` — within one Windows process, a cache key can fold the digest of the file's earlier bytes.

#### A readable but corrupted `.dynsym` entry can still read as a clean compare (2026-10-27)

**Status (2026-10-28): PARTIAL.** A shared object whose section header table is zeroed, absent or stripped (`sstrip`, `e_shoff`/`e_shnum` cleared) is no longer refused: the loader's view of the dynamic table (`PT_DYNAMIC`, `DT_SYMTAB`/`DT_STRTAB`/`DT_STRSZ`/`DT_SYMENT`, the `DT_GNU_HASH` or `DT_HASH` count, `DT_VERSYM`/`DT_VERDEF`/`DT_VERNEED`) yields the same exports, bindings, visibility and versions as the intact build (`abicheck/extract/elf_loader_view.py`, `tests/test_dynamic_table_loader_view.py`). An export table that is itself unreadable (no `PT_DYNAMIC`, a `.dynsym` filled with `0xff`, truncated, or exported entries with no resolvable name) is still refused at dump with exit 1 (`tests/test_export_table_unreadable_matrix.py`, OLD/NEW/both). Residual: corruption that leaves every entry structurally valid is read as the table it now says. Random byte flips inside `.dynsym` (`st_info`, `st_shndx`, `st_value`, or a `st_name` that still lands on a non-empty string) and garbage bytes in `.dynstr` that still yield non-empty names parse without a failure. On one side the result is a fabricated `BREAKING`; on the corrupted side of a pair it can be `COMPATIBLE` (exit 0, removals hidden) or, with both sides identically corrupted, `NO_CHANGE`. No reader can tell these from a legitimate table without a checksum.
<!-- no-canary: needs a hand-corrupted ELF whose damaged entries still parse; the fixed half is pinned by tests/test_dynamic_table_loader_view.py, the residual has no minimal corrupter -->

`false-clean` — `compare` of a library whose `.dynsym` content is corrupted but structurally valid can exit 0 with `COMPATIBLE` or `NO_CHANGE`.

#### `--depth binary` on a header-built snapshot misses debug-only declaration findings (2026-10-25)

**Status (2026-10-25): PARTIAL — no longer a false clean; the findings themselves are still missing.** A header snapshot does not persist debug-info subprogram/variable records or record relations, so its `--depth binary` projection cannot report the signature, typedef, vtable or base findings a binary-only dump reports. Fixed 2026-10-25: such a projection (header-built side with debug info) now reads `analysis_assurance` `partial` and names both missing evidence families (`policy/depth_projection_gaps.py`, `policy/analysis_assurance/_depth_projection.py`; `tests/test_depth_projection_declaration_gap.py`). Residual: the findings are not reported; each is named in `tests/test_depth_projection_matches_shallow_dump.py`.
`false-clean` — `compare old.json new.json --depth binary` on two header snapshots can read `NO_CHANGE` (with `partial` assurance naming the missing evidence) where a binary-only dump of the same libraries reads `BREAKING` (a return or parameter type change). [Full write-up](known-gaps/depth-binary-projection-cannot-reproduce-debug-declaration-findings.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_depth_binary_projection_of_header_snapshots_misses_a_return_change -->

#### A removed class-metadata export of a templated or destructor-only class is not confirmed by its header (2026-10-22)

**Status (2026-10-25): PARTIAL, narrowed.** A `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT` export is confirmed by its owning class when the class's exact path is declared, or when a public, unambiguous, non-pattern record's own mangled member spells the owner's mangled scope exactly (`policy/class_metadata_identity.declared_class_owner_scopes`): a virtual slot (inline namespaces and specialization arguments kept verbatim, so `_ZN3api3BoxIiE3getEv` proves `_ZTVN3api3BoxIiEE`), or the record's implicit `operator=`, whose parameter is the record itself (this proves a dtor-only class in an inline namespace). A primary template, a different specialization and a user-declared `operator=` prove nothing. Residual: a specialization or inline-namespace dtor-only class with no mangled member in the snapshot (no non-destructor virtual and no implicit `operator=` recorded, e.g. a class with a deleted copy assignment, or a backend that does not emit implicit members) stays `UNKNOWN_UNRESOLVED`. `tests/test_class_metadata_specialization_owner.py` pins both.
`false-clean` — in the residual case, the removal of such a class's vtable or typeinfo reads `NOT_EVALUATED` under `--contract public` and does not gate the verdict.
<!-- canary: tests/test_class_metadata_specialization_owner.py::test_specialization_without_mangled_member_stays_unconfirmed -->
<!-- canary: tests/test_class_metadata_specialization_owner.py::test_dtor_only_without_assign_stays_unconfirmed -->

#### A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml (2026-10-21)

**Status (2026-10-25): PARTIAL — the clang alias/variable default is now a named gap, not a finding; residual: the textual scan misses macro-generated or indirectly-included alias and variable templates, whose default change can still read `complete` NO_CHANGE.** castxml 0.7 emits no element for an uninstantiated template and names an instantiation as written (`Box<char>`, `f` with `_ZN1m1fIiEEvv`), so the default cannot be recovered from its XML; its run reads `NO_CHANGE` with `partial` assurance and `template-parameter defaults (castxml cannot record them)` named (ADR-085 D3), for class, function, alias and variable templates and for type, non-type and template-template defaults. Fixed 2026-10-24: a clang function template whose default cannot be spelled (`int N = sizeof(T)`) records the defaults as `UNSUPPORTED` and names the gap. Fixed 2026-10-25 (extractor generation 23): the clang backend still models neither alias nor variable templates, but a clang run now scans its public headers for one with a default template argument (`extract/headers/template_default_scan.alias_or_variable_template_default_scan`) and names `template-parameter defaults (the clang backend does not model alias or variable templates)`, so a default change on one reads `partial`, never `complete` `NO_CHANGE`. Residual: on clang such a change is not reported as `template_param_default_changed` (no model entity carries the default; an unchanged default also reads `partial`), and the scan is textual, so a macro-generated alias or variable template is not seen. `tests/test_template_default_assurance_gap.py` pins every family on both backends.
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_castxml_class_template_default_change_reads_partial_no_change -->
<!-- canary: tests/test_known_gap_canaries_header_backends.py::test_clang_macro_spelled_alias_template_default_is_not_seen -->

`false-clean` — A default template argument changed on a header-only template reads `NO_CHANGE` on castxml (clang reports `API_BREAK`), and the source-level consequence appears only as an L4 risk on castxml. [Full write-up](known-gaps/a-header-only-default-template-argument-change-reads-no-change-at-l2.md)

#### A header-depth `kind: bundle` check has no runner-scale memory receipt, and no Action emits bundle facts (2026-09-12)

**Status (2026-11-01): PARTIAL, narrowed.** Fixed 2026-11-01: a bundle check runs at `depth: headers`. `check-target` takes the OLD side from `resolve-baseline`'s `member-snapshots-dir` (not `binaries-dir`), refuses a baseline whose member snapshots do not all carry header evidence, dumps each candidate member against its own `public_headers:` (run-plan `member_headers`, `workflows/bundle_candidate_snapshots`), and compares the two snapshot directories with `--depth headers`, so the fan-out is budgeted at the headers rung. `BUNDLE_CHECK_DEPTHS` is `{binary, headers}`; a header-depth bundle needs `public_headers:` on every member. Still open: (c) no measured peak-memory/wall-clock receipt for a large header-depth bundle on the 16 GB runner (a run that overruns fails loudly, it does not pass), and (d) no first-party Action declares a bundle-facts output. `tests/test_bundle_header_depth_routing.py`.
`hygiene` — A large header-depth bundle check is not proven to fit the 16 GB runner, and no Action emits a bundle-facts document. [Full write-up](known-gaps/a-kind-bundle-check-still-cannot-run-at-depth-headers-only-the.md)
<!-- no-canary: residual is a missing runner-scale memory receipt and Action wiring; it is measured (scripts/bench_release_memory.py), not asserted -->

#### Header-pass body coverage is one blanket unit, not per body (2026-10-11)

**Status (updated 2026-10-14): PARTIAL, narrowed.** The header pass now marks each declaration whose body it parsed in full (`header_body` node attribute), and `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` judges call/reference edges per body seen on both sides. Still open: the edge-query coverage records (`pass_coverage_records`) still give a header pass the blanket `HEADER_INLINE_BODIES` unit, and `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED` still needs whole-project call coverage on both sides.  
`false-clean` — Edge queries and the call-reachability finding still decline body-dependent edges from a header-only pass, even for a body seen on both sides. [Full write-up](known-gaps/header-body-coverage-is-not-per-body.md)
<!-- no-canary: coverage-unit granularity of edge-query records; the conservative blanket unit declines findings, so a pin would restate the record shape -->

#### Type identity: typedef and tag names keyed by bare spelling

`false-clean` — Two entries on one shape: a typedef's identity is its bare spelling, so a tag-namespace record and a same-spelled typedef collide in the clang parser, and typedef diffing still reads the bare-keyed `AbiSnapshot.typedefs` instead of the qualified twin.

- [`dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace identity with an ordinary-namespace typedef identity that happens to share the same spelling — pre-existing, not introduced by PR #719's own changes, but investigated and confirmed reachable in this pass (Codex review, investigated, not fixed)](known-gaps/dumper-clangpys-parse-types-conflates-a-cc-tag-namespace.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** The code moved to extract/headers/clang/records.py:145, where identity = "::".join([*entry.scope, name]) still uses the anon-typedef fallback name. Tag and ordinary namespaces are not distinguished, so `struct Foo; typedef struct{} Foo;` still collides.
  <!-- no-canary: the colliding C spelling needs a clang AST parse of a tag/typedef pair; not yet reduced to a CLI fixture -->
- [Qualified typedef twin exists but the surface graph identity table still reads bare-keyed AbiSnapshot.typedefs](known-gaps/qualified-typedef-twin-exists-but-diff-typessurface-still-read.md)  
  **Status (2026-10-21): PARTIAL, narrowed.** Typedef diffing in diff_types.py is wired (prefers `typedefs_qualified`, falls back to the bare map for pre-v25). The public-surface closure is wired by branch `fix/surface-qualified-typedefs`. Still bare-keyed: the surface graph's typedef identities (`model/graph_entity_identity.py`, `compare/surface_graph.py`) and a qualified-spelling tail candidate; the page lists the exact remainder.
  <!-- no-canary: internal identity-table keying; the user-visible typedef diff is already qualified, so no CLI-visible wrong answer to pin -->

#### The castxml L4 source-ABI extractor does not fold the resolved EMULATED compiler's identity into either `fact_set.compiler_version` or the D8 TU cache key — attempted once for the persisted half, and REVERTED after a follow-up review caught it as a real regression, not a fix (Codex review, PR #719, three follow-up rounds)

**Status (re-verified 2026-10-25): OPEN.** buildsource/source_extractors/castxml.py still resolves cc_bin. compiler_version is _castxml_identity_with_stat_fallback(self.castxml_bin) (line 723), and the comment at line 719 says a `cc_bin --version` probe is still future work.  
`false-clean` — The castxml L4 extractor's persisted `compiler_version` and TU cache key omit the emulated compiler's identity, so changing that compiler can reuse stale facts. One attempted fix was a regression and was reverted. [Full write-up](known-gaps/the-castxml-l4-source-abi-extractor-does-not-fold-the-resolved.md)
<!-- no-canary: needs two emulated compilers (different --castxml-cc identities) and a warm L4 cache; no second compiler in CI -->

#### L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/adapter_contract.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.  
`false-clean` — An `icpx`/`dpcpp` override now gets the right `-fsycl` flags and no longer crashes, but L4 replay still runs one context; host+device dual-context replay is not implemented. [Full write-up](known-gaps/l4-sycl-replay-via-a-resolved-gcc-path-icpxdpcpp-override-flag.md)
<!-- no-canary: needs the Intel icpx/dpcpp SYCL compiler -->

#### Include-graph dependency files are never captured in production

`false-clean` — `depfile_args_from_argv`'s `trusted_root` has no production caller, so `@file` tokens are dropped, and the L2 header parse never writes a dependency file, so `depfile_resolved_paths` is never fed.

- [`depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail vulnerability is closed, real production wiring not implemented](known-gaps/depfile-args-from-argvs-trusted-root-parameter-the-self-jail.md)  
  **Status (re-verified 2026-10-25): OPEN.** grep finds no `trusted_root=` argument at any production call site; only buildsource/include_graph.py defines the parameter (lines 119-216), so production callers still drop the @file token.
  <!-- no-canary: unwired parameter: no production behaviour differs until a caller passes it; a canary would pin a call-site absence -->
- [The L2 header parse never captures a dependency file (2026-10-03)](known-gaps/the-l2-header-parse-never-captures-a-dependency-file-2026-10-03.md)  
  **Status (re-verified 2026-10-25): OPEN.** `depfile_resolved_paths` is a parameter of `abicheck/comparability.py` (line 314, forwarded at 443) and named in a `header_conditionals.py` docstring; no production caller passes it.
  <!-- no-canary: unwired parameter: no production caller passes depfile_resolved_paths, so there is no observable output to pin -->

#### `Param.is_va_list` (G31 Phase C continued) inherits the toolchain- identity-probe gap above, rather than being a new problem

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** snapshot_reliability.py:182 and fact_registry_schema.py:273 have clang_va_list_facts_reliable as a plain flag without target identity, and no toolchain-identity probe exists yet (see entry 9).  
`false-clean` — `clang_va_list_facts_reliable` is a plain flag with no target identity, inheriting the toolchain-identity-probe gap rather than being a new defect. [Full write-up](known-gaps/paramis-va-list-g31-phase-c-continued-inherits-the-toolchain.md)
<!-- no-canary: depends on a toolchain-identity probe that does not exist; reproducing needs two targets with different va_list layouts -->

#### A using-declaration re-exporting a namespace-scope constant produces two `AbiSnapshot.constants` entries for one declaration on the castxml backend — reported against real oneDAL headers. A per-snapshot, name-shape-based dedup was implemented, reviewed, found unsound in two independent ways, and reverted rather than shipped broken (Codex review, three rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The entry's own later verification (known-gaps.md ~4030-4068) found both backends now drop the alias spelling (a false negative), so the alias-identity gap remains open. There is no UsingShadowDecl threading in the code.  
`false-clean` — A using-declaration re-exporting a namespace-scope constant: the reverted dedup was unsound; both backends now drop the alias spelling (a false negative) and no `UsingShadowDecl` identity is threaded. [Full write-up](known-gaps/a-using-declaration-re-exporting-a-namespace-scope-constant.md)
<!-- no-canary: both backends now drop the alias spelling; pinning the false negative needs an L4 replay fixture that is not yet reduced -->

#### Dependency static/dynamic linking-mode change has no ChangeKind

**Status (re-verified 2026-10-25): OPEN.** docs/contribute/abi-taxonomy-coverage.md:177 still lists dependency-abi.linking-mode-change as NOT_IMPLEMENTED, and no ChangeKind containing linking_mode exists in abicheck/model/change_catalog.  
`false-clean` — A dependency switching between static and dynamic linking has no `ChangeKind` (`dependency-abi.linking-mode-change` is NOT_IMPLEMENTED). [Full write-up](known-gaps/dependency-staticdynamic-linking-mode-change-has-no-changekind.md)
<!-- no-canary: missing detector (no ChangeKind exists); tracked as NOT_IMPLEMENTED in docs/contribute/abi-taxonomy-coverage.md -->

#### AArch64 AAPCS64 by-value aggregate passing is not modeled

**Status (re-verified 2026-10-25): OPEN.** abicheck/dwarf_advanced.py:893-899 models only SysV AMD64 (_SYSV_AMD64_RETURN_ARCHES), and its comment says an AArch64 HFA flip is just a generic value-ABI change.  
`false-clean` — By-value aggregate passing is modelled only for SysV AMD64; an AArch64 HFA/HVA flip reads as a generic value-ABI change or not at all. [Full write-up](known-gaps/aarch64-aapcs64-by-value-aggregate-passing-is-not-modeled.md)
<!-- no-canary: needs an AArch64 cross toolchain to build the HFA pair -->

#### A resolved PDB/DWP/dSYM artifact reaches no dump path

**Status (re-verified 2026-10-25): OPEN.** `abicheck/dumper.py:1051` `_dump_pe` has no debug_roots parameter, and `dwp_path`/`dsym_path` are set only by `debug_resolver.py` and read only by `extract/debug_artifact.py`'s description (no dump consumer). The directory form still resolves PDB/DWP/dSYM artifacts that are then dropped.  
`false-clean` — The directory form resolves PDB/DWP/dSYM artifacts, but no dump path consumes them, so the debug evidence is dropped. [Full write-up](known-gaps/a-resolved-pdbdwpdsym-artifact-reaches-no-dump-path.md)
<!-- no-canary: needs PDB (MSVC) or dSYM (Apple) artifacts; the DWP half needs split-DWARF packaging not in CI -->

#### A failed header parse on PE or Mach-O falls back to export-table mode and can exit 0 (2026-11-01)

**Status (recorded 2026-11-01): OPEN.** `workflows/dump/header_scoped.py` catches any header-parse failure of a PE or Mach-O binary, warns `Falling back to export-table mode -- --header/--include were ignored`, and returns an export-table snapshot. The run then reads `requested_depth: binary` (implicit) and `header_context_status: not_evaluated`, and can exit 0, where the same failure on ELF fails the run. Found by the macOS lane of `tests/test_sided_include_labelled_composition.py`, whose negative control pins the current behaviour on darwin.
`false-clean` — An explicitly supplied header that does not parse on PE/Mach-O is reported as a warning and a binary-depth run instead of a failed acquisition. [Full write-up](known-gaps/a-failed-header-parse-on-pe-or-mach-o-falls-back-to-export-table-mode.md)
<!-- no-canary: PE/Mach-O only; the macOS lane of tests/test_sided_include_labelled_composition.py pins the current behaviour on darwin -->

#### A stored snapshot keeps no record of a partial export-table read

**Status (2026-11-03): PARTIAL — fixed for persistence and consumers ([ADR-079](adr/079-partial-export-table-read-in-the-stored-snapshot.md), stored as the `binary` section's `abicheck.export_read` extension fact, ADR-062 D19); narrowed.** Each platform block's export read is recorded (`complete`/`partial`/`failed`; a document without the fact reads `unrecorded`, never `complete`). The export fact, `exports` coverage, consumer scoping, the comparison (a removal a partial NEW table cannot establish is kept as `COMPATIBLE_WITH_RISK`, rule `partial_export_table_read`), analysis assurance and `run_outcome.scope` honor it, live and stored alike. Tests: `tests/unit/storage/test_export_read_persisted.py`, `tests/test_cli_compare_partial_export_read.py`. Still open: an older baseline cut short has no fact and stays unrecorded (re-dump it); a release/package member's partial read lowers that member's assurance but not the release `comparison_scope` record.  
`false-clean` — A stored snapshot now records a partial export-table read and every reader honors it; only an older baseline with no record can still hide one. [Full write-up](known-gaps/a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read.md)
<!-- no-canary: residual applies to baselines written before ADR-079, which cannot be produced by the current dumper -->

#### `debug = 1` DWARF has no function types, so a return-type break reads `NO_CHANGE` with `assurance: complete` (2026-10-15)

**Status (2026-10-15): OPEN.** The DWARF section is present, but exported subprograms carry no `DW_AT_type` or parameters, and assurance still reads `complete`.
`false-clean` — A Rust cdylib built with `debug = 1` misses a return-type change and exits 0 with a complete-assurance label. [Full write-up](known-gaps/debug-1-dwarf-drops-function-types-with-complete-assurance.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_g1_dwarf_return_type_change_reads_complete_no_change -->

#### A function-pointer parameter's signature is not compared, so a callback arity change reads `NO_CHANGE` on the header path (2026-10-15)

**Status (2026-10-24): PARTIAL — fixed for the header (castxml) path; the DWARF residual below remains.** `extract/headers/castxml/type_resolution.py` spells a castxml `FunctionType` as its full declarator (`int (*)(int, int)`, with `...`, calling-convention attributes and the pointer/reference/cv/array declarator) instead of the opaque `FunctionType` tag, so an inline callback parameter, a pointer to one, a callback typedef, a struct field and a returned callback report `func_params_changed`/`typedef_base_changed`/`type_field_type_changed`. `EXTRACTOR_GENERATION` advanced to 16 for this fix; a pre-16 baseline's `FunctionType*` against a fresh declarator is demoted as generation drift (`policy/generation_drift_demotion.py`), and a callback typedef is no longer read as an opaque handle (`idioms.py`). Tests: `tests/test_castxml_function_pointer_spelling.py`, `tests/test_castxml_function_pointer_integration.py`. **Status (generation 29): the DWARF subroutine part is FIXED.** `DW_TAG_subroutine_type` spells its return, parameters and convention (`void (*)(int) __attribute__((ms_abi))`) in both DWARF readers (`dwarf_utils.subroutine_type_spelling`), so a DWARF-only callback signature change is reported. castxml drops `ms_abi`/`sysv_abi` from its XML; the clang AST join (`extract/headers/castxml/function_type_cc.py`) restores the convention on fields, typedefs and parameters, and the clang frontend already carried it. Still open: (1) a GCC `-g` build writes no `DW_AT_calling_convention` for `ms_abi` on a subroutine type, so the DWARF lane cannot see that convention in a GCC binary (header lanes only; `tests/test_dwarf_subroutine_calling_convention.py` pins it as an xfail); (2) a function-pointer **return** of a non-default convention is not joined from clang (DWARF covers it); (3) C++ `noexcept` on a function type is not in castxml's output; (4) the PDB reader still spells a subroutine type `fn(...)` (`pdb_parser.py`). Matrix: `tests/test_type_spelling_differential_matrix.py`.
`false-clean` — (DWARF residual) A callback that gains a parameter is reported as no change on DWARF unless the type name happens to carry the signature. [Full write-up](known-gaps/function-pointer-signatures-are-not-compared-in-header-mode.md)
<!-- no-canary: DWARF residual not reproduced on 2026-11-05: gcc -g on a C int(*)(int) -> int(*)(int,int) parameter reports func_params_changed; re-verify the residual's shape before writing a canary -->

#### A default release-profile Rust run without headers reports `NO_CHANGE ... ACCEPTED` while its own warnings say the result is unconfirmed (2026-10-15)

**Status (2026-10-15): OPEN.** The headline label, the gate and the `analysis_assurance: partial` field disagree with each other, and the label is the clean one.
`false-clean` — A real return-type change in a header-less, no-debug run is labelled `NO_CHANGE` and accepted at exit 0. [Full write-up](known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md)
<!-- no-canary: needs a Rust toolchain (cargo) to build the cdylib pair -->

#### ADR-062 D14-D18 data-evolution rules are partly implemented (2026-10-30)

**Status (2026-10-31): PARTIAL.** D14, D15 and D17 are implemented with
the residuals below; D16's `evidence` and `transport` digests are
implemented; D18 is partial. A newer `section_schema_version` in a
directory package now blocks only that section, as in a single-file
document (closed 2026-11-02; `provenance` still refuses). Owner: `storage/`;
per-item status in the
[storage format v2 plan](plans/storage-format-v2.md).
<!-- no-canary: umbrella entry over several storage-format items; each residual is tracked per item in plans/storage-format-v2.md -->

- **D14 residual: a flat mapping cannot say what it lost. Closed
  2026-11-03.** `snapshot_to_dict` now refuses a snapshot whose flat
  document would lose stored state (a `not_collected` section included:
  the flat split emits every section, so it re-saved as present), so no writer that accepts a flat
  mapping (`to_sectioned_document`, `import_legacy_snapshot`,
  `import_bundle_facts`, `import_baseline_set`) can be handed one that
  silently lost carriers; `write_legacy_snapshot_package` takes only an
  `AbiSnapshot`, and the `dump` write (which stored a PE dump's failed
  `debug` section as present) writes the snapshot. Remaining, reader side:
  `from_sectioned_document` without `carry_extensions` returns a flat
  mapping without the carriers. Its four callers only peek (`library`, the
  `elf` section, the baseline-set content hash), and none hands the result
  to a writer, but `buildsource.baseline_set.compute_snapshot_content_hash`
  therefore hashes two documents that differ only in a blocked or failed
  section as equal.
- **D15 residual: one producer records a whole-section failure.** The PE
  dump records `debug` as `failed` when a located PDB fails to parse, on the
  export-table path only. Checked 2026-10-31 for every other extractor, and
  none fails a whole section: a castxml failure aborts the dump (requested
  headers that cannot be parsed are a dump error, not a section state); a
  DWARF open/parse failure (`AdvancedExtractionStatus.FAILED`) does not
  cover `debug` (which also holds the header AST's provenance, toolchain and
  reliability facts) or `layout` (scope, contract and coherence facts), and
  DWARF is an optional input on the binary path, so failing either section
  would make an unrequested input lower a passing run's assurance; a
  header-scoped PE dump's `debug` also holds the header parse's provenance.
  Each stays a fact-family `FactAvailability`. No producer records a
  whole section as `not_collected` yet: the reader honors one (its readers
  are blocked, never shown an empty section; closed 2026-11-03, it used to
  read as empty), but every in-tree writer stamps the sections it writes
  `present`, including an empty `types` on a binary-only dump. Recording
  that as `not_collected` would block every type detector on the standard
  binary-only path, which the evidence tiers already report; it needs its
  own decision. The inventory is generated from the encoders' declarations
  (closed 2026-11-03), but stops at record-class fields: a key path inside
  an opaque value (a fact object, `elf.versions_required`) is pinned by the
  section sha256, not enumerated. A directory package names its sections in
  its ref, so a section missing from one is never reported `unrecorded`.
- **D16 residual: `comparison_input`** has no consumer (no comparison-result
  cache exists) and is not implemented. A persisted evidence digest is used
  only against another persisted one: a snapshot decoded from the stored
  document can differ from it after load-time backfill, so mixing the two
  would read as "different" (the safe direction).
- **D17 residual: no CLI for package GC, no platform validation.**
  `workflows.package_gc.collect_package_garbage` is a Python API only (no
  `project` subcommand maintains packages yet). The amendment's oneDAL/MKL,
  macOS and real-Windows validation has not been run; the Windows lock path
  is tested against a simulated `msvcrt`.
- **D18 (partial; decode split and lazy declaration store landed
  2026-10-31).** `declarations`, `types` and debug layout now load lazily,
  together, as a pending `AbiSnapshot.semantic_ir` cell
  (`storage/snapshot_lazy_decode.py`, `model/lazy_declarations.py`), on a
  document with recorded `from_headers`, a schema past every legacy
  fact-backfill rule, and text proven free of closure markers. Still open:
  (1) every other document decodes eagerly, including every
  `snapshot_from_dict(dict)` caller with no scanned text (bundle archive
  blobs, project packages, matrix findings), whose load-time
  `SnapshotError` translation a deferred error would bypass; (2) a malformed
  deferred section is reported at first use, not at load, as for the graph;
  (3) **A2.5 is not met** -- nearly every detector reads
  `declarations`/`types` (`compare/section_readers.DETECTOR_SECTIONS`), so a
  full compare decodes the cell anyway, and peak RSS is set by the
  whole-document `json.loads` (140 MiB synthetic snapshot: 419 MiB peak
  before and after; 8-library project compare 393 vs 391-406 MiB); (4)
  the shared `evidence`-keyed decoded-section cache is not built (no
  consumer reads a section it could share), and a stored `BundleFacts`
  comparison still holds every `per_library_snapshots` member decoded.
  Closing (3) needs a streamed or per-section parse so a raw section is not
  held as parsed JSON. Measurements: the plan's A1.9 D18 entry.
  **Decision 2026-11-02 (maintainer): the target for (3) is that release
  fan-out peak memory does not grow with member count, bounded by the
  largest member (about 8x its decoded size); the streaming decode is
  deferred.** The release fan-out already loads and drops one pair at a
  time; what grew with N was per-member retention, ~75% of it both sides'
  contract type graphs, now held packed (`contract_evidence.TypeGraphSnapshot`).
  Still open: ~0.19 MiB per 1000 declarations stays per finished member by
  design (packed graph, public-declaration roots, compact bundle evidence),
  so an in-process fan-out still grows slowly with N, and concurrent
  in-process members raise the peak by the worker count, not by N.
  Before/after figures: plan A2.5.
  Per-section parse measurements: on a 140 MiB synthetic sectioned snapshot the parsed tree is 226 MiB and
  `declarations` holds 85% of the text; the decoded snapshot is 43 MiB.
  Ceilings (tracemalloc): today's text plus whole tree 359 MiB; whole text
  plus only `declarations` parsed 323 MiB; a streamed per-section read that
  never holds the whole text 287 MiB. Two load-time contracts force every
  section to be parsed at load anyway: D14's record-extras split
  (`wire_record_extensions.collect_record_extras` walks every record) and
  the generation spelling migration, both whole-document. So a per-section
  parse cannot leave an unread section unparsed without moving those errors
  to first use. Its best case (-20%) also needs a streaming read that
  reimplements `snapshot_io`'s decompression-bomb and size guards. The
  deferred item is an element-level decode: the `declarations` arrays parsed,
  checked for record extras, migrated and decoded one entity at a time, so
  that neither the whole text nor the section's tree is ever resident.
  Estimated bound: text plus decoded snapshot, about 180 MiB on the same
  input. That rewrites `split_wire_extensions`, the DTO migration,
  `export_legacy_sections`, `view_for` and `decode_declaration_store` as
  streaming passes. The decoded-section cache still has no reader.
  Measurements: plan A2.5.

#### castxml output carries no `noexcept` exception specification (2026-11-06)

**Status (2026-11-06): OPEN.** castxml 0.7 (`--castxml-output=1`) writes no attribute for a function's or method's exception specification, so `extract/headers/castxml/functions.py` always records `is_noexcept=False` (its `noexcept` search of the `attributes` string only matches an annotate attribute) and the castxml header lane never reports `func_noexcept_removed`/`func_noexcept_added`. The clang header backend reads the qualifier from the type spelling and reports it. Seen on case15 (declared in `ground_truth.json` `expected_kinds_gaps`). A fix would need a clang-AST join like `function_type_cc.py` or a source re-read; not done here. `false-clean` on castxml for noexcept changes. Owner: `extract/headers/castxml/`.
<!-- no-canary: the expected_kinds gate fails on case15's declaration once castxml reports the kind -->

#### castxml and clang report different change-kind sets for the same case (2026-11-06)

**Status (2026-11-06): PARTIAL.** The catalog's frontend kind-parity gate (`tests/catalog_kind_parity.py`) found castxml and clang reporting different kinds for one case in both native lanes, beyond the differences already attributed to a named gap (template defaults, case85 instantiation kind, record naming, implicit specializations). Fixed at extractor generation 43: clang now records a builtin-typed variable's natural alignment (case11 `var_alignment_changed`, case39 `exported_object_alignment_reduced`) and counts a parameter's pointer depth through a typedef (case73 `param_pointer_level_changed`); the DWARF/header dedup folds `long int` to `long` (case40 `struct_field_type_changed`). Traced backend limits, each declared with a `TRACED` reason: implicit special members (castxml emits every implicit member, clang's AST only the ones Sema declared: case68, 69, 107, 174, 28, 30); castxml 0.7 drops a `= delete`d member (case38); clang's function type carries no desugared return type (case73 `return_pointer_level_changed`); clang's scope-relative field spellings miss the DWARF dedup (case126). castxml's XML carries no record data size (dsize), so `tail_padding_reuse_changed` cannot run on castxml snapshots (case43, verified against clang's record layout dump); clang reporting `std::size_t` as an STL type by value (case17) was a false positive, fixed. Still untraced, declared with an `UNEXPLAINED:` reason: clang-only `struct_field_type_changed` (case187), `typedef_base_changed`/`type_removed` (case55, 80), `standard_layout_lost` (case203), `exported_not_public` (case105), `public_surface_shrank` (case108), `header_only_function_params_changed` (case122), `tail_padding_reuse_changed` (case72), `declaration_moved` (case229), `func_return_changed` (case28); castxml-only `overload_set_rerouted` (case78, 86), `abi_equivalent_type_changed` (case28), case80's implicit-member and `struct_field_type_changed` set, and `public_reachability_changed` (case72 build-source). `false-clean` — the same headers can yield a finding on one frontend only. Owner: `extract/headers/`.
<!-- no-canary: the catalog-nightly kind-parity gate fails on each declaration once it goes stale -->

### False-positive risk

#### A typedef retargeted inside a template argument still reads as a removal plus an addition under headers (2026-10-25)

**Status (2026-10-25): OPEN.** #1812 pairs a header-declared parameter retype into `func_params_changed` when the typedef it names changed target, but `compare/parameter_replacement.py`'s `typedef_target_changed` reads only a spelling that reduces to one name.
`false-positive` — `void f(std::span<V>)` or `void g(const vec<V>&)` with `V` retargeted is reported as `func_removed` + `func_added`, unless the two resolved parameter identities differ. Closing it means resolving each typedef named inside the written spelling, not only the whole spelling.
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_typedef_retarget_inside_template_argument_reads_removed_plus_added -->

#### On PE/COFF a header-only dependency's exported template instances stay in contract (2026-10-26)

**Status (2026-10-26): OPEN.** A PE export directory records no coalesced (COMDAT) definition, so `workflows/dump/dependency_internals.py` reads every PE export as the library's own; ELF `WEAK` and Mach-O weak definitions are read (ADR-089's PE limit).
`false-positive` — On Windows (mingw auto-export, or an MSVC export list), a `-mavx2` against `-mavx512f` rebuild gates on the dependency's hidden friends and template instances; `tests/field_shapes/header_only_dependency_internals` is excluded on `win32` for this reason. [Full write-up](known-gaps/pe-dependency-instances-stay-in-contract.md)
<!-- no-canary: PE/COFF only: needs an MSVC- or MinGW-built DLL with COMDAT template instances -->

#### A toolchain-only rebuild still fails a stated contract's coverage (2026-10-23)

**Status (2026-10-24 at `9513369`): PARTIAL.** #1751 fixed import edges and leaked-dependency symbols; #1770 fixed the binary/toolchain kinds a stated `public` left `UNKNOWN_UNRESOLVED`. Still open: libstdc++ `_ZGTt` transactional-memory clones are not seen as runtime-owned, and runtime exports make the `exports` domain `unmatched_exports` (even for a byte-identical pair). Pinned by shrink-only lists in the toolchain-drift tests.  
`false-positive` — Under a stated `--contract public|exports`, a rebuild from unchanged source with a static libstdc++ or clang `-O0` `std::` instantiations exits 1 on unsuppressible coverage failures. [Full write-up](known-gaps/toolchain-runtime-rebuild-fails-stated-contract-coverage.md)
<!-- no-canary: needs two toolchain versions; already pinned by the shrink-only lists in the toolchain-drift tests -->

#### A binary-only dump loses exports with no debug subprogram, so a still-exported symbol reads as removed (2026-10-25)

**Status (2026-10-25): OPEN.** When debug info yields any function, the binary-only dump declares only debug-confirmed functions. Exports with no subprogram drop out, so a version dumped through the other path disagrees.
`false-positive` — `abicheck compare libv1.so libv2.so` on catalog case38 reports `Processor::validate` as "dynamic export removed" although `libv2.so` still exports it. [Full write-up](known-gaps/binary-only-dump-drops-exports-without-a-debug-subprogram.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_binary_only_dump_drops_an_export_compiled_without_debug_info -->

#### Below `source`, `TemplateInstanceAbsence` loses the clang-graph instantiation origins (H2a residual, 2026-10-23)

**Status (2026-10-23): OPEN, unobserved.** `compare/detector_inputs.py` declares `surface_graph` as a *tolerated* input of the `functions`/`variables`/`types` detectors: its L2 declaration-node role survives below `source` as `header_declaration_ids`, but `_graph_origins` (the `template_instantiation` nodes' `instantiated_from` + `DECL_INSTANTIATES_TEMPLATE` edges) does not. At `--depth headers`/`build` the rule then proves a vanished specialization's origin from the castxml class-head reading alone, so a specialization only the clang graph could place may read as `type_removed`. `tests/test_depth_metamorphic_examples.py` found no such case in `catalog/cases`. Fix, if one appears: keep the proven `(spelling, origin, pattern)` triples as a second runtime-only L2 fact alongside `header_declaration_ids`, and move the read from `tolerates` to the substitute.
<!-- no-canary: unobserved: tests/test_depth_metamorphic_examples.py found no catalog case that triggers it -->

#### `--lang c++` explicitness not threaded to remaining fan-out callers (l0_export_delta/appcompat); dump/compare fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed for dump/compare CLI and the DumpRequest/CompareRequest API. `lang_explicit` is threaded through frontends/cli/commands/dump.py, compare.py, workflows/dump/{native,pe,macho}.py and workflows/artifact/*, and release_member_request.py now carries it too. The scan follow-up is moot (scan was deleted). appcompat and l0_export_delta are unverified. l0_export_delta.py takes a plain `lang: str` with no lang_explicit, so that follow-up looks still open.  
`false-positive` — An explicit `--lang c++` reaches dump/compare, but `l0_export_delta` (plain `lang: str`) and appcompat still guess the language themselves. [Full write-up](known-gaps/lang-c-explicitness-not-threaded-to-remaining-fan-out-callers.md)
<!-- no-canary: internal plumbing of l0_export_delta/appcompat; no reduced CLI input shows a different answer -->

#### A compatible-but-ambiguous opaque redeclaration set (`class H; struct H;`) that later gains a same-key COMPLETE definition still produces a false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex review, PR #719, fourth follow-up round on this same area)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for kind_ambiguous in abicheck/ finds nothing, so RecordType has no provenance for an ambiguous forward-declaration kind.  
`false-positive` — `class H; struct H;` followed by a complete definition produces a false `SOURCE_LEVEL_KIND_CHANGED`, because `RecordType` keeps no provenance for an ambiguous forward-declaration kind. [Full write-up](known-gaps/a-compatible-but-ambiguous-opaque-redeclaration-set-class-h.md)
<!-- no-canary: needs a three-version header history with an ambiguous class/struct redeclaration; not yet reduced -->

#### A pre-fix clang-backend baseline compares against a post-fix candidate as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards `Function.is_inline`

**Status (re-verified 2026-10-12): PARTIAL FIXED — see [ADR-081](adr/081-inline-fact-reliability-and-producer-generation.md).** Stale header-backend `is_inline` values now decline the transition, recorded as `inline_facts_reliable`. A baseline re-saved by a pre-change build still reads as reliable until re-dumped.  
`false-positive` — A baseline from the pre-fix clang backend compared against a post-fix candidate reports a wave of `FUNC_BECAME_INLINE`; no reliability flag gates `Function.is_inline`. [Full write-up](known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md)
<!-- no-canary: residual needs a baseline re-saved by a pre-ADR-081 build, which the current dumper cannot produce -->

#### type_base_changed declines on incomplete bases facts but still fires on a PRESENT-but-empty capture gap

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The vtable half is fixed. compare/base_class_diff.py:36-71 diff_bases now gates bases_fact/virtual_bases_fact through compare_facts and declines when a side is NOT_COLLECTED/FAILED/PARTIAL. A PRESENT-but-empty capture gap can still fabricate type_base_changed, as the entry describes.  
`false-positive` — `type_base_changed` now declines on NOT_COLLECTED/FAILED/PARTIAL bases facts, but a PRESENT-but-empty capture gap still fabricates the finding. [Full write-up](known-gaps/type-base-changed-declines-on-incomplete-bases-facts-but-still.md)
<!-- no-canary: needs a PRESENT-but-empty bases capture that no current extractor produces; only a hand-built snapshot shows it -->

#### Type reachability (direct vs. transitive stdlib references) — computed and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef paths remain unwired

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** type_reachability.py is wired into the RecordType detectors (diff_types_surface.py and others), but the diff_types.py:1113-1118 enum paths still call _is_non_abi_surface_type(e.name) directly, as the entry says.  
`false-positive` — Direct-vs-transitive stdlib reachability is wired into the `RecordType` detectors only; enum and typedef paths still use the bare-name non-ABI-surface check. [Full write-up](known-gaps/type-reachability-direct-vs-transitive-stdlib-references.md)
<!-- no-canary: internal code path (enum detectors call the name heuristic directly); no reduced input shows a different answer -->

#### Closure-parameterized non-ctor functions still churn (func_params_changed) on lambda line shifts; '?' ctor param unexplained

**Status (re-verified 2026-10-12 at `b694ccc2f`): PARTIAL.** Type-level marker fix, ctor/dtor binary-evidence fix and L5 declaration_renamed fix (model.graph_identity.closure_location_free_identity) are recorded as landed. Non-ctor func_params/return churn on an inserted unrelated closure is fixed for exported symbols (`compare/closure_respelling.py`). Still open: the same churn on unexported/inline declarations (`make_guard`, template members, synthetic ctor keys; fails closed, decision proposed in ADR-083 D1), `exported_not_public` noise on identical builds (ADR-083 D2), the mangled-only surface filter gap, and residual (2), the literal '?' ctor parameter (not reproduced on any dump in this work).  
`false-positive` — Non-ctor functions parameterized over a lambda churn when an unrelated closure is inserted earlier in the header; fixed for exported symbols. Still open: inline/unexported declarations and identical-build `exported_not_public` noise (both decisions proposed in ADR-083), the literal `?` ctor parameter (not reproduced), and the mangled-only surface filter gap. [Full write-up](known-gaps/closure-parameterized-non-ctor-functions-still-churn-func.md)
<!-- no-canary: remaining items await ADR-083 rulings; the '?' parameter was not reproduced -->

#### The L5 source graph's own node identities are never renumbered alongside the flat snapshot's closure markers (Codex review on PR #868, fresh evidence)

**Status (2026-10-31): PARTIAL — fixed for every snapshot written from now on; residual for older baselines.** `storage/closure_graph_renumber.py` renumbers the graph's node ids, labels, edge endpoints and attributes with the flat fields' ordinals, on the dump path and on load (a stored graph on its first decode, so ADR-062 D18 laziness is kept). A graph-only closure is numbered after its group's flat ordinals, so the graph never moves a flat ordinal. Residual: a baseline written before this fix whose flat fields are already in `#N` form but whose graph still spells `:line:col` cannot be reconciled (the flat coordinates are gone), so its graph closures are numbered as graph-only.
`false-positive` — a pre-fix baseline's graph closures can still be numbered differently from its flat ones. [Full write-up](known-gaps/the-l5-source-graphs-own-node-identities-are-never-renumbered.md)
<!-- no-canary: residual needs a pre-fix baseline, which the current dumper cannot produce -->

#### Declared-header order is still load-bearing because the driver TU is sequential

**Status (2026-10-12): PARTIAL.** A reorder of a header set is now comparable when both dumps prove the canonical order (ADR-082). Still refused: an unproven declared order, and a macro/pragma leak that changes no declaration (not visible to the witness). The scope-confirmed header growth waiver landed (comparability.py:845,1298 reference _header_sequence_is_scope_confirmed_growth). Still open: the sequential aggregate driver TU itself, so reordering an existing header still refuses. Canonical sorting of the driver TU is measured unsound; the remaining options are in ADR-082.  
`false-positive` — The scope-confirmed header-growth waiver landed, but the aggregate driver TU is still sequential, so reordering an existing header refuses the comparison. Sorting the driver TU is measured unsound (it breaks a valid declared order); removing the refusal alone reports a false BREAKING. [Full write-up](known-gaps/declared-header-order-is-still-load-bearing-because-the-driver.md)
<!-- no-canary: refusal is the deliberate safe answer pending ADR-082's remaining options; sorting was measured unsound (see Do not re-attempt) -->

#### Export-loss ownership: inline-copy proxy and template-specialization owner remain open (see export-loss-ownership-root-cause.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** This is a pointer stub to export-loss-ownership-root-cause.md. It still lists two open items: is_inline is used as a proxy, and there is no Itanium type decoder for specialization owners. Not re-verified in code beyond that.  
`false-positive` — Export-loss ownership still uses `is_inline` as a proxy for an inline copy and has no Itanium decoder for template-specialization owners; see `export-loss-ownership-root-cause.md`. [Full write-up](known-gaps/export-loss-ownership-inline-copy-proxy-and-template.md)
<!-- no-canary: pointer stub; each residual is tracked in its own write-up -->

#### Class-template specialization a new header stops enumerating keeps `type_removed` when its origin is unproven (narrowed)

**Status (2026-10-21): NARROWED.** A class-template specialization the new header stops instantiating is no longer reported as removed while the pattern it was instantiated from is still declared. That pattern is either the primary template or a partial specialization. Its members are judged by the new export table (`compare/template_instance_absence.py`). The origin comes from castxml's declaring line or from the clang header graph's AST structure (`buildsource/template_class_origin.py`), which also covers a macro-produced class-head and a partial specialization. Still open, each as a residual false positive:
<!-- no-canary: residual false positives listed in the write-up need specific macro/partial-specialization fixtures not yet reduced -->
- an old snapshot with neither origin record;
- a dump made without clang;
- a dropped explicit instantiation (`template struct Box<double>;`).

`false-positive` — Such a specialization still reports `type_removed` and its members' removals when no producer proved its origin: an old snapshot from before this change and schema v61, a dump without clang (macro-produced class-head, partial specialization), or an explicit instantiation the new header drops. [Full write-up](known-gaps/class-template-specialization-members-a-new-header-stops-enumerating.md)

#### Explicit function and variable instances: removal is castxml-blind and class-scope-ambiguous (2026-10-25)

**Status: NARROWED** (2026-10-25). An explicitly dropped function or static member is a removal on the clang header backend; castxml and two-instances-in-one-TU cases still take the export-loss or unsupported path. [Full write-up](known-gaps/explicit-function-and-variable-instances-removal-is-castxml-blind.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_castxml_dropped_explicit_function_instance_is_not_a_removal -->

#### MinGW toolchain headers are not classified as dependencies (2026-09-23)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/declaration_provenance.py:270 _is_toolchain_compiler_include_dir still recognises only lib/gcc/<triple>/<ver>/include, lib/clang, and castxml layouts. provenance.py has no handling for MinGW's <prefix>/<triple>/include.  
`false-positive` — Toolchain include detection knows GCC/clang/castxml layouts but not MinGW's `<prefix>/<triple>/include`, so MinGW headers are not classified as dependencies. [Full write-up](known-gaps/mingw-toolchain-headers-are-not-classified-as-dependencies-2026.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_mingw_triple_include_tree_is_not_a_toolchain_dir -->

#### The clang JSON header backend records no resolved type identities, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public` (2026-10-01)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for type_identities under abicheck/extract/headers/clang/ finds nothing, so the clang backend still records no identities.  
`false-positive` — The clang JSON backend records no `type_identities`, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public`. [Full write-up](known-gaps/the-clang-json-header-backend-records-no-resolved-type.md)
<!-- no-canary: needs two same-leaf records in different scopes reaching one contract decision; not yet reduced to a CLI fixture -->

#### A library's own `_Float128` API has no correct mangled name from castxml

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: castxml mangling was not run. The entry records the defect as an unresolved castxml limitation: _Float128 functions read as not exported.  
`false-positive` — Unverified: castxml gives a library's own `_Float128` functions no correct mangled name, so they read as not exported. [Full write-up](known-gaps/a-librarys-own-float128-api-has-no-correct-mangled-name-from.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_castxml_float128_function_is_unmangled_and_read_as_not_exported -->

#### Renaming a Rust crate changes every crate-qualified type name, so an unchanged C surface reads `API_BREAK` on the DWARF path (2026-10-15)

**Status (2026-10-15): OPEN.** Header mode is clean; DWARF mode reports each type as removed and re-added under the new crate name.
`false-positive` — A crate rename with the same C ABI gives `API_BREAK` on DWARF-only compares, because DWARF type names are crate-qualified. [Full write-up](known-gaps/crate-rename-changes-rust-type-identity.md)
<!-- no-canary: needs a Rust toolchain (cargo) to build the renamed crates -->

#### Header-less runs flag `exported_object_alignment_reduced` on an unrelated exported static after an additive change (2026-10-15)

**Status (2026-10-15): OPEN.** The alignment is derived from link-time address, so a neighbour's addition can move it; headers suppress the finding.
`false-positive` — An added exported function gives `COMPATIBLE_WITH_RISK` through an alignment finding on a static it does not touch, when no headers are passed. [Full write-up](known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md)
<!-- canary: tests/test_known_gap_canaries_extraction.py::test_headerless_additive_change_flags_alignment_on_an_untouched_static -->

### Performance

#### Snapshot write peak

`perf` — Writing a snapshot builds the largest section's encoded tree whole (A2.2's record-level remainder).

- [Snapshot write still builds the largest section's tree whole](known-gaps/snapshot-write-still-builds-the-whole-document-tree.md)  
  **Status (2026-10-31): NARROWED.** Sections are encoded and written one at a time (550 -> 286 MiB on a two-section synthetic); a binary-only dump is one section, so its peak barely moves (39.8 -> 37.1 MiB).
  <!-- no-canary: peak-memory residual; measured by scripts/bench_release_memory.py, not assertable as a unit test -->

#### Bundle and multi-library memory

`perf` — Two retention costs on the multi-library path: `read_bundle_facts_package` holds raw JSON and decoded snapshots together, and every member snapshot stores its own copy of the shared header evidence. (`--bundle-facts-out` pinning OLD snapshots per member is closed, see the [archive](archive/known-gaps-closed.md).)

- [`bundle_facts_store.read_bundle_facts_package` holds a bundle's raw JSON and its decoded `AbiSnapshot`s concurrently at peak](known-gaps/bundle-facts-storeread-bundle-facts-package-holds-a-bundles-raw.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/import_bundle_facts.py:597 export_bundle_facts still returns one whole dict[str, Any] document (no streaming form). It is bounded by DEFAULT_MAX_BUNDLE_DECODED_BYTES, as the entry notes.
  <!-- no-canary: peak-memory residual, bounded by DEFAULT_MAX_BUNDLE_DECODED_BYTES; measured, not asserted -->
- [The release public surface is acquired once, but each member snapshot still stores its own copy of the header evidence](known-gaps/the-release-public-surface-is-acquired-once-but-each-member.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** The per-member header evidence is still stored once per member. abicheck/storage/import_bundle_facts.py:107 still has _BUNDLE_FACTS_SCHEMA_VERSION = 3, so it still refuses schema 4.
  <!-- no-canary: storage-size residual (per-member header copies); measured, not asserted -->

#### Interpreter startup dominates a small-input CLI run

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Measurement only. The entry says import cost was never investigated, and no lazy-import work is recorded; the Click registration-by-import pattern is unchanged. unverified: wall time was not re-measured.  
`perf` — On a small input the run is dominated by interpreter startup and import cost, which was never investigated. [Full write-up](known-gaps/interpreter-startup-dominates-a-small-input-cli-run.md)
<!-- no-canary: wall-time measurement only; not assertable without a timing gate -->

#### The `clang -M` include-graph pass

`perf` — The include pass is memoized per process only: it is never persisted across runs, never batched, and a warm AST cache does not shorten it.

- [The `clang -M` include-graph pass is per-header and cached only in-process, never across runs](known-gaps/the-clang-m-include-graph-pass-is-per-header-and-cached-only-in.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** abicheck/buildsource/header_include_memo.py now holds a process-wide memo, keyed in header_graph.py:635-650, so release members and sides no longer re-run the same clang -M set. Still open: the memo lives only in-process (never persisted across runs), and nothing batches per-header invocations.
  <!-- no-canary: performance residual (no cross-run persistence); measured by scripts/check_l2_cli_perf.py -->
- [The `clang -M` include pass is unaffected by a warm AST cache — confirmed at `--repeat 3`](known-gaps/the-clang-m-include-pass-is-unaffected-by-a-warm-ast-cache.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** header_include_memo.py is a process-wide memo with no persistent/AST-cache tier, so a new run still recomputes the include graph whatever the warm AST cache holds.
  <!-- no-canary: performance residual; measured at --repeat 3 by scripts/check_l2_cli_perf.py -->

#### A directory `compare` feeds the union `-H` set to every member again: per-member header narrowing was reverted (2026-10-12)

**Status (2026-10-12): OPEN, reopened.** The narrowing from #1554 (`882482c47`) is reverted. It gave each release member the headers its exported names appeared in, decided per side. On a real oneDAL release (2026.0.0 to 2026.1.0, four members) three members stopped being compared: one got a header subset that did not parse (castxml exit 1), and two were refused as NOT_COMPARABLE because each side's assignment followed that side's exports and header set (scope_fingerprint mismatch). The release exit moved from 4 to 16. The union operand is back, and the release gate is again the one that was proven.
`perf` — Every member still parses the whole union `-H` set, so release header work grows with members x headers, and a type from one member's header is parsed by its siblings too. The verdict half is closed: a type that a member's own exports prove unreachable is recorded out of that member's contract, and it gates only through a sibling that reaches it (see the [archive](archive/known-gaps-closed.md)).
<!-- no-canary: the union operand is the proven safe answer after a revert; the residual is cost, measured on a real oneDAL release -->

Do not re-attempt token-claiming. A header's export tokens cannot tell whether it is self-contained: the umbrella header that supplies a prelude is an includer, not an include, so an include-closure does not recover it. Per-side assignment is also not symmetric, so the two sides parse different surfaces. A retry needs include-graph evidence, one assignment shared by both sides, and a castxml failure that is recorded as a FAILED fact, never a silent fall-back. [Full write-up](known-gaps/a-directory-compare-s-per-member-header-narrowing-is-elf-only.md)

#### Multi-library L2 compare scales quadratically with library count

`perf` — Memoization landed (scaling budget 1.7). Step 2 was reverted on 2026-10-12, so every member walks the union header set again; the scaling budget has not been re-measured since the revert.

- [Multi-library L2 compare scales quadratically with library count](known-gaps/multi-library-l2-compare-scales-quadratically-with-library-count.md)  
  **Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The memoization landed: the libraries budget is 1.7 in scripts/check_l2_scaling_perf.py:103 and header_scan_memo/include memo exist. Still open: every member walks the union header set and runs the per-member field parse (step 2 of entry 73).
  <!-- no-canary: scaling residual; gated as a growth exponent by scripts/check_l2_scaling_perf.py -->

#### Spelling alternation matcher scales linearly with vocabulary (nested same-offset bug fixed)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The under-report half is fixed and pinned by tests/test_spelling_nested_same_offset.py. The entry itself says the alternation-scaling half (linear in vocabulary size) remains open.  
`perf` — The nested same-offset under-report is fixed; the alternation matcher still scales linearly with vocabulary size. [Full write-up](known-gaps/spelling-alternation-matcher-scales-linearly-with-vocabulary.md)
<!-- no-canary: scaling residual; the fixed under-report half is pinned by tests/test_spelling_nested_same_offset.py -->

#### A member's peak memory is the clang JSON AST, not the header graph (2026-09-19)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The 'currently ungated' clause is closed: scripts/check_header_graph_perf.py:267 defines MEMORY_METRICS attach_peak_rss_mib/attach_end_rss_mib. The peak from materialising the clang JSON AST is still open, and the empty-list reduction is a recorded negative result.  
`perf` — A member's attach peak is the clang JSON document plus its parsed tree, not the header graph; the memory metrics are now gated, the peak itself is not reduced. [Full write-up](known-gaps/a-members-peak-memory-is-the-clang-json-ast-not-the-header.md)
<!-- no-canary: peak-memory residual; gated by scripts/check_header_graph_perf.py MEMORY_METRICS -->

#### Python-level AST walks, the side boundary and the snapshot cache dominate large runs (2026-10-11)

**Status (2026-10-11): OPEN.** Location-pass, cache trace and cache write switch landed; threads-on-free-threaded, a faster `json_compact` and pickle protocol 5 were measured on oneDAL and rejected; walk fusion is smaller than profiled (cProfile ~7x); closure renumbering after scoping and the cold GIL member-parallelism A/B remain.  
`perf` — The clang JSON tree is walked at least eight times in Python, snapshots are pickled across the side boundary, and populating the snapshot cache costs ~30 % wall on a 28-member release. [Full write-up](known-gaps/python-level-ast-walks-side-boundary-and-cache-costs.md)
<!-- no-canary: performance residual measured on oneDAL; no assertable unit -->

### Hygiene

#### The catalog evidence-monotonicity gate compares Debug binaries against Release ones (2026-11-06)

**Status (2026-11-06): OPEN.** `tests/catalog_kind_parity.py` gate A pairs the `debug-headers` lane (Debug build) with `build-source` (Release build with debug info), so a kind that only the Debug binary carries (an `-O0` out-of-line instance, a different libc/libstdc++ import set, a NEEDED entry) reads as "lost when build/source evidence was added" although the two lanes compared different binaries. case114/116 carry `lane_kind_exceptions` saying exactly that, and the 2026-11-05 recheck confirmed with `readelf -d` that case114's Debug v1/v2 differ in `NEEDED libc.so.6` while Release v1/v2 do not. Closing it means pairing `build-source` with `release-headers` (same Release binaries, no build/source), which `validate_examples.py` already supports but the catalog nightly does not run, and judging Debug vs Release as its own axis; the Debug/Release exceptions would then go stale and be deleted. `hygiene` — the gate's exceptions document a build-mode difference, not an evidence loss, and a real loss masked by one would be missed.
<!-- no-canary: needs the catalog nightly's lane artifacts; the gate fails on each exception once it goes stale -->

#### A live release capture is not byte-reproducible run to run: member dict key order varies (2026-11-01)

**Status (2026-11-01): OPEN.** Found while measuring the `--bundle-facts-out` spool. Two `compare --bundle-facts-out` runs of one unchanged revision (`8eb4d128a`) over one compiled six-member fixture (`scripts/bench_release_memory.py --members 6 --apis 1800 --records 40`) wrote JSON documents that are equal as JSON values but not as bytes: 72 objects listed their keys in a different order, six per member, among them `elf.versions_required`, `dwarf.structs`, `dwarf.base_types`, `dwarf_advanced.calling_conventions`, `ast_toolchain` and `contract.profile_fields`. The cause is in extraction, upstream of every writer, and not established: some of those maps are built in an order that varies between runs. The writer is deterministic over fixed snapshots (`tests/test_bundle_facts_out_spool.py`, `tests/test_bundle_facts_streaming_write.py`). This settles the question the closed `--bundle-facts-out` retention entry left unverified (it suspected `surface_graph` ordering): the nondeterminism predates that work and is not confined to `surface_graph`. The two archive-form documents from the same runs happened to be byte-identical, so one pair of equal archives proves nothing either way.
`hygiene` — A stored baseline's `stored_sha256` can differ between two captures of the same release, so a digest comparison reads a no-op re-capture as a change.
<!-- no-canary: nondeterministic key order that appears only across processes at six-member scale; a canary would be flaky by construction -->

#### Release report: live and stored operands render different documents

**Status (2026-10-25): PARTIAL — closed for the document shape; the stored JUnit and refused formats below remain.** A stored `BundleFacts` comparison renders the live release document: its JSON is the live release document (release schema 2.5), and JSON, Markdown, JUnit, `oneline` and HTML all come from one live-shaped evaluation of the stored facts (`tests/unit/report/test_release_document.py`, `tests/test_cli_compare_stored_release_parity.py`). Fields a stored comparison cannot compute are explicit `unavailable` markers with a reason, and its receipt is marked `reconstructed_from_stored_facts`. Remaining limits, named rather than hidden: the stored JUnit omits the passing testcase for each unchanged symbol (no old-side inventory); `output.view` `show=`, `sarif` and `review` stay refused for a stored OLD_INPUT.  
`hygiene` — Closed: the stored document now carries the live release shape (schema 2.5, breaking for the stored document). [Full write-up](known-gaps/release-report-live-and-stored-operands-render-different-documents.md) [Full write-up](known-gaps/release-report-live-and-stored-operands-render-different-documents.md)
<!-- no-canary: remaining limits are named refusals and an omitted passing testcase, deliberate until an old-side inventory exists -->

#### A one-sided release member with a damaged native body reads as unmatched (2026-11-01)

**Status (recorded 2026-11-01): OPEN.** A one-sided member that no reader recognizes by content is now `failed` (archive: "An unreadable NEW-only release member..."). The probe reads the format only, so a file with valid ELF, PE or Mach-O magic and a truncated or corrupt body is never extracted when it has no counterpart, and still reads as `not_supplied`. Paired, it fails at extraction as before.
`hygiene` — A one-sided member whose native image is damaged is reported as one-sided rather than unreadable; closing it needs a bounded structural parse of unmatched native members. [Full write-up](known-gaps/a-one-sided-member-with-a-damaged-native-body-reads-as-unmatched.md)
<!-- no-canary: needs a corrupt-body ELF fixture inside a two-sided directory run; not yet reduced -->

#### Public-surface graph: surface_graph vs L5 header_graph node-id namespaces not unified; type_reachability stdlib query unmigrated

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The traversal half is superseded: the closure walk moved to policy/public_surface_closure.py. Still true: abicheck/type_reachability.py is unmigrated, and the compare/surface_graph.py and header_graph node-id namespaces are not unified.  
`hygiene` — The closure walk moved to `policy/public_surface_closure.py`, but `type_reachability.py` is unmigrated and the surface-graph and header-graph node-id namespaces are still separate. [Full write-up](known-gaps/public-surface-graph-surface-graph-vs-l5-header-graph-node-id.md)
<!-- no-canary: internal node-id namespace split; no user-visible wrong answer to pin -->

#### The weekly `Mutation testing` scheduled lane (`.github/workflows/mutation.yml`, job `mutmut (detector core)`) can outgrow its own job timeout before producing a receipt — investigated, partially mitigated, not fully fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** .github/workflows/mutation.yml:327 timeout-minutes: 355 (mitigation landed). The entry itself says there is no measurement that a full run completes, and only_mutate scoping/splitting is not done.  
`hygiene` — The weekly `mutmut (detector core)` job's timeout was raised to 355 minutes, but no run is measured to finish inside it and `only_mutate` scoping/splitting is not done. [Full write-up](known-gaps/the-weekly-mutation-testing-scheduled-lane.md)
<!-- no-canary: CI-lane runtime residual; only a full scheduled run can measure it -->

#### `compare`'s migrated cross-source checks drop `--since`'s changed-path confidence boost

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** workflows/cross_source_hygiene.py:360 is still `def compute_cross_source_evolution(old, new)` with no changed_paths parameter, and checker.py has no changed_paths.  
`hygiene` — `compute_cross_source_evolution` takes no changed paths, so `compare --since` loses the changed-path confidence boost the old scan path applied. [Full write-up](known-gaps/compares-migrated-cross-source-checks-drop-sinces-changed-path.md)
<!-- no-canary: a confidence boost that is not applied; needs a git-history fixture with changed paths, not yet reduced -->

#### `compare --depth binary` skipping matrix-wide `--sources`/`--build-info` is tested only on snapshot operands, which cannot prove it

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** tests/test_scenarios.py:653 test_sc_scan_binary_depth_matrix_args was revived, but per docs/contribute/usecase-registry.yaml:1040-1055 it runs on stored JSON snapshots (tests/scenarios/ci_gating.yaml:120), so it cannot tell 'binary depth skipped collection' apart from 'snapshot inputs always skip it'.  
`hygiene` — The only test runs on stored JSON snapshots, which always skip collection, so it cannot prove that `--depth binary` skips matrix-wide `--sources`/`--build-info`. [Full write-up](known-gaps/compare-depth-binary-skipping-matrix-wide-sources-build-info-is.md)
<!-- no-canary: test-coverage gap (the scenario runs on stored snapshots); the gap is the missing test itself -->

#### ADR-061 gap F: nested `buildsource`/`impact` modules still carry no disposition

**Status (re-verified 2026-10-25): PARTIAL, narrowed.** The compat/* modules are gone. `impact/use_case_impact.py` is deleted (reachability moved to `policy/use_case_reachability.py`), and `buildsource/fact_set.py` and every remaining `impact/*` module (`assessment`, `consumer_graph`, `correlation`, `engine`) are now classified in `architecture/modules.yaml`. Still with no `architecture/*.yaml` entry: `buildsource/build_evidence.py`, `build_output.py`, `evidence_policy.py`, `merge_support.py` and `source_graph.py`.  
`hygiene` — Five nested `buildsource/` modules (`build_evidence.py`, `build_output.py`, `evidence_policy.py`, `merge_support.py`, `source_graph.py`) have no ADR-061 disposition in `architecture/*.yaml`. [Full write-up](known-gaps/adr-061-gap-f-nested-buildsourceimpact-modules-still-carry-no.md)
<!-- no-canary: architecture-ledger debt; check_architecture.py owns the module dispositions -->

#### `internal_leak` is now four `compare/` modules, but its test module is still grandfathered above the test cap (ADR-061 migration, 2026-10-22)

**Status (2026-10-22): PARTIAL.** The physical move and split landed: the detectors are in `compare/internal_leak.py`, the layout walk in `compare/internal_leak_paths.py`, the call-graph walk in `compare/internal_leak_graph.py`, and the subject classification in `compare/internal_leak_names.py`. Each is under the 800-line cap, and nothing re-exports from the old root path. Still open: `tests/test_internal_leak.py` (1933 lines) remains above the 1200-line test cap on its `no_growth` record.  
`hygiene` — the leak rule is owned by `compare/` as four modules, but its main test module is still a `no_growth` grandfather and has not yet been split by behavior axis. [Full write-up](known-gaps/internal-leak-is-now-four-compare-modules-but-its-test.md)
<!-- no-canary: file-size debt; the no_growth record in architecture/debt.yaml already fails on growth -->

#### Source-read licence: enforced at the one consumer, not repo-wide (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Both deliberate residuals are still open: scripts/check_ai_readiness.py has no source-read licence gate (grep 'licen' finds only license-header checks), and there are no per-input content digests. The third residual is recorded as closed.  
`hygiene` — The source-read licence is enforced at its one consumer; there is no repo-wide gate and no per-input content digest. [Full write-up](known-gaps/source-read-licence-enforced-at-the-one-consumer-not-repo-wide.md)
<!-- no-canary: missing repo-wide gate; no runtime behaviour differs -->

#### Five SVS-scan report findings not addressed by the change-vs-inventory/depth/exclusion fixes (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: four of the five SVS report items were not checked in code (header accounting does not lower status, the not-applicable detector vocabulary, partial telemetry, the identical-binary channel). The 294 KB Markdown default is fixed (2026-10-23): see that item below.  
`hygiene` — Unverified: four SVS report items (header accounting, not-applicable detector vocabulary, partial telemetry, identical-binary channel) are not marked fixed; the 294 KB Markdown default is fixed. [Full write-up](known-gaps/five-svs-scan-report-findings-not-addressed-by-the-change-vs.md)
<!-- no-canary: four items unverified in code; each needs the SVS operands to reproduce -->

#### A snapshot is not byte-reproducible across processes

**Status (2026-10-31): NARROWED.** The graph section's node/edge rows are now written in canonical id order (`storage/graph_table_codec.encode_graph_table`), so the observed `surface_graph.nodes` drift cannot reach the bytes whatever its producer; `tests/test_snapshot_byte_reproducibility.py` checks permutation invariance and a cross-process dump under three `PYTHONHASHSEED` values. Residual: no oneDAL-scale corpus is in CI, so another section's ordering is proven only on the generated corpus.  
`hygiene` — Graph rows are canonicalized; byte reproducibility of the other sections is proven only on a generated corpus, not on oneDAL scale. [Full write-up](known-gaps/a-snapshot-is-not-byte-reproducible-across-processes.md)
<!-- no-canary: residual is oneDAL-scale ordering that the generated corpus in tests/test_snapshot_byte_reproducibility.py cannot reach -->

#### Destructive test-helper resets are guarded one helper at a time (2026-09-30)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** No gate guards against rmtree of a caller-supplied path: 40 rmtree call sites remain under tests/ and scripts/, and the entry says the /tmp canary detector is not wired into CI. Not further verified.  
`hygiene` — About 40 `rmtree` sites under `tests/` and `scripts/` are guarded one helper at a time; the `/tmp` canary detector is not wired into CI. [Full write-up](known-gaps/destructive-test-helper-resets-are-guarded-one-helper-at-a-time.md)
<!-- no-canary: test-tooling hygiene; the gap is a missing guard, not a behaviour -->

#### `compare --no-baseline` has no typed Python request, scalar or directory (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for 'NoBaseline.*Request' in abicheck/ finds nothing, so there is still no typed no-baseline request.  
`hygiene` — There is no typed Python request for `compare --no-baseline`, scalar or directory; only the CLI drives it. [Full write-up](known-gaps/compare-no-baseline-has-no-typed-python-request-scalar-or.md)
<!-- no-canary: missing API surface; a canary would pin the absence of a class -->

#### The clang backend's guard retry still maps an aggregate line to a header (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/headers/clang/error_header_retry.py:69-79 still maps the aggregate line index to the header. The entry notes it is latent: not broken unless a preamble is added. The writers are now in extract/headers/clang/backend.py:304 and clang_layout_tool.py:203.  
`hygiene` — The clang backend's guard retry maps an aggregate-TU line index straight to a header; latent until a preamble is added to the aggregate. [Full write-up](known-gaps/the-clang-backends-guard-retry-still-maps-an-aggregate-line-to.md)
<!-- no-canary: latent: not broken unless a preamble is added to the aggregate TU -->

#### The agent-eval `base_commit` test checks object presence, not ancestry from the default branch (2026-10-12)

**Status (2026-10-12): OPEN.** `test_manifest_base_commit_exists_in_history` runs `git cat-file -e` only; a manifest pinned to a fetchable but unmerged commit passes.  
`hygiene` — The test named "exists in history" does not check that a task's `base_commit` is reachable from the default branch. [Full write-up](known-gaps/agent-eval-base-commit-test-checks-presence-not-ancestry.md)
<!-- no-canary: test-tooling gap in the agent-eval harness; needs an unmerged-commit git fixture -->


## Do not re-attempt

Approaches that were tried, measured or reviewed and did not help. Their value is stopping a re-attempt; read the write-up before trying anything similar.

| Approach | Measured result | Evidence caveat | Write-up |
|---|---|---|---|
| Demote a vanished weak/vague-linkage export (`Function.is_inline`, then COMDAT membership) | Both proved a fact about the library's build, not that consumers own a copy; `extern template` breaks the second. Reverted twice. | — | [Linkage-blind removal — attempted twice, reverted twice. The evidence keeps proving something adjacent to the invariant](known-gaps/linkage-blind-removal-attempted-twice-reverted-twice-the.md) |
| Treat a symbol the new binary still exports as not removed, with no declaration gate | Silences a plain function the header dropped while the binary still exports it (`ns::bar`), a real API removal that reports `func_removed` today. The gate on the primary template's declaration is what keeps it. | — | [Class-template specialization a new header stops enumerating (narrowed)](known-gaps/class-template-specialization-members-a-new-header-stops-enumerating.md) |
| Demote stdlib closure instantiations as "unnameable" | Source nameability is not ABI compatibility; the fix was also dead code for its ELF-only case. Reverted. | — | [A third instance of the same shape (code-review report item 3): demoting a stdlib closure instantiation as "unnameable" — attempted, reverted](known-gaps/a-third-instance-of-the-same-shape-code-review-report-item-3.md) |
| Per-finding evidence-provider downgrade for header-only layout findings | The suspected misattribution does not occur on either header backend; no fix applied. | Premise did not reproduce | [Evidence-provider model — investigated, found not to reproduce as described; no fix applied](known-gaps/evidence-provider-model-investigated-found-not-to-reproduce-as.md) |
| L2 performance bottlenecks found by the full-CLI harness | Frame for the measurement-only perf rows below (`scripts/check_l2_cli_perf.py`). | — | [L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)](known-gaps/l2-performance-bottlenecks-found-by-the-full-cli-harness.md) |
| Share one dependency-header context across a set of libraries | 20 extractions either way; the count is driven by top-level headers. | Original fixture was flawed (shared arm shared nothing); conclusion re-measured on a corrected fixture | [A set of libraries pays full cost per library, and a shared header context does not reduce it](known-gaps/a-set-of-libraries-pays-full-cost-per-library-and-a-shared.md) |
| Four digest/save reductions (`iterencode`, nested hashing, `InMemoryObjectStore.detach`, split `update()`) | Peak unchanged at 474/576 MiB; three reverted, one kept as waste removal. | An 18% "win" for attempt 3 was a measurement artifact; corrected in the entry | [Snapshot digest/save amplification is in the sectioning layer, and four attempts to reduce it bought nothing (2026-09-17)](known-gaps/snapshot-digestsave-amplification-is-in-the-sectioning-layer.md) |
| Drop the release fan-out's `futures` map as results complete | Saves only `Future` wrappers; `results_by_key` holds the same result objects. | — | [The release fan-out's future map is not a last owner, so dropping it saves nothing (2026-09-17)](known-gaps/the-release-fan-outs-future-map-is-not-a-last-owner-so-dropping.md) |
| Release the clang AST before the header-graph build | Peak unchanged (2215 MiB, 0.05%); steady state ~8-12 MiB worse. Kept for mid-attach residency only. | — | [Releasing the clang AST before the graph build does not reduce the peak (2026-09-19)](known-gaps/releasing-the-clang-ast-before-the-graph-build-does-not-reduce.md) |
| m1360 asks not taken (shared stdlib scan, union vocabulary, `loc` strip, derived primary-pass form, forked child) | Each needs a rewrite or an ADR, not a cache. | — | [m1360 performance round: the asks not taken, and why (2026-09-24)](known-gaps/m1360-performance-round-the-asks-not-taken-and-why-2026-09-24.md) |
| A `gcc -aux-info` names-only header frontend for `compare --no-baseline` | On MKL (27,111 functions, same names as castxml) aux-info takes 0.60 s, castxml 1.07 s and clang 2.0 s, against a 77 s cold run. That is at most about a 2% gain, and aux-info is C-only, with no variables and no mangling. | One library (MKL, C); the time is in the Python stages, not the parse | [A `gcc -aux-info` names-only header frontend does not pay for itself (2026-10-22)](known-gaps/a-gcc-aux-info-names-only-header-frontend-does-not-pay-for.md) |
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
| Declarations under an `#ifdef` the parse does not define (e.g. `SYCL_LANGUAGE_VERSION` `sycl::queue&` overloads) are not header-checked by default; the hidden exports are counted in the coverage block (sycl as a named cause), and `-D` plus a user stub header parses them. No stub is bundled. | [`docs/use/dump-compare-flags.md`](../use/dump-compare-flags.md#declarations-behind-ifdef-sycl_language_version) |
| A release binary without debug info disables the DWARF layout detectors by design; layout then comes only from headers. Not a bug. | [Evidence depth](../use/evidence-depth.md) |
| Local-exec TLS in an AArch64 shared object leaves no binary evidence; toolchain limit. | [Local-exec TLS in an AArch64 shared object leaves no binary evidence](known-gaps/local-exec-tls-in-an-aarch64-shared-object-leaves-no-binary.md) |

## Unreached oneDAL profiling hotspots (perf, 2026-10-23)

**Status: OPEN.** No use-case scenario executes any oneDAL-profiled hotspot.
The repository records oneDAL only as whole-run receipts (wall time, peak RSS,
verdict) in [`performance.md`](performance.md) and as the periodic
`scripts/l2_real_profiles.py` profile; neither names the hot functions, so no
function-level list is traceable from the repo. Function-level coverage
needs the oneDAL operands (about 120 build-minutes and 25 GB), which
`usecase_flows.yaml` cannot provide: its flows run against self-contained
catalog cases only. Reach stays unrecorded until a maintainer supplies the
profiled function list and runs `usecase_paths.py record --source flows` against
a built oneDAL tree. The perf-side mapping for the measured hot paths is
`USE_CASES` in `scripts/complexity_bench.py`.
<!-- no-canary: needs the oneDAL operands (about 120 build-minutes and 25 GB) -->

`perf` — oneDAL hot paths (`compare()` over a 125-header-root L2 surface, the
rows in the 2026-09 receipt series) are not exercised by any scenario, so a
regression there shows up only in the manual real-profile lane.
