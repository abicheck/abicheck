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

from abicheck.policy.exit_decision import ExitDecision
from abicheck.policy.exit_decision_precedence import resolve_release_exit_decision


def _release_decision(**kwargs: object) -> ExitDecision:
    """`resolve_release_exit_decision` with only its three required kwargs
    defaulted, so a test names exactly the axis it is about.

    Annotated `-> ExitDecision`, not `-> object` (CodeRabbit): every caller
    reads `ExitDecision` attributes off the result, so `object` was simply
    wrong -- it happens not to fail today only because the type-check step
    runs `mypy abicheck/` and does not cover `tests/`.
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
            # A *proven* removed library OUTRANKS it (8 > 7), and this row
            # asserted the opposite when the axis first landed. Two reasons
            # it was wrong, per Codex P2 on 656d9b0:
            #   - ADR-065 D6 only emits 8 for a removal proven against NEW's
            #     own complete inventory, which does not rest on the depth
            #     evidence this axis reports short: depth governs change
            #     detection *within* a library, inventory governs which
            #     libraries exist. The old rationale ("the unmet contract
            #     means the removal set may not be trustworthy") conflated
            #     the two.
            #   - It was unrepresentable. A dominant decision must exceed
            #     every contribution it carries, so "dominant 7, preserved
            #     8" has no encoding -- ranking evidence first therefore
            #     *dropped* the removal axis, which is what made an
            #     unrelated depth shortfall lower a real exit 8 to 7 and
            #     flip `run_outcome.gate` from `abi_breaking` to `none`.
            (
                {"removed_required_library": True, "severity_scheme_active": True},
                8,
                "removed_required_library",
            ),
            # ...but only where the scheme would actually have honoured the
            # removal. Under the legacy scheme a nonzero verdict already
            # outranks a removed library, so the removal is not an active
            # axis and the evidence axis decides -- adding a shortfall must
            # not *raise* such a release from 4 to 8.
            (
                {
                    "removed_required_library": True,
                    "verdict_or_severity_contribution": 4,
                },
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


class TestTheExitAndTheReportComeFromOneResolution:
    """The P1: `_exit_compare_release` must not re-implement the precedence.

    It used to be a parallel ladder of `sys.exit` calls beside
    `resolve_release_exit_decision_for_report`, with the two asserted to
    agree as an *invariant* rather than agreeing by construction. They did
    agree -- a reachable-state sweep found 0 divergences across 2240
    states -- so the duplication was a latent hazard, not a live bug. It
    stopped being latent in this very PR: a fifth axis had to be added
    twice, and the second copy is what let a proven removal outrank the
    evidence axis in one implementation and not the other.

    The sweep is kept as a test rather than discarded once the delegation
    landed, because its job now is to fail if anyone forks the algorithm
    again. It also pins the derivations the delegation depends on, which a
    single hand-written case would not: `worst_verdict` is *derived* from
    `library_results` through the real release rollup, never chosen
    independently of it. A first version of this sweep chose the two freely
    and reported 240 "divergences" that were all impossible states.
    """

    @staticmethod
    def _rollup(results: list[dict[str, object]]) -> str:
        from abicheck.cli_compare_release_helpers import _RELEASE_VERDICT_ORDER

        worst = "NO_CHANGE"
        for entry in results:
            if _RELEASE_VERDICT_ORDER.get(
                str(entry["verdict"]), 0
            ) > _RELEASE_VERDICT_ORDER.get(worst, 0):
                worst = str(entry["verdict"])
        return worst

    def test_every_reachable_release_state_agrees(self) -> None:
        import itertools

        from abicheck.frontends.cli.release_exit import _exit_compare_release
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision_for_report,
        )

        member_verdicts = [
            "NO_CHANGE",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            "API_BREAK",
            "BREAKING",
            "ERROR",
            "not_comparable",
        ]
        member_sets = [[v] for v in member_verdicts] + [
            list(pair)
            for pair in itertools.combinations_with_replacement(member_verdicts, 2)
        ]
        checked = 0
        for members, severity, (
            removed,
            fail_on,
        ), coverage, evidence, scope in itertools.product(
            member_sets,
            [None, 0, 2, 4],
            [([], False), (["libx"], True)],
            [0, 1],
            [0, 7],
            [0, 1],
        ):
            results: list[dict[str, object]] = [
                {
                    "library": f"l{i}",
                    "verdict": verdict,
                    "evidence_contract_error_contribution": evidence if i == 0 else 0,
                }
                for i, verdict in enumerate(members)
            ]
            worst = self._rollup(results)
            try:
                _exit_compare_release(
                    worst,
                    fail_on,
                    removed,
                    severity,
                    contract_coverage_exit_contribution=coverage,
                    library_results=results,
                    incomplete_scope_exit_contribution=scope,
                    no_comparison_completed_exit_contribution=0,
                )
                exited = 0
            except SystemExit as exc:
                exited = int(exc.code or 0)
            decision = resolve_release_exit_decision_for_report(
                worst,
                fail_on,
                removed,
                severity,
                coverage,
                results,
                incomplete_scope_contribution=scope,
                no_comparison_completed_contribution=0,
            )
            assert exited == decision.code, (
                f"process exit {exited} != reported {decision.code} for "
                f"members={members} severity={severity} removed={removed} "
                f"coverage={coverage} evidence={evidence} scope={scope}"
            )
            # And the invariant that makes the whole encoding legal.
            contributions = [
                value
                for key, value in decision.to_dict().items()
                if key.endswith("_contribution")
            ]
            assert decision.code == max(contributions), decision
            checked += 1
        assert checked == 2240, checked

    def test_a_shortfall_never_hides_a_proven_removed_library(self) -> None:
        """The P2 as an end-to-end statement, in exit-code terms.

        Adding an unrelated depth shortfall to a release that removed a
        required library used to lower its exit from 8 to 7 and report
        `run_outcome.gate: none`. Stated against both surfaces at once,
        since the report field and the process status were both wrong.
        """
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        def decide(evidence: bool) -> tuple[int, str, str]:
            decision = _release_decision(
                removed_required_library=True,
                evidence_contract_error_contribution=7 if evidence else 0,
            )
            outcome = run_outcome_dict_for_release("NO_CHANGE", decision.to_dict())
            return decision.code, str(outcome["gate"]), str(outcome["operational"])

        without = decide(evidence=False)
        with_shortfall = decide(evidence=True)
        assert without == (8, "abi_breaking", "none")
        # The removal still decides and is still gated; the shortfall is
        # additionally reported on the orthogonal operational axis.
        assert with_shortfall == (8, "abi_breaking", "evidence_contract_error")


class TestTheDecoderIsHonestAboutInputItCannotRead:
    """The decode's three fallbacks, which a happy-path test never reaches.

    Each one exists so a malformed or sentinel input degrades to "unknown"
    rather than to a confident wrong answer -- the failure mode this whole
    PR keeps running into -- so they are worth asserting rather than
    leaving as uncovered defensive code.
    """

    def test_an_operational_sentinel_verdict_reports_unknown_compatibility(
        self,
    ) -> None:
        """`ERROR`/`not_comparable` are not `Verdict` members.

        `compatibility: null` is the honest answer: no real comparison
        verdict was reached. The dishonest alternative is the floor
        `NO_CHANGE`, which would read as "nothing changed".
        """
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        decision = _release_decision()
        for sentinel in ("ERROR", "not_comparable", None):
            outcome = run_outcome_dict_for_release(sentinel, decision.to_dict())
            assert outcome["compatibility"] is None, sentinel

    def test_a_compatibility_contribution_outside_the_gate_scale_is_dropped(
        self,
    ) -> None:
        """A contribution that is not a gate exit code cannot name a gate.

        Reading it through anyway would invent a `PolicyGateDecision` from a
        number the gate scale has no meaning for.
        """
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        block = dict(_release_decision().to_dict())
        block["compatibility_contribution"] = 3  # not a gate code (0/1/2/4)
        assert run_outcome_dict_for_release("NO_CHANGE", block)["gate"] == "none"

    def test_a_non_mapping_member_entry_is_skipped_not_fatal(self) -> None:
        """`library_results` is decoded from JSON, so it can hold anything.

        A release must not crash on one malformed member entry while
        computing its own exit code.
        """
        from abicheck.policy.release_exit_decision import (
            resolve_release_exit_decision_for_report,
        )

        decision = resolve_release_exit_decision_for_report(
            "BREAKING",
            False,
            [],
            None,
            0,
            ["not-a-dict", None, {"library": "a", "verdict": "BREAKING"}],  # type: ignore[list-item]
        )
        assert decision.code == 4


class TestNoContributionPassedToADominantDecisionIsSilentlyDropped:
    """The bug class behind two separate Codex findings on this PR.

    `_dominant_decision` accepts a contribution keyword, then rebuilds the
    `ExitDecision` from a hand-listed set of fields. Twice now a field was
    added to the signature and not to that list -- accepted by the caller,
    silently absent from the result, and invisible to the guard that exists
    to catch a decision whose `code` is not `max(contributions)`. The first
    time it hid a depth shortfall; the second it hid a proven removed
    library, flipping `run_outcome.gate` to `none`.

    Naming a third field here would repeat the mistake, so this derives the
    field list from the signature itself: whatever `_dominant_decision`
    accepts, it must also carry. A field added later is covered without
    anyone remembering to add a case.
    """

    @staticmethod
    def _contribution_keywords() -> list[str]:
        import inspect

        from abicheck.policy.exit_decision_precedence import _dominant_decision

        return [
            name
            for name, param in inspect.signature(_dominant_decision).parameters.items()
            if name.endswith("_contribution")
            and param.default is not inspect.Parameter.empty
        ]

    def test_the_signature_actually_exposes_contribution_keywords(self) -> None:
        """Guard the guard: an empty list would make the sweep vacuous."""
        assert len(self._contribution_keywords()) >= 6

    @pytest.mark.parametrize("dominant_code", (5, 9))
    def test_every_accepted_contribution_survives_into_the_decision(
        self, dominant_code: int
    ) -> None:
        from abicheck.policy.exit_decision import ExitReason
        from abicheck.policy.exit_decision_precedence import _dominant_decision

        for keyword in self._contribution_keywords():
            # `NOT_COMPARABLE`'s own field is not among the keywords, so it
            # is never the dominant axis for any field under test here.
            decision = _dominant_decision(
                dominant_code,
                ExitReason.NOT_COMPARABLE,
                **{keyword: 1},
            )
            assert getattr(decision, keyword) == 1, (
                f"{keyword} was accepted by _dominant_decision but is absent "
                f"from the ExitDecision it built (got "
                f"{getattr(decision, keyword)!r})"
            )
            assert decision.code == dominant_code

    def test_a_custom_code_below_a_passed_contribution_is_rejected(self) -> None:
        """The guard must see every field too, not just the listed ones.

        A contribution missing from the *preserved* tuple is worse than one
        missing from the result: the `code == max(contributions)` check
        cannot fire, so an illegal decision is constructed rather than
        refused.
        """
        from abicheck.policy.exit_decision import ExitReason
        from abicheck.policy.exit_decision_precedence import _dominant_decision

        for keyword in self._contribution_keywords():
            with pytest.raises(ValueError, match="must strictly exceed"):
                _dominant_decision(2, ExitReason.NOT_COMPARABLE, **{keyword: 4})

    def test_the_custom_removal_code_case_end_to_end(self) -> None:
        """Codex's own reproduction, through the public resolver.

        A caller using the documented custom-code support with a removal
        code *below* the evidence code takes the fallback branch, where the
        removal contribution was being dropped -- reporting `gate: none`
        for a release with a proven removed library.
        """
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        decision = _release_decision(
            removed_required_library=True,
            severity_scheme_active=True,
            evidence_contract_error_contribution=7,
            removed_required_library_code=6,
        )
        assert decision.code == 7
        assert decision.removed_required_library_contribution == 6
        outcome = run_outcome_dict_for_release("NO_CHANGE", decision.to_dict())
        assert outcome["gate"] == "abi_breaking"
        assert outcome["operational"] == "evidence_contract_error"
