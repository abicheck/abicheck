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

### Option vocabulary (ADR-091 follow-ups)

The option-vocabulary migration is complete (ADR-091, Accepted and
implemented). Its three follow-ups were closed on 2026-10-31 (see the
archive): the `--debug-info` and `deps.*` config homes were added, and
`--sources` stays a flag only, by design. One gap found while adding the
homes remains:

- `false-clean` — **Status (2026-10-31): OPEN.** A directory/package `compare` silently ignores `--follow-deps`, `--search-path`, `--ld-library-path` and a directory or detached-file `--debug-info`: the release fan-out takes none of them. `--set deps.*` is refused on that route. [Full write-up](known-gaps/release-compare-drops-dependency-walk-and-debug-roots.md)

### Report and detector coverage


#### The standing-inventory headline folds observed compatible findings into one clause (2026-10-28)

**Status (2026-10-28): OPEN.** A one-line summary with standing cross-source inventory states the observed COMPATIBLE verdict as a single `compatible` clause, so an informational or quality finding is counted there, not named. The `summary.change_inventory` block has no additions/quality/informational split and its JSON shape is fixed, so the split needs an additive field and its schema ruling. The parts still add up to the total. Owner: `report/change_inventory.py`, `report/render_text.py`.

#### Contract-unscored findings are not named by the JSON summary or the release line (2026-10-28)

**Status (2026-10-28): OPEN.** A finding the compatibility policy left `NOT_EVALUATED` is in `summary.total_changes` and in no bucket, and the JSON names it nowhere except as that surplus; its per-change status is emitted only under a stamped contract relevance. The one-line summary states it as `N not evaluated`. The release line's total is the sum of member buckets, so unscored release findings are absent from it. Owner: `report/release_oneline.py`, `report/json_sections.py`.

#### case98 release-headers is refused as a profile mismatch since 44473ffcb (2026-10-25)

**Status (2026-10-25): OPEN.** Since #1778 the release-headers compare of case98 returns no verdict (`profile_mismatch`) instead of the documented `NO_CHANGE`; the guard accepts a declared-gap no-verdict row, so the job is green but the change is unruled. [Full write-up](known-gaps/case98-release-headers-is-refused-as-profile-mismatch-since-44473ffcb.md)

#### The `#ifdef` guard census is runtime-only, so stored snapshots fall back to the `sycl::` name rule (2026-10-25)

**Status (2026-10-29): FIXED for persistence (schema v67).** The census of exports declared only under an excluded `#ifdef` (`extract/headers/preprocessor_branches.py`, reported per macro in `coverage_warnings` and `undeclared_export_census.by_guard`, report schema 6.7) is computed from the castxml run's own `-E -dD` output and is now persisted on `AbiSnapshot.live_preprocessor_hidden` as the document key of the same name. A compare of stored snapshots attributes a generic `#ifdef FOO` export to `FOO` exactly as a live compare does (`tests/test_live_stored_census_parity.py`, `tests/test_preprocessor_hidden_census_integration.py`). A pre-v67 snapshot carries no scan and uses the `sycl::` name rule only, and a clang-frontend parse and a multi-TU manifest still carry no scan. No `EXTRACTOR_GENERATION` bump: the scan's content is unchanged, only its persistence, which the `SCHEMA_VERSION` bump to 67 covers. Also open: the census reads only the ELF export table, and matches an export to a hidden declaration by function name only (no variables, no signature check).
`hygiene` — A stored-snapshot compare under-reports why exports are undeclared; it never fabricates a finding. Owner: `confidence.undeclared_export_censuses`.

#### Markdown/review lose `<…>` and backticks in symbol names; three formats omit the acknowledged count (2026-10-28)

**Status (2026-10-28): FIXED.** Prose, code spans and table cells are escaped by one helper (`md_text`), and the demangle pass escapes the names it inserts. JUnit, the terminal Audit line and the HTML disposition line state the acknowledged count. The renderer-parity matrix's strict xfail cells are now plain assertions. [Full write-up](known-gaps/renderer-parity-escaping-and-acknowledged-count.md)

#### The Markdown "Additions" section is one row per finding, so it grows linearly (2026-10-22)

**Status (2026-10-22): OPEN.** The review-group section is bounded; the per-finding Additions rows are not. [Full write-up](known-gaps/the-additions-markdown-section-is-one-row-per-finding.md)

#### The legacy gate counts a redundant finding the review groups omit (2026-10-23)

**Status (2026-10-26): OPEN — gate-semantics decision needed.** A finding folded out as redundant but kept for the verdict is `gating` under the legacy scheme and `non_gating` under a severity config. `result_counts.gating_retained` now partitions the retained findings and the summary names the folded one, so the counts no longer contradict each other; the scheme disagreement remains. [Full write-up](known-gaps/legacy-gate-counts-a-redundant-finding-the-review-groups-omit.md)

### Source-replay twin pairing (L4/L2 Phase 8)

**Status (2026-10-23): OPEN, narrowed.** Two observations are reported by both the header tier and the L4 replay: a header-only inline removal (`INLINE_FUNCTION_REMOVED`) and a C++ `constexpr` value change (`CONSTEXPR_VALUE_CHANGED` / `CONSTANT_CHANGED`). `compare.source_replay_twins.REPLAY_TWINS` keeps the header-tier finding and drops the replay copy, matched by name, one for one. For constants, the match is on the qualified name only: a replay copy whose value disagrees with the header copy is still dropped, and the header copy's value is what is reported. The "L2 tier not run, replay copy kept" case is covered only at unit level, because the replay alone does not produce the copy end to end (a no-header run matches no symbols). For inline removals the match is on the bare function name. Two limits remain there. (a) A header-tier symbol that does not demangle (no `demangled_symbol`, no demangler result) keeps its replay twin visible; this is conservative, not a silent loss. (b) Two overloads of one name pair by count, not by signature, because the replay `Change` carries no parameter list, so which of the two overloads is the twin is not recorded. Both replay copies are still counted in the ledger. Closing (b) needs the replay finding to carry its signature hash.

### Source-replay typedef pairing (L4/L2 Phase 8)

