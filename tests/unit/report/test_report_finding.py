# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 Phase 2 item 4b: every renderer must read the same pre-resolved
per-``Change`` verdict/category instead of independently re-deriving it.

Mirrors ``test_gate_decision_shared.py``'s pattern: a parametrized sweep
over several finding combinations and severity/policy configurations,
asserting :func:`build_report_findings`'s ``ReportFinding.verdict``/
``.category`` agree with calling ``effective_verdict_for_change``/
``classify_effective_change`` directly -- the two independent code paths
every renderer used to call before this module existed. Also asserts
:func:`report_findings_for` (the memoized per-``DiffResult`` convenience
``reporter_markdown.py``/``html_report.py`` actually use) agrees with a
fresh, unmemoized call, and that JUnit's resolved verdict/category (reached
through ``findings_by_id``) matches the JSON report's own per-finding
classification for the same changes.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.junit_report import _is_failure, to_junit_xml
from abicheck.policy.severity import classify_effective_change
from abicheck.reclassify import effective_verdict_for_change
from abicheck.report.finding import (
    build_report_findings,
    findings_by_change_id,
    report_findings_for,
)
from abicheck.reporter import to_json

_BREAKING = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
_POTENTIAL = Change(ChangeKind.ENUM_MEMBER_RENAMED, "Color::RED", "enum member renamed")
_QUALITY = Change(ChangeKind.VISIBILITY_LEAK, "_Z3barv", "visibility leak")
_ADDITION = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
_RISK = Change(ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED, "libx.so", "risk upgrade")

_CHANGE_COMBINATIONS: list[list[Change]] = [
    [],
    [_BREAKING],
    [_ADDITION],
    [_QUALITY],
    [_POTENTIAL],
    [_RISK],
    [_BREAKING, _ADDITION],
    [_BREAKING, _POTENTIAL, _QUALITY, _ADDITION, _RISK],
]

_POLICIES = ["strict_abi", "permissive"]


def _result(changes: list[Change], policy: str = "strict_abi") -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=list(changes),
        policy=policy,
    )


class TestBuildReportFindingsAgreesWithDirectResolution:
    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    @pytest.mark.parametrize("policy", _POLICIES)
    def test_verdict_and_category_match_direct_calls(
        self, changes: list[Change], policy: str
    ) -> None:
        result = _result(changes, policy)
        kind_sets = result._effective_kind_sets()

        findings = build_report_findings(
            result.changes,
            policy=result.policy,
            kind_sets=kind_sets,
            policy_file=result.policy_file,
        )
        assert len(findings) == len(changes)
        for finding, change in zip(findings, changes, strict=True):
            assert finding.change is change
            assert finding.verdict == effective_verdict_for_change(
                change, policy=policy, kind_sets=kind_sets, policy_file=None
            )
            assert finding.category == classify_effective_change(
                change, policy=policy, kind_sets=kind_sets, policy_file=None
            )

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_memoized_result_findings_match_fresh_build(
        self, changes: list[Change]
    ) -> None:
        result = _result(changes)
        first = report_findings_for(result)
        second = report_findings_for(result)
        assert first is second  # memoized, not recomputed

        fresh = build_report_findings(
            result.changes,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        assert first == fresh


class TestFindingsByChangeId:
    def test_lookup_resolves_every_change_by_identity(self) -> None:
        changes = [_BREAKING, _ADDITION, _QUALITY]
        findings = build_report_findings(changes, policy="strict_abi")
        by_id = findings_by_change_id(findings)
        assert len(by_id) == len(changes)
        for change, finding in zip(changes, findings, strict=True):
            assert by_id[id(change)] is finding


class TestJunitAgreesWithJsonPerFinding:
    """The JUnit renderer's pre-resolved-finding path must agree with JSON's
    own independent per-finding classification for the same changes --
    JSON never reads ``ReportFinding`` (it derives its verdict/severity
    fields its own way), so this is a genuine cross-renderer agreement
    check, not a tautology against the same code path."""

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_failure_presence_matches_json_severity_bucket(
        self, changes: list[Change]
    ) -> None:
        result = _result(changes)
        kind_sets = result._effective_kind_sets()
        findings = build_report_findings(
            result.changes, policy=result.policy, kind_sets=kind_sets
        )
        by_id = findings_by_change_id(findings)

        junit_xml = to_junit_xml(result)
        report = json.loads(to_json(result))
        json_changes_by_symbol = {c["symbol"]: c for c in report["changes"]}

        for change in changes:
            is_failure = _is_failure(change, result, kind_sets, findings_by_id=by_id)
            json_change = json_changes_by_symbol[change.symbol]
            json_severity = json_change["severity"]
            json_breaking = json_severity in ("breaking", "api_break")
            assert is_failure == json_breaking, (
                f"{change.kind}: junit is_failure={is_failure} "
                f"disagrees with json severity={json_severity!r}"
            )
            assert f'name="{change.symbol}"' in junit_xml
