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
`docs/contribute/plans/bug-class-regression-testing.md`'s Phases 2-9, first
half. Split out of `manifest.py` to keep that file under its no-growth
baseline; the split follows source order and carries no taxonomy.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["ANALYSIS_1_BUG_CLASSES"]


ANALYSIS_1_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="extraction.ast_wrapper_chain_traversal",
        invariant=(
            "Extracting a semantic value (e.g. an enum constant, or "
            "whether an initializer is a bare literal) from a "
            "clang/castxml AST subtree gives the same answer regardless "
            "of which semantics-preserving wrapper nodes (implicit "
            "casts, parens, constant-folding wrappers) sit between the "
            "declaration and its value — and every independently-"
            "maintained copy of this same wrapper-descent primitive "
            "agrees with the others. Scoped deliberately: this does NOT "
            "extend to a non-literal initializer's own structural "
            "fingerprint, which is by design sensitive to the exact "
            "wrapper shape (a different cast/paren nesting hashes "
            "differently), not a residual gap of this invariant."
        ),
        fixed_by=(839,),
        seed_tests=(
            "tests/test_dumper_clang_enum_value_properties.py",
            "tests/test_ast_wrapper_chain_properties.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Both seed tests call the primitives directly on "
                    "hand-built AST-node dicts (via the shared "
                    "`tests/_wrapper_chain_gen.py` generator) — no real "
                    "clang invocation, no CLI/python-api surface — so this "
                    "is unit-level primitive coverage only, now widened "
                    "from one primitive (`dumper_clang._evaluated_int_"
                    "value`) to all three independently-written 'unwrap "
                    "until X' implementations "
                    "(`dumper_clang._evaluated_int_value`, "
                    "`dumper_clang_expr._unwrap_expr`/`_initializer_value`, "
                    "`buildsource.source_extractors.clang_nodes."
                    "_unwrap_expr`/`_expr_value`) plus a cross-module "
                    "`_WRAPPER_EXPR_KINDS`/`_unwrap_expr` agreement check "
                    "and a mutant-killing test reproducing the original "
                    "#839 bug — there is still no cross-surface "
                    "(CLI/python-api) or real-clang-backend test for this "
                    "class."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-2",
            ),
        ),
    ),
    BugClass(
        id="api.positional_slot_rebinding",
        invariant=(
            "The positional parameter order of a long-lived entry point -- a "
            "public dataclass's fields, a pipeline method's signature -- is "
            "append-only. Inserting a parameter or field mid-signature, where "
            "every trailing parameter already has a default, silently rebinds "
            "every existing positional caller one slot to the left: nothing "
            "raises, nothing fails to type-check, and the caller's argument "
            "lands on a different field. A new parameter is appended, or "
            "better, made keyword-only."
        ),
        fixed_by=(1231,),
        seed_tests=(
            "tests/test_positional_signature_stability.py",
            "tests/test_policy_experimental_namespaces.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Registered because two independent instances landed in "
                    "one change (PR #1231) and both were caught in review, "
                    "not by any test -- `PolicyFile` (an 8th positional "
                    "argument for `source_only_findings` binding to "
                    "`experimental_namespaces`, dropping the evidence policy) "
                    "and `PostProcessingPipeline.run` (a positional "
                    "`disposition_ledger` binding to `experimental_"
                    "namespaces`, disabling auditing and handing a "
                    "non-iterable ledger to a namespace consumer). Both "
                    "signatures already carried comments warning about "
                    "exactly this, so prose in the file demonstrably does not "
                    "prevent it. The seed test pins those two signatures' "
                    "whole positional order, which catches any future "
                    "mid-signature insertion into *them*. What it does not do "
                    "is enumerate every other long-lived entry point in the "
                    "codebase: there is no automatic discovery of "
                    '"signatures whose order is load-bearing", and a naive '
                    "sweep over every public callable would pin churn-prone "
                    "internal helpers and be deleted within a release. "
                    "Extending coverage means adding a signature to the seed "
                    "test when a call site is found to pass it positionally."
                ),
                reference="PR #1231",
            ),
        ),
    ),
    BugClass(
        id="detector.diff_confirmation_precondition",
        invariant=(
            "A finding whose kind asserts a *diff-confirmed* regression -- it "
            "is BREAKING, and its description states what will now fail -- is "
            "emitted only when the OLD side positively carries the state the "
            "regression is a loss of. Absence of the thing on the NEW side is "
            "half the claim; a detector that reports on the NEW side alone "
            "reports every always-absent input as a fresh regression. The "
            "precondition is a guard on emission, never one input among "
            "several to a suppression heuristic that an unrelated guard can "
            "keep from ever being consulted. Where the OLD side does not "
            "establish it, the observation is either suppressed or emitted "
            "under a kind that claims no diff confirmation (RISK), never "
            "under the confirmed one."
        ),
        fixed_by=(1283,),
        seed_tests=(
            "tests/test_bundle.py",
            "tests/test_bundle_self_compare_invariant.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Registered from a real false positive on Intel MKL: "
                    "`bundle_intra_dep_removed` reported 293 removals -- "
                    "`fflush`, `sincos`, `MPI_Finalize` -- for a conda-forge "
                    "2026.0.0 -> 2026.1.0 pair whose ground truth (nm) is +1 "
                    "symbol, -0, turning a compatible release into exit 4. "
                    "The detector did compute `ever_provided_in_bundle`, but "
                    "only read it inside an allow-list branch gated on a "
                    "non-empty `extra_needed`; every MKL library carries zero "
                    "DT_NEEDED, so the branch was never entered and the "
                    "precondition was never consulted. Nine of the kind's own "
                    "tests passed throughout, because each used "
                    "`compare_bundle(new, new, ...)` as shorthand for "
                    "'nothing exports this symbol' -- asserting that a bundle "
                    "compared against *itself* reports removals. Seeded with "
                    "a batched grid over DT_NEEDED shape x versioned x "
                    "allow-listed symbol name, in both directions (never "
                    "reported without an OLD provider; still reported with "
                    "one), the self-comparison metamorphic invariant, and an "
                    "end-to-end `-nostdlib` reproduction through the real "
                    "CLI. What is not covered: the other detectors whose "
                    "kinds carry the same diff-confirmed claim have not been "
                    "audited against this invariant one by one, and there is "
                    "no mechanical check that a BREAKING kind's emission site "
                    "reads an OLD-side fact at all."
                ),
                reference="PR #1283",
            ),
        ),
    ),
    BugClass(
        id="policy.public_surface_reachability",
        invariant=(
            "A declaration's public/private classification is a function "
            "of reachability from an explicit public root through the "
            "real include graph, language visibility, and export-table "
            "evidence — never directory containment or name-shape alone."
        ),
        fixed_by=(235, 834, 835, 843),
        seed_tests=(
            "tests/test_surface_property.py",
            "tests/test_surface_seed_predicate_properties.py",
            "tests/test_provenance_classification_properties.py",
            "tests/test_clang_castxml_origin_parity.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "The two pre-existing seed tests run on synthetic "
                    "snapshots built directly (no castxml/clang, no CLI). "
                    "Phase 3 (bug-class-regression-testing.md) added a "
                    "third seed test targeting the path/segment-based "
                    "'abicheck.extract.declaration_provenance' origin classifier "
                    "('classify_origin'/'_matches_public') with metamorphic "
                    "(checkout relocation, './..' spelling), independent-"
                    "oracle (real directory containment vs. a sibling-"
                    "directory string-prefix false positive), and mutant-"
                    "killing properties, plus a fourth seed test "
                    "(a new sibling module, 'test_clang_castxml_origin_"
                    "parity.py' — kept separate from 'test_clang_header_"
                    "backend_integration.py' rather than folded into it, "
                    "since that pre-existing module carries an ADR-061 "
                    "'debt.yaml' no-growth line-count baseline a new test "
                    "there would have breached) adding cross-backend "
                    "(castxml vs. direct-clang) agreement on PUBLIC_HEADER/"
                    "PRIVATE_HEADER origin for a mixed public+private "
                    "header pair, complementing the pre-existing plain "
                    "public-surface-parity test already in the older file. "
                    "Deliberately NOT attempted: the full generated "
                    "include-DAG model ('-I'/'-isystem'/'-idirafter' "
                    "resolution order, '#include' cycles, symlinked roots) "
                    "the plan document sketches for this phase — flagged "
                    "there as needing its own design review before "
                    "implementation, so building it inside the same PR "
                    "risked the under-designed, maintenance-burden outcome "
                    "the plan warns against. (An earlier draft of this "
                    "entry also claimed AGENTS.md's 'Known gaps' section "
                    "documents a direct-clang path-normalization gap and "
                    "nested/anonymous-namespace record gaps from PR #843; "
                    "that claim could not be verified — no matching text "
                    "was found there — so it is not repeated here.)"
                ),
                reference="PR #843",
            ),
        ),
    ),
    BugClass(
        id="identity.environment_taint",
        invariant=(
            "Canonical identity (finding IDs, type/function identity keys, "
            "node IDs) is a function of semantic scope and source identity "
            "— never of checkout root, absolute path spelling, "
            "temp-directory location, or unrelated line/column drift from "
            "an edit elsewhere in the file."
        ),
        fixed_by=(837, 843, 846, 868, 985),
        seed_tests=(
            "tests/test_castxml_anonymous_type_location.py",
            "tests/test_anon_type_location_properties.py",
            "tests/test_lambda_identity_ordinal.py",
            "tests/test_identity_taint_end_to_end.py",
            "tests/test_source_graph_directory_taint.py",
            "tests/test_family_f3_identity.py",
        ),
        axes={"frontend": ("clang", "castxml")},
        known_gaps=(
            KnownGap(
                description=(
                    "The L5 source graph's own node identities are not "
                    "renumbered alongside the flat snapshot's closure "
                    "markers (AGENTS.md, PR #868's own follow-up note). "
                    "Phase 4 (bug-class-regression-testing.md) added a "
                    "dedicated canary asserting this residual's own bound "
                    "in tests/test_lambda_identity_ordinal.py's "
                    "TestL5SourceGraphIdentitiesAreNotRenumbered — a real "
                    "L5 fix now fails this test loudly instead of the gap "
                    "silently closing or widening unnoticed."
                ),
                reference="PR #868",
                canary_test="tests/test_lambda_identity_ordinal.py",
            ),
            KnownGap(
                description=(
                    "The two original seed tests still feed a hand-built "
                    "AST-node/XML fragment into an internal parser class "
                    "directly. Phase 4 closed the real-subprocess/cross-"
                    "surface gap this entry originally flagged: a third "
                    "seed test, tests/test_identity_taint_end_to_end.py, "
                    "runs real g++ + direct-clang (plus one castxml-marked "
                    "cross-backend variant of the core #843 checkout-"
                    "relocation case) end to end through compare(), "
                    "covering checkout relocation, a symlinked root, "
                    "unrelated blank-line/comment drift, and declaration "
                    "reordering — each asserted NO_CHANGE — plus the "
                    "negative-control counterexamples from the using-"
                    "declaration known-gap entry below (two lambdas in one "
                    "header, two same-named nested records in different "
                    "namespaces), confirming they stay distinct across the "
                    "identical relocation. Two transformations from the "
                    "plan's own list are deliberately NOT attempted, and "
                    "recorded honestly rather than faked: Windows-style "
                    "path separators (no such filesystem in this sandbox "
                    "to produce a genuine backslash-separated compiler-"
                    "recorded path) and archive member order (a .a "
                    "static-archive concept; every extraction path this "
                    "class's own escape history and this suite exercise is "
                    "ELF .so-only). A compilation-database-root change is "
                    "real and reproducible but already covered by the "
                    "pre-existing L3-focused test_build_context_"
                    "completeness.py suite (test_dump_scan_l3_"
                    "comparability.py covered this too, until it was "
                    "deleted with the rest of `scan` in ADR-068 Phase 6), "
                    "so it was not re-derived here under a new name. "
                    "Still not through a real CLI invocation or "
                    "abicheck.service (the registry's own stricter bar for "
                    "the 'cli'/'python-api' public_surfaces tags) — only "
                    "direct abicheck.dumper.dump()/abicheck.checker."
                    "compare() calls — so public_surfaces stays empty per "
                    "this registry's own rule."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-4",
            ),
        ),
    ),
    BugClass(
        id="matching.dedup_key_soundness",
        invariant=(
            "For every key used in matching, grouping, or deduplication: "
            "totality (every producer-valid value yields a key), "
            "determinism, injectivity for semantically distinct findings "
            "(two unequal inputs describing different logical events don't "
            "collide — this is deliberately narrower than 'no two unequal "
            "values ever collide': a batch/library-level finding samples "
            "an arbitrary affected export as its 'spokesperson', so two "
            "Change instances differing only in which export was sampled "
            "describe the same logical event and must collide by design, "
            "per test_finding_identity_properties.py's own "
            "TestBatchShapedChangeIgnoresTheSample), and order-invariance "
            "for unordered inputs."
        ),
        fixed_by=(753, 759, 879, 905, 1125, 1693),
        seed_tests=(
            "tests/test_cross_tier_dedup_unhashable_value.py",
            "tests/test_finding_identity_properties.py",
            # A header-only inline removal is one observation at two tiers: the
            # header tier and the L4 replay both emit INLINE_FUNCTION_REMOVED
            # under different wording, so the exact key never collapsed them.
            # Pinned by name-level, one-for-one pairing across C and C++ shapes.
            "tests/test_inline_removal_dedup.py",
            "tests/test_inline_removal_source_replay_e2e.py",
            # A removed or retargeted typedef is one observation at two tiers:
            # the header tier's TYPEDEF_* and the L4 replay's PUBLIC_TYPEDEF_*
            # differ in kind, so no exact key pairs them. Pinned by one-for-one
            # qualified-name pairing across C and C++ (nested namespace included).
            "tests/test_typedef_dedup.py",
            "tests/test_typedef_source_replay_e2e.py",
            # A namespace-scope constexpr value change is one observation at two
            # tiers: CONSTANT_CHANGED (header) and CONSTEXPR_VALUE_CHANGED (L4
            # replay). Same name-level, one-for-one pairing as the inline pair.
            "tests/test_constexpr_constant_dedup.py",
            "tests/test_constexpr_constant_source_replay_e2e.py",
            "tests/unit/compare/test_dedup_key.py",
            "tests/test_diff_namespaces.py",
            # workflows/cross_source_hygiene.py's own per-side OLD/NEW
            # lookup was keyed by a bare `Change.symbol` -- non-injective
            # for `private_header_leak`, whose findings are keyed by
            # `(mangled_or_name, leaked_type)` in cross_source_checks.py, so one
            # function leaking two distinct private types silently
            # collided into one `Change`. Fixed by generalizing to a
            # per-check identity function (`_IDENTITY_FUNCS`), defaulting
            # to `symbol` (still correct for `unversioned_exported_symbol`)
            # and registering `(symbol, new_value)` for `private_header_leak`.
            "tests/test_cross_source_evolution.py",
            # Graph identity reconciliation picked a redeclared entity's
            # declaring file by edge order (last SOURCE_DECLARES edge wins),
            # so one file set read as unchanged or as a RISK-tier move
            # depending on run; pinned under permuted edges/inputs and
            # several PYTHONHASHSEED values.
            "tests/test_graph_reconcile_order_invariance.py",
        ),
        public_surfaces=("cli",),
        # Phase 5's own two originally-tracked gaps here (a compare()-level
        # collision test, and an adversarial generator over the shapes
        # #879's post-mortem named) are now closed -- see PR #905 for the
        # full account, including the real `cross_tier_transition` crash
        # that building the compare()-level test's proper engine-level
        # counterpart surfaced and fixed. `KnownGap` records a residual the
        # current tests deliberately do NOT close (see its own docstring),
        # so closed work does not get an entry here -- it lives in `fixed_by`
        # and the PR/commit history instead (Codex review, PR #905: an
        # earlier revision kept "what got closed" narratives in this tuple,
        # which made the registry read as if genuinely open work remained
        # where none did).
        known_gaps=(
            KnownGap(
                description=(
                    "No seed test reaches a real public surface with the "
                    "list-valued value-slot collision shape: the typedef "
                    "CLI test (`test_typedef_source_replay_e2e.py`) drives "
                    "`compare` through `CliRunner`, but on typedef findings "
                    "only. The `PYTHON_STABLE_ABI_VIOLATION` case still "
                    "needs a `compare`/`scan` invocation proving its "
                    "collision survives into the reported output, not just "
                    "`checker.compare()`'s return value (Codex review, PR "
                    "#905)."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-5",
            ),
            KnownGap(
                description=(
                    "Phase 5's own text also asks for the 'constant/type "
                    "identity fallbacks' AGENTS.md's 'Primitive-level "
                    "property tests' section names — constants' value-"
                    "equality identity and types' structural-fingerprint-"
                    "then-source_location identity — to have their pairs "
                    "promoted into a fixed corpus the same way "
                    "`_paired_stable_indices`'s were. Deliberately NOT "
                    "attempted: both are already-documented, twice-"
                    "falsified-and-accepted heuristics (see "
                    "`_type_index_items`'s and `_diff_constants`'s own "
                    "docstrings, and this repo's own "
                    "'attempted twice, reverted twice' rule) with no "
                    "single correct behavior to state as an invariant — a "
                    "property test for either would have to pin the "
                    "CURRENT accepted collision shape as its own bound "
                    "(the same design `TestL5SourceGraphIdentitiesAreNot"
                    "Renumbered` in `tests/test_lambda_identity_ordinal.py` "
                    "uses for a different residual), which is a real, "
                    "separate design task rather than a follow-up to this "
                    "PR's registry/generator-registration work."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-5",
            ),
        ),
    ),
    BugClass(
        id="storage.third_party_contract_at_scale",
        invariant=(
            "read(write(x)) == x at realistic production scale for every "
            "supported storage algorithm — not only a toy-scale, "
            "highly-compressible fixture whose actual required parameters "
            "never approach the boundary being defended."
        ),
        fixed_by=(699, 721, 911),
        seed_tests=(
            "tests/test_snapshot_compression.py",
            "tests/test_snapshot_compression_public_api_scale.py",
        ),
        public_surfaces=("python-api", "cli"),
        axes={"algorithm": ("zstd", "gzip")},
        known_gaps=(
            KnownGap(
                description=(
                    "No archive/bundle-reader (`workflows/dump/cache/`, "
                    "the G40 bundle-facts archive path) or python-api/"
                    "CLI-level round trip has yet been generalized to "
                    "the same production scale for a mixed-container "
                    "payload — only the flat AbiSnapshot storage "
                    "envelope has a scale-realistic seed test at every "
                    "layer."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="storage.out_of_band_snapshot_reader_envelope_drift",
        invariant=(
            "A consumer outside `abicheck/` that hand-parses a snapshot "
            "document written by `abicheck dump` must answer identically "
            "for the sectioned envelope and the equivalent flat document. "
            "Indexing a formerly-top-level key (`build_source`, "
            "`dump_provenance`, `declarations`, ...) without unwrapping the "
            "ADR-063 Phase 8 envelope first reads `None` for every real "
            "dump output, and nothing distinguishes that from the field "
            "genuinely being absent -- so the failure is silent wherever "
            "absence is a legitimate answer. The in-tree readers go "
            "through `snapshot_from_dict`, which accepts either shape; "
            "only the out-of-band ones can drift, and they drift at the "
            "moment the envelope changes, not at the moment they are "
            "written."
        ),
        fixed_by=(1225, 1463),
        seed_tests=(
            "tests/test_snapshot_envelope_out_of_band_readers.py",
            # The field-eval runner's reader, against a snapshot written by
            # save_snapshot itself (the hand-built fixtures it replaced
            # encoded the same stale flat shape the reader did).
            "tests/test_eval_runner.py",
        ),
        # `()` deliberately: the seed test calls the reader helpers directly
        # (importlib-loaded modules, no CliRunner, no Action step), so it
        # reaches neither surface. This field documents what a seed test
        # actually exercises -- a claimed surface it does not reach conceals
        # the very cross-surface gap a contributor should find here
        # (CodeRabbit review; the rule is this schema's own, from PR #885).
        public_surfaces=(),
        axes={
            "encoding": ("flat", "sectioned"),
            "embedded_layers": ("none", "L3", "L4", "L5", "combinations"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The behavioural half covers the three readers known "
                    "today and only the `build_source` key in depth; the "
                    "other keys the envelope moved (`dump_provenance`, "
                    "`declarations`, `types`, `enums`) are covered by the "
                    "structural AST scan alone, which proves an unwrap is "
                    "*mentioned* in the reading function, not that it is "
                    "applied to the right value on every path. A reader "
                    "that unwraps one document and then indexes a second, "
                    "un-unwrapped one in the same function passes the scan. "
                    "The scan reaches one hop: a same-module caller passing "
                    "a raw `json.load(s)` result (directly or through a local "
                    "name) into a parameter the callee indexes a moved key on "
                    "is flagged -- the shape that left the field-eval "
                    "runner's source tier reading 0 compile units (#1463). A "
                    "document that crosses a module boundary, travels "
                    "through an attribute or container, or passes through "
                    "more than one call is still not followed."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
            KnownGap(
                description=(
                    "The scan covers `actions/`, `contrib/`, `scripts/`, "
                    "`skills-src/evaluation/validation/` and `tests/` -- first-party trees only. "
                    "A downstream consumer (a user's CI script, a "
                    "third-party integration) reading `build_source` off a "
                    "dump-written baseline has the identical bug and "
                    "nothing here can see it; the durable fix for that "
                    "population is a documented, supported reader entry "
                    "point rather than a scan."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="extract.aggregate_layout_header_attribution",
        invariant=(
            "When one header in a multi-header aggregate parse fails, the "
            "unparseable-header fallback must drop exactly the header whose "
            "include chain raised the error, for any aggregate layout. The "
            "header is the file the aggregate included at the failing frame "
            "(the chain's next location), never one derived from the "
            "frame's line number: castxml's aggregate gained a leading "
            "compatibility-preamble include, which silently shifted the "
            "'header i is on line i+1' arithmetic by one, so the fallback "
            "dropped a parseable header, kept the broken one, and the dump "
            "failed on every platform. An error reached only through the "
            "preamble names no listed header and drops nothing."
        ),
        fixed_by=(1463,),
        seed_tests=(
            "tests/test_unparseable_header_fallback.py",
            "tests/test_family_f3_identity.py",
        ),
        public_surfaces=(),
        axes={
            "preamble_lines": ("0", "1", "3"),
            "chain_depth": ("0", "1-4"),
            "frame_style": ("clang", "gcc"),
        },
    ),
    BugClass(
        id="registry.kind_completeness",
        invariant=(
            "Every declared ChangeKind/evidence-kind/provider is accounted "
            "for by every total downstream consumer — bidirectionally: "
            "every kind has a mapping, and every mapping key names an "
            "existing kind."
        ),
        fixed_by=(753, 759, 932),
        seed_tests=(
            "tests/test_canonical_finding_id_completeness.py",
            "tests/test_report_classifications_unit.py",
            "tests/test_catalog_rule_registry.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "canonical_finding_id's classification and "
                    "report_classifications.py's seven hand-maintained "
                    "kind-keyed frozensets are now reverse-completeness "
                    "checked (every member names a live ChangeKind, with a "
                    "mutation check proving the assertion actually fails on "
                    "a corrupted set) — but the same totality property is "
                    "not yet generalized to every other kind-keyed registry "
                    "named in Phase 9 of the plan (evidence kinds, "
                    "providers, other report renderers)."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
            KnownGap(
                description=(
                    "The seed tests call `finding_identity."
                    "report_canonical_finding_id`/`report_classifications` "
                    "helpers directly — no CLI, no python-api — so nothing "
                    "here proves a real registry omission reaches a "
                    "`compare()` report."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
        ),
    ),
    # `evidence.tier_shortcut_without_substitute` (the DWARF-vs-header-AST
    # cost-shortcut class fixed_by=(1186,)) was retired here, not
    # retargeted, when ADR-068 Phase 6 deleted `scan` outright: its only two
    # seed tests (`tests/test_scan_depth_evidence_shortcut.py`,
    # `tests/test_scan_compare_parity.py`) exercised
    # `cli_scan_helpers._uses_debug_presence_only` -- the scan-only decision
    # function that computed *when* to set `dumper`'s `debug_presence_only`
    # shortcut from scan's own depth/headers logic -- which was deleted with
    # the rest of `cli_scan_helpers.py`. `debug_presence_only` itself is
    # still a real, plumbed-through parameter
    # (`dumper.py`/`workflows/dump/cache/api.py`/`workflows/dump/native.py`/
    # `dumper_layout_backfill.py`/`workflows/input_resolution.py`), but a
    # repo-wide audit at retirement time found no remaining production call
    # site that ever passes `debug_presence_only=True` -- every live caller
    # forwards the parameter at its default `False`, only tests set it
    # directly. So the class this entry protected (a shortcut silently
    # starving a run of evidence with no substitute present) currently has
    # no reachable trigger from any command, and per this module's own
    # schema docstring ("a class with no test is a 'Known gaps' AGENTS.md
    # paragraph, not a registry entry") it does not belong here with an
    # empty `seed_tests`. See `docs/contribute/known-gaps.md`'s
    # "`compare --depth binary` still performs a deep DWARF type walk the
    # public evidence-depth contract says that rung skips" entry (reopened
    # 2026-09-11 -- an earlier pass at this PR incorrectly marked it CLOSED
    # on the theory that retiring `scan` mooted it; it did not, since
    # `scan` was only this entry's oracle, not the bug) for the retirement
    # note on `debug_presence_only` specifically -- including what would
    # need to be true (a real caller computing `debug_presence_only` from a
    # depth/collect-mode decision again) for *that* mechanism to become a
    # live registry entry once more, retargeted rather than restored
    # verbatim, since the scan-specific invariant text above would need to
    # be re-stated against whatever new caller reintroduces the shortcut.
    # The `--depth binary` DWARF-type-walk defect itself is unrelated to
    # `debug_presence_only` and remains open, independent of this class.
    BugClass(
        id="evidence.silent_degradation_to_clean_verdict",
        invariant=(
            "Missing, rejected, ignored, or malformed evidence can never "
            "become a clean compatibility result without explicit, "
            "policy-visible degradation — analysis status, report "
            "content, verdict, gate decision, exit code, and aggregate "
            "result must agree, checked independently, not all derived "
            "from one production helper."
        ),
        fixed_by=(834, 838, 860, 883, 932),
        seed_tests=(
            "tests/test_fact_conservation_properties.py",
            "tests/test_bundle_side_input.py",
            "tests/test_contract_adaptive_default_fallback.py",
            # ADR-049 Phase 7's evidence-adaptive default: a finding no
            # domain could place (an ELF version node; an undeclared
            # `_ZTV`/`_ZTI` export under `-H`; a `--sources`/`--build-info`
            # finding under an `exports` default) read UNKNOWN_UNRESOLVED
            # and stopped gating.
            "tests/test_contract_adaptive_default_monotone.py",
            "tests/test_export_reconciliation_and_obligations.py",
            # Lane C stage 5: a recognised consumer binary whose import table
            # failed to parse read as "requires nothing" -> NO_CHANGE/100%.
            "tests/unit/workflows/test_failed_consumer_read.py",
            # Lane C stage 6: a library whose export table was not read
            # (parse failure, skipped .dynsym, parse-failed stored block)
            # read as "exports nothing" -> every required symbol "missing".
            "tests/unit/extract/test_library_export_read_failures.py",
            # An ELF/PE/Mach-O whose export table could not be read (zeroed
            # section table, no .dynsym, unnamed exports) dumped to an empty
            # surface: a compare against it read every export as added, or
            # COMPATIBLE / NO_CHANGE with exit 0. Refused at the dump, across
            # the CLI, for OLD, NEW and both sides.
            "tests/test_export_table_unreadable_matrix.py",
            # A clang AST whose `inner` is a number, string, mapping or null
            # (or holds non-object children) read as "call graph ran, found
            # nothing": the readers treat a wrong-typed container as empty.
            # Refused as a failure at the projection, the L5 fold and the L4 extractor.
            "tests/test_header_graph_projection_cache.py",
            "tests/test_source_extractors_clang_ast_shape.py",
            "tests/test_field_added_unknown_polymorphism.py",  # vtables unseen (PDB UNSUPPORTED/FAILED/empty PARTIAL) read as non-polymorphic -> appended field COMPATIBLE
            # Field report H5: exports whose declarations an undefined #ifdef
            # (SYCL_LANGUAGE_VERSION) hid from the header parse vanished with
            # no count; now counted per side, sycl:: as a named cause.
            # ADR-085 D3: a template-default change whose default no backend
            # recorded (castxml; an unspellable clang function-template default;
            # a clang alias or variable template) read complete NO_CHANGE; now
            # partial with the gap named.
            "tests/test_template_default_assurance_gap.py",
            "tests/test_template_default_header_scan.py",
            "tests/test_undeclared_export_census.py",
            "tests/test_undeclared_sycl_census_integration.py",
            "tests/test_sycl_stub_parse_integration.py",
            "tests/test_field_shapes.py",
            # A development package given to a single-pair compare is named,
            # not dropped; a header set that declared nothing is reduced in the
            # evidence-coverage table, not shown as high-confidence evidence.
            "tests/test_cli_single_pair_devel_package_warning.py",
            "tests/test_cli_header_input_text_and_empty_surface.py",
            "tests/test_empty_header_surface_counts.py",
        ),
        # Only the consumer-scoping and CLI header-input seeds reach a public surface: their integration tests drive `abicheck compare` end to end.
        public_surfaces=("cli",),
        known_gaps=(
            KnownGap(
                description=(
                    "The fact-conservation suite covers a selected "
                    "detector-family subset today, not every ChangeKind "
                    "family and evidence-completeness failure mode named "
                    "in `docs/contribute/known-gaps.md`."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
            KnownGap(
                description=(
                    "Most seed tests call `abicheck.checker.compare` "
                    "directly on hand-built snapshots — real detection "
                    "logic, but no CLI/python-api/exit-code layer. Only "
                    "`test_failed_consumer_read.py` and "
                    "`test_library_export_read_failures.py` drive the CLI "
                    "and check an exit code, and they do not cross-check "
                    "report/gate/exit-code agreement (the second half of "
                    "this class's own invariant) independently. Incident "
                    "#883's own gap — a dropped `policy_file` reaching "
                    "silently unverified through a mocked "
                    "`compare_snapshots` — is now closed independently by "
                    "`test_bundle_side_input.py`'s "
                    "`test_policy_file_override_genuinely_demotes_a_real_"
                    "verdict`, which runs the real, unmocked "
                    "`compare_snapshots` and asserts the returned verdict "
                    "is actually demoted (a mutation check: reverting the "
                    "production fix fails this test)."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
        ),
    ),
    BugClass(
        id="scoping.aggregate_view_starvation",
        invariant=(
            "A detector that reasons across multiple already-scoped "
            "evidence views (e.g. a cross-library bundle check reading "
            "each library's own public-surface-scoped `DiffResult`) must "
            "not silently starve on a real change merely because an "
            "earlier, independent scoping decision (public-surface "
            "filtering, suppression) demoted it out of the one view the "
            "detector happened to read — either the detector consults the "
            "unscoped/recorded-but-demoted evidence its own contract "
            "actually needs, or the scoping gap is documented as a known, "
            "accepted limitation, never left silent."
        ),
        fixed_by=(896,),
        seed_tests=("tests/test_bundle_diff_derived_scoping.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The fix reads `DiffResult.out_of_surface_changes` "
                    "(demoted-by-scoping evidence), which itself never "
                    "passes through `ApplySuppression` — a change a user's "
                    "own suppression rule targets can still starve the "
                    "bundle detectors reading it, since suppression only "
                    "ever runs on the in-surface `changes` list "
                    "(`docs/use/multi-binary.md`, G38 Phase 14 notes)."
                ),
                reference="docs/contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md#phase-14",
            ),
        ),
    ),
    BugClass(
        id="extraction.implicit_declaration_leaks_into_surface",
        invariant=(
            "A header-AST backend's own compiler-synthesized declaration "
            "(an implicit default/copy/move constructor, destructor, or "
            "copy/move `operator=` the user never wrote) is treated as "
            "reachable public/exported API by a downstream consumer only "
            "when it matches a genuine, ODR-used export in the real export "
            "table — never merely because the source declares it, "
            "regardless of the declaration's origin or access. An "
            "unresolved export table (not yet known, as opposed to "
            "genuinely empty) keeps every such candidate, deferring the "
            "drop until authoritative matching can actually run."
        ),
        fixed_by=(920,),
        seed_tests=(
            "tests/test_castxml_compiler_generated.py",
            "tests/test_dumper_clang_compiler_generated.py",
            "tests/test_serialization_function_compiler_generated.py",
            "tests/test_castxml_l4_phantom_members.py",
        ),
        public_surfaces=(),
        axes={"frontend": ("castxml",)},
        known_gaps=(
            KnownGap(
                description=(
                    'Only castxml\'s own `artificial="1"` marker is read '
                    "(`Function.is_compiler_generated`); the direct-clang "
                    "L2 backend never emits an implicit declaration as a "
                    "`Function` at all (its AST walk skips `isImplicit` "
                    "nodes outright), so it needs no equivalent per-node "
                    "signal and is covered structurally rather than by a "
                    "real clang-invoking seed test for this axis. If a "
                    "future clang change ever started emitting an "
                    "implicit node, this class's clang-side seed test "
                    "(a hand-built AST dict, not a real clang subprocess) "
                    "would not by itself catch it."
                ),
                reference="tests/test_dumper_clang_compiler_generated.py::"
                "test_parse_functions_skips_implicit_declarations_entirely",
            ),
        ),
    ),
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
)
