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
    P0.4 tables.

    Asserted directly against ``compare_cmd``'s ``.help``/the ``--no-baseline``
    option's ``.help`` -- the actual source ``--help``/``--help-all`` render
    from -- rather than rendered ``--help-all`` terminal output (Codex
    review: the rendered panel hard-wraps at column width, splitting words
    across lines with no join marker, and every one of the plain substrings
    this class checks (a bare ``3``, ``audit``, ``--severity-preset``,
    ``info-only``, ``docs/reference/exit-codes.md``) already occurred
    elsewhere in ``--help-all``'s *pre-existing* output -- from other exit
    codes, --no-baseline's own pre-existing text, the --severity-preset
    option's own listing, and the --require-complete-analysis paragraph's
    identical doc link -- so a version of this test asserting only those
    substrings against rendered output would pass unchanged even with the
    new paragraph and --no-baseline addendum reverted entirely, per Codex's
    own before/after check against ``ef4be748^``."""

    def test_docstring_states_the_audit_gate_axis_and_its_exit_code(self) -> None:
        from abicheck.cli import main

        doc = main.commands["compare"].help or ""
        # Whitespace-normalized (single spaces) so a phrase spanning a
        # docstring line wrap can't fail this assertion over incidental
        # newline/indentation formatting -- only the wording is under test.
        normalized_doc = " ".join(doc.split())
        assert "AUDIT_GATE_EXIT_CODE" in normalized_doc
        assert "third, independent orthogonal axis" in normalized_doc
        assert "opts that audit into gating on its own findings" in normalized_doc
        assert "contributing exit 3" in normalized_doc
        assert "docs/reference/exit-codes.md" in normalized_doc

    def test_no_baseline_help_names_severity_preset_as_the_arming_switch(
        self,
    ) -> None:
        """The one fact a reader cannot get from the exit-code tables
        alone: which flag actually arms this axis, and its opt-out."""
        from abicheck.cli import main

        no_baseline = next(
            p for p in main.commands["compare"].params if p.name == "no_baseline"
        )
        normalized_help = " ".join((no_baseline.help or "").split())
        assert "--severity-preset is the sole switch that arms" in normalized_help
        assert "this audit's own gate" in normalized_help
        assert "contributes exit 3" in normalized_help
        assert SEVERITY_PRESET_DISABLES_AUDIT_GATE in normalized_help

    def test_help_all_actually_renders_both_additions(self) -> None:
        """A shallower smoke test on the real rendered surface: the two
        distinctive, single-token/no-wrap-risk markers above must still
        appear once the panel renders and wraps -- proving the docstring/
        help= text checked above is not orphaned (e.g. via a broken
        decorator, a hidden option, or a help formatter that drops it)."""
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "AUDIT_GATE_EXIT_CODE" in result.output
        assert AUDIT_GATE_EXIT_CODE == 3  # sanity: the code this axis names
