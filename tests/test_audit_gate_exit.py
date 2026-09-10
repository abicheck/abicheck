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

from abicheck.policy.audit_gate_exit import (
    AUDIT_GATE_EXIT_CODE,
    SEVERITY_PRESET_DISABLES_AUDIT_GATE,
    audit_gate_enabled_for_severity_preset,
    audit_gate_exit_contribution,
    fold_audit_gate_exit,
)
from abicheck.policy.classification import Verdict


@dataclass
class _FakeFinding:
    """Stands in for a :class:`~abicheck.report.finding.ReportFinding` --
    only the ``.verdict`` attribute the axis actually reads."""

    verdict: Verdict


@dataclass
class _FakeChangeWithNoVerdict:
    """A raw, unresolved object (e.g. ``Change``) that carries no
    ``.verdict`` at all -- must never be mistaken for a gating finding."""

    kind: object = None


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
            audit_gate_enabled_for_severity_preset(SEVERITY_PRESET_DISABLES_AUDIT_GATE)
            is False
        )
        assert SEVERITY_PRESET_DISABLES_AUDIT_GATE == "info-only"

    @pytest.mark.parametrize("preset", ["default", "strict"])
    def test_other_presets_are_enabled(self, preset: str) -> None:
        assert audit_gate_enabled_for_severity_preset(preset) is True


class TestAuditGateExitContribution:
    def test_disabled_is_always_zero(self) -> None:
        """Even a BREAKING-verdict finding contributes 0 when the axis
        was never opted into -- the opt-in gate is checked before anything
        about the findings themselves."""
        findings = [_FakeFinding(Verdict.BREAKING)]
        assert audit_gate_exit_contribution(findings, enabled=False) == 0

    def test_empty_findings_is_zero(self) -> None:
        assert audit_gate_exit_contribution([], enabled=True) == 0

    def test_breaking_verdict_gates(self) -> None:
        findings = [_FakeFinding(Verdict.BREAKING)]
        assert (
            audit_gate_exit_contribution(findings, enabled=True) == AUDIT_GATE_EXIT_CODE
        )

    def test_api_break_verdict_gates(self) -> None:
        """case148/case149's own classification -- the exact reproduction
        this axis exists for."""
        findings = [_FakeFinding(Verdict.API_BREAK)]
        assert (
            audit_gate_exit_contribution(findings, enabled=True) == AUDIT_GATE_EXIT_CODE
        )

    def test_risk_verdict_does_not_gate(self) -> None:
        """case143's own classification -- the exact regression this axis
        must not reintroduce."""
        findings = [_FakeFinding(Verdict.COMPATIBLE_WITH_RISK)]
        assert audit_gate_exit_contribution(findings, enabled=True) == 0

    def test_compatible_verdict_does_not_gate(self) -> None:
        findings = [_FakeFinding(Verdict.COMPATIBLE)]
        assert audit_gate_exit_contribution(findings, enabled=True) == 0

    def test_one_gating_finding_among_many_non_gating_ones_still_gates(self) -> None:
        findings = [
            _FakeFinding(Verdict.COMPATIBLE_WITH_RISK),
            _FakeFinding(Verdict.COMPATIBLE),
            _FakeFinding(Verdict.API_BREAK),
        ]
        assert (
            audit_gate_exit_contribution(findings, enabled=True) == AUDIT_GATE_EXIT_CODE
        )

    def test_a_policy_promoted_verdict_gates(self) -> None:
        """The regression this contract change closes (Codex security
        review, P1): a ``--policy`` ``overrides:``/``reclassify:`` rule
        that promotes a finding's *effective* verdict to BREAKING/API_BREAK
        must gate here even though nothing about its raw ``ChangeKind``
        changed -- this axis never re-derives a category from the raw kind,
        it only ever reads the already-resolved ``.verdict``."""
        findings = [_FakeFinding(Verdict.BREAKING)]
        assert (
            audit_gate_exit_contribution(findings, enabled=True) == AUDIT_GATE_EXIT_CODE
        )

    def test_an_object_with_no_verdict_attribute_is_never_mistaken_for_gating(
        self,
    ) -> None:
        """A raw, unresolved object (e.g. an unwrapped ``Change``) carries
        no ``.verdict`` at all and must be skipped, not misread as
        non-gating *or* gating -- a caller that forgot to resolve findings
        through ``build_report_findings`` first must never silently gate
        (or silently fail to), it should simply find nothing to read."""
        findings = [_FakeChangeWithNoVerdict()]
        assert audit_gate_exit_contribution(findings, enabled=True) == 0


class TestFoldAuditGateExit:
    def test_raises_a_clean_zero(self) -> None:
        assert fold_audit_gate_exit(0, AUDIT_GATE_EXIT_CODE) == AUDIT_GATE_EXIT_CODE

    def test_never_lowers_a_higher_base(self) -> None:
        assert fold_audit_gate_exit(7, AUDIT_GATE_EXIT_CODE) == 7

    def test_zero_contribution_never_raises(self) -> None:
        assert fold_audit_gate_exit(0, 0) == 0


class TestCompareHelpAllDocumentsTheAuditGateAxis:
    """The CLI help surface must name this axis, not just
    ``docs/reference/exit-codes.md`` -- a user reading ``compare --help``/
    ``--help-all`` has no other way to discover that ``--severity-preset``
    is the sole switch arming it under ``--no-baseline``. Regression test
    for the gap: the axis landed (``26978b98``..``08c8748e``) with the docs
    page updated but the CLI's own docstring and ``--no-baseline`` help=
    text left describing only the legacy/severity/contract-coverage/
    P0.4 tables."""

    def test_help_all_mentions_audit_gate_exit_code(self) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert str(AUDIT_GATE_EXIT_CODE) in result.output
        assert "audit" in result.output.lower()

    def test_help_all_names_severity_preset_as_the_arming_switch(self) -> None:
        """The one fact a reader cannot get from the exit-code tables
        alone: which flag actually arms this axis."""
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert "--severity-preset" in result.output
        assert SEVERITY_PRESET_DISABLES_AUDIT_GATE in result.output

    def test_help_all_points_to_exit_codes_doc(self) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert "docs/reference/exit-codes.md" in result.output
