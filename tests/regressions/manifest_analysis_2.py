# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The analysis-pipeline bug classes named in
`docs/contribute/plans/bug-class-regression-testing.md`'s Phases 2-9, second
half. See `manifest_analysis_1.py` for why the list is split.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["ANALYSIS_2_BUG_CLASSES"]


ANALYSIS_2_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="policy.disposition_conservation",
        invariant=(
            "A policy disposition (suppression, scope exclusion, "
            "deduplication, contract exclusion) moves a detected change "
            "between buckets; it never changes how many changes were "
            "detected, and every report projection states both the "
            "detected total and the effective (gating) total. Two "
            "corollaries the pre-ADR-067 code violated: a consumer of the "
            "comparison result that needs the *raw* delta -- "
            "`semver.recommend_release` -- must read the conserved ledger "
            "rather than the post-disposition `changes` list, so a "
            "suppressed major-class break can never degrade to 'no bump "
            "needed'; and a detector that never ran must read as "
            "`not_evaluated`, never as a real `changes_count: 0`."
        ),
        fixed_by=(1082,),
        seed_tests=(
            "tests/test_disposition_audit.py",
            "tests/test_disposition_audit_states.py",
            "tests/test_disposition_scope_matrix.py",
            "tests/test_disposition_rule_identity.py",
            "tests/test_rule_id_token_properties.py",
            "tests/test_disposition_state_sweep.py",
            "tests/test_used_by_audit_exit_consistency.py",
        ),
        axes={
            "application_point": (
                "apply_suppression",
                "merge_late_findings",
                "filter_suppressed_changes",
                "filter_pattern_synthetic",
                "consumer_overlay",
            ),
            "projection": (
                "json",
                "stat-json",
                "one-line",
                "review-digest",
                "pr-comment",
                "sarif",
                "junit",
                "html",
                "markdown-full",
                "markdown-leaf",
                "markdown-root-cause",
            ),
            "gate": ("legacy-verdict", "severity-preset"),
            # Enumerated exhaustively rather than sampled:
            # `tests/test_disposition_scope_matrix.py` runs the full
            # cross-product of these three against a hand-written oracle,
            # because the consumer-scoping half of this mechanism was fixed
            # once per reported corner across six review rounds.
            "scope_membership": ("in-scope", "scope-excluded"),
            "record_timing": ("recorded-before-close", "recorded-during-close"),
            "initial_disposition": (
                "gating",
                "non_gating",
                "suppressed",
                "out_of_contract",
                "unresolved_relevance",
                "deduplicated",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Scoped to the scalar single-pair `compare` path "
                    "(ADR-067 S1). The bundle, aggregate, release fan-out "
                    "and consumer-report projections do not yet carry the "
                    "audit, so the conservation invariant is unchecked "
                    "there -- that is S2. The seed tests likewise drive "
                    "`checker.compare` and the reporters directly rather "
                    "than the CLI or typed API, so `public_surfaces` is "
                    "deliberately empty."
                ),
                reference=(
                    "docs/contribute/plans/vision-api-abi-evolution.md"
                    " -- workstream C, slice S2"
                ),
            ),
            KnownGap(
                description=(
                    "`RuleProvenance.intent` is always 'unspecified': the "
                    "explicit `intent: waiver|false_positive` rule field is "
                    "ADR-067 D5 (S3). Until it lands, the audit cannot "
                    "distinguish a claimed detector false positive from a "
                    "deliberate waiver, which is why the suppressed-break "
                    "release recommendation asks for review rather than "
                    "asserting a MAJOR release outright."
                ),
                reference=(
                    "docs/contribute/adr/"
                    "067-change-intent-acknowledgment-and-disposition-audit.md"
                    " -- D5"
                ),
            ),
        ),
    ),
    BugClass(
        id="storage.legacy_availability_collapse",
        invariant=(
            "A legacy document's per-declaration fact must never be read "
            "as a confirmed, positively-observed value on the strength of "
            "a whole-snapshot/producer-level reliability flag alone when a "
            "finer-grained availability signal already recorded on that "
            "same document (e.g. per-declaration provenance) is silent "
            "about that exact declaration -- the coarser signal may "
            "confirm, but may never override, the absence the finer one "
            "reports."
        ),
        fixed_by=(1091,),
        seed_tests=("tests/test_deprecation_family_facts.py",),
        axes={"ast_producer": ("hybrid", "castxml")},
        known_gaps=(
            KnownGap(
                description=(
                    "Closes the gap only for the seven fields gated by "
                    "`AbiSnapshot.fact_provenance` (the legacy-hybrid "
                    "backfill blocker, ADR-063 T9 / duplication-and-"
                    "convergence-assessment Phase 6 item 4). The sibling "
                    "gap for DWARF's own per-translation-unit vtable "
                    "coverage (`RecordType.vtable_fact` reading a genuine "
                    "`PRESENT` for a class whose virtuals live in a TU "
                    "only the other side's debug info covers) is a "
                    "different evidentiary shape -- no per-declaration "
                    "signal exists to consult at all, DWARF's own extractor "
                    "cannot see the gap either -- and remains open, "
                    "tracked in that same plan document rather than here."
                ),
                reference=(
                    "docs/contribute/plans/"
                    "duplication-and-convergence-assessment.md -- T9"
                ),
            ),
        ),
    ),
    BugClass(
        id="gating.consumer_scope_enrichment",
        invariant=(
            "A supplied --used-by/--required-symbol(s) consumer's own "
            "confirmed/potential/unresolved result must never substitute "
            "for, narrow, or override the full-library compatibility "
            "result: the process's exit code and the JSON verdict/"
            "severity/run_outcome/summary always describe the full-library "
            "comparison, exactly as an unscoped run would, whatever that "
            "consumer's own scoped verdict/exit code says."
        ),
        fixed_by=(1094,),
        seed_tests=("tests/test_used_by_gate_enrichment.py",),
        axes={
            "exit_code_scheme": ("legacy", "severity"),
            "consumer_verdict": ("COMPATIBLE", "API_BREAK", "BREAKING"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Covers the compare CLI's own compatibility-contribution "
                    "resolution and the real compare --used-by process exit "
                    "code. Does not independently property-test SARIF's "
                    "invocations[0].exitCode/results[].level, JUnit's "
                    "failure count, or the HTML/PR-comment renderers -- "
                    "those are covered by fixed-example tests only "
                    "(tests/test_sarif.py, tests/test_junit_report.py, "
                    "tests/test_sprint9_html.py, tests/unit/report/"
                    "test_render_html.py, tests/test_pr_comment.py)."
                ),
                reference=(
                    "docs/contribute/plans/vision-api-abi-evolution.md -- "
                    "D. Optional prebuilt-consumer lifecycle, S1"
                ),
            ),
        ),
    ),
    BugClass(
        id="extraction.language_mode_export_evidence",
        invariant=(
            "Whole-TU C/C++ language-mode auto-detection must not rely on "
            "header syntax alone: a header with no structural C++ syntax "
            "gives that heuristic nothing to key on. A real Itanium/Mach-O/"
            "MSVC mangled export that CORRELATES with an identifier this "
            "header declares is direct proof of C++ linkage and must "
            "resolve the WHOLE TU to C++, not patch one symbol after the "
            "fact -- a wrong mode corrupts `mangled`/`is_extern_c`/"
            "`visibility` together. An explicit `--lang`, real C++ syntax, "
            "a bare-name export, an UNRELATED header's own export "
            "elsewhere in the same multi-header binary, or a name that "
            "only appears in a COMMENT/STRING LITERAL/inactive `#if 0` "
            "block are all not evidence for THIS header -- only active "
            "declaration text correlates. Every later parse of the same "
            "headers in the same dump (the header graph's own clang parse, "
            "even one prefetched before the decision existed) follows the "
            "resolved mode rather than re-deciding from header text."
        ),
        fixed_by=(1138, 1565),
        seed_tests=(
            "tests/test_dumper_language_mode_export_evidence.py",
            "tests/test_crosscheck_language_mode_export_evidence.py",
            # the header graph follows the primary decision (live dumps,
            # both frontends, C controls) and its two seams, compiler-free.
            "tests/test_header_graph_language_mode.py",
            "tests/test_header_graph_language_mode_unit.py",
        ),
        public_surfaces=("python-api", "cli"),
        axes={
            "frontend": ("castxml", "clang", "hybrid"),
            "declaration_shape": ("function", "ns_function", "extern_c", "variable"),
            "export_mangling": ("itanium", "macho_itanium", "msvc", "bare_c"),
            "excluded_text_source": ("comment", "string_literal", "if_zero_block"),
            "consumer": ("primary_parse", "header_graph_parse"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No general preprocessor/macro-expansion evaluator: an "
                    "identifier appearing only as a macro parameter name, "
                    "or inside an #ifdef/#ifndef branch not a literal "
                    "0/1/false/true guard, still counts as a correlation "
                    "candidate -- conservative (widens what might confirm, "
                    "never fabricates), but not fully precise."
                ),
                reference="PR #1138 follow-up, second CodeRabbit round",
            ),
        ),
    ),
    BugClass(
        id="extraction.macho_mangled_identity_normalization",
        invariant=(
            "On Darwin, strip a linker-decorated spelling to the pure spelling AT THE POINT "
            'OF ORIGIN: a real Itanium name (`__Z...` -> `_Z...`, unconditional), a genuine extern "C"'
            '/plain-C bare name (`_foo` -> `foo`, gated on `is_extern_c`, itself gated `and not (has_asm_label and is_cxx)` in its own Darwin `symbol_candidates` branch -- a single-underscore explicit `asm("_foo")` label is exactly as candidate-matchable as genuine decoration, so without the gate the mangled name stayed preserved but `entity_id_for_function`/`entity_id_for_variable` still took the wrong, signature-free `("extern_c",)` branch instead of `("mangled", "_foo")`; gating on `is_cxx` -- the caller\'s own resolved compile-language mode -- is required because C has no mangling to override, so a genuinely plain-C asm-labeled declaration must stay `("extern_c",)`, matching castxml, or self-comparing an unchanged plain-C header spuriously reports FUNC_LANGUAGE_LINKAGE_CHANGED). Both gate on '
            "`is_darwin_target(target_triple)`, always False for a bare `None`/empty triple (never "
            "guess Darwin from host OS there). A `sys.platform` guess for a REAL probe failure lives "
            "one layer up, in `dumper._run_clang`, after `_compiler_options.explicit_target_triple`. "
            "Whether CL mode is EFFECTIVELY active (`_compiler_options.effective_driver_mode_is_cl`) is the last `--driver-mode=<value>` override if any, else the binary's own name -- not a plain OR of the two, which can never be revoked back to GNU mode. Under CL mode there is no `sys.platform` guess, and explicit-target recovery (`cl_style=True`) is narrowed to the spellings actually honored (attached `--target=<value>`, separate `-target <value>`, either `/clang:`-forwarded), never one silently ignored. "
            "A bare re-probe of the SAME resolved `clang_bin` (keeping only its effective `--driver-mode=<value>` if any, `dumper._forwarded_driver_mode_token`) is tried under BOTH driver modes when the explicit-target recovery finds nothing -- a CL-style `-print-target-triple` is honored exactly like GNU's, so a target-prefixed CL-style name (e.g. `aarch64-apple-darwin-clang-cl`) reports its own prefixed default from the bare probe too, not just under GNU mode. Dropping the driver-mode token would silently revert a `clang-cl --driver-mode=g++` re-probe to CL mode, since the identical binary reports a different target bare vs. with that override: real evidence beats a static name-shape heuristic, since the earlier, option-bearing probe may have failed for a reason unrelated to `clang_bin`'s own identity. Only when that bare probe ALSO fails (GNU mode only) does `sys.platform` apply, gated on BOTH the RESOLVED `clang_bin` being the plain host default by invocation BASENAME (`abicheck.extract.compiler_driver.is_default_clang_bin`, version-suffix-stripped -- real Clang derives its own default target from argv[0], so basename -- not real identity, not raw spelling, and NOT merely differing from the plain name -- is what matters: an absolute path or a native version suffix still matches, a target-prefixed symlink correctly does not, and an arbitrary custom rename is answered by the bare re-probe, never by guessing it must be a cross-compiler) AND NOT `abicheck.extract.compiler_driver.clang_bin_is_explicitly_configured` (a `--compiler`/`--compiler-prefix` wrapper can coincidentally share the plain default's basename while genuinely not being it -- once BOTH probes fail for an explicitly-configured binary, that failure is itself evidence of an anomaly, not confirmation of a real native clang; this now ALSO suppresses the guess for a genuine absolute-path `--compiler` naming the real native clang, superseding this bug class's own earlier claim to the contrary). No fallback at all when no `@response-file`/`--config=<file>`/`--config-{user,system}-dir=<dir>` is forwarded AT ALL (`forwards_response_file`/`explicit_target_triple`'s shared `_opaque_option_source_tokens` void-back-to-None -- a config-*-dir points at a directory whose `clang.cfg` is loaded IMPLICITLY, no explicit `--config=` needed; real Clang scans the WHOLE arg list, response/config files included, before parsing even starts, so one could carry a later target OR retroactively flip CL-vs-GNU mode regardless of its position relative to a visible target). Shape check: `model.mangled_name.strip_macho_itanium_"
            "decoration`, NOT applied to castxml."
        ),
        fixed_by=(1138, 1156, 1167),
        seed_tests=(
            "tests/test_dumper_clang_extern_c_identity.py",
            "tests/test_dumper_hybrid_macho_idempotence.py",
            "tests/test_mangled_name_macho_decoration.py",
            "tests/test_compiler_options.py",
            "tests/test_dumper_target_triple_fallback.py",
            "tests/test_castxml_literal_double_underscore_mangled.py",
        ),
        public_surfaces=(),
        axes={
            "frontend": ("clang",),
            "declaration_shape": ("function", "variable"),
            "mangled_shape": (
                "macho_decorated_itanium",
                "macho_decorated_extern_c",
                "already_pure",
                "bare_asm_label",
                "single_underscore_asm_label",
                "single_underscore_asm_label_in_c_mode",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No Mach-O toolchain here -- Codex/CodeRabbit code review, plus one real Clang 18 install (used to verify the eighteenth-through-twenty-sixth items below). "
                    'Recurred TWENTY-SIX times within #1138/#1149/#1156/#1167: Itanium/plain-C shapes reading False on a failed probe; a guess INSIDE `is_darwin_target` plus an unconditional castxml strip corrupting `asm("__Zfake")`; that guess moved back inside `is_darwin_target` (caught by real macos-latest CI); a possibly-ignored '
                    "explicit target recovered under a CL-style driver; the `sys.platform` guess leaking outside that gate; `--driver-mode=cl` on a plain `clang` name evading the name-only CL check; that gate recovering NO spelling when two are honored; a `/clang:`-forwarded spelling still missed; a CL-named binary reverted to GNU mode "
                    "still treated as CL; the `sys.platform` guess applying to an explicit cross-compiler unrelated to host OS; that same guess wrongly suppressed for a `gcc_path` `resolve_clang_bin` itself ignores; an absolute-path spelling of the identical native binary failing string equality; a real-executable-identity fix wrongly equating a target-prefixed symlink with plain `clang`; a `@response-file`'s own hidden target being ignored; a native versioned "
                    "driver name (`clang-18`) failing the basename check; a visible target trusted despite a later response file that could override it; that same fix wrongly trusting a preceding one too, since Clang's own driver-mode scan reads the whole arg list up front regardless of position; an exact-basename comparison alone wrongly treating ANY custom rename of the native compiler (e.g. `company-clang`) as a cross-compiler, closed by trying a bare re-probe of the identical binary first; an explicit `--config=<file>` left undetected by the response-file gate, closed by folding both into one shared `_opaque_option_source_tokens` check; that bare re-probe dropping an explicit `--driver-mode=` override too, silently reverting a `clang-cl --driver-mode=g++` re-probe to CL mode; a `--config-{user,system}-dir=<dir>` implicitly loading a `clang.cfg` with no explicit `--config=` at all, left just as undetected as the file form; an explicitly-"
                    "configured `--compiler` wrapper sharing the plain default's basename while not being it, still getting the guess purely on basename evidence; and, once a genuine "
                    'Darwin target IS confirmed, a literal `asm("__Zfake")` label being indistinguishable by shape alone from a real compiler-generated decorated Itanium mangling '
                    "(both are equally `__Z...`-shaped), closed by `has_explicit_asm_label` detecting clang's own distinct `AsmLabelAttr` child node under the declaration's `\"inner\"` "
                    "list (verified against a real Clang 18 install) and short-circuiting both stripping branches whenever it is present; and the fifteenth item's bare re-probe being "
                    "confined to the GNU branch only, leaving a target-prefixed CL-style driver (e.g. `aarch64-apple-darwin-clang-cl`) with no fallback once its own explicit-target "
                    "recovery found nothing, even though a real `clang-cl -print-target-triple` reports that same prefixed default (verified against a real Clang 18 "
                    'install), closed by trying the identical bare re-probe under CL mode too; a single-underscore explicit `asm("_foo")` label still tripping `is_extern_c`\'s Darwin '
                    "`symbol_candidates` fallback even though the twenty-fourth item's own fix kept the mangled name preserved -- the resulting entity identity still took the wrong "
                    '`("extern_c",)` branch instead of `("mangled", "_foo")` (verified against a real Clang 18 install), closed by gating that fallback `and not has_asm_label`; and that '
                    "very fix then wrongly excluding EVERY asm-labeled declaration, including a genuinely plain-C one -- real Clang emits the identical AST shape for "
                    '`void foo(void) asm("_foo");` whether compiled as C or C++ (verified against a real Clang 18 install: same literal `mangledName`, same `AsmLabelAttr`, no way to tell '
                    "them apart from the node alone), but C has no mangling to override in the first place, so treating a routine glibc/POSIX-style C asm label as a deliberate identity "
                    "override broke self-comparison of an unchanged plain-C header (spurious FUNC_LANGUAGE_LINKAGE_CHANGED); closed by threading the caller's own resolved compile-language "
                    "mode (`dumper._clang_header_dump`'s `resolved_force_cpp`) down as `is_cxx`, gating the exclusion `and not (has_asm_label and is_cxx)` instead."
                ),
                reference="#1138/#1149 follow-ups, fixed by #1156/#1167",
            ),
        ),
    ),
    BugClass(
        id="extraction.macho_export_index_double_strip",
        invariant=(
            "`model.export_index.default_versioned_names`'s Mach-O branch "
            "must not re-strip a leading underscore: `macho_metadata` "
            "already strips the platform's own one underscore while "
            "parsing the real export trie/symtab, so a `MachoExport.name` "
            "reaching this projection is ALREADY the pure spelling "
            "(`_ZN2ns3fooEv`, matching `Function.mangled`). Stripping "
            "again corrupts every Mach-O C++ export by eating its own "
            '"_Z" prefix, breaking `exported_not_public`/`public_not_'
            "exported` correlation even though the declared side is "
            "correctly normalized."
        ),
        fixed_by=(1138,),
        seed_tests=(
            "tests/test_export_index.py",
            "tests/test_crosscheck_macho_export_index_normalization.py",
        ),
        public_surfaces=(),
        axes={
            "declaration_shape": ("plain_c", "namespaced_cxx"),
            "mangled_shape": ("macho_itanium",),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No Mach-O toolchain in this dev environment; verified "
                    "via unit tests over model.export_index/crosscheck "
                    "directly, constructing a MachoMetadata fixture rather "
                    "than a real compiled binary."
                ),
                reference=(
                    "PR #1140 follow-up: CI's integration-tests "
                    "(macos-latest) job reported 3 residual failures after "
                    "the sibling extraction.macho_mangled_identity_"
                    "normalization fix landed -- per-declaration identity "
                    "was already correct, but export-table correlation "
                    "still disagreed for every Itanium-mangled sibling."
                ),
            ),
        ),
    ),
    BugClass(
        id="invariant.blanket_assertion_over_widened_population",
        invariant=(
            'A whole-set assertion ("this collection is always empty") '
            "is only valid while the collection has exactly one "
            "population. When a later change adds a second, legitimately "
            "non-empty population to the same collection, the assertion "
            "must be split into a scoped invariant per population — never "
            "left blanket (it then fires on correct behavior) and never "
            "simply deleted (the original population loses its guard). "
            "The scoped guard must also survive `python -O`, so it is a "
            "raised error rather than an `assert`."
        ),
        # #1181-era: `compare --no-baseline`'s `assert not diff.changes`
        # was correct until ADR-068 Phase 2a/2b moved the eleven
        # cross-source checks and the pattern/preprocessor pre-scan into
        # `compare()`, which legitimately emit findings on a self-compare.
        # All eleven G20 audit fixtures then aborted with an unhandled
        # AssertionError, and a live candidate rendered an empty document.
        fixed_by=(1181,),
        seed_tests=(
            "tests/test_no_baseline_d3_properties.py",
            "tests/parity/test_no_baseline_audit_corpus_parity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "population": ("comparison-finding", "candidate-side-finding"),
            "evolution_state": (
                "persistent",
                "not_evaluated",
                "introduced",
                "resolved",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The registry has no mechanical sweep for *other* "
                    "blanket emptiness assertions over collections a "
                    "later stage may widen — this entry names the class "
                    "and carries the one instance's generalized tests, "
                    "but a second instance elsewhere in the codebase "
                    "would still be found by hand, not by a gate."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="imports.first_import_order_cycle",
        invariant=(
            "Whether an abicheck module imports must not depend on which "
            "abicheck module a fresh interpreter imports first: a "
            "root-level model-layer module that `abicheck.model` imports "
            "may not need `model/__init__` to have finished, or importing "
            "it first raises ImportError while every suite run that "
            "happened to import `model` earlier stays green."
        ),
        # #1453 made `name_classification` import `model.execution_cache`;
        # `tests/test_anon_type_location_properties.py` then failed on its
        # own (ImportError at collection) yet passed in the full suite.
        fixed_by=(1453,),
        seed_tests=("tests/test_first_import_order.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The sweep covers `model` and every module it imports "
                    "from outside itself, not all ~800 modules; the static "
                    "`import-cycle-growth` gate does not model the implicit "
                    "edge to a parent package's `__init__`, so it cannot "
                    "see this class."
                ),
                reference="PR #1453",
            ),
        ),
    ),
)
