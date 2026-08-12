"""Tests for SARIF 2.1.0 output (Sprint 7)."""

from __future__ import annotations

import json

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.sarif import to_sarif, to_sarif_not_comparable, to_sarif_str


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
        c1 = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="foo removed"
        )
        c2 = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="bar", description="bar removed"
        )
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

    def test_rule_help_uri_points_at_real_doc(self) -> None:
        """helpUri must reference a doc file that actually exists in the repo."""
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert "docs/reference/change-kinds.md#" in rule["helpUri"]


# ---------------------------------------------------------------------------
# _parse_source_location (direct unit tests)
# ---------------------------------------------------------------------------


class TestParseSourceLocation:
    def test_file_and_line(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("include/foo.h:42") == ("include/foo.h", 42, None)

    def test_file_line_column(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("include/foo.h:42:7") == ("include/foo.h", 42, 7)

    def test_windows_path_with_column(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("C:\\foo\\bar.h:42:7") == (
            "C:\\foo\\bar.h",
            42,
            7,
        )

    def test_bare_filename_no_colon(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("foo.h") == ("foo.h", None, None)

    def test_non_numeric_line(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("foo.h:notaline") == (
            "foo.h:notaline",
            None,
            None,
        )

    def test_column_non_numeric_still_yields_line(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("foo.h:42:notacol") == ("foo.h", 42, None)

    def test_colon_in_path_prefix_still_yields_line(self) -> None:
        # A synthetic/virtual path scheme (colon before the first colon that
        # actually separates line info) — CodeRabbit review, PR #557. The
        # naive "file is everything before the first colon" reading would
        # treat "headers/foo.h" as the line and fail to parse a region.
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("generated:headers/foo.h:42") == (
            "generated:headers/foo.h",
            42,
            None,
        )

    def test_colon_in_path_prefix_with_column(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("generated:headers/foo.h:42:7") == (
            "generated:headers/foo.h",
            42,
            7,
        )

    def test_windows_path_no_column(self) -> None:
        from abicheck.sarif import _parse_source_location

        assert _parse_source_location("C:\\foo\\bar.h:42") == (
            "C:\\foo\\bar.h",
            42,
            None,
        )


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

    def test_result_location_file_and_line(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="include/foo.h:42",
        )
        doc = to_sarif(_make_result([c]))
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "include/foo.h"
        assert phys["region"] == {"startLine": 42}

    def test_result_location_file_line_column(self) -> None:
        """A file:line:column location must not leak ':line' into the URI."""
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="include/foo.h:42:7",
        )
        doc = to_sarif(_make_result([c]))
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "include/foo.h"
        assert phys["region"] == {"startLine": 42, "startColumn": 7}

    def test_result_location_bare_filename_no_colon(self) -> None:
        """A source_location with no colon at all has no line to extract."""
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="foo.h",
        )
        doc = to_sarif(_make_result([c]))
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "foo.h"
        assert "region" not in phys

    def test_result_location_non_numeric_line(self) -> None:
        """A source_location whose 'line' segment isn't numeric has no region."""
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="foo.h:notaline",
        )
        doc = to_sarif(_make_result([c]))
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "foo.h:notaline"
        assert "region" not in phys

    def test_result_location_windows_path_with_column(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="C:\\include\\foo.h:42:7",
        )
        doc = to_sarif(_make_result([c]))
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "C:\\include\\foo.h"
        assert phys["region"] == {"startLine": 42, "startColumn": 7}

    def test_result_properties(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["symbol"] == "_Z3foov"
        assert props["oldVersion"] == "1.0"
        assert props["newVersion"] == "2.0"

    def test_result_correlated_change_kind(self) -> None:
        # ADR-041 P0 roadmap item 2: the structured correlation sibling to the
        # description prose must also reach SARIF's properties bag, not only
        # the JSON report's _change_to_dict.
        c = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="demo::compute",
            description="reaches an internal decl",
            correlated_change_kind=ChangeKind.INLINE_BODY_CHANGED.value,
        )
        doc = to_sarif(_make_result([c]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["correlatedChangeKind"] == "inline_body_changed"

    def test_result_correlated_change_kind_absent_when_unset(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert "correlatedChangeKind" not in props

    def test_correlation_cleared_when_show_only_filters_out_the_target(self) -> None:
        """``--show-only`` can keep a LAYOUT_UNVERIFIABLE (risk) finding
        while filtering out the co-reported TYPE_VTABLE_CHANGED (breaking)
        its correlated_change_kind names — SARIF's ``correlatedChangeKind``
        property must not reference a finding this filtered view no longer
        includes (Codex review, fresh evidence: a fix scoped only to
        markdown left SARIF with the identical gap)."""
        layout_change = Change(
            kind=ChangeKind.LAYOUT_UNVERIFIABLE,
            symbol="Foo",
            description="layout evidence unverifiable",
            qualified_name="Foo",
            correlated_change_kind=ChangeKind.TYPE_VTABLE_CHANGED.value,
        )
        vtable_change = Change(
            kind=ChangeKind.TYPE_VTABLE_CHANGED,
            symbol="Foo",
            description="vtable changed",
            qualified_name="Foo",
        )
        result = _make_result([layout_change, vtable_change])

        doc_full = to_sarif(result)
        results_full = doc_full["runs"][0]["results"]
        layout_result_full = next(
            r for r in results_full if r["ruleId"] == "layout_unverifiable"
        )
        assert (
            layout_result_full["properties"]["correlatedChangeKind"]
            == "type_vtable_changed"
        )

        doc_filtered = to_sarif(result, show_only="risk")
        results_filtered = doc_filtered["runs"][0]["results"]
        rule_ids = {r["ruleId"] for r in results_filtered}
        assert "type_vtable_changed" not in rule_ids
        assert "layout_unverifiable" in rule_ids
        layout_result_filtered = next(
            r for r in results_filtered if r["ruleId"] == "layout_unverifiable"
        )
        assert "correlatedChangeKind" not in layout_result_filtered["properties"]

        # The original Change object is never mutated by the filtered render.
        assert (
            layout_change.correlated_change_kind == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_result_reachability_fields(self) -> None:
        # ADR-044 P1 item 4: reachability evidence (previously description-
        # prose-only, via the suppression_would_hide_public_break diagnostic)
        # must also reach SARIF's properties bag as structured fields.
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::train_ops_dispatcher",
            description="removed",
            public_reachable=True,
            reachability_kind="symbol_availability",
            reachability_proof_path="pubFn --[DECL_CALLS_DECL]--> ns::detail::train_ops_dispatcher",
        )
        doc = to_sarif(_make_result([c]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["publicReachable"] is True
        assert props["reachabilityKind"] == "symbol_availability"
        assert props["reachabilityProofPath"] == (
            "pubFn --[DECL_CALLS_DECL]--> ns::detail::train_ops_dispatcher"
        )

    def test_result_reachability_fields_absent_when_unset(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        props = doc["runs"][0]["results"][0]["properties"]
        assert "publicReachable" not in props
        assert "reachabilityKind" not in props
        assert "reachabilityProofPath" not in props

    def test_result_evidence_status_breaking(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["evidenceStatus"] == "artifact_proven"

    def test_result_evidence_status_absent_for_compatible(self) -> None:
        doc = to_sarif(_make_result([_compatible_change()], verdict=Verdict.COMPATIBLE))
        props = doc["runs"][0]["results"][0]["properties"]
        assert "evidenceStatus" not in props

    def test_result_evidence_status_unattributed_without_binary_evidence(self) -> None:
        # P0 evidence-provider audit: a comparison known to have never
        # examined a real binary (evidence_tiers == ["header"]) must not
        # claim "artifact_proven" in SARIF output either.
        result = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        result.evidence_tiers = ["header"]
        doc = to_sarif(result)
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["evidenceStatus"] == "unattributed"

    def test_result_evidence_status_artifact_proven_with_binary_evidence(self) -> None:
        result = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        result.evidence_tiers = ["header", "elf"]
        doc = to_sarif(result)
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["evidenceStatus"] == "artifact_proven"


# ---------------------------------------------------------------------------
# Invocation / automation details
# ---------------------------------------------------------------------------


class TestInvocation:
    def test_invocation_breaking_still_execution_successful(self) -> None:
        """executionSuccessful reports the tool run, not the ABI/severity gate.

        Per the SARIF spec, a completed analysis is a successful execution
        even when it reports blocking findings — that outcome belongs in
        exitCode/exitCodeDescription/result levels, not executionSuccessful.
        """
        doc = to_sarif(_make_result([_breaking_change()], verdict=Verdict.BREAKING))
        assert doc["runs"][0]["invocations"][0]["executionSuccessful"] is True
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 4

    def test_invocation_no_change_successful(self) -> None:
        doc = to_sarif(_make_result([], verdict=Verdict.NO_CHANGE))
        assert doc["runs"][0]["invocations"][0]["executionSuccessful"] is True

    def test_automation_details_id(self) -> None:
        doc = to_sarif(
            _make_result(
                [_breaking_change()], library="libfoo.so.1", old="1.0", new="2.0"
            )
        )
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
        doc = to_sarif(
            _make_result([risk_change], verdict=Verdict.COMPATIBLE_WITH_RISK)
        )
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
        # Legacy inference would say COMPATIBLE -> exitCode 0.
        assert inv["exitCode"] == 1
        # executionSuccessful reports the tool run, not the gate outcome.
        assert inv["executionSuccessful"] is True

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
        # Legacy inference would say BREAKING -> exitCode 4.
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

    def test_result_level_follows_severity_addition_override(self) -> None:
        """Codex review on #549: per-result `level` was built by `_result_for`
        from the legacy policy severity, ignoring `severity_config` entirely.
        `--severity-addition error` blocks the build (exitCode=1) but the
        added-symbol result kept `level: warning` — a code-scanning UI reading
        result levels would disagree with the configured gate."""
        from abicheck.severity import resolve_severity_config

        cfg = resolve_severity_config("default", addition="error")
        r = _make_result([_compatible_change()], verdict=Verdict.COMPATIBLE)
        doc = to_sarif(r, severity_config=cfg)
        assert doc["runs"][0]["results"][0]["level"] == "error"

    def test_result_level_follows_severity_abi_breaking_override(self) -> None:
        """Same fix, the inverse direction: `--severity-abi-breaking info` lets
        the invocation pass (exitCode=0) but the removed-symbol result kept
        `level: error` under the legacy mapping."""
        from abicheck.severity import resolve_severity_config

        cfg = resolve_severity_config("default", abi_breaking="info")
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        doc = to_sarif(r, severity_config=cfg)
        assert doc["runs"][0]["results"][0]["level"] == "note"


class TestScopedGate:
    """`--used-by`/`--required-symbol(s)` scoping (ADR-043 + CLI-audit P1).

    The scoped gate (`scoped_verdict`/`scoped_exit_code`) is authoritative for
    this document's own `invocations[0].exitCode` and each result's `level`
    when scoping is active -- `result.verdict` (the full, unscoped library
    verdict) is still reported as `fullLibraryVerdict` for context, but no
    longer drives what a SARIF consumer treats as blocking."""

    def test_no_scoped_gate_when_no_scoping(self) -> None:
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        doc = to_sarif(r)
        assert "scopedGate" not in doc["runs"][0]["properties"]
        # No scoping -> results keep the full-library severity, unaffected.
        assert doc["runs"][0]["results"][0]["level"] == "error"

    def test_scoped_gate_exit_code_wins_over_full_library_exit_code(self) -> None:
        # The scoped gate can legitimately disagree with the full-library
        # verdict (a --used-by app unaffected by an otherwise-BREAKING
        # change) -- the document's own exitCode must follow the scoped gate,
        # not the full library, since that's what the CLI process itself
        # exits with.
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.COMPATIBLE  # type: ignore[attr-defined]
        r.scoped_exit_code = 0  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.used_by = [{"app": "/bin/myapp", "verdict": "COMPATIBLE"}]  # type: ignore[attr-defined]
        doc = to_sarif(r)
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["gateVerdict"] == "COMPATIBLE"
        assert scoped_gate["fullLibraryVerdict"] == "BREAKING"
        assert scoped_gate["gateScope"] == "used_by"
        assert scoped_gate["usedBy"] == r.used_by  # type: ignore[attr-defined]
        # The document's own exitCode now follows the scoped gate (0), not
        # the full-library BREAKING verdict's exit code (4).
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0
        assert "COMPATIBLE" in doc["runs"][0]["invocations"][0]["exitCodeDescription"]

    def test_scoped_gate_carries_required_symbol_contract(self) -> None:
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.required_symbols = {"verdict": "BREAKING", "missing_entrypoints": ["_Z3foov"]}  # type: ignore[attr-defined]
        doc = to_sarif(r)
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["requiredSymbolContract"]["verdict"] == "BREAKING"

    def test_scoped_gate_exit_code_follows_severity_scheme(self) -> None:
        # Under a severity scheme (e.g. --severity-preset info-only) the
        # scoped exit code can be floored at 0 even for a BREAKING scoped
        # verdict -- the document's exitCode must reflect that actual
        # computed value, not re-derive 4 from the verdict.
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 0  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "severity"  # type: ignore[attr-defined]
        doc = to_sarif(r)
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["gateExitCode"] == 0
        assert scoped_gate["gateExitCodeScheme"] == "severity"
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0

    def test_irrelevant_change_downgraded_to_note_and_marked(self) -> None:
        # A change outside the --used-by/--required-symbol gate's relevance
        # must not read as an "error" in the SARIF results -- it's downgraded
        # to "note" and marked relevantToGate: false so a consumer can tell
        # "not severe" apart from "out of scope" (CLI-audit P1).
        c = _breaking_change()
        r = _make_result([c], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.COMPATIBLE  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        doc = to_sarif(r)
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "note"
        assert result["properties"]["relevantToGate"] is False

    def test_relevant_change_keeps_its_level_and_marked(self) -> None:
        from abicheck.reporter import _finding_id

        c = _breaking_change()
        r = _make_result([c], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(c)})  # type: ignore[attr-defined]
        doc = to_sarif(r)
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "error"
        assert result["properties"]["relevantToGate"] is True

    def test_missing_contract_synthesizes_a_result(self) -> None:
        # A required symbol absent from the new library has no backing diff
        # Change -- without a synthetic result the scoped gate's own
        # exitCode could be nonzero (BREAKING) while `results` shows nothing
        # to explain it.
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        doc = to_sarif(r)
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "used_by_missing_symbol"
        assert results[0]["level"] == "error"
        assert results[0]["properties"]["relevantToGate"] is True
        assert results[0]["properties"]["blocksGate"] is True
        assert "_Z6vanishv" in results[0]["message"]["text"]
        # G29 Phase 3 slice 1 (ADR-052, Codex review): reachabilityState is
        # "always present" everywhere else this slice touches -- a missing
        # contract member has no backing Change, but it still needs the
        # honest UNKNOWN value rather than silently omitting the field.
        assert results[0]["properties"]["reachabilityState"] == "unknown"
        # The synthetic rule id must be registered too (Codex review) --
        # otherwise a SARIF consumer resolving annotations from
        # tool.driver.rules has no metadata for this finding.
        rule_ids = {rule["id"] for rule in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "used_by_missing_symbol" in rule_ids
        # Counted as relevant even with no backing Change (CodeRabbit review).
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["relevantFindingCount"] == 1

    def test_missing_contract_demoted_by_severity_config_is_not_blocking(
        self,
    ) -> None:
        # Regression (Codex review): under a severity config that demotes
        # abi_breaking (e.g. --severity-preset info-only), the scoped exit
        # code for a missing contract member is floored at 0 by
        # missing_contract_exit_code -- the synthetic result must not read
        # as "error" in that case, or a code-scanning consumer would flag/
        # block a finding the gate itself passed.
        from abicheck.severity import SeverityConfig, SeverityLevel

        demoted = SeverityConfig(abi_breaking=SeverityLevel.WARNING)
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 0  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "severity"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        doc = to_sarif(r, severity_config=demoted)
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "note"
        # relevantToGate stays true -- a missing-contract member is always in
        # the --used-by/--required-symbol scope by construction (that's an
        # orthogonal question from whether severity makes it block, which
        # blocksGate/level carry -- CodeRabbit review).
        assert result["properties"]["relevantToGate"] is True
        assert result["properties"]["blocksGate"] is False
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0

    def test_scoped_only_change_is_rendered(self) -> None:
        # Regression (Codex review): scope_diff_to_app synthesizes a fresh
        # Change (e.g. PE_ORDINAL_RETARGETED) that is relevant to the gate
        # but never added to result.changes -- without rendering it, a
        # --used-by run that fails solely because of one of these would
        # report a nonzero gate exitCode with zero results to explain it.
        from abicheck.reporter import _finding_id

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc",
            new_value="NewFunc",
        )
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(scoped_only)})  # type: ignore[attr-defined]
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        doc = to_sarif(r)
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "pe_ordinal_retargeted"
        assert results[0]["properties"]["relevantToGate"] is True
        rule_ids = {rule["id"] for rule in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "pe_ordinal_retargeted" in rule_ids
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["relevantFindingCount"] == 1
        assert scoped_gate["unrelatedFindingCount"] == 0

    def test_scoped_only_change_has_consumer_proven_evidence_status(self) -> None:
        """Codex review: a scoped-only change is proven by the real
        consumer's own import table/execution, not an artifact-level
        library diff -- its properties.evidenceStatus must be
        consumer_proven, not the BREAKING-category default artifact_proven."""
        from abicheck.reporter import _finding_id

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc",
            new_value="NewFunc",
        )
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(scoped_only)})  # type: ignore[attr-defined]
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        doc = to_sarif(r)
        results = doc["runs"][0]["results"]
        assert results[0]["properties"]["evidenceStatus"] == "consumer_proven"

    def test_scoped_only_change_respects_show_only(self) -> None:
        # Regression (Codex review): result.changes is filtered through
        # apply_show_only above, but scoped_only_changes was appended
        # unconditionally afterward -- a --show-only run that explicitly
        # filters out a scoped-only breaking change (e.g. an app-relevant
        # PE_ORDINAL_RETARGETED under --show-only compatible) must not still
        # upload it, unlike the normal result.changes path.
        from abicheck.reporter import _finding_id

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc",
            new_value="NewFunc",
        )
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(scoped_only)})  # type: ignore[attr-defined]
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        doc = to_sarif(r, show_only="compatible")
        assert doc["runs"][0]["results"] == []
        rule_ids = {rule["id"] for rule in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "pe_ordinal_retargeted" not in rule_ids

    def test_missing_contract_respects_show_only(self) -> None:
        # Regression (Codex review): scoped_missing_labels bypassed
        # --show-only entirely -- a missing required symbol has no backing
        # Change/ChangeKind, so it can't run through apply_show_only, but a
        # --show-only run that excludes breaking findings must still not
        # upload the `error`-level synthetic missing-contract result.
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        doc = to_sarif(r, show_only="compatible")
        assert doc["runs"][0]["results"] == []
        rule_ids = {rule["id"] for rule in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "used_by_missing_symbol" not in rule_ids

    def test_missing_contract_shown_when_show_only_includes_breaking(self) -> None:
        # A --show-only that includes "breaking" (the default missing-
        # contract severity) must still render the synthetic result.
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 4  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "legacy"  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        doc = to_sarif(r, show_only="breaking")
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "used_by_missing_symbol"


class TestRootCauseMode:
    """`--report-mode root-cause` (G29 Phase 3 slice 5, ADR-052): adds
    properties.rootCauseId/rootCause to every result instead of restructuring
    SARIF's flat one-result-per-finding shape -- shares the exact grouping
    decision (_root_cause_key_and_display) JSON/markdown root-cause mode use,
    so all three formats can never disagree about which findings correlate."""

    def test_full_mode_has_no_root_cause_properties(self) -> None:
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        doc = to_sarif(r)
        assert "rootCauseId" not in doc["runs"][0]["results"][0]["properties"]

    def test_groups_findings_sharing_caused_by_type(self) -> None:
        root = Change(
            ChangeKind.FUNC_REMOVED,
            "ns::internal::helper",
            "helper removed",
        )
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            "pub_entry",
            "required",
            caused_by_type="ns::internal::helper",
        )
        r = _make_result([root, overlay], verdict=Verdict.BREAKING)
        doc = to_sarif(r, report_mode="root-cause")
        results = doc["runs"][0]["results"]
        ids = {res["properties"]["rootCauseId"] for res in results}
        assert len(ids) == 1
        for res in results:
            assert res["properties"]["rootCause"] == "ns::internal::helper"

    def test_independent_findings_sharing_a_symbol_stay_separate(self) -> None:
        a = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo", "return type changed")
        b = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "parameter changed")
        r = _make_result([a, b], verdict=Verdict.BREAKING)
        doc = to_sarif(r, report_mode="root-cause")
        results = doc["runs"][0]["results"]
        ids = {res["properties"]["rootCauseId"] for res in results}
        assert len(ids) == 2
        for res in results:
            assert res["properties"]["rootCause"] == "foo"

    def test_scoped_only_change_gets_root_cause_properties(self) -> None:
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
        )
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        doc = to_sarif(r, report_mode="root-cause")
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["properties"]["rootCause"] == "ordinal:5"
        assert "rootCauseId" in results[0]["properties"]

    def test_missing_contract_label_gets_root_cause_properties(self) -> None:
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        doc = to_sarif(r, report_mode="root-cause")
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["properties"]["rootCause"] == "_Z6vanishv"
        assert "rootCauseId" in results[0]["properties"]

    def test_two_missing_labels_stay_separate(self) -> None:
        # Regression guard: two unreferenced missing-contract labels must not
        # collide on the same unique-key fallback (the label itself, not a
        # shared empty finding_id, disambiguates them).
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv", "_Z7vanish2v")  # type: ignore[attr-defined]
        doc = to_sarif(r, report_mode="root-cause")
        ids = {res["properties"]["rootCauseId"] for res in doc["runs"][0]["results"]}
        assert len(ids) == 2

    def test_hidden_scoped_only_cause_does_not_leak_into_referenced_causes(
        self,
    ) -> None:
        # Regression (Codex review): a scoped-only change filtered out by
        # --show-only must not still contribute its caused_by_type to
        # referenced_causes -- otherwise its hidden correlation could wrongly
        # group two unrelated *visible* findings that merely share its
        # symbol, disagreeing with JSON/markdown root-cause mode (which
        # computes referenced_causes from the filtered set only).
        hidden_scoped_only = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="unrelated_addition",
            description="added",
            caused_by_type="foo",
        )
        a = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo", "return type changed")
        b = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "parameter changed")
        r = _make_result([a, b], verdict=Verdict.BREAKING)
        r.scoped_only_changes = (hidden_scoped_only,)  # type: ignore[attr-defined]
        doc = to_sarif(r, report_mode="root-cause", show_only="breaking")
        results = doc["runs"][0]["results"]
        assert all(res["ruleId"] != "func_added" for res in results)
        ids = {res["properties"]["rootCauseId"] for res in results}
        assert len(ids) == 2


class TestImpactAssessmentRootCause:
    """``impactAssessment.root_cause_id``/``root_cause_display``/
    ``impact_group_id`` (G29 Phase 3 follow-up): computed unconditionally --
    independent of ``report_mode`` -- unlike ``properties.rootCauseId``/
    ``rootCause`` above, which stay exclusive to ``--report-mode
    root-cause``. Uses the same grouping decision, so the two never
    disagree about which findings correlate."""

    def test_uncorrelated_finding_has_no_impact_assessment_root_cause(self) -> None:
        r = _make_result([_breaking_change()], verdict=Verdict.BREAKING)
        doc = to_sarif(r)
        props = doc["runs"][0]["results"][0]["properties"]
        assessment = props.get("impactAssessment", {})
        assert "root_cause_id" not in assessment

    def test_correlated_findings_get_shared_impact_group_id_in_full_mode(
        self,
    ) -> None:
        root = Change(
            ChangeKind.FUNC_REMOVED,
            "ns::internal::helper",
            "helper removed",
        )
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            "pub_entry",
            "required",
            caused_by_type="ns::internal::helper",
        )
        r = _make_result([root, overlay], verdict=Verdict.BREAKING)
        # report_mode defaults to "full" -- properties.rootCauseId/rootCause
        # stay absent, but impactAssessment.root_cause_id is still populated.
        doc = to_sarif(r)
        results = doc["runs"][0]["results"]
        for res in results:
            assert "rootCauseId" not in res["properties"]
        assessments = [res["properties"]["impactAssessment"] for res in results]
        ids = {a["root_cause_id"] for a in assessments}
        assert len(ids) == 1
        for a in assessments:
            assert a["root_cause_display"] == "ns::internal::helper"
            assert a["impact_group_id"] == a["root_cause_id"]

    def test_matches_sarif_root_cause_mode_id(self) -> None:
        # Cross-check: the unconditional impactAssessment id must equal the
        # report_mode="root-cause" properties.rootCauseId for the same
        # finding -- both are the same underlying computation.
        root = Change(
            ChangeKind.FUNC_REMOVED,
            "ns::internal::helper",
            "helper removed",
        )
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            "pub_entry",
            "required",
            caused_by_type="ns::internal::helper",
        )
        r = _make_result([root, overlay], verdict=Verdict.BREAKING)
        full_doc = to_sarif(r)
        rc_doc = to_sarif(r, report_mode="root-cause")
        full_ids = {
            res["properties"]["impactAssessment"]["root_cause_id"]
            for res in full_doc["runs"][0]["results"]
        }
        rc_ids = {
            res["properties"]["rootCauseId"] for res in rc_doc["runs"][0]["results"]
        }
        assert full_ids == rc_ids


