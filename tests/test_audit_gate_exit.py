# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`abicheck.policy.audit_gate_exit` (ADR-068's
2026-09-10 amendment).

Directly against the leaf module, not only through the CLI -- the parity
corpus (``tests/parity/test_no_baseline_audit_corpus_parity.py``) proves the
axis reproduces legacy ``scan``'s gating on eleven real fixtures; this
module states the underlying contract and a property test
(``tests/test_audit_gate_exit_properties.py``) searches generated inputs
for a counterexample, per ``AGENTS.md``'s bug-class regression-testing rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.policy.audit_gate_exit import (
    AUDIT_GATE_EXIT_CODE,
    SEVERITY_PRESET_DISABLES_AUDIT_GATE,
    audit_gate_enabled_for_severity_preset,
    audit_gate_exit_contribution,
    fold_audit_gate_exit,
)
from abicheck.policy.classification import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
)


@dataclass
class _FakeChange:
    kind: ChangeKind


@dataclass
class _FakeFinding:
    change: _FakeChange


def _breaking_kind() -> ChangeKind:
    return next(iter(BREAKING_KINDS))


def _api_break_kind() -> ChangeKind:
    return next(iter(API_BREAK_KINDS))


def _risk_kind() -> ChangeKind:
    return next(iter(RISK_KINDS))


def _compatible_kind() -> ChangeKind:
    return next(iter(COMPATIBLE_KINDS))


class TestAuditGateExitCode:
    def test_never_two_or_four(self) -> None:
        """ADR-068 D2's own invariant, restated as a literal fact about the
        constant: the axis's own code must never collide with the
        compatibility family's source-break codes."""
        assert AUDIT_GATE_EXIT_CODE not in (2, 4)

    def test_not_zero(self) -> None:
        assert AUDIT_GATE_EXIT_CODE != 0


class TestAuditGateEnabledForSeverityPreset:
    def test_none_is_disabled(self) -> None:
        assert audit_gate_enabled_for_severity_preset(None) is False

    def test_info_only_is_disabled(self) -> None:
        assert (
            audit_gate_enabled_for_severity_preset(
                SEVERITY_PRESET_DISABLES_AUDIT_GATE
            )
            is False
        )
        assert SEVERITY_PRESET_DISABLES_AUDIT_GATE == "info-only"

    @pytest.mark.parametrize("preset", ["default", "strict"])
    def test_other_presets_are_enabled(self, preset: str) -> None:
        assert audit_gate_enabled_for_severity_preset(preset) is True


class TestAuditGateExitContribution:
    def test_disabled_is_always_zero(self) -> None:
        """Even a BREAKING-classified finding contributes 0 when the axis
        was never opted into -- the opt-in gate is checked before anything
        about the findings themselves."""
        findings = [_FakeFinding(_FakeChange(_breaking_kind()))]
        assert audit_gate_exit_contribution(findings, enabled=False) == 0

    def test_empty_findings_is_zero(self) -> None:
        assert audit_gate_exit_contribution([], enabled=True) == 0

    def test_breaking_kind_gates(self) -> None:
        findings = [_FakeFinding(_FakeChange(_breaking_kind()))]
        assert (
            audit_gate_exit_contribution(findings, enabled=True)
            == AUDIT_GATE_EXIT_CODE
        )

    def test_api_break_kind_gates(self) -> None:
        """case148/case149's own classification -- the exact reproduction
        this axis exists for."""
        findings = [_FakeFinding(_FakeChange(_api_break_kind()))]
        assert (
            audit_gate_exit_contribution(findings, enabled=True)
            == AUDIT_GATE_EXIT_CODE
        )

    def test_risk_kind_does_not_gate(self) -> None:
        """case143's own classification -- the exact regression this axis
        must not reintroduce."""
        findings = [_FakeFinding(_FakeChange(_risk_kind()))]
        assert audit_gate_exit_contribution(findings, enabled=True) == 0

    def test_compatible_kind_does_not_gate(self) -> None:
        findings = [_FakeFinding(_FakeChange(_compatible_kind()))]
        assert audit_gate_exit_contribution(findings, enabled=True) == 0

    def test_one_gating_finding_among_many_non_gating_ones_still_gates(self) -> None:
        findings = [
            _FakeFinding(_FakeChange(_risk_kind())),
            _FakeFinding(_FakeChange(_compatible_kind())),
            _FakeFinding(_FakeChange(_api_break_kind())),
        ]
        assert (
            audit_gate_exit_contribution(findings, enabled=True)
            == AUDIT_GATE_EXIT_CODE
        )

    def test_accepts_bare_change_objects_too(self) -> None:
        """A caller may pass bare ``Change``-shaped objects (``.kind``
        directly), not only ``ReportFinding``-shaped ones (``.change.kind``)
        -- both shapes appear across this codebase's call sites."""
        assert (
            audit_gate_exit_contribution([_FakeChange(_breaking_kind())], enabled=True)
            == AUDIT_GATE_EXIT_CODE
        )


class TestFoldAuditGateExit:
    def test_raises_a_clean_zero(self) -> None:
        assert fold_audit_gate_exit(0, AUDIT_GATE_EXIT_CODE) == AUDIT_GATE_EXIT_CODE

    def test_never_lowers_a_higher_base(self) -> None:
        assert fold_audit_gate_exit(7, AUDIT_GATE_EXIT_CODE) == 7

    def test_zero_contribution_never_raises(self) -> None:
        assert fold_audit_gate_exit(0, 0) == 0
