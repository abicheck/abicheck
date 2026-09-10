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


"""Bug-class registry entries, part 2 of 2.

The registry is one flat tuple assembled in :mod:`manifest`; it is split
across sibling modules for the same reason
``abicheck/model/change_catalog/kind_names_*.py`` is -- a registry whose
whole job is to grow one entry per closed bug class would otherwise carry a
single ever-growing literal past the repository's file-size gate, and
"trim it to fit" is exactly what ``architecture/debt.yaml``'s no-growth rule
forbids. Order is preserved across the parts, so ``BUG_CLASSES`` reads the
same as when it was one literal.

Part 2 holds the later half, and is where the most recently closed
classes live.

Add a new entry to whichever part is shorter at the time. Nothing else
imports these modules directly -- query the registry through
``manifest.get``/``manifest.BUG_CLASSES``.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["ENTRIES"]

ENTRIES: tuple[BugClass, ...] = (
    BugClass(
        id="adapter.duck_typed_view_attribute_drift",
        invariant=(
            "A duck-typed read-back adapter presented to a consuming "
            "function as a richer type (e.g. `_ReportChangeView` "
            '`cast("Change", ...)`-ed into `resolve_change_identity`) '
            "must expose every attribute that function actually reads on "
            "the real type -- not just the attributes it read when the "
            "adapter was written. Extending the consuming function's "
            "attribute surface without extending every such adapter in "
            "lockstep breaks it uniformly for any input at all, not a "
            "corner case: the function's own read is unconditional, so "
            "every call through the stale adapter raises the same "
            "`AttributeError` regardless of what the caller passed."
        ),
        fixed_by=(961,),
        seed_tests=("tests/test_report_change_view_entity_id.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "This class is pinned for exactly one adapter/consumer "
                    "pair (`_ReportChangeView` / `resolve_change_identity`). "
                    "No structural check (e.g. comparing the adapter "
                    "dataclass's fields against the consuming function's "
                    "actual attribute reads via AST analysis, the way "
                    "`scripts/fact_detector_misuse.py` does for a "
                    "different attribute-access pattern) enforces the "
                    "invariant generically across the codebase -- a future "
                    "sibling adapter drifting the same way would not be "
                    "caught until it also breaks every call through it."
                ),
                reference="https://github.com/abicheck/abicheck/pull/961",
            ),
        ),
    ),
    BugClass(
        id="serialization.str_enum_downcast_via_generic_rewrite",
        invariant=(
            "A generic tree-walking string-collect/rewrite primitive "
            "(`isinstance(value, str)`-gated) must never treat a "
            "`str`-subclass `Enum` field (e.g. `ParamKind(str, Enum)`) as "
            "ordinary rewritable free text -- even though isinstance() is "
            "true for it too -- because a real rewrite function (`re.sub`) "
            "returns a genuinely new, plain `str` object even on zero "
            "substitutions, silently downcasting the field's type while "
            "leaving its string value unchanged. The walk must exclude any "
            "`str`-subclass `Enum` member regardless of that enum's own "
            "vocabulary, not special-case the one field that happened to "
            "crash a caller."
        ),
        fixed_by=(985,),
        seed_tests=(
            "tests/test_param_kind_enum_identity.py",
            "tests/test_str_enum_downcast_walk.py",
        ),
    ),
    BugClass(
        id="tooling.platform_dependent_path_key",
        invariant=(
            "A repo-relative path computed to serve as a lookup/comparison "
            "key -- an allowlist entry, a cache key, anything compared "
            "against a hand-written or previously-persisted forward-slash "
            "string -- must render with `.as_posix()`, never bare "
            "`str(Path)`. `str()` of a relative `Path` renders with the "
            "*host's native* separator (backslash on Windows), so a key "
            "computed that way silently fails to match every "
            "forward-slash-spelled entry on Windows alone, in both "
            "directions: an already-reviewed, allowlisted call site reads "
            "as a fresh, unreviewed violation, and a genuinely live "
            "allowlist entry reads as stale. Distinct from the "
            "canonical-identity `environment_taint` shape this registry "
            "otherwise tracks -- this is a path-*rendering* bug, not an "
            "identity or environment-capture one, and it fails "
            "deterministically on every Windows run rather than "
            "intermittently."
        ),
        fixed_by=(995, 1004),
        seed_tests=("tests/test_fact_bridged_replace_guard.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "This class's fix and seed test are scoped to the one "
                    "file CI actually reported as broken "
                    "(`tests/test_fact_bridged_replace_guard.py`). No "
                    "repo-wide audit was run against the other AST-scan "
                    "gates that compute a similar path-shaped lookup key "
                    "(`scripts/check_ai_readiness.py`, "
                    "`scripts/fact_detector_misuse.py`/`fact_field_"
                    "readers.py` and their siblings) to confirm none of "
                    "them share the identical `str(path.relative_to(...))` "
                    "spelling -- a real, separate follow-up this class "
                    "does not yet close."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1004",
            ),
        ),
    ),
    BugClass(
        id="identity.platform_decorated_mangled_name",
        invariant=(
            "A signal derived from comparing a declaration's mangled "
            "spelling against its bare name (or matching a mangled string "
            "against an Itanium marker like `_Z`/`_ZL`/`_GLOBAL__N_`) must "
            "not assume the mangled spelling carries no platform-specific "
            "decoration. On a Darwin target, the linker convention "
            "prepends one leading underscore to *every* global symbol "
            "clang emits, mangled or not -- so `mangled == name` never "
            'holds for a genuinely plain-C/`extern "C"` declaration, and '
            "a real Itanium-mangled symbol reads as `__ZL...`/`__ZN...`, "
            "not `_ZL...`/`_ZN...`. Any call site deriving a linkage/"
            "locality/identity signal this way must normalize the extra "
            "underscore away first (or read an already-normalized "
            "upstream field, e.g. `Function.is_extern_c`) -- not only for "
            "the one call site a report named, but for every call site "
            "sharing the same `mangled == name` or bare-Itanium-prefix "
            "shape. This is not a new bug shape in this codebase: "
            "`model/mangled_name.py`'s `_itanium_strip_prefix` and "
            "`dumper_clang.py`'s `parse_variables`/`parse_functions` "
            "`is_extern_c` fallback already carry the identical fix for "
            "their own call sites -- `tu_merge.py`'s `_function_key`/"
            "`_variable_key`/`_has_local_linkage_mangling` (and their "
            "`extract/manifest_semantic_ir.py` mirrors, which may not "
            "import the root-level `tu_merge` module per ADR-061) simply "
            "hadn't been audited against it yet."
        ),
        fixed_by=(1048,),
        seed_tests=(
            "tests/test_tu_merge_darwin_linkage.py",
            "tests/test_manifest_semantic_ir_locality.py",
        ),
        # Both seed-test files build fake Function/Variable/TuFragment
        # objects reproducing the exact Darwin-decorated mangled-name
        # shapes a real clang parse reports (confirmed via this
        # codebase's own `dumper_clang.py`/`known-gaps.md` documentation
        # of the quirk) and call `tu_merge.merge_fragments`/
        # `manifest_semantic_ir.manifest_semantic_ir` directly -- neither
        # goes through a CLI/API entry point or a real clang invocation,
        # so `public_surfaces` stays empty and no `"frontend"`/`"platform"`
        # axis is claimed (this module's own docstring: an axis claim
        # requires exercising the real backend/target, not a hand-built
        # fixture standing in for one).
        known_gaps=(
            KnownGap(
                description=(
                    "Verified only at the primitive level against fake "
                    "Function/Variable/TuFragment objects built to the "
                    "documented Darwin mangled-name shape -- no macOS "
                    "toolchain was available to run this fix's own "
                    "`clang`-gated, self-skipping-on-Linux real-backend "
                    "tests (`tests/test_dumper_manifest_semantic_ir.py`, "
                    "`tests/test_tu_merge_variable_linkage.py`) against an "
                    "actual `--target=*-apple-darwin*` clang invocation, "
                    "only against the plain Linux target this sandbox's "
                    "installed clang defaults to. The real macOS CI run "
                    "on the PR that adds this entry is what closes that "
                    "gap; if it doesn't, this invariant's Darwin shape was "
                    "wrong somewhere the fake fixtures didn't catch."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1048",
            ),
            KnownGap(
                description=(
                    "The Itanium-marker-normalization half of this fix "
                    "(`tu_merge._has_local_linkage_mangling`/"
                    "`_looks_itanium_mangled` stripping one leading "
                    "underscore before the `_Z` check) is unconditional, "
                    "not gated on the target actually being Darwin -- "
                    "`extract/headers/clang/functions.py`'s own "
                    "`is_extern_c` determination gates its analogous "
                    "de-prefixed fallback on `is_darwin_target` precisely "
                    'because a real, explicit `asm("__ZL3foo")` label on '
                    "a non-Darwin target is a genuine, distinct mangled "
                    "identity, not decoration, and this fix's own "
                    "normalization has no target context available to "
                    "apply the identical gate. Such a symbol is "
                    "misclassified as local linkage. Left undone rather "
                    "than guess-fixed: closing it needs a target triple "
                    "threaded through every TuFragment/Function/Variable "
                    "reaching this module (none carry one today), and "
                    "even a target gate would not be fully sufficient "
                    "(the same asm-label shape is possible ON Darwin too) "
                    "-- see `tu_merge._has_local_linkage_mangling`'s own "
                    "docstring for the full account."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1048",
            ),
            KnownGap(
                description=(
                    "extract/manifest_semantic_ir.py's own per-fragment "
                    "locality classification (keying by each declaration's "
                    "source location within a colliding EntityId's bucket) "
                    "still cannot distinguish two colliding-EntityId "
                    "declarations that also share the identical source "
                    "location -- both on the same source line, or a nested "
                    "declaration clang omits line info for, falling back "
                    "to a bare filename that can coincide with another "
                    "declaration's own fallback. Deeper still: "
                    "extract.semantic_normalizer.normalize_header_ast "
                    "already keys its own raw occurrence dict by "
                    "OccurrenceId (entity_id + that same location string) "
                    "before this module's locality classification ever "
                    "runs, so a genuine same-location collision may "
                    "already have collapsed the two raw declarations into "
                    "one entry there -- data no per-location keying "
                    "downstream can recover. Closing this needs "
                    "declaration-level identity carried into normalize_"
                    "header_ast's own occurrence construction (an index or "
                    "similarly disambiguator-independent key), which "
                    "reaches a shared, foundational function well beyond "
                    "this module and every one of its other callers -- not "
                    "something to change speculatively without a real "
                    "toolchain to verify the combined result against. "
                    "Left as a tracked residual given how many independent "
                    "coincidences must align to reach it (a same-fragment "
                    "EntityId collision between a genuinely local and a "
                    "genuinely external declaration, AND those two "
                    "declarations additionally sharing one exact source "
                    "location)."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1048",
            ),
            KnownGap(
                description=(
                    "tu_merge._looks_itanium_mangled (and its extract/"
                    "manifest_semantic_ir.py mirror) cannot distinguish a "
                    'genuinely mangling-free `extern "C"` free '
                    "declaration whose literal identifier happens to "
                    "collide byte-for-byte with the Itanium local-linkage "
                    "grammar (e.g. named `_ZL3foo`) from a real "
                    "compiler-mangled local symbol -- both produce the "
                    "identical decorated string, and unlike the "
                    'class-nested-in-`extern "C"` case this guard was '
                    "built to catch (round four, above), there is no "
                    "structural difference in the string itself to key "
                    "on. Both cases share the identical `is_extern_c=True` "
                    "flag; only the nested-member case has that flag "
                    "wrongly inherited (dumper_clang.py's `_walk` "
                    "mis-propagates it into a nested CXXRecordDecl -- a "
                    "free, non-nested declaration gets no such incorrect "
                    "propagation), so telling the two apart needs a "
                    "caller-supplied record-membership signal independent "
                    "of `is_extern_c` itself. `entity_id.scope` cannot "
                    "supply it: `entity_id_for_function`/`entity_id_for_"
                    "variable` unconditionally erase scope for the "
                    "`is_extern_c` branch regardless of whether that flag "
                    "was correctly or wrongly set, so the information is "
                    "already lost by the time it reaches this module -- "
                    "closing this needs an upstream, cross-module change "
                    "to how Function/Variable/EntityId carry that bit, not "
                    "a fix this module can make on its own. Left as a "
                    "tracked residual: it requires an identifier "
                    'simultaneously (a) genuinely extern "C", (b) not a '
                    "class member, and (c) spelled to exactly collide "
                    "with the Itanium local-linkage grammar -- no real "
                    "codebase has been observed to use such a "
                    "deliberately reserved-looking name."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1048",
            ),
        ),
    ),
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
            "tests/test_disposition_state_sweep.py",
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
            "declaration text correlates."
        ),
        fixed_by=(1138,),
        seed_tests=(
            "tests/test_dumper_language_mode_export_evidence.py",
            "tests/test_crosscheck_language_mode_export_evidence.py",
        ),
        public_surfaces=("python-api", "cli"),
        axes={
            "frontend": ("castxml", "clang"),
            "declaration_shape": ("function", "ns_function", "extern_c", "variable"),
            "export_mangling": ("itanium", "macho_itanium", "msvc", "bare_c"),
            "excluded_text_source": ("comment", "string_literal", "if_zero_block"),
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
            "A bare re-probe of the SAME resolved `clang_bin` (keeping only its effective `--driver-mode=<value>` if any, `dumper._forwarded_driver_mode_token`) is tried under BOTH driver modes when the explicit-target recovery finds nothing -- a CL-style `-print-target-triple` is honored exactly like GNU's, so a target-prefixed CL-style name (e.g. `aarch64-apple-darwin-clang-cl`) reports its own prefixed default from the bare probe too, not just under GNU mode. Dropping the driver-mode token would silently revert a `clang-cl --driver-mode=g++` re-probe to CL mode, since the identical binary reports a different target bare vs. with that override: real evidence beats a static name-shape heuristic, since the earlier, option-bearing probe may have failed for a reason unrelated to `clang_bin`'s own identity. Only when that bare probe ALSO fails (GNU mode only) does `sys.platform` apply, gated on BOTH the RESOLVED `clang_bin` being the plain host default by invocation BASENAME (`dumper_clang._is_default_clang_bin`, version-suffix-stripped -- real Clang derives its own default target from argv[0], so basename -- not real identity, not raw spelling, and NOT merely differing from the plain name -- is what matters: an absolute path or a native version suffix still matches, a target-prefixed symlink correctly does not, and an arbitrary custom rename is answered by the bare re-probe, never by guessing it must be a cross-compiler) AND NOT `dumper_clang.clang_bin_is_explicitly_configured` (a `--compiler`/`--compiler-prefix` wrapper can coincidentally share the plain default's basename while genuinely not being it -- once BOTH probes fail for an explicitly-configured binary, that failure is itself evidence of an anomaly, not confirmation of a real native clang; this now ALSO suppresses the guess for a genuine absolute-path `--compiler` naming the real native clang, superseding this bug class's own earlier claim to the contrary). No fallback at all when no `@response-file`/`--config=<file>`/`--config-{user,system}-dir=<dir>` is forwarded AT ALL (`forwards_response_file`/`explicit_target_triple`'s shared `_opaque_option_source_tokens` void-back-to-None -- a config-*-dir points at a directory whose `clang.cfg` is loaded IMPLICITLY, no explicit `--config=` needed; real Clang scans the WHOLE arg list, response/config files included, before parsing even starts, so one could carry a later target OR retroactively flip CL-vs-GNU mode regardless of its position relative to a visible target). Shape check: `model.mangled_name.strip_macho_itanium_"
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
                    "still treated as CL; the `sys.platform` guess applying to an explicit cross-compiler unrelated to host OS; that same guess wrongly suppressed for a `gcc_path` `_resolve_clang_bin` itself ignores; an absolute-path spelling of the identical native binary failing string equality; a real-executable-identity fix wrongly equating a target-prefixed symlink with plain `clang`; a `@response-file`'s own hidden target being ignored; a native versioned "
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
        id="report.untrusted_value_escapes_its_cell",
        invariant=(
            "A value the report does not control — a detector's description, "
            "a demangled symbol, an extractor's error text, a file path — is "
            "data, never structure. Rendering it into a structured format "
            "(a Markdown table cell or code span today) must not let any "
            "character in it end a row, split the table, or close a span. "
            "The escaping rule has exactly one owner per format, shared by "
            "every renderer, and its contract is stated over generated "
            "adversarial values: a row assembled from n escaped cells always "
            "parses back to n cells, for any n and any values. The oracle "
            "counts separators off the rendered text and never re-runs the "
            "escaper, so a bug in the escaper cannot make the check agree "
            "with it."
        ),
        # #1185-era: `report/no_baseline.py`'s finding tables interpolated
        # `change.description`/`change.symbol` raw, and the only escaping
        # helper in the package was private to `report/comparison_scope.py`.
        # Sharing it exposed a hole that helper had shipped with for its
        # whole life: an odd-length run of backslashes immediately before a
        # pipe pairs off against the escape emitted for that pipe, leaving a
        # live cell separator. The generated-input property tests falsified
        # both the original rule and the first, too-narrow fix for it.
        fixed_by=(1185,),
        seed_tests=(
            "tests/test_markdown_cell.py",
            "tests/test_no_baseline_report_formats.py",
        ),
        public_surfaces=("cli",),
        axes={
            "hostile_character": (
                "pipe",
                "backslash",
                "backtick",
                "newline",
                "control",
            ),
            "renderer": ("comparison-scope-table", "audit-findings-table"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the Markdown renderers route through the shared "
                    "escaper. The HTML, SARIF, JUnit and text renderers each "
                    "carry their own quoting (or rely on a serializer's), and "
                    "no gate asserts that a renderer interpolating an "
                    "untrusted value uses an escaper at all — a new renderer "
                    "can still interpolate one raw."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="report.finding_entry_builder_parity",
        invariant=(
            "Every per-finding entry-builder for a shared `Change` "
            "(`compare`'s `changes[]`, `scan --against`'s baseline dicts, "
            "the release fan-out's capped `findings`) must resolve an "
            "audit field (e.g. `reclassified_by`) via one canonical helper "
            "-- never a sibling silently omitting a field another computes."
        ),
        fixed_by=(1176,),
        seed_tests=("tests/test_disposition_reclassification.py",),
    ),
)
