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

"""ADR-071's fold, as executable invariants over generated member sets.

`AGENTS.md`'s bug-class regression-testing contract, applied to a *new*
reusable fold primitive rather than to a reported defect: this is the
"Primitive-level property tests" treatment that file asks for whenever a
reusable merge/dedupe/grouping/folding primitive is added anywhere in this
codebase — a standalone property class stating the primitive's own contract
as invariants, decoupled from any one caller's domain logic, before the
domain-level example tests.

Three invariants, each named in this work's own task statement and each a
real way the fold could have been written wrongly:

1. **Cardinality agreement** (ADR-071 D1) — over one member the fold is the
   identity, so a one-member package yields exactly what the scalar path
   yields for that pair. Falsified by any fold that divides by member count,
   requires a quorum, or treats "release" as its own extra state.
2. **Non-maskability** (D2) — a member that fell short is never hidden by a
   complete sibling, at any sibling count and in any order. Falsified by
   `min`, by `all(...)`-as-"majority", or by an order-dependent reduce.
3. **Monotonicity** (D2) — adding a member never lowers the contribution or
   improves the status. Falsified by an averaging fold, and by any fold whose
   answer depends on position.

The oracle for each is stated independently of the implementation, per
`AGENTS.md`'s "against a stated oracle that is not the same formula/helper
the implementation itself uses": the expected contribution is derived from a
plain `any(status != "complete")` over the generated statuses, written here,
not from `release_assurance_exit_contribution`; the expected aggregate status
is derived from an independently written index into a locally restated order.

Generated, not fixed-example: Hypothesis drives member count, the per-member
status drawn from the whole vocabulary (plus an unknown status, so the
fail-closed rank is exercised too), names, and notes. A fourth class pins the
*agreement* between the two answers the decision carries, which is the
property ADR-071 D5 exists to guarantee and the one a report contradicting
its own exit code would violate.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.policy.release_assurance import (
    ASSURANCE_STATUS_ORDER,
    INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION,
    MemberAssurance,
    release_assurance_diagnostic,
    release_assurance_exit_contribution,
    release_assurance_status,
    resolve_release_assurance_decision,
)

#: The status vocabulary, restated locally rather than imported from the
#: implementation's own `AnalysisAssurance.ASSURANCE_STATUS_VALUES` -- a
#: generator seeded from the value under test cannot detect a value the
#: implementation forgot. Includes a deliberately unknown status so the
#: fail-closed rank (`_UNKNOWN_RANK`) is exercised as a real input rather
#: than only by a hand-written single case.
_STATUSES = (
    "complete",
    "partial",
    "failed",
    "not_comparable",
    "not_requested",
    "some_future_status",
)

#: The expected-worst order, written out here independently of
#: ASSURANCE_STATUS_ORDER. An unknown status sorts last (worst) by
#: construction -- see `_expected_status`.
_ORACLE_ORDER = ("complete", "not_requested", "partial", "failed", "not_comparable")


def _expected_contribution(statuses: tuple[str, ...], require_complete: bool) -> int:
    """The oracle for the exit floor, stated from the definition rather than
    from the implementation: nothing is contributed unless the setting is on,
    and then exactly when some member is not ``complete``."""
    if not require_complete:
        return 0
    return 1 if any(s != "complete" for s in statuses) else 0


def _expected_status(statuses: tuple[str, ...]) -> str:
    """The oracle for the aggregate status: the member whose status sits
    latest in :data:`_ORACLE_ORDER`, with an unrecognized status ranked
    beyond every recognized one."""
    if not statuses:
        return "not_requested"

    def rank(s: str) -> int:
        return _ORACLE_ORDER.index(s) if s in _ORACLE_ORDER else len(_ORACLE_ORDER)

    return max(statuses, key=rank)


_statuses = st.lists(st.sampled_from(_STATUSES), min_size=0, max_size=12)


def _members(statuses: list[str]) -> tuple[MemberAssurance, ...]:
    """Members carrying *statuses*, with distinct names so a name-keyed
    implementation bug (deduplicating two members that share a status, say)
    cannot hide behind a collision."""
    return tuple(
        MemberAssurance(name=f"lib{i}.so", status=s, notes=(f"note for {i}",))
        for i, s in enumerate(statuses)
    )


class TestFoldAgreesWithItsOracle:
    """The fold computes what the definition says, for every generated set."""

    @settings(max_examples=400)
    @given(_statuses, st.booleans())
    def test_contribution_matches_the_oracle(
        self, statuses: list[str], require_complete: bool
    ) -> None:
        got = release_assurance_exit_contribution(
            _members(statuses), require_complete=require_complete
        )
        assert got == _expected_contribution(tuple(statuses), require_complete)

    @settings(max_examples=400)
    @given(_statuses)
    def test_status_matches_the_oracle(self, statuses: list[str]) -> None:
        assert release_assurance_status(_members(statuses)) == _expected_status(
            tuple(statuses)
        )

    @settings(max_examples=200)
    @given(_statuses)
    def test_the_contribution_is_only_ever_zero_or_the_floor(
        self, statuses: list[str]
    ) -> None:
        """Never an arithmetic accumulation. A fold that summed member
        contributions would reach 2+ for two short members and silently
        outrank a real compatibility `2`."""
        got = release_assurance_exit_contribution(
            _members(statuses), require_complete=True
        )
        assert got in {0, INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION}


class TestCardinalityAgreement:
    """ADR-071 D1: a one-member release agrees with the scalar path."""

    @settings(max_examples=200)
    @given(st.sampled_from(_STATUSES), st.booleans())
    def test_one_member_fold_is_the_identity(
        self, status: str, require_complete: bool
    ) -> None:
        """The contribution for a single member equals what that member's own
        status alone implies -- which is exactly the rule
        `analysis_assurance_exit_contribution` applies to a scalar
        `DiffResult` (pinned against the real scalar function in
        ``test_scalar_and_release_floors_use_one_number`` below)."""
        one = release_assurance_exit_contribution(
            _members([status]), require_complete=require_complete
        )
        scalar_rule = 0 if (not require_complete or status == "complete") else 1
        assert one == scalar_rule

    @settings(max_examples=200)
    @given(st.sampled_from(_STATUSES))
    def test_one_member_status_is_that_member(self, status: str) -> None:
        assert release_assurance_status(_members([status])) == status

    def test_scalar_and_release_floors_use_one_number(self) -> None:
        """The release floor and the scalar floor are the same constant.

        Restated rather than imported into the implementation (the policy
        leaf may not import the flat-root module -- see its docstring), so
        this is the executable check that the two cannot drift apart."""
        from abicheck.analysis_assurance import (
            INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION as SCALAR,
        )

        assert INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION == SCALAR


class TestIncompleteMemberIsNotMaskable:
    """ADR-071 D2: a complete sibling never hides a short member."""

    @settings(max_examples=400)
    @given(
        st.sampled_from([s for s in _STATUSES if s != "complete"]), st.integers(0, 10)
    )
    def test_any_number_of_complete_siblings_cannot_mask_one_shortfall(
        self, bad: str, complete_siblings: int
    ) -> None:
        statuses = ["complete"] * complete_siblings + [bad]
        assert (
            release_assurance_exit_contribution(
                _members(statuses), require_complete=True
            )
            == INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION
        )
        assert release_assurance_status(_members(statuses)) != "complete"

    @settings(max_examples=400)
    @given(_statuses)
    def test_the_fold_is_order_independent(self, statuses: list[str]) -> None:
        """Both answers are invariant under permutation. An order-dependent
        reduce is the single most common way a hand-written fold over a
        member list goes wrong (`scope_completeness`'s own sibling primitive
        had exactly this class of finding; see AGENTS.md's
        ``_paired_stable_indices`` account)."""
        forward = _members(statuses)
        backward = _members(list(reversed(statuses)))
        assert release_assurance_exit_contribution(
            forward, require_complete=True
        ) == release_assurance_exit_contribution(backward, require_complete=True)
        assert release_assurance_status(forward) == release_assurance_status(backward)

    @settings(max_examples=200)
    @given(_statuses)
    def test_incomplete_members_are_recorded_even_when_the_setting_is_off(
        self, statuses: list[str]
    ) -> None:
        """ "Record before disposing": the setting decides acceptance, never
        whether the shortfall happened."""
        off = resolve_release_assurance_decision(
            _members(statuses), require_complete=False
        )
        on = resolve_release_assurance_decision(
            _members(statuses), require_complete=True
        )
        assert off.incomplete_member_count == on.incomplete_member_count
        assert off.exit_contribution == 0


class TestFoldIsMonotonic:
    """ADR-071 D2: adding a member never improves the answer."""

    @settings(max_examples=400)
    @given(_statuses, st.sampled_from(_STATUSES), st.booleans())
    def test_adding_a_member_never_lowers_the_contribution(
        self, statuses: list[str], extra: str, require_complete: bool
    ) -> None:
        before = release_assurance_exit_contribution(
            _members(statuses), require_complete=require_complete
        )
        after = release_assurance_exit_contribution(
            _members([*statuses, extra]), require_complete=require_complete
        )
        assert after >= before

    @settings(max_examples=400)
    @given(
        st.lists(st.sampled_from(_STATUSES), min_size=1, max_size=10),
        st.sampled_from(_STATUSES),
    )
    def test_adding_a_member_never_improves_the_status(
        self, statuses: list[str], extra: str
    ) -> None:
        """Rank never decreases. Checked on a non-empty base: the empty set's
        own ``not_requested`` is a deliberate floor of its own (a release that
        compared nothing does not claim a complete analysis), so the first
        member added to an empty set may legitimately *improve* the rank to
        ``complete`` -- stated as its own case below rather than silently
        excluded by the generator."""

        def rank(s: str) -> int:
            return (
                ASSURANCE_STATUS_ORDER.index(s)
                if s in ASSURANCE_STATUS_ORDER
                else len(ASSURANCE_STATUS_ORDER)
            )

        before = release_assurance_status(_members(statuses))
        after = release_assurance_status(_members([*statuses, extra]))
        assert rank(after) >= rank(before)

    def test_an_empty_release_does_not_claim_a_complete_analysis(self) -> None:
        decision = resolve_release_assurance_decision((), require_complete=True)
        assert decision.status == "not_requested"
        # ...but it does not borrow the scope axis's floor either: "nothing
        # was compared" is ADR-065 D7's question, not this axis's.
        assert decision.exit_contribution == 0


class TestDecisionIsSelfConsistent:
    """ADR-071 D5: the status a reader sees agrees with the number that gated
    them. A report contradicting its own exit code is the failure this
    property exists to foreclose."""

    @settings(max_examples=400)
    @given(st.lists(st.sampled_from(_STATUSES), min_size=1, max_size=10))
    def test_status_complete_iff_contribution_zero_under_the_setting(
        self, statuses: list[str]
    ) -> None:
        decision = resolve_release_assurance_decision(
            _members(statuses), require_complete=True
        )
        assert (decision.status == "complete") == (decision.exit_contribution == 0)

    @settings(max_examples=400)
    @given(_statuses, st.booleans())
    def test_incomplete_member_count_agrees_with_the_status(
        self, statuses: list[str], require_complete: bool
    ) -> None:
        decision = resolve_release_assurance_decision(
            _members(statuses), require_complete=require_complete
        )
        if decision.members:
            assert (decision.incomplete_member_count == 0) == (
                decision.status == "complete"
            )
        assert decision.incomplete_member_count <= len(decision.members)

    @settings(max_examples=300)
    @given(_statuses, st.integers(0, 5))
    def test_the_diagnostic_speaks_exactly_when_something_fell_short(
        self, statuses: list[str], base_exit: int
    ) -> None:
        """And never when the setting is off -- the axis must stay silent for
        every run that did not opt in, which is what keeps it additive."""
        members = _members(statuses)
        on = resolve_release_assurance_decision(members, require_complete=True)
        off = resolve_release_assurance_decision(members, require_complete=False)
        assert release_assurance_diagnostic(off, base_exit=base_exit) is None
        spoken = release_assurance_diagnostic(on, base_exit=base_exit)
        assert (spoken is not None) == (on.incomplete_member_count > 0)
        if spoken is not None:
            # Names the members, not merely a count (ADR-071 D6).
            for m in on.incomplete_members[:6]:
                assert m.name in spoken

    @settings(max_examples=200)
    @given(st.lists(st.sampled_from(_STATUSES), min_size=1, max_size=6))
    def test_the_diagnostic_never_claims_a_floor_it_did_not_impose(
        self, statuses: list[str]
    ) -> None:
        """Beside a real break, "Exit code floored to 1" would be false."""
        decision = resolve_release_assurance_decision(
            _members(statuses), require_complete=True
        )
        spoken = release_assurance_diagnostic(decision, base_exit=4)
        if spoken is not None:
            assert "floored to" not in spoken
            assert "which stands" in spoken


class TestReportProjectionFollowsTheDecision:
    """`report/AGENTS.md`: a renderer decides nothing. The section must be a
    projection of the decision, never a second derivation of it."""

    @settings(max_examples=300)
    @given(_statuses, st.booleans())
    def test_section_mirrors_the_decision_or_is_absent(
        self, statuses: list[str], require_complete: bool
    ) -> None:
        from abicheck.report.release_assurance import release_assurance_terms

        decision = resolve_release_assurance_decision(
            _members(statuses), require_complete=require_complete
        )
        terms = release_assurance_terms(decision)
        if not require_complete:
            assert terms.section is None
            return
        section = terms.section
        assert section is not None
        assert section["status"] == decision.status
        assert section["exit_contribution"] == decision.exit_contribution
        assert section["member_count"] == len(decision.members)
        assert section["incomplete_member_count"] == decision.incomplete_member_count
        assert [r["library"] for r in section["incomplete_members"]] == [
            m.name for m in decision.incomplete_members
        ]


class TestReleaseExitFoldCarriesTheAxis:
    """The axis reaches the real release `ExitDecision`, with `max`
    semantics: it raises a clean 0 to 1 and never lowers a real 2/4/8/16."""

    @pytest.mark.parametrize(
        "compat,expected_code,expected_named",
        [
            (0, 1, True),
            (1, 1, True),
            (2, 2, False),
            (4, 4, False),
        ],
    )
    def test_assurance_floor_folds_with_max_under_severity(
        self, compat: int, expected_code: int, expected_named: bool
    ) -> None:
        from abicheck.policy.exit_decision import ExitReason
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=True,
            verdict_or_severity_contribution=compat,
            analysis_assurance_contribution=1,
        )
        assert decision.code == expected_code
        assert decision.analysis_assurance_contribution == 1
        assert (ExitReason.ANALYSIS_ASSURANCE in decision.reasons) is expected_named

    @pytest.mark.parametrize(
        "dominant_kwargs,expected",
        [
            ({"not_comparable": True}, 16),
            ({"not_comparable": False, "removed_required_library": True}, 8),
            ({"not_comparable": False, "evidence_contract_error_contribution": 7}, 7),
        ],
    )
    def test_a_dominant_axis_preserves_but_is_never_lowered_by_assurance(
        self, dominant_kwargs: dict[str, object], expected: int
    ) -> None:
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        decision = resolve_release_exit_decision(
            severity_scheme_active=True,
            verdict_or_severity_contribution=0,
            analysis_assurance_contribution=1,
            **dominant_kwargs,  # type: ignore[arg-type]
        )
        assert decision.code == expected
        # Preserved for explainability, never deciding.
        assert decision.analysis_assurance_contribution == 1

    @settings(max_examples=200)
    @given(st.integers(0, 4), st.booleans())
    def test_omitting_the_axis_is_identical_to_passing_zero(
        self, compat: int, severity: bool
    ) -> None:
        """The additivity proof: a caller that never resolves the axis gets
        byte-identical decisions to one passing an explicit 0."""
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        omitted = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
        )
        explicit = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
            analysis_assurance_contribution=0,
        )
        assert omitted == explicit

    @settings(max_examples=400)
    @given(
        st.booleans(),  # not_comparable
        st.booleans(),  # severity_scheme_active
        st.integers(0, 4),  # verdict/severity contribution
        st.booleans(),  # removed_required_library
        st.sampled_from([0, 7]),  # evidence-contract axis
        st.sampled_from([0, 4]),  # operational error
    )
    def test_every_branch_carries_the_axis(
        self,
        not_comparable: bool,
        severity: bool,
        compat: int,
        removed: bool,
        evidence: int,
        operational: int,
    ) -> None:
        """No reachable branch of the release resolver drops the axis.

        The resolver has eight return sites across two schemes and four
        dominant overrides; a fold threaded through seven of them is the exact
        shape of the bug ADR-064's own history records twice (a new axis added
        to a signature that reached only some branches). Generated over the
        whole branch-selecting input space rather than asserted per branch, so
        a future branch added without the axis fails here without anyone having
        to remember to extend a parametrize list.

        Asserts the two things that must hold everywhere: the contribution is
        preserved on the decision (never silently dropped), and the resulting
        code is never *lower* than it would be with the axis at 0 (the axis can
        only raise, per D7).
        """
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        kwargs = {
            "not_comparable": not_comparable,
            "severity_scheme_active": severity,
            "verdict_or_severity_contribution": compat,
            "removed_required_library": removed,
            "evidence_contract_error_contribution": evidence,
            "operational_error_contribution": operational,
        }
        with_axis = resolve_release_exit_decision(
            **kwargs, analysis_assurance_contribution=1
        )
        without = resolve_release_exit_decision(
            **kwargs, analysis_assurance_contribution=0
        )
        assert with_axis.analysis_assurance_contribution == 1
        assert without.analysis_assurance_contribution == 0
        assert with_axis.code >= without.code
        # And it only ever *adds* the floor, never changes the rest.
        assert with_axis.code == max(without.code, 1)

    @settings(max_examples=400)
    @given(
        st.booleans(),
        st.booleans(),
        st.integers(0, 4),
        st.booleans(),
        st.sampled_from([0, 7]),
        st.sampled_from([0, 4]),
    )
    def test_the_notice_base_is_every_other_axis_not_just_compatibility(
        self,
        not_comparable: bool,
        severity: bool,
        compat: int,
        removed: bool,
        evidence: int,
        operational: int,
    ) -> None:
        """`ExitDecision.exit_without_analysis_assurance()` is the real base.

        The diagnostic's wording turns on "what would this run have exited
        without this axis", and a single field is not that number: under a
        dominant `16`/`8`/`7` the compatibility contribution can be `0` while
        the exit was decided elsewhere. Stated as a property over the whole
        branch-selecting space rather than the one `not_comparable` example
        that exposed it, and against an oracle (`max` over the other fields,
        recomputed here from `dataclasses.fields`) written independently of the
        method: an axis added to `ExitDecision` without being folded in fails
        here.
        """
        from dataclasses import fields as dc_fields

        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        decision = resolve_release_exit_decision(
            not_comparable=not_comparable,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
            removed_required_library=removed,
            evidence_contract_error_contribution=evidence,
            operational_error_contribution=operational,
            analysis_assurance_contribution=1,
        )
        oracle = max(
            (
                getattr(decision, f.name)
                for f in dc_fields(decision)
                if f.name.endswith("_contribution")
                and f.name != "analysis_assurance_contribution"
            ),
            default=0,
        )
        base = decision.exit_without_analysis_assurance()
        assert base == oracle
        # And the notice never claims a floor it did not impose: whenever some
        # other axis already reached the decision's own code, the wording must
        # not say "floored".
        spoken = release_assurance_diagnostic(
            resolve_release_assurance_decision(
                (MemberAssurance("lib.so", "partial", ("no dwarf",)),),
                require_complete=True,
            ),
            base_exit=base,
        )
        assert spoken is not None
        if base >= decision.code:
            assert "floored to" not in spoken

    @settings(max_examples=300)
    @given(_statuses)
    def test_the_section_carries_flat_attributed_notes(
        self, statuses: list[str]
    ) -> None:
        """The section must expose `notes` in the scalar block's own shape.

        The composite Action's `assurance_notes` query reads
        `analysis_assurance.notes` -- one flat list of strings -- to name *what*
        fell short in its `ANALYSIS_INCOMPLETE` job-summary line, and falls back
        to a bare "see the JSON report" when it is empty. A release section
        exposing only `incomplete_members[].notes` therefore reported the verdict
        without the reasons where a scalar run reported both: the same
        axis-reaches-some-consumers problem, one notch quieter.

        Two properties, both of which a naive flatten would break: every short
        member is represented (so the list cannot silently omit a member that
        had no notes of its own), and every line names its member (so a release
        reader can act on it at all).
        """
        from abicheck.report.release_assurance import build_release_assurance_section

        members = _members(statuses)
        decision = resolve_release_assurance_decision(members, require_complete=True)
        notes = build_release_assurance_section(decision)["notes"]
        assert isinstance(notes, list)
        assert all(isinstance(line, str) for line in notes)
        short = decision.incomplete_members
        # Every short member appears at least once, notes or not.
        for member in short:
            assert any(line.startswith(f"{member.name}: ") for line in notes), member
        # And nothing else does: a complete member contributes no line.
        complete_names = {m.name for m in members} - {m.name for m in short}
        for line in notes:
            assert line.split(":", 1)[0] not in complete_names
        assert (len(notes) == 0) == (len(short) == 0)
