"""Tests for SARIF 2.1.0 output (Sprint 7)."""
from __future__ import annotations

import json

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.sarif import to_sarif, to_sarif_str


def _make_result(
    changes: list[Change],
    verdict: Verdict = Verdict.BREAKING,
    library: str = "libfoo.so.1",
    old: str = "1.0",
    new: str = "2.0",
) -> DiffResult:
    return DiffResult(
        old_version=old,
        new_version=new,
        library=library,
        changes=changes,
        verdict=verdict,
    )


def _breaking_change() -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z3foov",
        description="Function foo() removed",
    )


def _compatible_change() -> Change:
    return Change(
        kind=ChangeKind.FUNC_ADDED,
        symbol="_Z3barv",
        description="Function bar() added",
    )


def _valued_change() -> Change:
    return Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED,
        symbol="_Z7get_valv",
        description="Return type changed",
        old_value="int",
        new_value="long",
    )


# ---------------------------------------------------------------------------
# Schema structure tests
# ---------------------------------------------------------------------------

class TestSarifSchema:
    def test_top_level_keys(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        assert doc["version"] == "2.1.0"
        assert "$schema" in doc
        assert "runs" in doc
        assert len(doc["runs"]) == 1

    def test_tool_driver(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        driver = doc["runs"][0]["tool"]["driver"]
        assert driver["name"] == "abicheck"
        assert "version" in driver
        assert "informationUri" in driver

    def test_rules_populated(self) -> None:
        doc = to_sarif(_make_result([_breaking_change(), _compatible_change()]))
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in rules}
        assert "func_removed" in rule_ids
        assert "func_added" in rule_ids

    def test_rules_deduplicated(self) -> None:
        """Two changes of same kind → one rule."""
        c1 = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="foo removed")
        c2 = Change(kind=ChangeKind.FUNC_REMOVED, symbol="bar", description="bar removed")
        doc = to_sarif(_make_result([c1, c2]))
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        func_removed_rules = [r for r in rules if r["id"] == "func_removed"]
        assert len(func_removed_rules) == 1

    def test_results_count(self) -> None:
        doc = to_sarif(_make_result([_breaking_change(), _compatible_change()]))
        assert len(doc["runs"][0]["results"]) == 2

    def test_empty_changes(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        assert doc["runs"][0]["results"] == []
        assert doc["runs"][0]["tool"]["driver"]["rules"] == []


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

class TestSeverityMapping:
    def test_func_removed_is_error(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "error"

    def test_func_added_is_warning(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "warning"

    def test_rule_default_level_breaking(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["defaultConfiguration"]["level"] == "error"

    def test_rule_default_level_compatible(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["defaultConfiguration"]["level"] == "warning"

    def test_rule_help_uri_uses_policy_doc_slug(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["helpUri"].endswith("#func_added")


# ---------------------------------------------------------------------------
# Result content
# ---------------------------------------------------------------------------

class TestResultContent:
    def test_result_message_plain(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        msg = doc["runs"][0]["results"][0]["message"]["text"]
        assert "Function foo() removed" in msg

    def test_result_message_with_values(self) -> None:
        doc = to_sarif(_make_result([_valued_change()]))
        msg = doc["runs"][0]["results"][0]["message"]["text"]
        assert "int" in msg
        assert "long" in msg
        assert "→" in msg

    def test_result_rule_id(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        result = doc["runs"][0]["results"][0]
        assert result["ruleId"] == "func_removed"

    def test_result_location_symbol(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        locs = doc["runs"][0]["results"][0]["locations"]
        assert locs[0]["logicalLocations"][0]["name"] == "_Z3foov"

    def test_result_location_library(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], library="libbar.so.2"))
        locs = doc["runs"][0]["results"][0]["locations"]
        assert locs[0]["physicalLocation"]["artifactLocation"]["uri"] == "libbar.so.2"

    def test_result_properties(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["symbol"] == "_Z3foov"
        assert props["oldVersion"] == "1.0"
        assert props["newVersion"] == "2.0"

    def test_result_evidence_status_breaking(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["evidenceStatus"] == "artifact_proven"

    def test_result_evidence_status_absent_for_compatible(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        props = doc["runs"][0]["results"][0]["properties"]
        assert "evidenceStatus" not in props


# ---------------------------------------------------------------------------
# Invocation / automation details
# ---------------------------------------------------------------------------

class TestInvocation:
    def test_invocation_breaking_not_successful(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        assert doc["runs"][0]["invocations"][0]["executionSuccessful"] is False

    def test_invocation_no_change_successful(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        assert doc["runs"][0]["invocations"][0]["executionSuccessful"] is True

    def test_automation_details_id(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], library="libfoo.so.1", old="1.0", new="2.0"))
        aid = doc["runs"][0]["automationDetails"]["id"]
        assert "abicheck/libfoo.so.1/1.0_to_2.0" == aid

    def test_run_properties(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        props = doc["runs"][0]["properties"]
        assert props["abiVerdict"] == "BREAKING"
        assert props["changeCount"] == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_sarif_str_is_valid_json(self) -> None:
        s = to_sarif_str(_make_result([_breaking_change()]))
        parsed = json.loads(s)
        assert parsed["version"] == "2.1.0"

    def test_to_sarif_str_indented(self) -> None:
        s = to_sarif_str(_make_result([]), indent=4)
        assert "    " in s  # 4-space indent present


# ---------------------------------------------------------------------------
# Exit code contract tests
# ---------------------------------------------------------------------------

class TestExitCodes:
    """SARIF invocations[].exitCode must mirror abicheck compare CLI contract.

    Contract:
      BREAKING     → exitCode=4
      API_BREAK    → exitCode=2
      COMPATIBLE   → exitCode=0
      COMPATIBLE_WITH_RISK → exitCode=0 (binary-compatible; risk surfaced via exitCodeDescription)
      NO_CHANGE    → exitCode=0
    """

    def test_breaking_exit_code_is_4(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 4

    def test_api_break_exit_code_is_2(self) -> None:
        from abicheck.checker import ChangeKind
        api_change = Change(
            kind=ChangeKind.ENUM_MEMBER_RENAMED,
            symbol="Status",
            description="Enum member renamed",
        )
        doc = to_sarif(_make_result([api_change], verdict=Verdict.API_BREAK))
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 2

    def test_compatible_exit_code_is_0(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0

    def test_compatible_with_risk_exit_code_is_0(self) -> None:
        """COMPATIBLE_WITH_RISK is binary-compatible — exits 0.

        Deployment risk is surfaced via exitCodeDescription, not a non-zero exit.
        """
        from abicheck.checker import ChangeKind, Verdict
        risk_change = Change(
            kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
            symbol="libc.so.6",
            description="New GLIBC_2.34 version requirement added",
        )
        doc = to_sarif(_make_result([risk_change], verdict=Verdict.COMPATIBLE_WITH_RISK))
        invocation = doc["runs"][0]["invocations"][0]
        assert invocation["exitCode"] == 0
        assert invocation["exitCodeDescription"] == "COMPATIBLE_WITH_RISK"

    def test_no_change_exit_code_is_0(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0

    def test_exit_code_description_matches_verdict(self) -> None:
        for verdict, expected_code in [
            (Verdict.BREAKING, 4),
            (Verdict.API_BREAK, 2),
            (Verdict.COMPATIBLE, 0),
            (Verdict.NO_CHANGE, 0),
        ]:
            doc = to_sarif(_make_result([], verdict=verdict))
            inv = doc["runs"][0]["invocations"][0]
            assert inv["exitCode"] == expected_code, (
                f"Verdict {verdict}: expected exitCode={expected_code}, got {inv['exitCode']}"
            )
            assert inv["exitCodeDescription"] == verdict.value


# ---------------------------------------------------------------------------
# Confidence, evidence tiers, and policy in SARIF properties
# ---------------------------------------------------------------------------

class TestSarifConfidenceAndPolicy:
    """SARIF run properties must include confidence, evidence, and policy metadata."""

    def test_confidence_default_high(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        props = doc["runs"][0]["properties"]
        assert props["confidence"] == "high"
        assert props["evidenceTiers"] == []
        assert "coverageWarnings" not in props

    def test_confidence_with_tiers_and_warnings(self) -> None:
        from abicheck.checker_policy import Confidence
        r = _make_result([_breaking_change()])
        r.confidence = Confidence.LOW
        r.evidence_tiers = ["elf"]
        r.coverage_warnings = ["DWARF not available"]
        doc = to_sarif(r)
        props = doc["runs"][0]["properties"]
        assert props["confidence"] == "low"
        assert props["evidenceTiers"] == ["elf"]
        assert props["coverageWarnings"] == ["DWARF not available"]

    def test_policy_in_properties(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        props = doc["runs"][0]["properties"]
        assert props["policy"] == "strict_abi"

    def test_policy_overrides_in_properties(self) -> None:
        from abicheck.policy_file import PolicyFile
        r = _make_result([_breaking_change()])
        r.policy_file = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        doc = to_sarif(r)
        props = doc["runs"][0]["properties"]
        assert props["policyOverrides"] == {"func_removed": "COMPATIBLE"}

    def test_policy_overrides_absent_when_no_file(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        props = doc["runs"][0]["properties"]
        assert "policyOverrides" not in props


# ---------------------------------------------------------------------------
# Severity-aware invocation gate
# ---------------------------------------------------------------------------

class TestSeverityGate:
    """Without severity_config, the invocation exit is inferred purely from
    the compatibility verdict — which can misreport the actual CI gate once
    severity configuration is in play (compatibility and "blocks CI" are
    independent decisions). These guard the fix."""

    def test_no_severity_config_keeps_legacy_behaviour(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        props = doc["runs"][0]["properties"]
        assert "severityGate" not in props

    def test_compatible_addition_configured_as_error_fails_invocation(self) -> None:
        from abicheck.severity import resolve_severity_config

        cfg = resolve_severity_config("default", addition="error")
        r = _make_result([_compatible_change()], verdict=Verdict.COMPATIBLE)
        doc = to_sarif(r, severity_config=cfg)
        inv = doc["runs"][0]["invocations"][0]
        # Legacy inference would say COMPATIBLE -> exitCode 0, executionSuccessful.
        assert inv["exitCode"] == 1
        assert inv["executionSuccessful"] is False

        gate = doc["runs"][0]["properties"]["severityGate"]
        assert gate["exitCode"] == 1
        assert gate["blocking"] is True
        assert gate["blockingCategories"] == ["addition"]
        assert gate["config"]["addition"] == "error"

    def test_breaking_demoted_to_info_passes_invocation(self) -> None:
        from abicheck.severity import resolve_severity_config

        cfg = resolve_severity_config("default", abi_breaking="info")
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        doc = to_sarif(r, severity_config=cfg)
        inv = doc["runs"][0]["invocations"][0]
        # Legacy inference would say BREAKING -> exitCode 4, executionSuccessful=False.
        assert inv["exitCode"] == 0
        assert inv["executionSuccessful"] is True

        gate = doc["runs"][0]["properties"]["severityGate"]
        assert gate["blocking"] is False
        assert gate["blockingCategories"] == []

    def test_to_sarif_str_forwards_severity_config(self) -> None:
        from abicheck.severity import resolve_severity_config

        cfg = resolve_severity_config("default", addition="error")
        r = _make_result([_compatible_change()], verdict=Verdict.COMPATIBLE)
        text = to_sarif_str(r, severity_config=cfg)
        doc = json.loads(text)
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 1
