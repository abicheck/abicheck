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

"""The release-wide ``analysis_assurance`` roll-up over several libraries.

``policy.analysis_assurance_merge`` answers one question -- how much to trust
a multi-library result -- and answers it by taking each judgement axis to its
weakest member. Dropping the block instead is not neutral: every reporter
then omits it, and ``--require-complete-analysis`` stops gating on a member
whose evidence was partial or failed.

Split out of ``tests/test_compat_multi_library.py`` (a distinct subject, and
that file passed the 1200-line test maximum).
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.analysis_assurance import AnalysisAssurance
from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.compat.multi_library import merge_results
from abicheck.policy.analysis_assurance_merge import merge_analysis_assurance


def _result(
    library: str, *, verdict: Verdict = Verdict.NO_CHANGE, **kw: object
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library=library,
        verdict=verdict,
        **kw,  # type: ignore[arg-type]
    )


class TestAssuranceIsRolledUpNotDropped:
    """Merging must publish a release-wide assurance block.

    Dropping it is not neutral: every reporter omits the block, so a member
    whose evidence was ``partial``/``failed``/``not_comparable`` contributes
    no release-wide signal and ``--require-complete-analysis`` stops gating on
    it -- incomplete evidence silently upgraded to no stated concern, which is
    the inversion of ``vision.md``'s "weaker evidence narrows conclusions"
    (Codex review).
    """

    def test_the_merged_result_carries_one(self) -> None:
        merged = merge_results(
            [
                _result(
                    "liba", analysis_assurance=AnalysisAssurance(status="complete")
                ),
                _result("libb", analysis_assurance=AnalysisAssurance(status="partial")),
            ],
            label="release",
        )
        assert merged.analysis_assurance is not None
        assert merged.analysis_assurance.status == "partial"

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            (["complete", "complete"], "complete"),
            (["complete", "partial"], "partial"),
            (["partial", "failed"], "failed"),
            (["complete", "not_comparable"], "not_comparable"),
            (["not_comparable", "failed"], "failed"),
            # "no claim was made" is not a good claim.
            (["complete", "not_requested"], "not_requested"),
            (["not_requested", "partial"], "partial"),
        ],
    )
    def test_status_takes_the_weakest_member(
        self, statuses: list[str], expected: str
    ) -> None:
        merged = merge_analysis_assurance(
            [AnalysisAssurance(status=s) for s in statuses]  # type: ignore[arg-type]
        )
        assert merged is not None
        assert merged.status == expected

    def test_the_rule_is_order_insensitive(self) -> None:
        """A roll-up that depended on which library was read first would give
        one release two different assurances depending on directory order."""
        blocks = [
            AnalysisAssurance(status="complete", schema_staleness_status="clean"),
            AnalysisAssurance(status="partial", schema_staleness_status="stale"),
            AnalysisAssurance(status="failed", l0_context_status="asymmetric"),
        ]
        first = merge_analysis_assurance(blocks)
        second = merge_analysis_assurance(list(reversed(blocks)))
        assert first is not None and second is not None
        assert dataclasses.replace(first, notes=()) == dataclasses.replace(
            second, notes=()
        )

    def test_optimistic_defaults_are_never_carried_over_a_worse_member(self) -> None:
        """``schema_staleness_status`` defaults to ``"clean"``, so a naive
        field-wise merge would report a stale release as clean."""
        merged = merge_analysis_assurance(
            [
                AnalysisAssurance(status="complete"),
                AnalysisAssurance(status="complete", schema_staleness_status="stale"),
            ]
        )
        assert merged is not None
        assert merged.schema_staleness_status == "stale"

    def test_the_shallowest_effective_depth_wins(self) -> None:
        merged = merge_analysis_assurance(
            [
                AnalysisAssurance(status="complete", effective_depth="source"),
                AnalysisAssurance(status="complete", effective_depth="binary"),
            ]
        )
        assert merged is not None
        assert merged.effective_depth == "binary"

    def test_a_lone_member_passes_through_unchanged(self) -> None:
        """A one-library descriptor and the scalar path must agree
        (``AGENTS.md``'s "one model, any cardinality")."""
        only = AnalysisAssurance(status="partial", effective_depth="headers")
        assert merge_analysis_assurance([only]) is only

    def test_absent_blocks_are_skipped_not_read_as_complete(self) -> None:
        assert merge_analysis_assurance([None, None]) is None
        merged = merge_analysis_assurance([None, AnalysisAssurance(status="failed")])
        assert merged is not None and merged.status == "failed"

    def test_the_rollup_says_accounting_is_not_aggregated(self) -> None:
        """The accounting blocks are per-library and are left at their own
        "nothing requested / nothing evaluated" defaults. A reader must not
        have to infer that from an empty block."""
        merged = merge_analysis_assurance(
            [AnalysisAssurance(status="complete"), AnalysisAssurance(status="partial")]
        )
        assert merged is not None
        assert any("not aggregated" in n for n in merged.notes)


class TestAssuranceRollUpOnInputItCannotPlace:
    """Unknown input must not be read as good input.

    Both ladders in the roll-up rank an unrecognised value **worst**, which
    is the same discipline ``multi_library._WORST_SCALES`` records: a status
    or depth this build cannot place is not evidence of a good one. Both
    fallbacks were reachable and untested -- and an untested fail-closed
    branch is the one most likely to be "simplified" into a fail-open one.
    """

    def test_an_unrecognised_status_outranks_every_known_one(self) -> None:
        merged = merge_analysis_assurance(
            [
                AnalysisAssurance(status="complete"),
                AnalysisAssurance(status="failed"),
                # Not in the vocabulary: a future build's value reaching an
                # older one, or a hand-built block.
                AnalysisAssurance(status="something_new"),  # type: ignore[arg-type]
            ]
        )
        assert merged is not None
        assert merged.status == "something_new"

    def test_an_unrankable_depth_wins_over_every_ranked_one(self) -> None:
        merged = merge_analysis_assurance(
            [
                AnalysisAssurance(status="complete", effective_depth="source"),
                AnalysisAssurance(status="complete", effective_depth="binary"),
                AnalysisAssurance(status="complete", effective_depth="not_a_depth"),
            ]
        )
        assert merged is not None
        assert merged.effective_depth == "not_a_depth"

    def test_member_notes_are_carried_and_deduplicated(self) -> None:
        """A member's own explanation of *why* it is not complete is the part
        a reader needs; dropping it would leave a weakened status with no
        stated reason."""
        merged = merge_analysis_assurance(
            [
                AnalysisAssurance(
                    status="partial", notes=("no DWARF on either side", "shared note")
                ),
                AnalysisAssurance(
                    status="partial", notes=("shared note", "header parse degraded")
                ),
            ]
        )
        assert merged is not None
        assert merged.notes.count("shared note") == 1
        assert "no DWARF on either side" in merged.notes
        assert "header parse degraded" in merged.notes
