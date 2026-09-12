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

"""Bug classes about a *guard's own predicate*, not the analysis it gates.

The defects here are found by reading a condition against the argument
written next to it: what the code tests versus what its justification
claims, and what the branch it rejects into then asserts. That makes them
invisible to the kind of test that exercises the surrounding feature --
every such test passes, because the feature works; it is the population the
guard admits, and the claim the fallback makes, that are wrong.

Its own module for the reason ``manifest_report.py``/
``manifest_tool_surface.py`` are: ``manifest.py`` sits at its
``architecture/debt.yaml`` ``no_growth`` baseline and is an assembly point
over per-axis entry lists, so a new class goes in a sibling rather than
growing it (PR #1228).
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["GUARD_BUG_CLASSES"]


GUARD_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="guard.proxy_predicate_overshoots_justification",
        invariant=(
            "A guard's predicate must test the property its own "
            "justification names, and must be checked against the "
            "population it targets. Two failures follow when it does not: "
            "a narrowing guard can eliminate the entire population it was "
            "meant to discriminate within (the outcome becomes reachable "
            "from no real input, and nothing fails anywhere), and the "
            "fallback it hands those inputs to can assert a STRICTLY "
            "STRONGER claim than the one the guard declined — declining a "
            "weak claim for lack of evidence, then making a strong one on "
            "the same absent evidence. An early return justified by a "
            "semantic property (content identity) but implemented via a "
            "proxy (object identity) is the same defect from the other "
            "side: the claim the return exists to reject is made anyway, "
            "on the shape users actually hit."
        ),
        # #1228: `graph_reconcile._classify_outcome`'s declaring-file guard
        # made OUTCOME_COORDINATES_ONLY unreachable (0 occurrences
        # repo-wide) and sent every eligible pair to OUTCOME_RECONCILED's
        # "both name and location evidence changed" — on absent location
        # evidence. Same PR: `schema_staleness_status`'s `old is new`
        # self-diff return, whose own argument ("comparing a value against
        # itself can never produce a false pairwise finding") never
        # depended on object identity, reported one stored snapshot loaded
        # twice as `degraded` and flipped `assurance.status` to `partial`.
        fixed_by=(1228,),
        seed_tests=(
            "tests/test_graph_reconcile_coordinate_outcome.py",
            "tests/test_analysis_assurance_content_identity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "evidence_presence": ("both_sides", "one_side", "absent"),
            "claim_strength": ("declined", "weak_claim", "strong_claim"),
            "identity_test": ("object", "content", "differing"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Reachability is asserted per-classifier in the seed "
                    "tests, not swept mechanically: another outcome "
                    "elsewhere in the codebase that a later guard made "
                    "unproducible would still be found by hand. The "
                    "residuals on the first instance are narrower and "
                    "both stated rather than hidden: a pair with no "
                    "positive evidence on EITHER axis still falls through "
                    "to OUTCOME_RECONCILED's overstated prose (ADR-048's "
                    "accepted case197 'no clean split'), and a "
                    "coordinate-only pair resting on qualified-name "
                    "evidence alone cannot rule out a move that kept the "
                    "same file basename -- recorded as "
                    "`ReconciledPair.coordinate_evidence` and disclosed in "
                    "the finding's own text, since separating that from a "
                    "declaring-file-confirmed shift at the *kind* level "
                    "would need a fifth outcome and a new ChangeKind."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="gate.per_member_axis_fold",
        invariant=(
            "An orthogonal 0/1 gate axis computed per compared member of a "
            "directory/package (release) comparison folds across members "
            "with max() and nothing else: the fold is the identity over one "
            "member (so a one-member package gates and reports exactly as "
            "the scalar path does for that pair -- AGENTS.md's 'One model, "
            "any cardinality'), is independent of member order, never lets a "
            "clean sibling mask a member that fell short, and is monotonic "
            "(adding a member never lowers the contribution or improves the "
            "aggregate status). The decision's reported status and its exit "
            "contribution are resolved together and must agree, so a report "
            "can never contradict its own exit code. The axis is also "
            "strictly additive: a caller that never opts in gets a decision "
            "byte-identical to one passing an explicit 0."
        ),
        fixed_by=(1237,),
        seed_tests=(
            "tests/test_release_assurance_properties.py",
            "tests/test_release_assurance_cli.py",
        ),
        public_surfaces=("cli",),
        known_gaps=(
            KnownGap(
                description=(
                    "Stated and exercised for ADR-070's analysis-assurance "
                    "axis specifically. The two sibling axes with the same "
                    "shape -- ADR-049's contract-coverage floor and ADR-065 "
                    "D6's incomplete-scope floor -- are covered by their own "
                    "example-level tests only; neither has an order-"
                    "independence or monotonicity property test of its own, "
                    "so a regression in *their* fold would not be caught "
                    "here. Generalizing this class's properties over all "
                    "three (one shared property harness parameterized by "
                    "axis) is the real remaining work, not another per-axis "
                    "copy."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
)
