"""ADR-065 D8: a FAILED member is an operational error on the release exit.

A degraded member (extraction or comparison failed) is recorded ``failed``
in the scope record. It exits ``4`` under every completeness policy, whether
it matched a counterpart or exists on OLD only (no library result at all),
and never reads as a clean release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model.scope_acquisition import AcquisitionState, ScopeAcquisitionRecord
from abicheck.policy.exit_decision import ExitDecision, ExitReason
from abicheck.workflows.release_scope import (
    build_release_scope_record,
    release_inventory_evidence,
)

# ---------------------------------------------------------------------------
# ADR-065 D8: a FAILED member is an operational error (exit 4), under every
# policy, whether it matched a counterpart or was present on OLD only.
# ---------------------------------------------------------------------------


def _failed_member_exit(
    *,
    policy: str,
    libs: dict[str, str],
    old_only_failed: bool = False,
) -> tuple[ScopeAcquisitionRecord, ExitDecision]:
    """Drive the real record builder, scope decision and release resolver
    with one member per entry of *libs* (name -> verdict). A ``failed``
    verdict on a matched member is recorded the way ``release_compare``
    records it; with *old_only_failed* the degraded member exists on OLD
    only and gets no library result at all (only the scope record knows)."""
    from abicheck.policy.release_exit_decision import (
        resolve_release_exit_decision_with_assurance,
    )
    from abicheck.policy.scope_completeness import resolve_scope_decision

    old_names = list(libs)
    old_map = {k: Path(f"/o/{k}") for k in old_names}
    new_map = {k: Path(f"/n/{k}") for k in old_names}
    matched = list(old_names)
    old_failed: dict[str, str] = {}
    if old_only_failed:
        matched = [k for k in old_names if libs[k] != "failed"]
        old_failed = {k: "degraded capture" for k in old_names if libs[k] == "failed"}
        new_map = {k: Path(f"/n/{k}") for k in matched}
        results = [{"library": k, "verdict": libs[k]} for k in matched]
    else:
        results = [
            {"library": k, "verdict": libs[k]}
            | ({"reason": "degraded capture"} if libs[k] == "failed" else {})
            for k in old_names
        ]
    record = build_release_scope_record(
        old_map,
        new_map,
        matched,
        results,
        release_inventory_evidence(old_stored=False, new_stored=False),
        old_failed=old_failed,
    )
    decision = resolve_scope_decision(record, policy)
    worst = "BREAKING" if "BREAKING" in libs.values() else "NO_CHANGE"
    ex = resolve_release_exit_decision_with_assurance(
        worst,
        False,
        [],
        None,
        library_results=results,
        incomplete_scope_exit_contribution=decision.incomplete_scope_exit_contribution,
        no_comparison_completed_exit_contribution=(
            decision.no_comparison_completed_exit_contribution
        ),
        failed_member_exit_contribution=decision.failed_member_exit_contribution,
    )
    return record, ex


class TestFailedMemberOperationalError:
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_matched_failed_member_beside_a_clean_one_exits_4(
        self, policy: str
    ) -> None:
        record, ex = _failed_member_exit(
            policy=policy, libs={"libA.so": "NO_CHANGE", "libB.so": "failed"}
        )
        assert any(m.state is AcquisitionState.FAILED for m in record.members)
        assert ex.code == 4
        assert ExitReason.OPERATIONAL_ERROR in ex.reasons
        assert ExitReason.CLEAN not in ex.reasons

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_old_only_failed_member_exits_4_without_a_library_result(
        self, policy: str
    ) -> None:
        record, ex = _failed_member_exit(
            policy=policy,
            libs={"libA.so": "NO_CHANGE", "libB.so": "failed"},
            old_only_failed=True,
        )
        assert any(m.state is AcquisitionState.FAILED for m in record.members)
        assert ex.code == 4
        assert ExitReason.OPERATIONAL_ERROR in ex.reasons
        assert ExitReason.CLEAN not in ex.reasons

    def test_failed_beside_a_breaking_member_stays_4(self) -> None:
        _, ex = _failed_member_exit(
            policy="warn", libs={"libA.so": "BREAKING", "libB.so": "failed"}
        )
        assert ex.code == 4
        assert ExitReason.OPERATIONAL_ERROR in ex.reasons

    def test_all_members_compared_is_still_clean(self) -> None:
        record, ex = _failed_member_exit(
            policy="warn", libs={"libA.so": "NO_CHANGE", "libB.so": "NO_CHANGE"}
        )
        assert not record.is_incomplete
        assert ex.code == 0
        assert ex.reasons == (ExitReason.CLEAN,)

    def test_library_result_verdict_failed_is_operational_without_a_record(
        self,
    ) -> None:
        from abicheck.policy.release_exit_decision import (
            resolve_release_exit_decision_with_assurance,
        )

        ex = resolve_release_exit_decision_with_assurance(
            "NO_CHANGE",
            False,
            [],
            None,
            library_results=[
                {"library": "libA.so", "verdict": "NO_CHANGE"},
                {"library": "libB.so", "verdict": "failed"},
            ],
        )
        assert ex.code == 4
        assert ExitReason.OPERATIONAL_ERROR in ex.reasons
