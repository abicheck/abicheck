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

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnownGap:
    """A residual the class's current tests deliberately do not close.

    Per AGENTS.md's "Fix the cause, not the instance": a gap is tracked
    here rather than left as prose only. `canary_test`, when set, must be
    a *dedicated* executable canary written specifically for this gap that
    fails loudly if the residual silently closes or silently widens — never
    a pointer to an existing suite that happens to cover the same class but
    doesn't encode this specific gap. Leave it `None` for a gap that is
    tracked but not yet monitored by a canary; `None` is honest, a
    mismatched path is not (Codex review, PR #885).

    "Fails loudly" is a strict requirement, checked by
    `tests/test_regressions_manifest.py::test_known_gap_canaries_exist`, not
    just documented here: an ordinary `@pytest.mark.xfail` is non-strict by
    this repository's own pytest config (no `xfail_strict` ini option), so
    an unexpected pass (XPASS, i.e. the gap silently closed) still reports
    green — use `@pytest.mark.xfail(..., strict=True)` instead. A bare
    `@pytest.mark.skip` is rejected outright: a skipped test never executes
    at all, so it cannot detect the residual closing *or* widening — it
    only proves the file exists. A conditional runtime `pytest.xfail(...)`
    call (`if not fixed_yet(): pytest.xfail(...)`) is **not** an equivalent
    substitute for `strict=True`, despite looking like one: once the
    guarding condition stops being met (the gap closes), execution falls
    through to whatever follows and, if that now passes, pytest records an
    ordinary PASS — not an XPASS — so nothing distinguishes it from any
    other passing test and CI stays green with no alert (Codex review,
    PR #885, fresh evidence after the first review round). A canary with no
    xfail/skip decorator at all must instead directly assert the *residual's
    own bound* (the specific degraded/wrong value the gap currently
    produces) rather than the eventually-correct behavior — asserting the
    bound fails loudly the moment the real behavior diverges from it, in
    either direction.
    """

    #: What's not covered (one sentence — the full account lives in
    #: AGENTS.md's "Known gaps" section or the linked issue/PR).
    description: str
    #: Issue or PR number this gap traces to, e.g. "PR #843".
    reference: str
    #: Path to a *dedicated* canary test for this exact gap, or `None` if
    #: this residual is tracked but not yet monitored by one.
    canary_test: str | None = None


@dataclass(frozen=True)
class BugClass:
    """One durable, cross-PR bug-class entry."""

    #: Stable, dotted identifier — e.g. "identity.environment_taint".
    #: Referenced by a future PR's "Bug class" answer instead of restating
    #: the invariant from scratch.
    id: str
    #: One-sentence statement of the invariant that must hold for every
    #: input, not just the originally reported one.
    invariant: str
    #: Issue/PR numbers this class's own escape history traces through —
    #: for traceability, not for the integrity check to validate against
    #: GitHub (this registry has no network access).
    fixed_by: tuple[int, ...]
    #: Paths to the test(s) carrying the generalized/property/metamorphic
    #: suite for this class. At least one is required — a class with no
    #: test is a "Known gaps" AGENTS.md paragraph, not a registry entry.
    seed_tests: tuple[str, ...]
    #: Documented, user-facing entry points this class's own seed_tests
    #: actually invoke — "cli" only for a real Click/`CliRunner`
    #: invocation, "python-api" only for a call through `abicheck.service`,
    #: "github-action" only for a real execution of a workflow/composite-
    #: action step. A seed test that imports an internal module directly
    #: (`abicheck.checker`, `abicheck.surface`, `abicheck.dumper_clang`,
    #: ...) — which is most of this registry today — exercises none of
    #: these, and this field must stay `()` for it: a claimed surface a
    #: seed test doesn't reach conceals exactly the missing cross-surface
    #: coverage a contributor is supposed to discover here (Codex review,
    #: PR #885). Free-form beyond that rule; not yet cross-checked against
    #: a fixed vocabulary.
    public_surfaces: tuple[str, ...] = ()
    #: Axis name -> the values *actually exercised*, e.g.
    #: {"algorithm": ("zstd", "gzip")} when a seed test genuinely round-
    #: trips through both. The same rule as `public_surfaces` applies to a
    #: "frontend" axis specifically: {"frontend": ("castxml", "clang")}
    #: requires a seed test that invokes the real castxml/clang backend —
    #: not one that feeds a hand-built AST/XML fragment into an internal
    #: parser class directly, which is frontend-agnostic and earns no
    #: frontend axis entry at all. Free-form beyond that rule; documents
    #: *coverage* breadth, not a schema this module enforces.
    axes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Residuals this class's current tests do not close (see `KnownGap`).
    known_gaps: tuple[KnownGap, ...] = ()


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
        fixed_by=(753, 759, 879, 905),
        seed_tests=(
            "tests/test_cross_tier_dedup_unhashable_value.py",
            "tests/test_finding_identity_properties.py",
            "tests/unit/compare/test_dedup_key.py",
            "tests/test_diff_namespaces.py",
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
                    "override, output/report options) — none has yet had "
                    "this same five-state/full-chain/mutation-check "
                    "treatment; consumer_compile was chosen as the first "
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
        id="trust_boundary.shell_workflow_injection",
        invariant=(
            "Every scalar input to a shell script or composite-Action "
            "step arrives as exactly one argument with exactly the same "
            "bytes, and untrusted data cannot create additional commands, "
            "$GITHUB_OUTPUT records, paths, or side effects."
        ),
        fixed_by=(705, 758, 836, 919),
        seed_tests=(
            "tests/test_reusable_workflow_execution.py",
            "tests/test_check_project_workflow_execution.py",
            "tests/test_action_run_sh_helpers.py",
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
                    "Only castxml's own `artificial=\"1\"` marker is read "
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
