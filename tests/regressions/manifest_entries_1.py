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


"""Bug-class registry entries, part 1 of 2.

The registry is one flat tuple assembled in :mod:`manifest`; it is split
across sibling modules for the same reason
``abicheck/model/change_catalog/kind_names_*.py`` is -- a registry whose
whole job is to grow one entry per closed bug class would otherwise carry a
single ever-growing literal past the repository's file-size gate, and
"trim it to fit" is exactly what ``architecture/debt.yaml``'s no-growth rule
forbids. Order is preserved across the parts, so ``BUG_CLASSES`` reads the
same as when it was one literal.

Part 1 holds the earlier half of the registry's declaration order.

Add a new entry to whichever part is shorter at the time. Nothing else
imports these modules directly -- query the registry through
``manifest.get``/``manifest.BUG_CLASSES``.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["ENTRIES"]

ENTRIES: tuple[BugClass, ...] = (
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
                    "'abicheck.provenance' origin classifier "
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
                    "completeness.py/test_dump_scan_l3_comparability.py "
                    "suites, so it was not re-derived here under a new "
                    "name. Still not through a real CLI invocation or "
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
        fixed_by=(753, 759, 879, 905, 1125),
        seed_tests=(
            "tests/test_cross_tier_dedup_unhashable_value.py",
            "tests/test_finding_identity_properties.py",
            "tests/unit/compare/test_dedup_key.py",
            "tests/test_diff_namespaces.py",
            # workflows/cross_source_evolution.py's own per-side OLD/NEW
            # lookup was keyed by a bare `Change.symbol` -- non-injective
            # for `private_header_leak`, whose findings are keyed by
            # `(mangled_or_name, leaked_type)` in cross_source_checks.py, so one
            # function leaking two distinct private types silently
            # collided into one `Change`. Fixed by generalizing to a
            # per-check identity function (`_IDENTITY_FUNCS`), defaulting
            # to `symbol` (still correct for `unversioned_exported_symbol`)
            # and registering `(symbol, new_value)` for `private_header_leak`.
            "tests/test_cross_source_evolution.py",
        ),
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
                    "No seed test for this class reaches a real public "
                    "surface (a Click invocation or a call through "
                    "`abicheck.service`, per `BugClass.public_surfaces`'s "
                    "own contract) with a value-slot collision shape — "
                    "every one of this entry's `seed_tests` calls "
                    "`abicheck.checker`/`abicheck.compare.dedup_key`/"
                    "`abicheck.diff_helpers`/`abicheck.diff_filtering`/"
                    "`abicheck.diff_namespaces` directly, which this "
                    "registry treats as internal regardless of how "
                    "thorough the coverage is at that layer. Not attempted "
                    "in Phase 5: a CLI (`CliRunner`) or `abicheck.service` "
                    "-level test proving the same list-valued "
                    "`PYTHON_STABLE_ABI_VIOLATION` collision case survives "
                    "into a real `compare`/`scan` invocation's reported "
                    "output, not just `checker.compare()`'s return value "
                    "(Codex review, PR #905)."
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
        id="config.propagation_completeness",
        invariant=(
            "An accepted configuration value either reaches every "
            "relevant consumer with identical semantics, or is rejected "
            "at the public boundary — no third state."
        ),
        fixed_by=(860, 883, 886, 906),
        seed_tests=(
            "tests/test_run_plan.py",
            "tests/test_run_plan_consumer_compile_active.py",
            "tests/test_project_targets_consumer_compile.py",
            "tests/test_cli_compare_release_bundle_signature_wiring.py",
            "tests/test_reusable_workflows_project_evidence.py",
            "tests/test_action_compile_context_parity.py",
            "tests/test_gha_expr.py",
            "tests/test_consumer_compile_full_chain_propagation.py",
            "tests/test_explicit_source_extractor_propagation.py",
            "tests/test_dump_scan_l3_comparability.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Still open: (a) this closed chain reaches config -> "
                    "generate_run_plan() -> the composite-Action/reusable-"
                    "workflow path only — no seed test drives the same "
                    "consumer_compile value through the native Python API "
                    "or a `project`/`aggregate` CLI invocation end to end; "
                    "(b) Phase 6 names eight other configurable concerns "
                    "(policy/policy-file, frontend/compiler as a general "
                    "per-entry-point concern beyond this one profile field, "
                    "include roots, evidence-pack/target attribution, "
                    "safety budgets, suppression/filtering, per-library "
                    "override, output/report options) — of these only the "
                    "frontend concern has since had any of this treatment, "
                    "and only for one chain: `--ast-frontend` -> the L4 "
                    "source-ABI replay backend, where `scan` accepted the "
                    "value and then ignored it "
                    "(tests/test_explicit_source_extractor_propagation.py, "
                    "exhaustive over the frontend x env domain, grounded in "
                    "_make_source_extractor, with the end-to-end half in "
                    "tests/test_dump_scan_l3_comparability.py). The same "
                    "concern's *other* consumers (the L2 header parse, the "
                    "preprocessor/pattern pre-scans) are untouched, and one "
                    "frontend divergence is deliberately still open rather "
                    "than closed: an UNFLAGGED `auto` resolves to clang for "
                    "`scan` and castxml for `dump`/`compare`, which is a "
                    "real default change to make deliberately (the CLI "
                    "cleanup phase-two plan's PR 3A item 2), not a "
                    "propagation bug to patch. consumer_compile was chosen "
                    "as the first "
                    "worked example specifically because #860/#883's own "
                    "history and this class's pre-existing seed tests "
                    "already pointed at it, not because it's necessarily "
                    "representative of the others' own chain shapes."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-6",
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
                    "No archive/bundle-reader (`snapshot_cache.py`, "
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
        id="storage.short_decode_mistaken_for_complete_decode",
        invariant=(
            "A bounded read over a compressed/streamed source must never "
            'treat "the decoder returned without raising" as "the '
            'decode is complete": a stream cut at an arbitrary raw-byte '
            "boundary yields a *short* result -- commonly zero bytes -- "
            "with no exception at all, so a result shorter than the "
            "requested length is a truncation signal that must escalate "
            "the raw read exactly as a raised exception does, unless the "
            "underlying source is already exhausted. Concretely: "
            "bounded_decoded_prefix(p, n) == read_snapshot_bytes(p)[:n] "
            "for every valid storage envelope whose first n decoded bytes "
            "are reachable within the reader's own raw-input budget, at "
            "every compression ratio; past that budget the answer is None "
            '("no prefix within budget"), never a short value that would '
            "read as the real prefix."
        ),
        fixed_by=(1164,),
        seed_tests=("tests/test_bounded_decoded_prefix_properties.py",),
        public_surfaces=("python-api",),
        axes={
            "algorithm": ("zstd", "gzip"),
            "compression_ratio": ("low", "high"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    'The same "short read is not EOF" shape exists in '
                    "every other bounded/streamed reader in the tree "
                    "(`workflows/bundle_compare_operand.py`'s own larger-"
                    "window probe, `snapshot_cache.py`'s archive reader, "
                    "the DWARF/PE section readers). Only the snapshot "
                    "storage envelope's prefix primitive has a "
                    "generalized suite; the siblings are untested against "
                    "this invariant. Separately, the bounded reader's own "
                    "guarantee stops at its raw-input cap: an envelope can "
                    "place arbitrarily much stored input before its n-th "
                    "decoded byte (a tiny data frame, a megabyte skippable "
                    "frame, then the payload), and reading far enough to "
                    "decode it is the whole-file decompression the bounded "
                    "reader exists to avoid. That case answers None by "
                    "design and is pinned by a test, but it is a real "
                    "narrowing of the invariant above, not a closed case."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="trust_boundary.shell_workflow_injection",
        invariant=(
            "Every scalar input to a shell script or composite-Action "
            "step arrives as exactly one argument with exactly the same "
            "bytes, and untrusted data cannot create additional commands, "
            "$GITHUB_OUTPUT records, paths, or side effects."
        ),
        fixed_by=(705, 758, 836, 919, 1165),
        seed_tests=(
            "tests/test_reusable_workflow_execution.py",
            "tests/test_check_project_workflow_execution.py",
            "tests/test_action_run_sh_helpers.py",
            # PR #1165: `action/validate-inputs.sh`'s own annotation
            # emitters. Every message there interpolates a
            # workflow-controlled INPUT_* value into a line-delimited
            # GitHub annotation, so a value carrying a newline forged an
            # `::error::`/`::set-output::` command of its own -- a live
            # defect on the pre-existing `build-info` site, not only on
            # the tombstones the same PR added. Fixed once in the shared
            # `_warn`/`_fail` helpers.
            "tests/test_action_validate_inputs.py",
        ),
        public_surfaces=("github-action",),
        axes={
            "adversarial-shape": (
                "path-traversal",
                "shell-metacharacters",
                "command-substitution",
                "spaces",
                "tab",
                "leading-dash-flag-shaped",
                "multiple-flags-shaped",
                "quotes",
                "redirects",
                "newline-record-injection",
                "non-ascii",
                "empty-string",
            )
        },
        known_gaps=(
            KnownGap(
                description=(
                    "`action/run.sh` emits its own annotations with "
                    'inline `echo "::error::..."` calls rather than '
                    "through a shared helper, and several interpolate an "
                    "INPUT_* value the same way validate-inputs.sh did. "
                    "PR #1165 fixed validate-inputs.sh at its two "
                    "emitters (covering every site in that file at once) "
                    "but deliberately did not widen into run.sh's ~20 "
                    "inline sites, which need their own pass and their "
                    "own executing corpus."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "The hostile-input execution corpus (shared via "
                    "`_workflow_exec.HOSTILE_SCALAR_CORPUS`, Phase 8) now "
                    "covers two independently-maintained real sanitizer "
                    "copies (`check-single.yml`/`check-project.yml`) plus "
                    "`action/run.sh`'s word-splitting-sensitive `add_flag`/"
                    "`add_sided_flag` helpers — not every scalar input "
                    "across the repository's other shell scripts and "
                    "composite-action steps (e.g. the other workflows' "
                    "`run:` steps enumerated in the plan's own target-"
                    "script inventory), which is still the full scope "
                    "Phase 8's invariant is stated over."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-8",
            ),
        ),
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
        ),
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
                    "The seed test calls `abicheck.checker.compare` "
                    "directly on hand-built snapshots — real detection "
                    "logic, but no CLI/python-api/exit-code layer, so "
                    "report/gate/exit-code agreement (the second half of "
                    "this class's own invariant) is untested. Incident "
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
        id="ci.unrelated_apt_source_gates_the_job",
        invariant=(
            "A CI lane never fails because of a package repository none "
            "of its packages come from. `apt-get update` fails as a whole "
            "when any configured source fails -- including the "
            "third-party vendor repositories pre-baked into GitHub's "
            "runner images -- so its exit status must never gate a step; "
            "`apt-get install` is the gate, and a post-install check "
            "confirms the packages are actually present so relaxing the "
            "first gate cannot turn into a silent false success."
        ),
        fixed_by=(1182,),
        seed_tests=("tests/test_apt_install_hardening.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The workflow-wide half of the invariant is "
                    "structural (parsed YAML), not executed: a step's own "
                    "wiring needs a real GitHub Actions runner. The "
                    "script's behaviour is executed against a simulated "
                    "apt; that a given lane calls the script is asserted "
                    "over the workflow files only. A lane could still "
                    "install packages through a mechanism this scan does "
                    "not model (a Makefile, a setup script it invokes)."
                ),
                reference="tests/test_apt_install_hardening.py",
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
)