**Status (2026-10-23): OPEN, narrowed.** A removed or retargeted typedef is reported by the header tier (`TYPEDEF_REMOVED`, `TYPEDEF_BASE_CHANGED`) and by the L4 replay (`PUBLIC_TYPEDEF_REMOVED`, `PUBLIC_TYPEDEF_TARGET_CHANGED`). `compare.source_replay_twins.REPLAY_TWINS` (the typedef pairs) keeps the header-tier finding and drops the replay copy, matched by qualified name (the header entity id rendered through `render_display_name_or_leaf`), one for one, and recorded as `deduplicated`. Two limits remain. (a) The value spellings are not compared, so a pair whose two tiers disagree on the type text (`long int` vs `long`) still pairs; this is deliberate, since a value check would miss every real pair, but it means a replay finding is dropped even when the tiers name different types. (b) A header-tier finding with no entity id pairs by bare symbol, so in C++ a namespaced typedef without an entity id keeps its replay twin visible (conservative).

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

#### The clang projection walks every declaration, including system headers, so its output is not the public surface (2026-10-22)
**Status (2026-10-22): FIXED for the projection path.** `PublicSurfaceClangParser` (`abicheck/buildsource/source_extractors/clang_projection.py`) applies the public-surface provenance predicate to the declaration lists before any canonical parse, and `project_clang_surface()` projects only those. The 66953 out-of-scope declarations (`fclose`, `__uflow`, `asprintf`) no longer reach the projection. The clang parser's unfiltered `parse_*` methods are unchanged and still walk every declaration, so any new consumer must use the public-surface parser.

#### Clang-projected member and namespace names are bare, so `qualified_name` differs from the current L4 name (2026-10-22)
**Status (2026-10-22): OPEN.** A member function is projected as `execute` where L4 emits `Processor::execute`; a namespaced record as `task_arena` where L4 emits `mylib::task_arena`; an enum likewise. `parse_typedefs_qualified` exists, but no qualified form is provided for functions, records or enums. On the corpus, `qualified_name` differs on 1045 of 1531 paired functions (205 TUs), 130 of 290 records (75 TUs) and 5 of 5 enums. A qualified name must be built from the declaration scope before a projection can use it as an identity input.

#### A C++ declaration with no clang `mangledName` falls back to its bare name and loses its mangled identity in the projection (2026-10-22)
**Status (2026-10-22): OPEN; cause not traced.** For `distance(Point, Point)` in `case69_trivial_to_nontrivial` (`v1.cpp` and `v2.cpp`) clang's AST carries no usable `mangledName`, so `Function.mangled` falls back to the bare name `distance`. `base.entity_from_function` then blanks `mangled_name`, and `SourceEntity.identity()` keys the declaration on `qualified#signature_hash` where L4 keys it on `_Z8distance5PointS_`. This is the fabricated-identity case ADR-063 A1 names. Pairing-free counters show 502 projected function entities with a blank mangled name against 404 on the current side, so the blanking is wider than these two; the cause of the 404 and 502 split has not been traced.

#### Hash fields and the default-argument string are spelled differently by the two producers, so cross-producer equality is not a semantic check (2026-10-22)
**Status (2026-10-22): OPEN.** `signature_hash` differs on 1374 of 1531 paired functions, record `type_hash` on 290 of 290, and typedef `type_hash` on 22 of 22. The inputs differ (L4 hashes its own `_signature(node)` string, `base.py` hashes `return(params)`), so the hashes cannot show the underlying types are equal. The default-argument `value` differs on 18 pairs: L4 spells `p0=sha256:…` and the projection spells `max_threads=expr:…`. A pinned set cannot include these fields until both producers use one canonical spelling.

### False-clean risk

#### A readable but corrupted `.dynsym` entry can still read as a clean compare (2026-10-27)

**Status (2026-10-28): PARTIAL.** A shared object whose section header table is zeroed, absent or stripped (`sstrip`, `e_shoff`/`e_shnum` cleared) is no longer refused: the loader's view of the dynamic table (`PT_DYNAMIC`, `DT_SYMTAB`/`DT_STRTAB`/`DT_STRSZ`/`DT_SYMENT`, the `DT_GNU_HASH` or `DT_HASH` count, `DT_VERSYM`/`DT_VERDEF`/`DT_VERNEED`) yields the same exports, bindings, visibility and versions as the intact build (`abicheck/extract/elf_loader_view.py`, `tests/test_dynamic_table_loader_view.py`). An export table that is itself unreadable (no `PT_DYNAMIC`, a `.dynsym` filled with `0xff`, truncated, or exported entries with no resolvable name) is still refused at dump with exit 1 (`tests/test_export_table_unreadable_matrix.py`, OLD/NEW/both). Residual: corruption that leaves every entry structurally valid is read as the table it now says. Random byte flips inside `.dynsym` (`st_info`, `st_shndx`, `st_value`, or a `st_name` that still lands on a non-empty string) and garbage bytes in `.dynstr` that still yield non-empty names parse without a failure. On one side the result is a fabricated `BREAKING`; on the corrupted side of a pair it can be `COMPATIBLE` (exit 0, removals hidden) or, with both sides identically corrupted, `NO_CHANGE`. No reader can tell these from a legitimate table without a checksum.

`false-clean` — `compare` of a library whose `.dynsym` content is corrupted but structurally valid can exit 0 with `COMPATIBLE` or `NO_CHANGE`.

#### `--depth binary` on a header-built snapshot misses debug-only declaration findings (2026-10-25)

**Status (2026-10-25): PARTIAL — no longer a false clean; the findings themselves are still missing.** A header snapshot does not persist debug-info subprogram/variable records or record relations, so its `--depth binary` projection cannot report the signature, typedef, vtable or base findings a binary-only dump reports. Fixed 2026-10-25: such a projection (header-built side with debug info) now reads `analysis_assurance` `partial` and names both missing evidence families (`policy/depth_projection_gaps.py`, `policy/analysis_assurance/_depth_projection.py`; `tests/test_depth_projection_declaration_gap.py`). Residual: the findings are not reported; each is named in `tests/test_depth_projection_matches_shallow_dump.py`.
`false-clean` — `compare old.json new.json --depth binary` on two header snapshots can read `NO_CHANGE` (with `partial` assurance naming the missing evidence) where a binary-only dump of the same libraries reads `BREAKING` (a return or parameter type change). [Full write-up](known-gaps/depth-binary-projection-cannot-reproduce-debug-declaration-findings.md)

