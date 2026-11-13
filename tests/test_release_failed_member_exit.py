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


# ---------------------------------------------------------------------------
# Every consumer of the decision agrees with the exit (self-review of #2019):
# the PR comment headline and the no-baseline set's run_outcome.
# ---------------------------------------------------------------------------


def _scope_section(states: list[str]) -> dict[str, object]:
    return {
        "completeness": "incomplete",
        "policy": "warn",
        "incomplete_scope_exit_contribution": 0,
        "no_comparison_completed": False,
        "no_comparison_completed_exit_contribution": 0,
        "members": [{"name": f"lib{i}.so", "state": s} for i, s in enumerate(states)],
    }


@pytest.mark.parametrize(
    ("states", "blocking"),
    [
        (["completed", "failed"], True),  # D8: failed blocks under warn
        (["completed", "not_supplied"], False),  # warn-accepted gap stays advisory
        (["completed", "completed"], False),
    ],
)
def test_pr_comment_scope_blocking_follows_a_failed_member(
    states: list[str], blocking: bool
) -> None:
    from abicheck.pr_comment import _from_release

    model = _from_release({"libraries": [], "comparison_scope": _scope_section(states)})
    assert model.scope_blocking is blocking


@pytest.mark.parametrize(
    ("failed", "expected"), [(True, "extraction_error"), (False, "none")]
)
def test_no_baseline_set_run_outcome_names_a_failed_member(
    failed: bool, expected: str
) -> None:
    from abicheck.report.audit_set import _operational
    from abicheck.workflows.no_baseline_set import (
        MemberAudit,
        MemberAuditStatus,
        NoBaselineSetResult,
        build_no_baseline_set_record,
    )
    from tests.test_no_baseline_set_record import _plan

    plan = _plan(
        {"liba.so", "libb.so"},
        failed={"libb.so": "degraded capture"} if failed else None,
    )
    audits = tuple(
        MemberAudit(
            member=k,
            path=plan.side.members[k],
            status=MemberAuditStatus.COMPLETED,
            reason="ok",
        )
        for k in plan.audit_keys
    )
    record = build_no_baseline_set_record(plan, list(audits))
    result = NoBaselineSetResult(plan=plan, audits=audits, record=record)
    assert _operational(result, evidence_contract=False).value == expected


# ---------------------------------------------------------------------------
# ADR-065 D8 on the report: the release's comparison_scope block carries the
# same 0/4 the exit folds, so a JSON consumer sees why the exit is 4.
# Drives the real `compare` fan-out over stored packages (libA identical on
# both sides = NO_CHANGE; libB degraded on NEW = FAILED).
# ---------------------------------------------------------------------------

from schema_validation import validate_instance  # noqa: E402
from test_release_scope_bundle import _lib  # noqa: E402
from test_release_scope_completeness import (  # noqa: E402
    _invoke_json,
    _release_config,
    _write_stored_package,
)

from abicheck.schemas import load_compare_report_schema  # noqa: E402


def _release_pair(tmp_path: Path, *, degraded: bool) -> tuple[Path, Path]:
    # Two members on both sides, so the run is a release (not a scalar pair).
    shared = {
        "libA.so": _lib("libA.so", exports=("a",)),
        "libC.so": _lib("libC.so", exports=("c",)),
    }
    old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
    _write_stored_package(old, shared)
    if degraded:
        extra = {"libB.so": _lib("libB.so", exports=("b",))}
        _write_stored_package(
            new, {**shared, **extra}, degraded={"libB.so": "dump failed: boom"}
        )
    else:
        _write_stored_package(new, shared)
    return old, new


@pytest.mark.parametrize("policy", ["warn", "block"])
def test_release_scope_block_reports_failed_member_contribution_4(
    tmp_path: Path, policy: str
) -> None:
    old, new = _release_pair(tmp_path, degraded=True)
    cfg = _release_config(tmp_path, on_incomplete_scope=policy)
    code, doc = _invoke_json("compare", str(old), str(new), "--config", str(cfg))
    assert code == 4
    scope = doc["comparison_scope"]
    assert scope["failed_member_exit_contribution"] == 4
    assert (
        scope["failed_member_exit_contribution"]
        == (doc["exit"]["operational_error_contribution"])
    )
    verdicts = sorted(lib["verdict"] for lib in doc["libraries"])
    assert verdicts == ["NO_CHANGE", "NO_CHANGE"]


@pytest.mark.parametrize("policy", ["warn", "block"])
def test_all_clean_release_reports_failed_member_contribution_0(
    tmp_path: Path, policy: str
) -> None:
    old, new = _release_pair(tmp_path, degraded=False)
    cfg = _release_config(tmp_path, on_incomplete_scope=policy)
    code, doc = _invoke_json("compare", str(old), str(new), "--config", str(cfg))
    assert code == 0
    assert doc["comparison_scope"]["failed_member_exit_contribution"] == 0


@pytest.mark.parametrize("degraded", [True, False])
def test_failed_member_contribution_validates_against_schema(
    tmp_path: Path, degraded: bool
) -> None:
    old, new = _release_pair(tmp_path, degraded=degraded)
    cfg = _release_config(tmp_path, on_incomplete_scope="warn")
    _, doc = _invoke_json("compare", str(old), str(new), "--config", str(cfg))
    defs = load_compare_report_schema()["$defs"]
    # Only $defs: the root schema's own `required` belongs to the whole report.
    validate_instance(
        doc["comparison_scope"],
        {"$defs": defs, "$ref": "#/$defs/comparison_scope"},
    )
    expected = 4 if degraded else 0
    assert doc["comparison_scope"]["failed_member_exit_contribution"] == expected


def test_failed_member_contribution_is_optional_in_schema() -> None:
    scope = load_compare_report_schema()["$defs"]["comparison_scope"]
    assert scope["properties"]["failed_member_exit_contribution"]["enum"] == [0, 4]
    assert "failed_member_exit_contribution" not in scope["required"]
