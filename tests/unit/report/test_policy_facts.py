"""``report/policy_facts.py``: the policy and release facts source modules consume.

Covers every resolver's branches (severity config present and absent, findings
supplied and not, an analysis-assurance block present and absent, and
``--require-complete-analysis`` set and not), and the ``--stat`` JSON,
root-cause JSON and Markdown ``--stat`` paths that feed from them.
"""

from __future__ import annotations

import json

from abicheck.checker import DiffResult
from abicheck.policy.analysis_assurance import (
    INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION,
    AnalysisAssurance,
)
from abicheck.policy.evaluate import effective_kind_sets
from abicheck.policy.gate_decision import gate_decision_for_result
from abicheck.policy.severity import SeverityConfig, SeverityLevel
from abicheck.report.diff_report import to_json, to_stat_json
from abicheck.report.policy_facts import (
    kind_sets_for,
    policy_facts_for,
    release_recommendation_for,
    stat_facts_for,
    summary_for,
)
from abicheck.report_summary import build_summary
from abicheck.semver import recommend_release_for_report
from tests._report_test_helpers import stat_line


def _result() -> DiffResult:
    return DiffResult(old_version="1.0", new_version="2.0", library="libfoo.so")


def _config() -> SeverityConfig:
    return SeverityConfig(addition=SeverityLevel.ERROR)


class TestPolicyFactsFor:
    def test_no_severity_config_resolves_no_gate(self) -> None:
        facts = policy_facts_for(_result(), None)

        assert facts.gate is None

    def test_severity_config_resolves_the_owner_gate(self) -> None:
        result = _result()
        config = _config()

        facts = policy_facts_for(result, config)

        expected = gate_decision_for_result(result, config)
        assert expected is not None
        assert facts.gate is not None
        assert facts.gate.exit_code == expected.exit_code

    def test_kind_sets_match_the_owner(self) -> None:
        result = _result()

        assert kind_sets_for(result) == effective_kind_sets(result)
        assert policy_facts_for(result, None).kind_sets == effective_kind_sets(result)


class TestSummaryAndRelease:
    def test_summary_without_findings_matches_build_summary(self) -> None:
        result = _result()

        assert summary_for(result).total_changes == build_summary(result).total_changes
        assert summary_for(result).total_changes == 0

    def test_summary_with_supplied_findings_is_forwarded(self) -> None:
        # An empty envelope-resolved finding sequence is still a supplied one:
        # the resolver must pass it through, not recompute.
        assert summary_for(_result(), findings=()).total_changes == 0

    def test_release_recommendation_matches_the_owner(self) -> None:
        result = _result()

        assert (
            release_recommendation_for(result).to_dict()
            == recommend_release_for_report(result).to_dict()
        )


class TestStatFacts:
    def test_no_assurance_block_resolves_no_assurance(self) -> None:
        assert stat_facts_for(_result(), None).assurance is None

    def test_assurance_block_carries_exit_contribution(self) -> None:
        result = _result()
        result.analysis_assurance = AnalysisAssurance(status="partial")

        facts = stat_facts_for(result, None)
        assert facts.assurance is not None
        block, exit_contribution = facts.assurance

        assert block == AnalysisAssurance(status="partial").to_dict()
        assert exit_contribution == 0

    def test_require_complete_analysis_raises_the_exit_contribution(self) -> None:
        result = _result()
        result.analysis_assurance = AnalysisAssurance(status="partial")

        facts = stat_facts_for(result, None, require_complete_analysis=True)

        assert facts.assurance is not None
        assert facts.assurance[1] == INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION


class TestStatJsonPath:
    def test_without_severity_config_adds_no_severity_block(self) -> None:
        payload = json.loads(to_stat_json(_result()))

        assert "severity" not in payload
        assert payload["release_recommendation"] == (
            recommend_release_for_report(_result()).to_dict()
        )

    def test_with_severity_config_adds_the_gate_block(self) -> None:
        payload = json.loads(to_stat_json(_result(), severity_config=_config()))

        assert payload["severity"]["exit_code"] == 0
        assert payload["severity"]["blocking"] is False

    def test_assurance_block_and_exit_contribution_are_emitted(self) -> None:
        result = _result()
        result.analysis_assurance = AnalysisAssurance(status="partial")

        payload = json.loads(to_stat_json(result, require_complete_analysis=True))

        assert payload["analysis_assurance"] == (
            AnalysisAssurance(status="partial").to_dict()
        )
        assert (
            payload["analysis_assurance_exit_contribution"]
            == INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION
        )


class TestRootCauseJsonPath:
    def test_root_cause_mode_carries_the_gate_block(self) -> None:
        payload = json.loads(
            to_json(_result(), report_mode="root-cause", severity_config=_config())
        )

        assert payload["changes"] == []
        assert payload["root_cause_count"] == 0
        assert payload["severity"]["exit_code"] == 0

    def test_root_cause_mode_with_show_only_still_resolves_kind_sets(self) -> None:
        payload = json.loads(
            to_json(
                _result(),
                report_mode="root-cause",
                show_only="breaking",
                severity_config=_config(),
            )
        )

        assert payload["changes"] == []


class TestMarkdownStatPath:
    def test_plain_stat_line_has_no_gate_suffix(self) -> None:
        assert stat_line(_result()) == "NO_CHANGE: no changes (0 total)"

    def test_stat_line_with_severity_config_reports_the_gate(self) -> None:
        assert (
            stat_line(_result(), severity_config=_config())
            == "NO_CHANGE: no changes (0 total) [gate: PASS]"
        )