#### A removed class-metadata export of a templated or destructor-only class is not confirmed by its header (2026-10-22)

**Status (2026-10-25): PARTIAL, narrowed.** A `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT` export is confirmed by its owning class when the class's exact path is declared, or when a public, unambiguous, non-pattern record's own mangled member spells the owner's mangled scope exactly (`policy/class_metadata_identity.declared_class_owner_scopes`): a virtual slot (inline namespaces and specialization arguments kept verbatim, so `_ZN3api3BoxIiE3getEv` proves `_ZTVN3api3BoxIiEE`), or the record's implicit `operator=`, whose parameter is the record itself (this proves a dtor-only class in an inline namespace). A primary template, a different specialization and a user-declared `operator=` prove nothing. Residual: a specialization or inline-namespace dtor-only class with no mangled member in the snapshot (no non-destructor virtual and no implicit `operator=` recorded, e.g. a class with a deleted copy assignment, or a backend that does not emit implicit members) stays `UNKNOWN_UNRESOLVED`. `tests/test_class_metadata_specialization_owner.py` pins both.
`false-clean` — in the residual case, the removal of such a class's vtable or typeinfo reads `NOT_EVALUATED` under `--contract public` and does not gate the verdict.

#### A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml (2026-10-21)

**Status (2026-10-25): PARTIAL — the clang alias/variable default is now a named gap, not a finding; residual: the textual scan misses macro-generated or indirectly-included alias and variable templates, whose default change can still read `complete` NO_CHANGE.** castxml 0.7 emits no element for an uninstantiated template and names an instantiation as written (`Box<char>`, `f` with `_ZN1m1fIiEEvv`), so the default cannot be recovered from its XML; its run reads `NO_CHANGE` with `partial` assurance and `template-parameter defaults (castxml cannot record them)` named (ADR-085 D3), for class, function, alias and variable templates and for type, non-type and template-template defaults. Fixed 2026-10-24: a clang function template whose default cannot be spelled (`int N = sizeof(T)`) records the defaults as `UNSUPPORTED` and names the gap. Fixed 2026-10-25 (extractor generation 23): the clang backend still models neither alias nor variable templates, but a clang run now scans its public headers for one with a default template argument (`extract/headers/template_default_scan.alias_or_variable_template_default_scan`) and names `template-parameter defaults (the clang backend does not model alias or variable templates)`, so a default change on one reads `partial`, never `complete` `NO_CHANGE`. Residual: on clang such a change is not reported as `template_param_default_changed` (no model entity carries the default; an unchanged default also reads `partial`), and the scan is textual, so a macro-generated alias or variable template is not seen. `tests/test_template_default_assurance_gap.py` pins every family on both backends.

`false-clean` — A default template argument changed on a header-only template reads `NO_CHANGE` on castxml (clang reports `API_BREAK`), and the source-level consequence appears only as an L4 risk on castxml. [Full write-up](known-gaps/a-header-only-default-template-argument-change-reads-no-change-at-l2.md)

#### A refused clang AST still leaves the header type pass collected (2026-10-17)

