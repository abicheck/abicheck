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

"""``abicheck.compare.fact_comparison.compare_facts`` — ADR-063 Phase 5B's
shared ``FactStatus`` x ``FactStatus`` -> detector-meaning table.

Split out of ``tests/test_model_fact.py`` (Codex review, PR #1033):
``compare_facts`` itself moved from ``model/fact.py`` to
``compare/fact_comparison.py`` — deciding whether two facts differ is a
`compare/`-owned question per `model/AGENTS.md`'s own scoped contract, not
a `model/`-owned one — so its tests moved with it.
"""

from __future__ import annotations

import pytest

from abicheck.compare.fact_comparison import FactComparability, compare_facts
from abicheck.model.availability import FactStatus
from abicheck.model.fact import Fact


class TestCompareFacts:
    """Exhaustive over every combination the six-member ``FactStatus`` enum
    admits, not just the happy path, per this repo's own "bug class, not the
    one reported input" testing convention: the whole point of this
    primitive is that a detector must be able to trust it for a status
    combination nobody hand-picked a regression test for yet.
    """

    ALL_STATUSES = list(FactStatus)

    def test_both_present_is_comparable(self) -> None:
        cmp = compare_facts(Fact.present(["a"]), Fact.present(["b"]), [])
        assert cmp.comparability is FactComparability.COMPARABLE
        assert cmp.is_comparable
        assert cmp.old_value == ["a"]
        assert cmp.new_value == ["b"]
        assert not cmp.degraded

    def test_both_present_empty_is_comparable_not_incomplete(self) -> None:
        """A confirmed-empty pair on both sides is real evidence of 'no
        change', not a gap -- must not be conflated with NOT_COLLECTED."""
        cmp = compare_facts(Fact.present([]), Fact.present([]), [])
        assert cmp.comparability is FactComparability.COMPARABLE
        assert cmp.old_value == []
        assert cmp.new_value == []

    @pytest.mark.parametrize("partial_side", ["old", "new"])
    def test_partial_is_comparable_but_degraded(self, partial_side: str) -> None:
        present, partial = Fact.present(["x"]), Fact.partial(["y"])
        old, new = (partial, present) if partial_side == "old" else (present, partial)
        cmp = compare_facts(old, new, [])
        assert cmp.comparability is FactComparability.COMPARABLE
        assert cmp.degraded

    @pytest.mark.parametrize(
        "gap_status", [FactStatus.NOT_COLLECTED, FactStatus.FAILED]
    )
    @pytest.mark.parametrize("gap_side", ["old", "new"])
    def test_incomplete_evidence_on_either_side_declines_to_compare(
        self, gap_status: FactStatus, gap_side: str
    ) -> None:
        gap = Fact(status=gap_status)
        present = Fact.present(["real base"])
        old, new = (gap, present) if gap_side == "old" else (present, gap)
        cmp = compare_facts(old, new, [])
        assert cmp.comparability is FactComparability.INCOMPLETE
        assert not cmp.is_comparable
        assert cmp.old_value is None
        assert cmp.new_value is None
        assert cmp.reason

    def test_incomplete_both_sides(self) -> None:
        cmp = compare_facts(Fact.not_collected(), Fact.failed("boom"), [])
        assert cmp.comparability is FactComparability.INCOMPLETE

    @pytest.mark.parametrize("unsupported_side", ["old", "new"])
    def test_unsupported_on_either_side_is_unsupported_not_incomplete(
        self, unsupported_side: str
    ) -> None:
        unsupported = Fact.unsupported()
        present = Fact.present(["x"])
        old, new = (
            (unsupported, present)
            if unsupported_side == "old"
            else (present, unsupported)
        )
        cmp = compare_facts(old, new, [])
        assert cmp.comparability is FactComparability.UNSUPPORTED

    def test_unsupported_outranks_incomplete(self) -> None:
        """UNSUPPORTED (permanent) is a more specific diagnosis than
        NOT_COLLECTED (transient/scope) when the two disagree between sides."""
        cmp = compare_facts(Fact.unsupported(), Fact.not_collected(), [])
        assert cmp.comparability is FactComparability.UNSUPPORTED

    def test_both_not_applicable_is_confirmed_non_applicability(self) -> None:
        cmp = compare_facts(Fact.not_applicable(), Fact.not_applicable(), [])
        assert cmp.comparability is FactComparability.NOT_APPLICABLE

    @pytest.mark.parametrize("na_side", ["old", "new"])
    def test_mismatched_not_applicable_declines_to_compare(self, na_side: str) -> None:
        na = Fact.not_applicable()
        present = Fact.present(["x"])
        old, new = (na, present) if na_side == "old" else (present, na)
        cmp = compare_facts(old, new, [])
        assert cmp.comparability is FactComparability.INCOMPLETE

    def test_unsupported_outranks_mismatched_not_applicable(self) -> None:
        cmp = compare_facts(Fact.unsupported(), Fact.not_applicable(), [])
        assert cmp.comparability is FactComparability.UNSUPPORTED

    def test_none_fact_is_treated_as_not_collected(self) -> None:
        cmp = compare_facts(None, Fact.present(["x"]), [])
        assert cmp.comparability is FactComparability.INCOMPLETE

    def test_exhaustive_status_pairs_always_classify(self) -> None:
        """Every one of the 6x6 status combinations must resolve to exactly
        one FactComparability -- no combination may raise or fall through
        unclassified."""
        for old_status in self.ALL_STATUSES:
            for new_status in self.ALL_STATUSES:
                old = Fact(
                    status=old_status,
                    value=["x"]
                    if old_status in (FactStatus.PRESENT, FactStatus.PARTIAL)
                    else None,
                )
                new = Fact(
                    status=new_status,
                    value=["y"]
                    if new_status in (FactStatus.PRESENT, FactStatus.PARTIAL)
                    else None,
                )
                cmp = compare_facts(old, new, [])
                assert isinstance(cmp.comparability, FactComparability)
                if cmp.is_comparable:
                    assert old_status in (FactStatus.PRESENT, FactStatus.PARTIAL)
                    assert new_status in (FactStatus.PRESENT, FactStatus.PARTIAL)
