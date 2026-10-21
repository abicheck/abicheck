"""The report-owned policy facts the HTML compute reads.

``html_report.py`` no longer imports policy directly; it reads these helpers
instead. Each test states the contract against the policy function the
helper composes, not against the helper's own body, so a wrong composition
(a dropped evidence caveat, an expired rule shown as active, a percentage
read from the wrong ledger) fails here rather than only in a golden file.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.policy.classification import evidence_status_for_result, impact_for
from abicheck.policy.evaluate import effective_kind_sets
from abicheck.policy.reclassify import ReclassifyRule
from abicheck.policy.severity import Verdict
from abicheck.report.disposition_audit import (
    compute_disposition_audit,
    disposition_audit_reusing_document,
)
from abicheck.report.document import ReportDocument
from abicheck.report.finding import (
    active_reclassify_labels,
    impact_text_for,
    kind_sets_for_result,
)
from abicheck.report.versioning_policy_section import compute_versioning_policy

_TODAY = date(2026, 10, 21)


def _change(kind: ChangeKind, symbol: str = "f") -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value} {symbol}")


def _result(*changes: Change) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=list(changes),
        policy="strict_abi",
    )


def _json(value: object) -> object:
    """Normalise tuple-vs-list and key order so two equal audits compare equal."""
    return json.loads(json.dumps(value, sort_keys=True))


class TestImpactTextFor:
    def test_change_without_kind_has_no_impact_text(self) -> None:
        assert impact_text_for(SimpleNamespace(kind=None)) == ""

    def test_known_kind_carries_the_policy_impact_text(self) -> None:
        change = _change(ChangeKind.FUNC_REMOVED)
        expected = impact_for(
            ChangeKind.FUNC_REMOVED, evidence_status_for_result(change, ())
        )
        assert impact_text_for(change, ()) == (expected or "")
        assert impact_text_for(change, ()) != ""


class TestActiveReclassifyLabels:
    def test_no_policy_file_has_no_labels(self) -> None:
        assert active_reclassify_labels(None, _TODAY) == ()

    def test_policy_file_without_reclassify_has_no_labels(self) -> None:
        assert active_reclassify_labels(SimpleNamespace(reclassify=[]), _TODAY) == ()

    def test_expired_rule_is_dropped_and_active_rule_is_disclosed(self) -> None:
        expired = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            change_kind="func_removed",
            symbol="_Z1fv",
            expires=date(2020, 1, 1),
        )
        active = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            change_kind="func_removed",
            symbol="_Z1gv",
            expires=date(2099, 1, 1),
        )
        policy_file = SimpleNamespace(reclassify=[expired, active])

        assert active_reclassify_labels(policy_file, _TODAY) == (active.describe(),)


class TestKindSetsForResult:
    def test_matches_the_policy_resolution_for_the_same_result(self) -> None:
        result = _result(_change(ChangeKind.FUNC_REMOVED))
        assert kind_sets_for_result(result) == effective_kind_sets(result)


class TestComputeVersioningPolicy:
    def test_no_policy_file_states_no_versioning_policy(self) -> None:
        assert compute_versioning_policy(_result()) is None

    def test_result_without_verdict_has_no_acceptance(self) -> None:
        assert compute_versioning_policy(SimpleNamespace(policy_file=None)) is None


class TestDispositionAuditReusingDocument:
    def test_without_document_computes_the_audit_independently(self) -> None:
        result = _result(_change(ChangeKind.FUNC_REMOVED))
        audit = disposition_audit_reusing_document(result, None, None)
        assert _json(audit.to_dict()) == _json(
            compute_disposition_audit(result).to_dict()
        )

    def test_with_document_reuses_the_documents_audit(self) -> None:
        result = _result(_change(ChangeKind.FUNC_REMOVED))
        document = ReportDocument.from_mapping(
            {"disposition_audit": compute_disposition_audit(result).to_dict()}
        )
        audit = disposition_audit_reusing_document(result, None, document)
        assert _json(audit.to_dict()) == _json(
            compute_disposition_audit(result).to_dict()
        )
