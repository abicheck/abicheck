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


def _release_decision(**kwargs: object) -> object:
    """`resolve_release_exit_decision` with only its three required kwargs
    defaulted, so a test names exactly the axis it is about.
    """
    return resolve_release_exit_decision(
        not_comparable=bool(kwargs.pop("not_comparable", False)),
        severity_scheme_active=bool(kwargs.pop("severity_scheme_active", False)),
        verdict_or_severity_contribution=int(
            kwargs.pop("verdict_or_severity_contribution", 0)  # type: ignore[arg-type]
        ),
        **kwargs,  # type: ignore[arg-type]
    )


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


class TestEveryExitAxisIsDecodedOntoRunOutcome:
    """The class behind this axis's fourth escape, stated once as a gate.

    Each of the four surfaces that carried an exit code without carrying
    this axis -- the JSON `exit` block, JUnit, Markdown, and now
    `run_outcome` -- failed the same way: a renderer or decoder enumerates
    the axes it knows by hand, so an axis added later is silently absent
    rather than loudly unhandled. `run_outcome` is the worst of the four,
    because `workflows.aggregate.gate.GateInfo.from_report_data` treats
    that block as authoritative: a saved release report said the run was
    operationally fine (`operational: "none"`) beside its own
    `exit.code: 7`, so a report-driven consumer read a failed run as
    nonblocking (Codex review, P1).

    `unclassified_release_contribution_fields()` is the general fix -- the
    decoder now states both the axes it maps and the exact complement it
    deliberately does not, over the field list `ExitDecision` itself
    publishes. The test below is what makes that mechanical: it does not
    name the evidence axis at all, so it fails for the *next* axis too,
    which is the property the three prior narrow fixes each lacked.
    """

    def test_no_contribution_field_is_left_unclassified(self) -> None:
        from abicheck.policy.outcome_release import (
            unclassified_release_contribution_fields,
        )

        assert unclassified_release_contribution_fields() == frozenset()

    @pytest.mark.parametrize(
        ("kwargs", "expected_operational"),
        (
            ({"evidence_contract_error_contribution": 7}, "evidence_contract_error"),
            ({"not_comparable": True}, "not_comparable"),
            ({"operational_error_contribution": 4}, "extraction_error"),
            ({"no_comparison_completed_contribution": 1}, "no_comparison_completed"),
            # A release with no operational axis at all still reads `none`,
            # so the decode cannot pass by answering something for every run.
            ({}, "none"),
            # ADR-049 §7: accepting incomplete assurance is not a broken
            # run, so the coverage floor must stay off this axis.
            ({"contract_coverage_contribution": 1}, "none"),
        ),
    )
    def test_each_operational_axis_reaches_run_outcome(
        self, kwargs: dict[str, object], expected_operational: str
    ) -> None:
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        decision = _release_decision(**kwargs)
        outcome = run_outcome_dict_for_release("NO_CHANGE", decision.to_dict())
        assert outcome["operational"] == expected_operational

    def test_the_status_names_the_axis_that_decided_the_exit(self) -> None:
        """Precedence agreement, not merely presence.

        When two operational axes are live the reported status has to be
        the one the exit code came from, or the report explains a code it
        does not name. `not_comparable`'s 16 outranks the evidence axis's
        7, so the status is `not_comparable` even though both
        contributions are preserved in the block.
        """
        decision = _release_decision(
            not_comparable=True, evidence_contract_error_contribution=7
        )
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        assert decision.code == 16
        assert decision.evidence_contract_error_contribution == 7
        outcome = run_outcome_dict_for_release("NO_CHANGE", decision.to_dict())
        assert outcome["operational"] == "not_comparable"


class TestASavedReleaseReportIsNotReadAsClean:
    """The consumer half, through the real reader rather than the shape.

    Asserting `run_outcome.operational` alone would restate the decoder's
    own table. What the P1 was actually about is what a *consumer* of the
    saved report concludes, so the oracle here is
    `GateInfo.from_report_data` itself -- the function ADR-063 Phase 7
    makes authoritative over the persisted block.
    """

    @pytest.mark.parametrize(
        ("kwargs", "expected_blocking"),
        (
            ({"evidence_contract_error_contribution": 7}, True),
            ({}, False),
        ),
    )
    def test_gate_info_blocks_on_a_persisted_evidence_shortfall(
        self, kwargs: dict[str, object], expected_blocking: bool
    ) -> None:
        from abicheck.policy.outcome_release import run_outcome_dict_for_release
        from abicheck.workflows.aggregate.gate import GateInfo

        decision = _release_decision(**kwargs)
        report = {
            "exit": decision.to_dict(),
            "run_outcome": run_outcome_dict_for_release(
                "NO_CHANGE", decision.to_dict()
            ),
        }
        assert GateInfo.from_report_data(report).blocking is expected_blocking
