# Copyright 2026 Nikolay Petrov
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

"""Tests for the severity configuration module."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.severity import (
    PRESET_DEFAULT,
    PRESET_INFO_ONLY,
    PRESET_STRICT,
    CategorizedChanges,
    IssueCategory,
    SeverityConfig,
    SeverityLevel,
    categorize_changes,
    classify_change,
    compute_exit_code,
    resolve_severity_config,
)


@dataclass
class _FakeChange:
    kind: ChangeKind


# ---------------------------------------------------------------------------
# classify_change
# ---------------------------------------------------------------------------


class TestClassifyChange:
    def test_breaking_kind(self) -> None:
        assert classify_change(ChangeKind.FUNC_REMOVED) == IssueCategory.ABI_BREAKING

    def test_api_break_kind(self) -> None:
        assert classify_change(ChangeKind.ENUM_MEMBER_RENAMED) == IssueCategory.POTENTIAL_BREAKING

    def test_risk_kind(self) -> None:
        assert classify_change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED) == IssueCategory.POTENTIAL_BREAKING

    def test_addition_kind(self) -> None:
        assert classify_change(ChangeKind.FUNC_ADDED) == IssueCategory.ADDITION

    def test_quality_issue_kind(self) -> None:
        assert classify_change(ChangeKind.VISIBILITY_LEAK) == IssueCategory.QUALITY_ISSUES

    def test_compatible_noexcept_is_quality(self) -> None:
        # noexcept changes are COMPATIBLE but not additions → quality issues
        assert classify_change(ChangeKind.FUNC_NOEXCEPT_ADDED) == IssueCategory.QUALITY_ISSUES

    def test_type_added_is_addition(self) -> None:
        assert classify_change(ChangeKind.TYPE_ADDED) == IssueCategory.ADDITION

    def test_var_added_is_addition(self) -> None:
        assert classify_change(ChangeKind.VAR_ADDED) == IssueCategory.ADDITION

    def test_enum_member_added_is_addition(self) -> None:
        assert classify_change(ChangeKind.ENUM_MEMBER_ADDED) == IssueCategory.ADDITION

    def test_soname_missing_is_quality(self) -> None:
        assert classify_change(ChangeKind.SONAME_MISSING) == IssueCategory.QUALITY_ISSUES

    def test_dwarf_info_missing_is_quality(self) -> None:
        assert classify_change(ChangeKind.DWARF_INFO_MISSING) == IssueCategory.QUALITY_ISSUES

    @pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
    def test_exhaustive_all_kinds_classified(self, kind: ChangeKind) -> None:
        """Every ChangeKind must map to a real category, never the fail-safe default."""
        from abicheck.checker_policy import (
            ADDITION_KINDS,
            API_BREAK_KINDS,
            BREAKING_KINDS,
            QUALITY_KINDS,
            RISK_KINDS,
        )
        cat = classify_change(kind)
        assert cat in set(IssueCategory), f"{kind} classified as unknown category {cat}"
        # Verify classify_change agrees with the canonical kind sets
        if kind in BREAKING_KINDS:
            assert cat == IssueCategory.ABI_BREAKING
        elif kind in API_BREAK_KINDS or kind in RISK_KINDS:
            assert cat == IssueCategory.POTENTIAL_BREAKING
        elif kind in ADDITION_KINDS:
            assert cat == IssueCategory.ADDITION
        elif kind in QUALITY_KINDS:
            assert cat == IssueCategory.QUALITY_ISSUES
        else:
            pytest.fail(f"{kind} not in any canonical kind set — update checker_policy.py")


# ---------------------------------------------------------------------------
# SeverityConfig
# ---------------------------------------------------------------------------


class TestSeverityConfig:
    def test_default_preset_values(self) -> None:
        cfg = PRESET_DEFAULT
        assert cfg.abi_breaking == SeverityLevel.ERROR
        assert cfg.potential_breaking == SeverityLevel.WARNING
        assert cfg.quality_issues == SeverityLevel.WARNING
        assert cfg.addition == SeverityLevel.INFO

    def test_strict_preset_all_error(self) -> None:
        cfg = PRESET_STRICT
        for cat in IssueCategory:
            assert cfg.level_for(cat) == SeverityLevel.ERROR

    def test_info_only_preset_all_info(self) -> None:
        cfg = PRESET_INFO_ONLY
        for cat in IssueCategory:
            assert cfg.level_for(cat) == SeverityLevel.INFO

    def test_level_for_kind(self) -> None:
        cfg = PRESET_DEFAULT
        assert cfg.level_for_kind(ChangeKind.FUNC_REMOVED) == SeverityLevel.ERROR
        assert cfg.level_for_kind(ChangeKind.ENUM_MEMBER_RENAMED) == SeverityLevel.WARNING
        assert cfg.level_for_kind(ChangeKind.VISIBILITY_LEAK) == SeverityLevel.WARNING
        assert cfg.level_for_kind(ChangeKind.FUNC_ADDED) == SeverityLevel.INFO

    def test_has_errors_true(self) -> None:
        cfg = PRESET_DEFAULT
        changes = [_FakeChange(ChangeKind.FUNC_REMOVED)]
        assert cfg.has_errors(changes) is True

    def test_has_errors_false_no_breaking(self) -> None:
        cfg = PRESET_DEFAULT
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        assert cfg.has_errors(changes) is False

    def test_has_errors_strict_additions(self) -> None:
        cfg = PRESET_STRICT
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        assert cfg.has_errors(changes) is True

    def test_describe(self) -> None:
        desc = PRESET_DEFAULT.describe()
        assert "abi_breaking: error" in desc
        assert "addition: info" in desc


# ---------------------------------------------------------------------------
# resolve_severity_config
# ---------------------------------------------------------------------------


class TestResolveSeverityConfig:
    def test_no_args_returns_default(self) -> None:
        cfg = resolve_severity_config()
        assert cfg == PRESET_DEFAULT

    def test_preset_strict(self) -> None:
        cfg = resolve_severity_config(preset="strict")
        assert cfg == PRESET_STRICT

    def test_preset_info_only(self) -> None:
        cfg = resolve_severity_config(preset="info-only")
        assert cfg == PRESET_INFO_ONLY

    def test_invalid_preset_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown severity preset"):
            resolve_severity_config(preset="nonexistent")

    def test_per_category_override(self) -> None:
        cfg = resolve_severity_config(addition="error")
        assert cfg.abi_breaking == SeverityLevel.ERROR  # from default
        assert cfg.addition == SeverityLevel.ERROR  # overridden

    def test_override_on_preset(self) -> None:
        cfg = resolve_severity_config(
            preset="info-only",
            abi_breaking="error",
        )
        assert cfg.abi_breaking == SeverityLevel.ERROR
        assert cfg.potential_breaking == SeverityLevel.INFO  # from info-only

    def test_invalid_override_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid severity level"):
            resolve_severity_config(abi_breaking="critical")

    def test_all_overrides(self) -> None:
        cfg = resolve_severity_config(
            abi_breaking="warning",
            potential_breaking="error",
            quality_issues="info",
            addition="error",
        )
        assert cfg.abi_breaking == SeverityLevel.WARNING
        assert cfg.potential_breaking == SeverityLevel.ERROR
        assert cfg.quality_issues == SeverityLevel.INFO
        assert cfg.addition == SeverityLevel.ERROR


# ---------------------------------------------------------------------------
# compute_exit_code
# ---------------------------------------------------------------------------


class TestComputeExitCode:
    def test_no_changes(self) -> None:
        assert compute_exit_code([], PRESET_DEFAULT) == 0

    def test_breaking_default_exits_4(self) -> None:
        changes = [_FakeChange(ChangeKind.FUNC_REMOVED)]
        assert compute_exit_code(changes, PRESET_DEFAULT) == 4

    def test_api_break_default_exits_0(self) -> None:
        # API breaks are WARNING by default, so exit 0
        changes = [_FakeChange(ChangeKind.ENUM_MEMBER_RENAMED)]
        assert compute_exit_code(changes, PRESET_DEFAULT) == 0

    def test_api_break_strict_exits_2(self) -> None:
        changes = [_FakeChange(ChangeKind.ENUM_MEMBER_RENAMED)]
        assert compute_exit_code(changes, PRESET_STRICT) == 2

    def test_additions_default_exits_0(self) -> None:
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        assert compute_exit_code(changes, PRESET_DEFAULT) == 0

    def test_additions_strict_exits_1(self) -> None:
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        assert compute_exit_code(changes, PRESET_STRICT) == 1

    def test_quality_strict_exits_1(self) -> None:
        changes = [_FakeChange(ChangeKind.VISIBILITY_LEAK)]
        assert compute_exit_code(changes, PRESET_STRICT) == 1

    def test_info_only_always_0(self) -> None:
        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        assert compute_exit_code(changes, PRESET_INFO_ONLY) == 0

    def test_worst_exit_code_wins(self) -> None:
        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),  # abi_breaking → exit 4
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),  # potential → exit 2
            _FakeChange(ChangeKind.FUNC_ADDED),  # additions → exit 1
        ]
        assert compute_exit_code(changes, PRESET_STRICT) == 4

    def test_mixed_severity_custom(self) -> None:
        # Only potential_breaking is error, rest is info
        cfg = SeverityConfig(
            abi_breaking=SeverityLevel.INFO,
            potential_breaking=SeverityLevel.ERROR,
            quality_issues=SeverityLevel.INFO,
            addition=SeverityLevel.INFO,
        )
        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),  # abi_breaking → info → exit 0
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),  # potential → error → exit 2
        ]
        assert compute_exit_code(changes, cfg) == 2


# ---------------------------------------------------------------------------
# categorize_changes
# ---------------------------------------------------------------------------


class TestCategorizeChanges:
    def test_empty(self) -> None:
        result = categorize_changes([])
        assert result == CategorizedChanges([], [], [], [])

    def test_all_categories(self) -> None:
        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),
            _FakeChange(ChangeKind.VISIBILITY_LEAK),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        result = categorize_changes(changes)
        assert len(result.abi_breaking) == 1
        assert result.abi_breaking[0].kind == ChangeKind.FUNC_REMOVED
        assert len(result.potential_breaking) == 1
        assert result.potential_breaking[0].kind == ChangeKind.ENUM_MEMBER_RENAMED
        assert len(result.quality_issues) == 1
        assert result.quality_issues[0].kind == ChangeKind.VISIBILITY_LEAK
        assert len(result.addition) == 1
        assert result.addition[0].kind == ChangeKind.FUNC_ADDED

    def test_risk_kinds_go_to_potential(self) -> None:
        changes = [_FakeChange(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED)]
        result = categorize_changes(changes)
        assert len(result.potential_breaking) == 1
        assert len(result.abi_breaking) == 0


# ---------------------------------------------------------------------------
# Additional exit-code and config edge cases
# ---------------------------------------------------------------------------


class TestComputeExitCodeEdgeCases:
    """Additional coverage for exit-code edge cases flagged during review."""

    def test_quality_default_exits_0(self) -> None:
        """Quality issues are WARNING under default preset → exit 0."""
        changes = [_FakeChange(ChangeKind.VISIBILITY_LEAK)]
        assert compute_exit_code(changes, PRESET_DEFAULT) == 0

    def test_quality_and_additions_only_exits_0_default(self) -> None:
        """Quality + additions under default preset → exit 0 (both below error)."""
        changes = [
            _FakeChange(ChangeKind.VISIBILITY_LEAK),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        assert compute_exit_code(changes, PRESET_DEFAULT) == 0


class TestSeverityConfigDescribe:
    """Tests for SeverityConfig.describe() method."""

    def test_describe_default(self) -> None:
        out = PRESET_DEFAULT.describe()
        assert "abi_breaking: error" in out
        assert "addition: info" in out

    def test_describe_with_title(self) -> None:
        out = PRESET_STRICT.describe(title="Strict preset:")
        assert out.startswith("Strict preset:")
        assert "abi_breaking: error" in out

    def test_describe_with_prefix(self) -> None:
        out = PRESET_DEFAULT.describe(prefix=">> ")
        for line in out.splitlines():
            assert line.startswith(">> ")

    def test_describe_with_title_and_prefix(self) -> None:
        out = PRESET_INFO_ONLY.describe(prefix="  ", title="Info-only:")
        lines = out.splitlines()
        assert lines[0] == "  Info-only:"
        assert "info" in lines[1]


class TestInfoOnlyAlias:
    """The info_only alias resolves to the same preset as info-only."""

    def test_alias_resolves(self) -> None:
        from abicheck.severity import SEVERITY_PRESETS
        assert SEVERITY_PRESETS["info_only"] is SEVERITY_PRESETS["info-only"]


# ---------------------------------------------------------------------------
# GateDecision / compute_gate_decision / CompatibilityDecision
# ---------------------------------------------------------------------------


class TestCompatibilityDecision:
    def test_is_verdict(self) -> None:
        from abicheck.checker import Verdict
        from abicheck.severity import CompatibilityDecision

        assert CompatibilityDecision is Verdict


class TestComputeGateDecision:
    def test_legacy_scheme_uses_supplied_exit_code(self) -> None:
        from abicheck.severity import compute_gate_decision

        decision = compute_gate_decision([], None, legacy_exit_code=4)
        assert decision.scheme == "legacy"
        assert decision.exit_code == 4
        assert decision.blocking is True
        assert decision.blocking_categories == ()

    def test_legacy_scheme_zero_exit_code_not_blocking(self) -> None:
        from abicheck.severity import compute_gate_decision

        decision = compute_gate_decision([], None, legacy_exit_code=0)
        assert decision.scheme == "legacy"
        assert decision.blocking is False

    def test_severity_scheme_addition_promoted_to_error(self) -> None:
        from abicheck.severity import compute_gate_decision, resolve_severity_config

        cfg = resolve_severity_config("default", addition="error")
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        decision = compute_gate_decision(changes, cfg)
        assert decision.scheme == "severity"
        assert decision.blocking is True
        assert decision.blocking_categories == ("addition",)
        assert decision.exit_code != 0

    def test_severity_scheme_no_error_level_findings(self) -> None:
        from abicheck.severity import PRESET_DEFAULT, compute_gate_decision

        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        decision = compute_gate_decision(changes, PRESET_DEFAULT)
        assert decision.scheme == "severity"
        assert decision.blocking is False
        assert decision.blocking_categories == ()
        assert decision.exit_code == 0

    def test_matches_compute_exit_code(self) -> None:
        """The gate decision's exit_code must never disagree with the
        standalone compute_exit_code for the same inputs — it's derived
        from it, not a parallel reimplementation."""
        from abicheck.severity import (
            PRESET_STRICT,
            compute_exit_code,
            compute_gate_decision,
        )

        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        decision = compute_gate_decision(changes, PRESET_STRICT)
        assert decision.exit_code == compute_exit_code(changes, PRESET_STRICT)

    def test_multiple_blocking_categories(self) -> None:
        from abicheck.severity import PRESET_STRICT, compute_gate_decision

        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        decision = compute_gate_decision(changes, PRESET_STRICT)
        assert set(decision.blocking_categories) == {"abi_breaking", "addition"}

    def test_single_input_keeps_exit_code_and_categories_in_sync(self) -> None:
        """compute_gate_decision takes exactly one `changes` sequence used
        for both exit_code and blocking_categories — by construction it
        cannot reproduce the bug class this type replaces (two independent
        call sites computing exit_code and blocking_categories from two
        different change sets, e.g. one --show-only-filtered and one not,
        and disagreeing as a result)."""
        from abicheck.severity import compute_gate_decision, resolve_severity_config

        cfg = resolve_severity_config("default", addition="error")
        changes = [
            _FakeChange(ChangeKind.ENUM_MEMBER_RENAMED),  # potential_breaking: warning
            _FakeChange(ChangeKind.FUNC_ADDED),  # addition: error (overridden)
        ]
        decision = compute_gate_decision(changes, cfg)
        assert decision.blocking is True
        assert decision.blocking_categories == ("addition",)
        assert "potential_breaking" not in decision.blocking_categories  # not error-level
