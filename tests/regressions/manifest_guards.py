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
        id="guard.differential_test_shares_state_with_itself",
        invariant=(
            "A test whose claim is that two configurations AGREE must "
            "establish, within that same comparison, that each "
            "configuration actually executed. Otherwise a shared cache, "
            "memo or fixture lets the second run be served the first's "
            "result, the test compares one configuration with itself, and "
            "it passes identically whether the second code path is "
            "correct, broken, or never entered at all. A sibling test "
            "proving the mechanism CAN engage on the same input does not "
            "discharge this: it says nothing about whether this "
            "comparison engaged it. The executable form of the invariant "
            "is to observe the mechanism (a spy on the real entry point, "
            "asserting it ran and what it reported) rather than to infer "
            "it from the outputs being compared -- outputs are exactly "
            "what a stale cache makes indistinguishable. The same "
            "reasoning bounds fixture sharing: sharing immutable INPUTS "
            "between two configurations is sound, sharing the OUTPUT "
            "whose equivalence is the claim is this bug."
        ),
        # PR #1243: both streaming-pruner tests in
        # tests/test_clang_header_backend_integration.py derived their
        # "fresh" AST-cache root from the same `tmp_path`, so the
        # pruning-on run hit the pruning-off run's on-disk cache. The
        # pruner never parsed anything (proven by reintroducing the shared
        # root: the loader's call count is 0), so the public-model
        # equivalence assertion and the method-shaped-node count equality
        # were both vacuously true. The comment on the second call
        # asserted "a *different*, still-fresh cache dir"; it never was.
        fixed_by=(1243,),
        seed_tests=(
            "tests/test_clang_header_backend_integration.py",
            "tests/test_conftest_cache_isolation.py",
        ),
        # Neither seed test reaches a documented public entry point: the
        # pruner tests call `dump()`/`_clang_header_dump` directly and the
        # isolation tests inspect a pytest fixture's own allocation, so
        # per this field's rule this stays empty rather than claiming
        # "cli"/"python-api".
        public_surfaces=(),
        axes={
            "shared_state": ("on_disk_cache", "in_process_memo"),
            "configuration_pair": ("pruning_off_vs_on", "allocator_old_vs_new"),
            "observation": ("outputs_only", "mechanism_spy"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the two known instances are repaired and pinned; "
                    "nothing sweeps the suite for other differential tests "
                    "that share a cache, memo, fixture or temp root "
                    "between the two configurations they compare. The "
                    "shape is not mechanically detectable from a test's "
                    "text -- a second call to one isolation helper is not "
                    "by itself a defect -- so a sibling instance elsewhere "
                    "would still be found by hand, the same residual the "
                    "`guard.proxy_predicate_overshoots_justification` "
                    "entry above records for reachability."
                ),
                reference="PR #1243",
            ),
        ),
    ),
)
