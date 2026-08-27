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
integrity mechanically (every named path exists, every `known_gaps` entry
names a real test), the same "a registry entry is checked, not just
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
    a *dedicated* executable canary (an ``xfail``/skip-with-reason test
    written specifically for this gap) that fails loudly if the residual
    silently closes or silently widens — never a pointer to an existing
    suite that happens to cover the same class but doesn't encode this
    specific gap. Leave it `None` for a gap that is tracked but not yet
    monitored by a canary; `None` is honest, a mismatched path is not
    (Codex review, PR #885).
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
    #: Public surfaces this class's tests are verified through (CLI,
    #: Python API, GitHub Action, ...). Free-form; not yet cross-checked
    #: against a fixed vocabulary.
    public_surfaces: tuple[str, ...] = ()
    #: Axis name -> the values verified for that axis, e.g.
    #: {"frontend": ("castxml", "clang")}. Free-form; documents *coverage*
    #: breadth, not a schema this module enforces.
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
            "Extracting a semantic value (e.g. an enum constant) from a "
            "clang/castxml AST subtree gives the same answer regardless of "
            "which semantics-preserving wrapper nodes (implicit casts, "
            "parens, constant-folding wrappers) sit between the "
            "declaration and its value."
        ),
        fixed_by=(839,),
        seed_tests=("tests/test_dumper_clang_enum_value_properties.py",),
        public_surfaces=("python-api",),
        axes={"frontend": ("clang",)},
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
        public_surfaces=("python-api", "cli"),
        axes={"frontend": ("castxml", "clang")},
        known_gaps=(
            KnownGap(
                description=(
                    "The direct-clang path-normalization and nested/"
                    "anonymous-namespace record gaps AGENTS.md's 'Known "
                    "gaps' section documents from PR #843 are not yet "
                    "encoded as canaries in the generalized suite."
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
        public_surfaces=("python-api",),
        axes={"frontend": ("castxml", "clang"), "storage": ("live",)},
        known_gaps=(
            KnownGap(
                description=(
                    "The L5 source graph's own node identities are not "
                    "renumbered alongside the flat snapshot's closure "
                    "markers (AGENTS.md, PR #868's own follow-up note)."
                ),
                reference="PR #868",
            ),
        ),
    ),
    BugClass(
        id="matching.dedup_key_soundness",
        invariant=(
            "For every key used in matching, grouping, or deduplication: "
            "totality (every producer-valid value yields a key), "
            "determinism, injectivity (unequal values don't collide), and "
            "order-invariance for unordered inputs."
        ),
        fixed_by=(753, 759, 879),
        seed_tests=(
            "tests/test_cross_tier_dedup_unhashable_value.py",
            "tests/test_finding_identity_properties.py",
        ),
        public_surfaces=("python-api",),
    ),
    BugClass(
        id="config.propagation_completeness",
        invariant=(
            "An accepted configuration value either reaches every "
            "relevant consumer with identical semantics, or is rejected "
            "at the public boundary — no third state."
        ),
        fixed_by=(860, 883),
        seed_tests=("tests/test_project_targets_consumer_compile.py",),
        public_surfaces=("cli", "python-api"),
        known_gaps=(
            KnownGap(
                description=(
                    "No generalized sentinel-propagation matrix exists yet "
                    "covering every public entry point named in "
                    "bug-class-regression-testing.md's Phase 6 — today's "
                    "seed test covers one concrete config path, not the "
                    "full matrix."
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
        public_surfaces=("python-api",),
        axes={"algorithm": ("zstd", "gzip")},
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
        public_surfaces=("python-api",),
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
        public_surfaces=("cli", "python-api"),
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
