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

"""ADR-061 Phase 2 (D9 "decisions computed once"): every report format's
severity gate must trace back to the *same* :class:`GateDecision`.

Before ``abicheck.policy.gate_decision.gate_decision_for_result`` existed,
``reporter._build_severity_json``, ``sarif._severity_gate_properties``, and
``html_report``'s CI-gate card each independently imported
``compute_gate_decision`` and hand-assembled the same arguments from a
``DiffResult``. They already agreed in practice -- this suite is not a
regression pin for a bug that happened, it is the property the refactor
claims to guarantee: JSON/SARIF/HTML *cannot* disagree because they all
project the one value ``gate_decision_for_result`` computes, rather than
each independently reconstructing it. A parametrized sweep over several
finding combinations and severity configurations is what makes this a
property test rather than a single golden-output pin -- any one of them
diverging on any case would fail here, not just the cases someone happened
to hand-pick.
"""

from __future__ import annotations

import json
import re

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.html_report import generate_html_report
from abicheck.policy.gate_decision import gate_decision_for_result
from abicheck.policy.severity import (
    SeverityConfig,
    SeverityLevel,
    resolve_severity_config,
)
from abicheck.reporter import to_json
from abicheck.sarif import to_sarif_str

# One representative Change per severity category (matches the mapping
# `tests/test_severity.py` already pins: FUNC_REMOVED=abi_breaking,
# ENUM_MEMBER_RENAMED=potential_breaking, VISIBILITY_LEAK=quality_issues,
# FUNC_ADDED=addition).
_BREAKING = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
_POTENTIAL = Change(ChangeKind.ENUM_MEMBER_RENAMED, "Color::RED", "enum member renamed")
_QUALITY = Change(ChangeKind.VISIBILITY_LEAK, "_Z3barv", "visibility leak")
_ADDITION = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")

_CHANGE_COMBINATIONS: list[list[Change]] = [
    [],
    [_BREAKING],
    [_ADDITION],
    [_QUALITY],
    [_POTENTIAL],
    [_BREAKING, _ADDITION],
    [_BREAKING, _POTENTIAL, _QUALITY, _ADDITION],
    [_QUALITY, _ADDITION],
]

_SEVERITY_CONFIGS: list[SeverityConfig] = [
    resolve_severity_config("default"),
    resolve_severity_config("strict"),
    resolve_severity_config("info-only"),
    # A non-preset config: promotes additions to error, matching the
    # "addition promoted to error still fails CI" scenario several of the
    # call sites' own docstrings use as their motivating example.
    SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.WARNING,
        quality_issues=SeverityLevel.INFO,
        addition=SeverityLevel.ERROR,
    ),
]


def _result(changes: list[Change]) -> DiffResult:
    verdict = Verdict.BREAKING if any(c.kind == ChangeKind.FUNC_REMOVED for c in changes) else (
        Verdict.COMPATIBLE if changes else Verdict.NO_CHANGE
    )
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=list(changes),
        verdict=verdict,
    )


def _html_gate(html: str) -> tuple[bool, int | None, frozenset[str]]:
    """Parse ``_gate_card_html``'s rendered card back into (passed, exit_code, categories)."""
    match = re.search(r"<h2>.*?CI Gate[^:]*: (PASS|FAIL \(exit (\d+)\))</h2>", html)
    assert match, f"expected a CI Gate card in the HTML report: {html!r}"
    passed = match.group(1) == "PASS"
    exit_code = int(match.group(2)) if match.group(2) is not None else 0
    categories_match = re.search(r"Blocked by: (.*?)</div>", html[match.start() :])
    categories: frozenset[str] = frozenset()
    if categories_match:
        categories = frozenset(re.findall(r"<code>(.*?)</code>", categories_match.group(1)))
    return passed, exit_code, categories


class TestGateDecisionComputedOnce:
    """JSON, SARIF, and HTML must all reflect the one shared GateDecision."""

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    @pytest.mark.parametrize("severity_config", _SEVERITY_CONFIGS)
    def test_json_sarif_html_gate_agree_with_shared_decision(
        self, changes: list[Change], severity_config: SeverityConfig
    ) -> None:
        result = _result(changes)
        expected = gate_decision_for_result(result, severity_config)
        assert expected is not None  # severity_config is never None here

        json_report = json.loads(to_json(result, severity_config=severity_config))
        json_severity = json_report["severity"]
        assert json_severity["exit_code"] == expected.exit_code
        assert json_severity["blocking"] == expected.blocking
        assert set(json_severity["blocking_categories"]) == set(
            expected.blocking_categories
        )

        sarif_report = json.loads(
            to_sarif_str(result, severity_config=severity_config)
        )
        sarif_gate = sarif_report["runs"][0]["properties"]["severityGate"]
        assert sarif_gate["exitCode"] == expected.exit_code
        assert sarif_gate["blocking"] == expected.blocking
        assert set(sarif_gate["blockingCategories"]) == set(expected.blocking_categories)

        html = generate_html_report(
            result,
            lib_name="libtest",
            old_version="1.0",
            new_version="2.0",
            severity_config=severity_config,
        )
        html_passed, html_exit_code, html_categories = _html_gate(html)
        assert html_passed == (not expected.blocking)
        assert html_exit_code == expected.exit_code
        assert html_categories == (
            frozenset(expected.blocking_categories) if expected.blocking else frozenset()
        )

    def test_gate_decision_for_result_is_none_without_severity_config(self) -> None:
        result = _result([_BREAKING])
        assert gate_decision_for_result(result, None) is None

    def test_json_sarif_html_omit_gate_without_severity_config(self) -> None:
        result = _result([_BREAKING])

        json_report = json.loads(to_json(result))
        assert "severity" not in json_report

        sarif_report = json.loads(to_sarif_str(result))
        assert "severityGate" not in sarif_report["runs"][0]["properties"]

        html = generate_html_report(
            result, lib_name="libtest", old_version="1.0", new_version="2.0"
        )
        assert "CI Gate" not in html
