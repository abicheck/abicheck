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
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Both seed tests run on synthetic snapshots built "
                    "directly (no castxml/clang, no CLI) — the "
                    "cross-surface (CLI) and cross-backend (castxml/clang) "
                    "coverage this class's own invariant is stated over "
                    "does not exist yet, and the direct-clang path-"
                    "normalization and nested/anonymous-namespace record "
                    "gaps AGENTS.md's 'Known gaps' section documents from "
                    "PR #843 are unmonitored (Codex review, PR #885 — "
                    "public_surfaces/axes here previously overclaimed "
                    "'cli'/'castxml'/'clang' coverage neither seed test "
                    "exercises)."
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
        fixed_by=(837, 843, 846, 868),
        seed_tests=(
            "tests/test_castxml_anonymous_type_location.py",
            "tests/test_anon_type_location_properties.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "The L5 source graph's own node identities are not "
                    "renumbered alongside the flat snapshot's closure "
                    "markers (AGENTS.md, PR #868's own follow-up note)."
                ),
                reference="PR #868",
            ),
            KnownGap(
                description=(
                    "Both seed tests feed a hand-built AST-node/XML "
                    "fragment into an internal parser class directly — no "
                    "real castxml/clang subprocess, no CLI, no python-api "
                    "call — so there is no cross-backend (castxml/clang) "
                    "or cross-surface (CLI/python-api) test for this class "
                    "yet, despite the invariant itself being backend-"
                    "agnostic (Codex review, PR #885)."
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
        fixed_by=(753, 759, 879),
        seed_tests=(
            "tests/test_cross_tier_dedup_unhashable_value.py",
            "tests/test_finding_identity_properties.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Both seed tests call internal matching/dedup "
                    "primitives directly (`diff_filtering`/"
                    "`finding_identity`) — no CLI, no python-api call — so "
                    "there is no public-surface-level test that a real "
                    "dedup-key collision actually reaches `compare()`'s "
                    "output."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-5",
            ),
            KnownGap(
                description=(
                    "Neither seed test's generator covers the specific "
                    "shapes Phase 5's own '#879's own history' section "
                    "names as previously missing (sets, varied mapping "
                    "insertion order, NaN/signed-zero/infinities, "
                    "structurally-equal copies, objects sharing a `repr()` "
                    "but not an identity) — the seeds exercise fixed list/"
                    "dict values and function/variable identities, not a "
                    "generated adversarial corpus over those shapes, so "
                    "the invariant's totality/injectivity/order-invariance "
                    "claims are untested for exactly the inputs that "
                    "caused #879 in the first place."
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
        fixed_by=(860, 883, 886),
        seed_tests=(
            "tests/test_run_plan.py",
            "tests/test_project_targets_consumer_compile.py",
            "tests/test_cli_compare_release_bundle_signature_wiring.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "No generalized sentinel-propagation matrix exists yet "
                    "covering every public entry point named in "
                    "bug-class-regression-testing.md's Phase 6. Of the two "
                    "seed tests, only `test_run_plan.py`'s "
                    "`TestConsumerCompileOverlayProjection` covers the "
                    "'reaches every consumer' half of this class's own "
                    "invariant (`profiles.<id>.consumer_compile` reaching "
                    "the generated run-plan cell, via `generate_run_plan` "
                    "directly — no CLI, no python-api); "
                    "`test_project_targets_consumer_compile.py` only "
                    "exercises the 'rejected at the public boundary' half "
                    "(schema parsing/round-tripping) and on its own would "
                    "leave a dropped forwarding edge undetected — it was "
                    "the sole seed here until this was found to be a real "
                    "gap (Codex review, PR #885). Neither reaches every "
                    "config path Phase 6 names, only this one."
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
        fixed_by=(699, 721),
        seed_tests=("tests/test_snapshot_compression.py",),
        axes={"algorithm": ("zstd", "gzip")},
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test calls `abicheck.serialization`/"
                    "`abicheck.snapshot_io`'s real read/write chokepoints "
                    "directly (real gzip/zstd, real scale) but never "
                    "through `abicheck.service`/the CLI — no python-api "
                    "or CLI-level round-trip test exists for this class "
                    "yet."
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
        fixed_by=(705, 758),
        seed_tests=("tests/test_reusable_workflow_execution.py",),
        public_surfaces=("github-action",),
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test's own hostile-input corpus is scoped "
                    "to `check-single.yml`'s shell steps only "
                    '(`CHECK_SINGLE = "check-single.yml"`) — not every '
                    "scalar input across the repository's other shell "
                    "scripts and composite-action steps, which "
                    "bug-class-regression-testing.md's Phase 8 names as "
                    "the full target-script inventory this class's "
                    "invariant is stated over (Codex review, PR #885)."
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
        fixed_by=(753, 759),
        seed_tests=("tests/test_canonical_finding_id_completeness.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "Only canonical_finding_id's classification is "
                    "exhaustiveness-checked today; the same totality "
                    "property is not yet generalized to every other "
                    "kind-keyed registry named in Phase 9 of the plan "
                    "(evidence kinds, providers, report renderers)."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
            KnownGap(
                description=(
                    "The seed test calls `finding_identity."
                    "report_canonical_finding_id` directly — no CLI, no "
                    "python-api — so nothing here proves a real registry "
                    "omission reaches a `compare()` report."
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
        fixed_by=(834, 838, 860, 883),
        seed_tests=("tests/test_fact_conservation_properties.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The fact-conservation suite covers a selected "
                    "detector-family subset today, not every ChangeKind "
                    "family and evidence-completeness failure mode named "
                    "in AGENTS.md's 'Known gaps' section."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
            ),
            KnownGap(
                description=(
                    "The seed test calls `abicheck.checker.compare` "
                    "directly on hand-built snapshots — real detection "
                    "logic, but no CLI/python-api/exit-code layer, so "
                    "report/gate/exit-code agreement (the second half of "
                    "this class's own invariant) is untested."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-9",
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
