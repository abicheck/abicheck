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

"""The bug-class regression registry (Phase 1 of
``docs/contribute/plans/bug-class-regression-testing.md``).

This is the durable, queryable index that plan document's Phase 0 process
change (AGENTS.md "A bug fix's regression test targets the bug class, not
the one reported input") points a `fix:` PR's "Bug class" answer at: check
here first for a matching `BugClass.id` before restating an invariant that
already has a home, and add a new entry here — not just prose in a PR body
or an AGENTS.md "Known gaps" paragraph — when a fix closes a genuinely new
class.

This module records *relationships*, not test logic: which files carry the
generalized test(s) for a class, which issues/PRs it traces back to, which
public surfaces and axes it has been verified across, and which known
residual gaps are tracked rather than silently open. It does not replace
the bug-fix test contract's per-PR gate (`scripts/check_bugfix_test_contract.py`)
— that gate is enforced at PR time; this registry is what a PR's declared
answer should reference, and what the *next* PR should search before writing
a fifth narrow reproducer for a mechanism a class already covers.

``tests/test_regressions_manifest.py`` enforces this registry's own
integrity mechanically — every named `seed_tests` path exists and is a
real, pytest-collected `test_*.py` file, every `known_gaps` entry names a
non-empty reference, and, when a `known_gaps` entry sets the optional
`canary_test` (most current entries leave it `None` — a tracked-but-
unmonitored residual is honest, not every gap has one), that path
resolves the same way — the same "a registry entry is checked, not just
written" discipline `scripts/check_ai_readiness.py`'s `changekind-*` checks
already apply to `ChangeKind`.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["BUG_CLASSES", "BugClass", "KnownGap", "all_ids", "get"]


#: The bug classes named in
#: `docs/contribute/plans/bug-class-regression-testing.md`'s Phases 2-9,
#: seeded with the generalized/property test(s) that already exist for
#: each. A class entry does not claim its phase is *complete* — see that
#: plan document for what each phase still has open; this registry only
#: records what already has a home so a future PR can find it.
BUG_CLASSES: tuple[BugClass, ...] = (
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
            "/plain-C bare name (`_foo` -> `foo`, gated on `is_extern_c`, itself gated `and not (has_asm_label and is_cxx)` in its own Darwin `symbol_candidates` branch -- a single-underscore explicit `asm(\"_foo\")` label is exactly as candidate-matchable as genuine decoration, so without the gate the mangled name stayed preserved but `entity_id_for_function`/`entity_id_for_variable` still took the wrong, signature-free `(\"extern_c\",)` branch instead of `(\"mangled\", \"_foo\")`; gating on `is_cxx` -- the caller's own resolved compile-language mode -- is required because C has no mangling to override, so a genuinely plain-C asm-labeled declaration must stay `(\"extern_c\",)`, matching castxml, or self-comparing an unchanged plain-C header spuriously reports FUNC_LANGUAGE_LINKAGE_CHANGED). Both gate on "
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
                    '(both are equally `__Z...`-shaped), closed by `has_explicit_asm_label` detecting clang\'s own distinct `AsmLabelAttr` child node under the declaration\'s `"inner"` '
                    "list (verified against a real Clang 18 install) and short-circuiting both stripping branches whenever it is present; and the fifteenth item's bare re-probe being "
                    "confined to the GNU branch only, leaving a target-prefixed CL-style driver (e.g. `aarch64-apple-darwin-clang-cl`) with no fallback once its own explicit-target "
                    "recovery found nothing, even though a real `clang-cl -print-target-triple` reports that same prefixed default (verified against a real Clang 18 "
                    'install), closed by trying the identical bare re-probe under CL mode too; a single-underscore explicit `asm("_foo")` label still tripping `is_extern_c`\'s Darwin '
                    '`symbol_candidates` fallback even though the twenty-fourth item\'s own fix kept the mangled name preserved -- the resulting entity identity still took the wrong '
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
    BugClass(
        id="cli_surface.retired_spelling_in_remediation",
        invariant=(
            "A user-facing message may only name flags the command it is "
            "advising actually accepts -- every `--flag` token in an error, "
            "warning or help string must be a live option of that command, "
            "so following the tool's own remediation can never itself be a "
            "usage error."
        ),
        fixed_by=(1184,),
        seed_tests=(
            "tests/test_cli_compare_release_project_snapshot_package.py",
        ),
        public_surfaces=("cli",),
        axes={
            "error_path": (
                "ambiguous-variant",
                "unknown-variant-id",
                "empty-variant-id",
                "unknown-both-sides-id",
                "zero-variants-declared",
            )
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test applies the oracle only to the variant "
                    "family's own error paths. A repo-wide AST sweep run "
                    "while fixing this (non-docstring string literals under "
                    "`abicheck/` containing any spelling in "
                    "`scripts/retired_surfaces.py`'s RETIRED_SURFACES) "
                    "reports 44 hits across 25 modules, and several look "
                    "like the same defect already sitting in the tree -- "
                    "`pdb_utils.py`'s 'use --pdb-path to override', "
                    "`reporter_markdown.py`'s 'Unknown --show-only token', "
                    "`dumper.py`'s '--dwarf-only requested but ...'. They "
                    "are NOT mechanically decidable: a flag retired from "
                    "`compare`/`dump` can still be live on `scan` (which "
                    "kept the whole compile-context and debug-resolution "
                    "families), and `cli_compare_release.py`'s unregistered "
                    "release engine legitimately still *defines* several of "
                    "them, so a sweep-turned-gate would need ~25 "
                    "hand-judged allowlist entries -- the shape AGENTS.md "
                    "warns is itself a smell. Each site needs reading "
                    "against the command that emits it. Deliberately not "
                    "attempted in PR #1184: it is a separate change from "
                    "the CLI option audit, and a hasty sweep would ship a "
                    "large unreviewed allowlist rather than close the class."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)


_BY_ID: dict[str, BugClass] = {bc.id: bc for bc in BUG_CLASSES}


def get(bug_class_id: str) -> BugClass:
    """Look up a registered `BugClass` by id.

    Raises `KeyError` (with the full set of valid ids in the message,
    via the dict's own `__getitem__`) rather than returning `None` — a
    lookup miss during a PR review is a "this class isn't registered
    yet, add it" signal, not a value to silently propagate.
    """
    return _BY_ID[bug_class_id]


def all_ids() -> tuple[str, ...]:
    """Every registered `BugClass.id`, in registry order."""
    return tuple(bc.id for bc in BUG_CLASSES)