class TestNotComparable:
    """ADR-050 D2 -- the comparability-gate hard-fail path has no DiffResult
    at all, so it gets its own dedicated renderer instead of to_sarif."""

    def test_structure(self) -> None:
        doc = to_sarif_not_comparable(
            "libfoo.so.1", "1.0", "2.0", "scope_mismatch", "scope drift"
        )
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["results"] == []
        invocation = run["invocations"][0]
        assert invocation["executionSuccessful"] is False
        assert invocation["exitCode"] == 16
        notif = invocation["toolExecutionNotifications"][0]
        assert notif["descriptor"]["id"] == "scope_mismatch"
        assert notif["level"] == "error"
        assert "scope drift" in notif["message"]["text"]
        assert run["properties"]["notComparable"] is True
        assert run["properties"]["abiVerdict"] is None
        assert run["properties"]["reason"] == {
            "kind": "scope_mismatch",
            "message": "scope drift",
        }

    def test_json_serializable(self) -> None:
        doc = to_sarif_not_comparable(
            "libfoo.so.1", "1.0", "2.0", "profile_mismatch", "dep.h changed"
        )
        # Round-trips cleanly -- no non-JSON-serializable values snuck in.
        assert json.loads(json.dumps(doc)) == doc


class TestContractEvaluationFields:
    """CLI-audit P1: an evaluated (IN_CONTRACT/NOT_APPLICABLE) finding must
    carry the same canonical contract-decision fields reporter.py's JSON
    output already has -- not just a NOT_EVALUATED finding."""

    def test_in_contract_finding_carries_full_decision(self) -> None:
        from abicheck.contract_relevance_types import (
            CompatibilityEvaluationStatus,
            ContractAssurance,
            ContractRelevance,
        )

        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="Function foo() removed",
            contract_relevance=ContractRelevance.IN_CONTRACT,
            contract_reason_code="public_header_direct",
            contract_assurance=ContractAssurance.COMPLETE,
            compatibility_evaluation_status=CompatibilityEvaluationStatus.EVALUATED,
            compatibility_decision=Verdict.BREAKING,
            contract_evidence_refs=("public_header:old",),
        )
        result = _make_result([change])
        doc = to_sarif(result)
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["contractRelevance"] == "IN_CONTRACT"
        assert props["contractReasonCode"] == "public_header_direct"
        assert props["contractAssurance"] == "complete"
        assert props["compatibilityEvaluationStatus"] == "EVALUATED"
        assert props["compatibilityDecision"] == "BREAKING"
        assert props["gateContribution"] > 0
        assert props["contractEvidenceRefs"] == ["public_header:old"]

    def test_unstamped_finding_carries_no_contract_properties(self) -> None:
        result = _make_result([_breaking_change()])
        doc = to_sarif(result)
        props = doc["runs"][0]["results"][0]["properties"]
        assert "contractRelevance" not in props
        assert "gateContribution" not in props

    def test_stamped_finding_without_optional_fields_omits_them(self) -> None:
        # contract_reason_code/contract_assurance/contract_evidence_refs are
        # independent optional fields -- a finding can have a stamped
        # relevance without any of them set.
        from abicheck.contract_relevance_types import ContractRelevance

        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="Function foo() removed",
            contract_relevance=ContractRelevance.NOT_APPLICABLE,
        )
        result = _make_result([change])
        doc = to_sarif(result)
        props = doc["runs"][0]["results"][0]["properties"]
        assert props["contractRelevance"] == "NOT_APPLICABLE"
        assert "contractReasonCode" not in props
        assert "contractAssurance" not in props
        assert "contractEvidenceRefs" not in props
        assert props["compatibilityEvaluationStatus"] == "EVALUATED"
        assert props["compatibilityDecision"] is None


