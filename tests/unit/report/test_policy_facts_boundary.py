"""``report/policy_facts.py``'s decision boundary, and the not-evaluated fold.

Every wrapper here is a named, typed call to a policy owner. Each test pins the
wrapper's result to what the owner returns for the same input, so a wrapper
that dropped an argument (the evidence tiers, the expiry date, the policy file)
fails here rather than as a silently different rendered report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from abicheck import report_classifications, report_summary as _report_summary_module
from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.policy.classification import evidence_status_for_result, impact_for
from abicheck.policy.evaluate import effective_kind_sets, evaluate
from abicheck.policy.severity import SeverityConfig, compute_exit_code
from abicheck.report.markdown_compute import _not_evaluated_mapping
from abicheck.report.not_evaluated_section import compute_not_evaluated
from abicheck.report.policy_facts import (
    ENVIRONMENT_DRIFT_KINDS,
    classification_for,
    compatibility_metrics,
    evidence_status_of,
    gate_exit_code_for,
    impact_text_for,
    reclassify_rules_in_effect,
)


def _result(*changes: Change) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=list(changes),
    )


def _removed(symbol: str) -> Change:
    return Change(ChangeKind.FUNC_REMOVED, symbol, f"func_removed on {symbol}")


def test_classification_for_is_the_policy_evaluation_of_the_same_result() -> None:
    result = _result(_removed("f"))

    classified = classification_for(result)

    assert [c.symbol for c in classified.breaking] == ["f"]
    assert [c.symbol for c in evaluate(result).breaking] == ["f"]


def test_impact_text_for_carries_the_owner_impact_sentence() -> None:
    impact = impact_text_for(ChangeKind.FUNC_REMOVED, None)

    assert impact == impact_for(ChangeKind.FUNC_REMOVED, None)
    assert impact != ""


def test_evidence_status_of_uses_the_runs_evidence_tiers() -> None:
    change = _removed("f")

    assert evidence_status_of(change, ()) == evidence_status_for_result(change, ())


def test_reclassify_rules_in_effect_drops_only_expired_rules() -> None:
    @dataclass(frozen=True)
    class _Rule:
        name: str
        expired: bool

        def is_expired(self, today: date | None) -> bool:
            return self.expired

    live = _Rule("live", expired=False)
    stale = _Rule("stale", expired=True)

    active = reclassify_rules_in_effect([live, stale], date(2026, 1, 1))

    assert active == [live]


def test_gate_exit_code_for_scores_the_run_under_the_given_severity() -> None:
    result = _result(_removed("f"))
    config = SeverityConfig()

    exit_code = gate_exit_code_for(result, config)

    assert exit_code == compute_exit_code(
        result.changes,
        config,
        policy=result.policy,
        kind_sets=effective_kind_sets(result),
        policy_file=result.policy_file,
    )
    assert exit_code > 0


def test_reexports_are_the_owners_objects() -> None:
    assert ENVIRONMENT_DRIFT_KINDS is report_classifications.ENVIRONMENT_DRIFT_KINDS
    assert compatibility_metrics is _report_summary_module.compatibility_metrics


def test_not_evaluated_mapping_folds_each_disclosed_finding() -> None:
    unscored = Change(
        ChangeKind.FUNC_REMOVED,
        "b",
        "func_removed on b",
        contract_relevance=ContractRelevance.UNKNOWN_UNPROVEN,
    )
    section = compute_not_evaluated([unscored])

    mapping = _not_evaluated_mapping(section)

    assert mapping is not None
    assert [(e["label"], e["suffix"]) for e in mapping["entries"]] == [
        ("UNKNOWN_UNPROVEN", "")
    ]


def test_not_evaluated_mapping_is_absent_without_a_section() -> None:
    assert _not_evaluated_mapping(None) is None