**Status (2026-10-25): FIXED.** `_attach_header_graph` calls `header_graph_ast_projection.degrade_header_type_pass` whenever `ast_failure` is set (a refused, malformed or partially bisected clang AST, #1674): the `header_type_graph` flag and its role-coverage keys are withdrawn and the pass is marked degraded, so `type_edges` reads not collected alongside the degraded call and semantic passes. A run with no AST failure (clang not selected) keeps the flat pass collected. Consequence: a failed header AST reports fewer confirmed type-edge findings, which is the intended narrowing. Tests: `tests/test_header_graph_ast_failure_coverage.py` (stubbed acquisition) and `tests/test_header_graph_ast_failure_integration.py` (a real clang refusal through `abicheck dump`: only header refused, one of two refused, control). Owner: `service_header_graph_attach._attach_header_graph`.

#### A `kind: bundle` check still cannot run at `depth: headers`: only the *baseline* half of per-member header staging is wired (2026-09-12)

**Status (re-verified 2026-10-25): OPEN, partly narrowed.** actions/check-target/action.yml:1832 still routes bundle old-library to `steps.resolve.outputs.binaries-dir`, not `member-snapshots-dir`. BUNDLE_CHECK_DEPTHS is unchanged (binary only, abicheck/buildsource/project_targets.py:144). The fail-early requirement for `depth: headers` on a bundle is met at input validation, with a corrected reason. So (a) routing and (b) candidate-side per-member headers are still open.  
`false-clean` — Bundle checks can stage per-member *baseline* header snapshots, but the Action still routes the old side to binaries and the candidate side has no per-member headers, so a bundle check cannot run at `depth: headers`. [Full write-up](known-gaps/a-kind-bundle-check-still-cannot-run-at-depth-headers-only-the.md)

#### Header-pass body coverage is one blanket unit, not per body (2026-10-11)

**Status (updated 2026-10-14): PARTIAL, narrowed.** The header pass now marks each declaration whose body it parsed in full (`header_body` node attribute), and `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` judges call/reference edges per body seen on both sides. Still open: the edge-query coverage records (`pass_coverage_records`) still give a header pass the blanket `HEADER_INLINE_BODIES` unit, and `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED` still needs whole-project call coverage on both sides.  
`false-clean` — Edge queries and the call-reachability finding still decline body-dependent edges from a header-only pass, even for a body seen on both sides. [Full write-up](known-gaps/header-body-coverage-is-not-per-body.md)

#### A virtual call through an out-of-line definition is classified as direct (2026-10-21)

**Status (2026-10-25): FIXED.** `call_graph._ref_is_virtual` now follows the resolved node's `previousDecl` chain (kept in the compact `member_index` record) back to the in-class declaration, so a call to a virtual method or override defined out of line is recorded `virtual`/`overapprox` and joins onto its `VIRTUAL_CALL_MAY_DISPATCH_TO` edges. `EXTRACTOR_GENERATION` 24 and `SOURCE_GENERATION` 10 for the changed edge attributes. Tests: `tests/test_call_graph_out_of_line_virtual.py`. [Full write-up](known-gaps/virtual-call-through-out-of-line-definition-is-classified-direct.md)

#### Type identity: typedef and tag names keyed by bare spelling

`false-clean` — Two entries on one shape: a typedef's identity is its bare spelling, so a tag-namespace record and a same-spelled typedef collide in the clang parser, and typedef diffing still reads the bare-keyed `AbiSnapshot.typedefs` instead of the qualified twin.

- [`dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace identity with an ordinary-namespace typedef identity that happens to share the same spelling — pre-existing, not introduced by PR #719's own changes, but investigated and confirmed reachable in this pass (Codex review, investigated, not fixed)](known-gaps/dumper-clangpys-parse-types-conflates-a-cc-tag-namespace.md)  
  **Status (re-verified 2026-10-09 at `456989f`): OPEN.** The code moved to extract/headers/clang/records.py:145, where identity = "::".join([*entry.scope, name]) still uses the anon-typedef fallback name. Tag and ordinary namespaces are not distinguished, so `struct Foo; typedef struct{} Foo;` still collides.
- [Qualified typedef twin exists but the surface graph identity table still reads bare-keyed AbiSnapshot.typedefs](known-gaps/qualified-typedef-twin-exists-but-diff-typessurface-still-read.md)  
  **Status (2026-10-21): PARTIAL, narrowed.** Typedef diffing in diff_types.py is wired (prefers `typedefs_qualified`, falls back to the bare map for pre-v25). The public-surface closure is wired by branch `fix/surface-qualified-typedefs`. Still bare-keyed: the surface graph's typedef identities (`model/graph_entity_identity.py`, `compare/surface_graph.py`) and a qualified-spelling tail candidate; the page lists the exact remainder.

#### The castxml L4 source-ABI extractor does not fold the resolved EMULATED compiler's identity into either `fact_set.compiler_version` or the D8 TU cache key — attempted once for the persisted half, and REVERTED after a follow-up review caught it as a real regression, not a fix (Codex review, PR #719, three follow-up rounds)

**Status (re-verified 2026-10-25): OPEN.** buildsource/source_extractors/castxml.py still resolves cc_bin. compiler_version is _castxml_identity_with_stat_fallback(self.castxml_bin) (line 723), and the comment at line 719 says a `cc_bin --version` probe is still future work.  
`false-clean` — The castxml L4 extractor's persisted `compiler_version` and TU cache key omit the emulated compiler's identity, so changing that compiler can reuse stale facts. One attempted fix was a regression and was reverted. [Full write-up](known-gaps/the-castxml-l4-source-abi-extractor-does-not-fold-the-resolved.md)

#### `advanced_facts_collected` infers "advanced DWARF extraction ran" from its output rather than recording it

**Status (2026-10-29): FIXED.** `AdvancedDwarfMetadata.extraction` is set by the extraction (`completed`, `failed`, `not_run`) and persisted as `dwarf_advanced.extraction` since schema v68 ([ADR-080](adr/080-persisting-the-advanced-dwarf-extraction-status.md), accepted). A stored snapshot reads `unrecorded` only when it was written before v68 or carries no status, and then keeps the payload inference. [Full write-up](known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md)
`false-clean` — The extraction records its own outcome, so a completed empty parse is no longer read as not run, and the outcome is persisted (schema v68) so a stored snapshot reads it too. [Full write-up](known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md)

#### L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/adapter_contract.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.  
`false-clean` — An `icpx`/`dpcpp` override now gets the right `-fsycl` flags and no longer crashes, but L4 replay still runs one context; host+device dual-context replay is not implemented. [Full write-up](known-gaps/l4-sycl-replay-via-a-resolved-gcc-path-icpxdpcpp-override-flag.md)

#### Include-graph dependency files are never captured in production

`false-clean` — `depfile_args_from_argv`'s `trusted_root` has no production caller, so `@file` tokens are dropped, and the L2 header parse never writes a dependency file, so `depfile_resolved_paths` is never fed.

- [`depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail vulnerability is closed, real production wiring not implemented](known-gaps/depfile-args-from-argvs-trusted-root-parameter-the-self-jail.md)  
  **Status (re-verified 2026-10-25): OPEN.** grep finds no `trusted_root=` argument at any production call site; only buildsource/include_graph.py defines the parameter (lines 119-216), so production callers still drop the @file token.
- [The L2 header parse never captures a dependency file (2026-10-03)](known-gaps/the-l2-header-parse-never-captures-a-dependency-file-2026-10-03.md)  
  **Status (re-verified 2026-10-25): OPEN.** `depfile_resolved_paths` is a parameter of `abicheck/comparability.py` (line 314, forwarded at 443) and named in a `header_conditionals.py` docstring; no production caller passes it.

#### `Param.is_va_list` (G31 Phase C continued) inherits the toolchain- identity-probe gap above, rather than being a new problem

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** snapshot_reliability.py:182 and fact_registry_schema.py:273 have clang_va_list_facts_reliable as a plain flag without target identity, and no toolchain-identity probe exists yet (see entry 9).  
`false-clean` — `clang_va_list_facts_reliable` is a plain flag with no target identity, inheriting the toolchain-identity-probe gap rather than being a new defect. [Full write-up](known-gaps/paramis-va-list-g31-phase-c-continued-inherits-the-toolchain.md)

#### A using-declaration re-exporting a namespace-scope constant produces two `AbiSnapshot.constants` entries for one declaration on the castxml backend — reported against real oneDAL headers. A per-snapshot, name-shape-based dedup was implemented, reviewed, found unsound in two independent ways, and reverted rather than shipped broken (Codex review, three rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The entry's own later verification (known-gaps.md ~4030-4068) found both backends now drop the alias spelling (a false negative), so the alias-identity gap remains open. There is no UsingShadowDecl threading in the code.  
`false-clean` — A using-declaration re-exporting a namespace-scope constant: the reverted dedup was unsound; both backends now drop the alias spelling (a false negative) and no `UsingShadowDecl` identity is threaded. [Full write-up](known-gaps/a-using-declaration-re-exporting-a-namespace-scope-constant.md)

#### Dependency static/dynamic linking-mode change has no ChangeKind

**Status (re-verified 2026-10-25): OPEN.** docs/contribute/abi-taxonomy-coverage.md:177 still lists dependency-abi.linking-mode-change as NOT_IMPLEMENTED, and no ChangeKind containing linking_mode exists in abicheck/model/change_catalog.  
`false-clean` — A dependency switching between static and dynamic linking has no `ChangeKind` (`dependency-abi.linking-mode-change` is NOT_IMPLEMENTED). [Full write-up](known-gaps/dependency-staticdynamic-linking-mode-change-has-no-changekind.md)

#### AArch64 AAPCS64 by-value aggregate passing is not modeled

**Status (re-verified 2026-10-25): OPEN.** abicheck/dwarf_advanced.py:893-899 models only SysV AMD64 (_SYSV_AMD64_RETURN_ARCHES), and its comment says an AArch64 HFA flip is just a generic value-ABI change.  
`false-clean` — By-value aggregate passing is modelled only for SysV AMD64; an AArch64 HFA/HVA flip reads as a generic value-ABI change or not at all. [Full write-up](known-gaps/aarch64-aapcs64-by-value-aggregate-passing-is-not-modeled.md)

#### A resolved PDB/DWP/dSYM artifact reaches no dump path

**Status (re-verified 2026-10-25): OPEN.** `abicheck/dumper.py:1051` `_dump_pe` has no debug_roots parameter, and `dwp_path`/`dsym_path` are set only by `debug_resolver.py` and read only by `extract/debug_artifact.py`'s description (no dump consumer). The directory form still resolves PDB/DWP/dSYM artifacts that are then dropped.  
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

**Status (2026-10-24): FIXED for the header (castxml) path.** `extract/headers/castxml/type_resolution.py` spells a castxml `FunctionType` as its full declarator (`int (*)(int, int)`, with `...`, calling-convention attributes and the pointer/reference/cv/array declarator) instead of the opaque `FunctionType` tag, so an inline callback parameter, a pointer to one, a callback typedef, a struct field and a returned callback report `func_params_changed`/`typedef_base_changed`/`type_field_type_changed`. `EXTRACTOR_GENERATION` advanced to 16 for this fix; a pre-16 baseline's `FunctionType*` against a fresh declarator is demoted as generation drift (`policy/generation_drift_demotion.py`), and a callback typedef is no longer read as an opaque handle (`idioms.py`). Tests: `tests/test_castxml_function_pointer_spelling.py`, `tests/test_castxml_function_pointer_integration.py`. **Status (generation 29): the DWARF subroutine part is FIXED.** `DW_TAG_subroutine_type` spells its return, parameters and convention (`void (*)(int) __attribute__((ms_abi))`) in both DWARF readers (`dwarf_utils.subroutine_type_spelling`), so a DWARF-only callback signature change is reported. castxml drops `ms_abi`/`sysv_abi` from its XML; the clang AST join (`extract/headers/castxml/function_type_cc.py`) restores the convention on fields, typedefs and parameters, and the clang frontend already carried it. Still open: (1) a GCC `-g` build writes no `DW_AT_calling_convention` for `ms_abi` on a subroutine type, so the DWARF lane cannot see that convention in a GCC binary (header lanes only; `tests/test_dwarf_subroutine_calling_convention.py` pins it as an xfail); (2) a function-pointer **return** of a non-default convention is not joined from clang (DWARF covers it); (3) C++ `noexcept` on a function type is not in castxml's output; (4) the PDB reader still spells a subroutine type `fn(...)` (`pdb_parser.py`). Matrix: `tests/test_type_spelling_differential_matrix.py`.
`false-clean` — (DWARF residual) A callback that gains a parameter is reported as no change on DWARF unless the type name happens to carry the signature. [Full write-up](known-gaps/function-pointer-signatures-are-not-compared-in-header-mode.md)

#### A default release-profile Rust run without headers reports `NO_CHANGE ... ACCEPTED` while its own warnings say the result is unconfirmed (2026-10-15)

**Status (2026-10-15): OPEN.** The headline label, the gate and the `analysis_assurance: partial` field disagree with each other, and the label is the clean one.
`false-clean` — A real return-type change in a header-less, no-debug run is labelled `NO_CHANGE` and accepted at exit 0. [Full write-up](known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md)

#### The root Action's baseline provenance is not recorded in the JSON report (2026-10-16)

**Status (2026-10-16): OPEN.** The root Action now says which baseline it compared against, in its `baseline-source`, `baseline-asset-name`, `baseline-project-ref`, `baseline-generation` and `baseline-profile` outputs and in a "Baseline source" step-summary block. The JSON report, which a downstream consumer reads on its own, does not carry that provenance, so it cannot tell a legacy asset from a baseline-set, or which `project_ref` was checked. The nearest existing owner is ADR-065's acquisition record (`abicheck/model/scope_acquisition.py`). It records a per-member *state* (`available`, `failed`, `expected_not_produced`, and so on), not the artifact or revision a member was read from, so it does not already carry this. Adding the fields there is a report-schema change with its own decision and golden updates, so it is not made here.

#### ADR-062 D14-D18 data-evolution rules are partly implemented (2026-10-30)

**Status (2026-10-31): PARTIAL.** D14, D15 and D17 are implemented with
the residuals below; D16's `evidence` and `transport` digests are
implemented; D18 is partial. Owner: `storage/`; per-item status in the
[storage format v2 plan](plans/storage-format-v2.md).

- **D14 residual: bundle documents and archives refuse the carriers.** A
  directory ProjectSnapshot package now carries every D14/D15 carrier
  (`ArtifactRef.carriers`, closed 2026-10-31). A G38 bundle document, a G40
  bundle archive and a bundle package are still built from the flat
  `snapshot_to_dict` document per member, so a member snapshot with a
  failed/blocked/unknown section or extensions is refused there
  (`wire_extensions.refuse_package_flattening`); save it as a single-file
  snapshot or a directory package. A flat mapping a caller made with
  `snapshot_to_dict` and handed to `write_legacy_snapshot_package` cannot say
  what it lost and is written as what it says; pass the `AbiSnapshot`.
- **D14 residual: a newer `section_schema_version` in a package refuses.**
  A single-file document blocks only that section; a directory package's
  manifest-level version check refuses the package. An unknown section-level
  must-understand name now blocks only its section in both.
- **D15 residual: one producer records a whole-section failure.** The PE
  dump records `debug` as `failed` when a located PDB fails to parse, on the
  export-table path only. A header-scoped PE dump's `debug` section also
  holds the header parse's provenance, so it is not failed wholesale (the
  PDB failure is logged). No other extractor fails a whole section: each
  section aggregates several extractors whose failures are fact-family
  `FactAvailability`. The inventory expands only what the fixed corpus
  populates. A `not_collected` section gives no assurance note when a
  detector reads it (decided in ADR-062 D15's implementation decisions).
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
  per-pair release of decoded sections and the shared `evidence`-keyed
  decoded-section cache are not built: no consumer path yet reads a section
  it could share or release early (`BundleFacts` holds every member
  decoded). Closing (3) needs a streamed or per-section parse so a raw
  section is not held as parsed JSON, and a release fan-out that loads
  members one pair at a time. Measurements: the plan's A1.9 D18 entry.

### False-positive risk

#### A typedef retargeted inside a template argument still reads as a removal plus an addition under headers (2026-10-25)

**Status (2026-10-25): OPEN.** #1812 pairs a header-declared parameter retype into `func_params_changed` when the typedef it names changed target, but `compare/parameter_replacement.py`'s `typedef_target_changed` reads only a spelling that reduces to one name.
`false-positive` — `void f(std::span<V>)` or `void g(const vec<V>&)` with `V` retargeted is reported as `func_removed` + `func_added`, unless the two resolved parameter identities differ. Closing it means resolving each typedef named inside the written spelling, not only the whole spelling.

#### On PE/COFF a header-only dependency's exported template instances stay in contract (2026-10-26)

**Status (2026-10-26): OPEN.** A PE export directory records no coalesced (COMDAT) definition, so `workflows/dump/dependency_internals.py` reads every PE export as the library's own; ELF `WEAK` and Mach-O weak definitions are read (ADR-089's PE limit).
`false-positive` — On Windows (mingw auto-export, or an MSVC export list), a `-mavx2` against `-mavx512f` rebuild gates on the dependency's hidden friends and template instances; `tests/field_shapes/header_only_dependency_internals` is excluded on `win32` for this reason. [Full write-up](known-gaps/pe-dependency-instances-stay-in-contract.md)

