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

"""ADR-064's exit-7 axis at release cardinality: its rank, and its survival.

Split from ``test_exit_decision.py``, which is at its
``architecture/debt.yaml`` baseline -- and this is a distinct question from
that module's general fold arithmetic: it is about one axis's precedence
against the two codes above it and everything below.
"""

from __future__ import annotations

import pytest

from abicheck.policy.exit_decision_precedence import resolve_release_exit_decision


class TestReleaseEvidenceContractAxisIsPreserved:
    """ADR-064's exit-7 axis at release cardinality (PR #1195).

    Two things a report reader depends on, and the second was wrong on
    first landing: the axis has to *decide* when nothing outranks it, and
    it has to *survive in the report* when something does. A dominant
    branch that returns without carrying it makes the aggregate JSON say
    `evidence_contract_error_contribution: 0` for a release whose member
    entry and stderr both record the shortfall (Codex review, P2).
    """

    @pytest.mark.parametrize(
        ("kwargs", "expected_code", "expected_reason"),
        (
            # Nothing outranks it -> it decides.
            ({}, 7, "evidence_contract_error"),
            # A real ABI break is below it (ADR-064: a run whose pinned
            # evidence contract was unmet never established what changed).
            ({"verdict_or_severity_contribution": 4}, 7, "evidence_contract_error"),
            # A proven removed library is *below* it, deliberately: exit 8
            # is a finding derived from the very comparison the unmet
            # contract says never established what changed -- including
            # whether the removal set is trustworthy. So it must not mask
            # the axis saying the analysis did not happen. This is the one
            # place the release resolver's order is not a flat max over
            # contributions (8 > 7); `not_comparable` below is, and wins,
            # because "could not be compared at all" is strictly stronger
            # than "did not reach the pinned rung".
            (
                {"removed_required_library": True, "severity_scheme_active": True},
                7,
                "evidence_contract_error",
            ),
            ({"not_comparable": True}, 16, "not_comparable"),
        ),
    )
    def test_rank_and_preservation(
        self, kwargs: dict, expected_code: int, expected_reason: str
    ) -> None:
        """Which code decides, and that the axis is reported either way."""
        decision = resolve_release_exit_decision(
            not_comparable=kwargs.pop("not_comparable", False),
            severity_scheme_active=kwargs.pop("severity_scheme_active", False),
            verdict_or_severity_contribution=kwargs.pop(
                "verdict_or_severity_contribution", 0
            ),
            evidence_contract_error_contribution=7,
            **kwargs,
        )
        assert decision.code == expected_code, decision
        assert [r.value for r in decision.reasons] == [expected_reason], decision
        # Preserved in every branch, whether or not it decided -- this is
        # the half that regressed.
        assert decision.evidence_contract_error_contribution == 7, decision

    def test_absent_axis_stays_absent(self) -> None:
        """The complementary control: a run that pinned no rung must not
        acquire the axis from this plumbing."""
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=0,
        )
        assert decision.code == 0
        assert decision.evidence_contract_error_contribution == 0