class TestSuppressionsArray:
    """ "Reporting must survive suppression": a suppressed finding must still
    appear in SARIF, via the standard `suppressions` array (§3.27.24), with
    rule provenance -- not just a bare `properties.suppressedCount` integer."""

    def _make_result_with_suppressed(self, suppressed: list[Change]) -> DiffResult:
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so.1",
            changes=[],
            verdict=Verdict.COMPATIBLE,
            suppressed_changes=suppressed,
        )

    def test_suppressed_change_appears_in_results(self) -> None:
        suppressed = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="Function foo() removed",
            suppression_rule="intentional",
        )
        doc = to_sarif(self._make_result_with_suppressed([suppressed]))
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "func_removed"

    def test_suppressed_result_carries_suppressions_array(self) -> None:
        suppressed = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="Function foo() removed",
            suppression_rule="intentional",
        )
        doc = to_sarif(self._make_result_with_suppressed([suppressed]))
        result = doc["runs"][0]["results"][0]
        assert result["suppressions"] == [
            {
                "kind": "external",
                "justification": "suppressed by --suppress rule: intentional",
            }
        ]

    def test_unsuppressed_result_carries_no_suppressions_key(self) -> None:
        doc = to_sarif(_make_result([_breaking_change()]))
        assert "suppressions" not in doc["runs"][0]["results"][0]

    def test_suppressed_change_registers_its_rule(self) -> None:
        """A suppressed-only rule id must still register in tool.driver.rules,
        or a SARIF consumer resolving by rule id finds nothing for it."""
        suppressed = Change(
            kind=ChangeKind.TYPE_REMOVED,
            symbol="Foo",
            description="Type Foo removed",
            suppression_rule="r1",
        )
        doc = to_sarif(self._make_result_with_suppressed([suppressed]))
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "type_removed" in rule_ids

    def test_suppressed_change_reuses_an_already_registered_rule(self) -> None:
        """A suppressed change sharing its kind with an active (unsuppressed)
        finding must not register a second, duplicate rule entry."""
        active = _breaking_change()  # func_removed
        suppressed = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3barv",
            description="Function bar() removed",
            suppression_rule="r1",
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so.1",
            changes=[active],
            verdict=Verdict.BREAKING,
            suppressed_changes=[suppressed],
        )
        doc = to_sarif(result)
        rules = [
            r
            for r in doc["runs"][0]["tool"]["driver"]["rules"]
            if r["id"] == "func_removed"
        ]
        assert len(rules) == 1
        assert len(doc["runs"][0]["results"]) == 2

    def test_missing_rule_label_still_states_a_justification(self) -> None:
        suppressed = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="Function foo() removed",
        )
        doc = to_sarif(self._make_result_with_suppressed([suppressed]))
        result = doc["runs"][0]["results"][0]
        assert result["suppressions"][0]["justification"] == (
            "suppressed by --suppress rule"
        )