#### A toolchain-only rebuild still fails a stated contract's coverage (2026-10-23)

**Status (2026-10-24 at `9513369`): PARTIAL.** #1751 fixed import edges and leaked-dependency symbols; #1770 fixed the binary/toolchain kinds a stated `public` left `UNKNOWN_UNRESOLVED`. Still open: libstdc++ `_ZGTt` transactional-memory clones are not seen as runtime-owned, and runtime exports make the `exports` domain `unmatched_exports` (even for a byte-identical pair). Pinned by shrink-only lists in the toolchain-drift tests.  
`false-positive` — Under a stated `--contract public|exports`, a rebuild from unchanged source with a static libstdc++ or clang `-O0` `std::` instantiations exits 1 on unsuppressible coverage failures. [Full write-up](known-gaps/toolchain-runtime-rebuild-fails-stated-contract-coverage.md)

#### A binary-only dump loses exports with no debug subprogram, so a still-exported symbol reads as removed (2026-10-25)

**Status (2026-10-25): OPEN.** When debug info yields any function, the binary-only dump declares only debug-confirmed functions. Exports with no subprogram drop out, so a version dumped through the other path disagrees.
`false-positive` — `abicheck compare libv1.so libv2.so` on catalog case38 reports `Processor::validate` as "dynamic export removed" although `libv2.so` still exports it. [Full write-up](known-gaps/binary-only-dump-drops-exports-without-a-debug-subprogram.md)

#### Below `source`, `TemplateInstanceAbsence` loses the clang-graph instantiation origins (H2a residual, 2026-10-23)

**Status (2026-10-23): OPEN, unobserved.** `compare/detector_inputs.py` declares `surface_graph` as a *tolerated* input of the `functions`/`variables`/`types` detectors: its L2 declaration-node role survives below `source` as `header_declaration_ids`, but `_graph_origins` (the `template_instantiation` nodes' `instantiated_from` + `DECL_INSTANTIATES_TEMPLATE` edges) does not. At `--depth headers`/`build` the rule then proves a vanished specialization's origin from the castxml class-head reading alone, so a specialization only the clang graph could place may read as `type_removed`. `tests/test_depth_metamorphic_examples.py` found no such case in `catalog/cases`. Fix, if one appears: keep the proven `(spelling, origin, pattern)` triples as a second runtime-only L2 fact alongside `header_declaration_ids`, and move the read from `tolerates` to the substitute.

#### Two BINARY_GATED detectors still assert public reachability from the export population (ADR-084)

**Status (2026-10-21): FIXED.** `unspecified_return_now_named` (`diff_templates.py`), `experimental_removed_without_replacement` and `experimental_graduated` (`diff_namespaces.py`, via a per-item `source_evidence` flag on `_IndexItem`), and a third instance the new ratchet found, `mandatory_template_param_added` (`diff_templates.py`, which counted every reconciled public function as public evidence), now build their tags with `public_reachability(has_observed_source_contract_evidence(...))`. A binary-only run tags them `UNKNOWN`; a header-declared subject keeps `PROVEN_REACHABLE`. `tests/test_binary_gated_reachability_tags.py` closes the class: an AST ratchet fails any call in a function that builds a `BINARY_GATED` kind and passes `public_reachable`/`reachability_state`/`reachability_kind` directly instead of through `public_reachability(...)`/`reachability_of(...)` (exemption list empty), plus binary-only and header-backed behavioural controls for the three detectors. Residual: the ratchet is lexical, so a helper that builds a gated finding from a kind passed in as a variable, outside the function that names the kind, is not seen. Originally: ADR-084 made the verdict of every `BINARY_GATED` kind depend on observed header or source evidence, and removed the hard-coded `public_reachable=True` from `std_reexport_removed` and `tag_type_renamed`. Two detectors in the same bucket still set the tags from the binary export population: `unspecified_return_now_named` (`diff_templates.py`, the `public_reachable=True` set on the finding built around line 1160, where the comment says `_index()` filters to `Visibility.PUBLIC`) and `experimental_removed_without_replacement` (`diff_namespaces.py`, `_emit_experimental_change`, `public_reachable=subject_is_public`, with `subject_is_public` derived from export at about lines 977 and 996). Their verdict is correct, because the gate ignores tags, but a consumer reading `public_reachable`/`reachability_kind` on these two kinds is told the subject is proven public when only the export table shows it. Fix by building them with `public_reachability(has_observed_source_contract_evidence(...))` as `compare/namespace_shape_detectors.py` now does.

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

#### Class-template specialization a new header stops enumerating keeps `type_removed` when its origin is unproven (narrowed)

**Status (2026-10-21): NARROWED.** A class-template specialization the new header stops instantiating is no longer reported as removed while the pattern it was instantiated from is still declared. That pattern is either the primary template or a partial specialization. Its members are judged by the new export table (`compare/template_instance_absence.py`). The origin comes from castxml's declaring line or from the clang header graph's AST structure (`buildsource/template_class_origin.py`), which also covers a macro-produced class-head and a partial specialization. Still open, each as a residual false positive:
- an old snapshot with neither origin record;
- a dump made without clang;
- a dropped explicit instantiation (`template struct Box<double>;`).

`false-positive` — Such a specialization still reports `type_removed` and its members' removals when no producer proved its origin: an old snapshot from before this change and schema v61, a dump without clang (macro-produced class-head, partial specialization), or an explicit instantiation the new header drops. [Full write-up](known-gaps/class-template-specialization-members-a-new-header-stops-enumerating.md)

#### Explicit function and variable instances: removal is castxml-blind and class-scope-ambiguous (2026-10-25)

**Status: NARROWED** (2026-10-25). An explicitly dropped function or static member is a removal on the clang header backend; castxml and two-instances-in-one-TU cases still take the export-loss or unsupported path. [Full write-up](known-gaps/explicit-function-and-variable-instances-removal-is-castxml-blind.md)

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

#### Dependency scoping after a cache round-trip

`perf` — Scoping a snapshot reloaded from the dump cache keeps typedefs that scoping the fresh extraction drops.

- [Re-scoping a cache-reloaded snapshot keeps typedefs that scoping the fresh extraction drops](known-gaps/rescoping-a-cache-reloaded-snapshot-keeps-typedefs-a-fresh-scope-drops.md)  
  **Status (2026-10-22): OPEN.** Warm `resolve_input` runs now reproduce cold output (finished-tier misses re-extract); the round-trip asymmetry itself is unfixed.

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

#### A forked child that is alive but never answers blocks the parent's pipe read

**Status (recorded 2026-10-28): OPEN.** The side-isolation receive (`side_isolation._run_children`, `recv.recv()`), the release member pool's receive (`keyed_process_pool`, `recv.recv()` and `wait_ready()`) and the release admission budget wait (`release_admission`, `self._cond.wait()`) return only when the child answers or its pipe reaches EOF. A child that is alive, deadlocked (for example on a lock its fork copied held) and never exits or closes its pipe therefore hangs the run when no `--budget` is set. The EOF and completed-child reap paths are bounded (R6, 2026-10-28), and `tests/test_unbounded_wait_gate.py` pins these three sites as the only unbounded waits left under `abicheck/`. Bounding them needs a time policy for an unbudgeted side (a maintainer decision). Under `--budget` the active deadline could bound them, but `workflows` may not import the unclassified `deadline` module yet (`architecture/dispositions.yaml`).  
`hygiene` — A hang here is silent: the run neither fails nor finishes. Decide the unbudgeted time limit, then route these reads through the deadline.

#### `consumers.required_symbols` `@FILE` read errors are checked on the CLI route only

**Status (recorded 2026-10-22): OPEN.** An unreadable `@FILE` is refused as a typed `CompareSettingsError` (exit 64, path named) by `workflows/compare_configuration.load_required_symbols`, pinned on the CLI and the typed function in `tests/test_compare_settings_cli_translation.py`. No bug-class harness covers it: the typed `CompareRequest` API does not accept `@FILE` required symbols, so there is no second route to compare against, and family F2's route-parity matrix has nothing to attach to.
`hygiene` — A harness cell becomes meaningful only if `@FILE` resolution is exposed on a second route; until then the CLI test is the guard. [Full write-up](known-gaps/required-symbol-file-read-errors-are-checked-on-the-cli-route-only.md)

#### Release report: live and stored operands render different documents

**Status (2026-10-25): CLOSED for the document shape.** A stored `BundleFacts` comparison renders the live release document: its JSON is the live release document (release schema 2.5), and JSON, Markdown, JUnit, `oneline` and HTML all come from one live-shaped evaluation of the stored facts (`tests/unit/report/test_release_document.py`, `tests/test_cli_compare_stored_release_parity.py`). Fields a stored comparison cannot compute are explicit `unavailable` markers with a reason, and its receipt is marked `reconstructed_from_stored_facts`. Remaining limits, named rather than hidden: the stored JUnit omits the passing testcase for each unchanged symbol (no old-side inventory); `output.view` `show=`, `sarif` and `review` stay refused for a stored OLD_INPUT.  
`hygiene` — Closed: the stored document now carries the live release shape (schema 2.5, breaking for the stored document). [Full write-up](known-gaps/release-report-live-and-stored-operands-render-different-documents.md) [Full write-up](known-gaps/release-report-live-and-stored-operands-render-different-documents.md)

#### A stored `BundleFacts` operand pairs versioned member names on one side only (2026-10-23)

**Status (recorded 2026-10-23): OPEN.** A stored operand keys a member `libX.so.N` by its canonical `libX.so`, while a directory NEW side keys it by its file name `libX.so.N`, so `compare OLD.bundle-facts.json NEW_DIR` pairs no versioned member: it reports "no library pair was compared" (exit 1) where the live `compare OLD_DIR NEW_DIR` pairs them. Unversioned names pair on both sides. Found while proving the release `surface_changes` block (schema 2.2); that block's stored/live agreement is asserted in-process, not through this CLI path.
`hygiene` — The stored and live operands key a versioned member name differently, so a stored comparison of a versioned directory compares nothing and reads as a no-comparison run. [Full write-up](known-gaps/a-stored-bundlefacts-operand-pairs-versioned-member-names-on-one-side-only.md)

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

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The engine half is gone: scan_engine.py/cli_scan*.py are deleted, policy/exit_decision.py:147-154 marks PROMOTED_CROSSCHECK a read-only retired producer, and resolve_exit_decision no longer takes it. Residue: ExitDecision still serializes crosscheck_promotion_contribution (exit_decision.py:327,442) and action/run.sh still reads promoted_crosscheck (re-verified 2026-10-25).  
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

**Status (re-verified 2026-10-25): PARTIAL, narrowed.** The compat/* modules are gone. `impact/use_case_impact.py` is deleted (reachability moved to `policy/use_case_reachability.py`), and `buildsource/fact_set.py` and every remaining `impact/*` module (`assessment`, `consumer_graph`, `correlation`, `engine`) are now classified in `architecture/modules.yaml`. Still with no `architecture/*.yaml` entry: `buildsource/build_evidence.py`, `build_output.py`, `evidence_policy.py`, `merge_support.py` and `source_graph.py`.  
`hygiene` — Five nested `buildsource/` modules (`build_evidence.py`, `build_output.py`, `evidence_policy.py`, `merge_support.py`, `source_graph.py`) have no ADR-061 disposition in `architecture/*.yaml`. [Full write-up](known-gaps/adr-061-gap-f-nested-buildsourceimpact-modules-still-carry-no.md)

#### `internal_leak` is now four `compare/` modules, but its test module is still grandfathered above the test cap (ADR-061 migration, 2026-10-22)

**Status (2026-10-22): PARTIAL.** The physical move and split landed: the detectors are in `compare/internal_leak.py`, the layout walk in `compare/internal_leak_paths.py`, the call-graph walk in `compare/internal_leak_graph.py`, and the subject classification in `compare/internal_leak_names.py`. Each is under the 800-line cap, and nothing re-exports from the old root path. Still open: `tests/test_internal_leak.py` (1933 lines) remains above the 1200-line test cap on its `no_growth` record.  
`hygiene` — the leak rule is owned by `compare/` as four modules, but its main test module is still a `no_growth` grandfather and has not yet been split by behavior axis. [Full write-up](known-gaps/internal-leak-is-now-four-compare-modules-but-its-test.md)

#### A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

**Status (2026-10-23): PARTIAL.** `action/validate-inputs.sh` now takes the release set from the generated `action/cli-surface.txt` (`release-compare`), and the generator also emits the stored set (`stored-bundle-facts`, from `STORED_RELEASE_FORMATS`), so both sets have one source. Still open: the stored narrowing itself is not applied before install, because deciding that an operand *is* a stored BundleFacts document needs `workflows/bundle_compare_operand.looks_like_stored_bundle_facts` (a multi-probe, FIFO-safe reader), and a shell copy of it would be the second transcription ADR-070 forbids. The narrowing is enforced by the CLI after install.  
`hygiene` — For a stored-`BundleFacts` baseline the Action checks the release allowlist before install but not the stored subset, so a format the stored driver cannot render fails late, not at input validation. [Full write-up](known-gaps/a-stored-bundlefacts-baselines-narrower-format-set-is-not-pre.md)

#### `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** --bundle-facts-out is still a compare option (frontends/cli/options/bundle_facts_library_manifest.py, commands/compare.py), and no dump command offers a bundle-facts format/fan-out. The blocker is removed, but the dump fan-out is unwritten.  
`hygiene` — `--bundle-facts-out` remains a `compare` flag because `dump` has no release fan-out; the blocker is gone but the fan-out is unwritten. [Full write-up](known-gaps/compare-bundle-facts-out-stays-a-compare-flag-because-dump-has.md)

#### A directory/package `compare` cannot render sarif/review; `bundles:` cannot state scope policy

**Status (2026-10-23): PARTIAL.** Closed: a directory/package `compare` now renders `sarif` (one run per library, each result stamped with `properties.releaseMember`) and `review` (a release digest rendered from the release JSON), so `RELEASE_FORMATS` is the full compare set minus `terminal`. Still open: BundleSpec has no scope-policy keys (`bundles:` cannot state scope policy at bundle level).  
`hygiene` — `bundles:` has no scope-policy keys. [Full write-up](known-gaps/a-directorypackage-compare-cannot-render-sarifreview-bundles.md)

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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: four of the five SVS report items were not checked in code (header accounting does not lower status, the not-applicable detector vocabulary, partial telemetry, the identical-binary channel). The 294 KB Markdown default is fixed (2026-10-23): see that item below.  
`hygiene` — Unverified: four SVS report items (header accounting, not-applicable detector vocabulary, partial telemetry, identical-binary channel) are not marked fixed; the 294 KB Markdown default is fixed. [Full write-up](known-gaps/five-svs-scan-report-findings-not-addressed-by-the-change-vs.md)

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


#### An `ownership_fact` is persisted only as a PRESENT decision (2026-10-27)

**Status (2026-10-29): FIXED (schema v67).** Non-`PRESENT` statuses and the decisions of a snapshot with no recorded scope are now persisted (`entity_ownership.statuses`, `ownership_without_scope`). The round-trip is pinned in `tests/unit/storage/test_snapshot_field_roundtrip.py`. [Write-up](known-gaps/ownership-fact-persists-only-a-present-decision.md)

#### Release fan-out cannot pair a stored snapshot directory with a live one

**Status (recorded 2026-10-28): OPEN.** A directory compare pairs members by library key, the file name, so a stored `libfoo.json` directory against a live `libfoo.so` directory completes zero comparisons and exits 1 (`no_comparison_completed`). The same bytes compared as scalar operands compare normally. Pinned by the strict-xfail cells `release@mixed_old` and `release@mixed_new` in `tests/test_family_f2_live_route_matrix.py`.
`hygiene` — Closing it needs a member-identity rule for a stored snapshot and the live binary it came from, which changes which members pair in existing directory compares. [Full write-up](known-gaps/release-fan-out-cannot-pair-a-stored-snapshot-directory-with-a-live-one.md)

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

`perf` — oneDAL hot paths (`compare()` over a 125-header-root L2 surface, the
rows in the 2026-09 receipt series) are not exercised by any scenario, so a
regression there shows up only in the manual real-profile lane.
