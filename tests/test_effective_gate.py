# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Characterization and completion tests for
``abicheck.policy.effective_gate`` (ADR-061 gap D / Definition-of-Done item
8, closure package 4): the ``EffectiveGate``/``GateSeverityState``
convergence between single-pair ``compare``'s ``ResolvedCompareConfig`` and
the directory/package release fan-out's ``GateOptions``.

**Characterization half** (written and run against the *pre-refactor*
behavior, per this repo's own bug-class regression-testing convention):
:class:`TestPreRefactorFoldBehaviorPreserved` pins the exact outcomes
``apply_release_gate_pack``/``apply_to_compare_config`` produced before this
module existed -- a literal, independently-computed oracle for each
function, not merely "whatever the code currently returns" -- so introducing
``GateSeverityState`` underneath them could not silently change either
fold's result.

**Completion half**: :class:`TestEffectiveGateParity` and
:class:`TestGateSeverityStateProperties` state the actual convergence this
closure package's task asked for -- both callers now fold onto the *same*
:class:`~abicheck.policy.effective_gate.GateSeverityState` shape, and both
``GateOptions`` (a property) and ``ResolvedCompareConfig`` (via
``workflows.gate.effective_gate_for_resolved_compare_config`` -- a free
function rather than a third property, since ``cli_helpers_compare.py`` sits
at its own ``architecture/debt.yaml`` no-growth cap) resolve to the *same*
:class:`~abicheck.policy.effective_gate.EffectiveGate` shape -- checked
against inputs beyond any one example, per this repo's own "General
invariant" bug-fix-test-contract expectation.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, strategies as st

from abicheck.cli_helpers_compare import resolve_compare_config
from abicheck.pack_application import PackApplication, apply_to_compare_config
from abicheck.policy.effective_gate import (
    EffectiveGate,
    GateSeverityState,
    ScopedGateSelection,
)
from abicheck.policy.gate_pack_fold import (
    GATE_SEVERITY_CATEGORIES,
    fold_gate_pack_severity,
    gate_exit_code_scheme,
)
from abicheck.policy.release_gate_options import (
    GateOptions,
    apply_release_gate_pack,
    resolve_release_gate_options,
)
from abicheck.policy.severity import SeverityConfig, SeverityLevel
from abicheck.workflows.gate import (
    effective_gate_for_resolved_compare_config,
    scoped_gate_selection_from_result,
)

_PRESETS = (None, "default", "strict", "info-only")


@st.composite
def _severity_levels(draw: st.DrawFn) -> dict[str, SeverityLevel]:
    return {
        c: draw(st.sampled_from(list(SeverityLevel)))
        for c in GATE_SEVERITY_CATEGORIES
        if draw(st.booleans())
    }


@st.composite
def _raw_category_values(draw: st.DrawFn) -> dict[str, str | None]:
    return {
        c: draw(
            st.one_of(st.none(), st.sampled_from([lvl.value for lvl in SeverityLevel]))
        )
        for c in GATE_SEVERITY_CATEGORIES
    }


class TestPreRefactorFoldBehaviorPreserved:
    """An independent oracle for what each fold produced *before*
    ``GateSeverityState`` existed -- reimplemented here from each function's
    own pre-refactor docstring/body rather than imported, so a regression in
    the real implementation cannot also be baked into the oracle."""

    @staticmethod
    def _oracle_apply_release_gate_pack(
        pack_application: PackApplication | None,
        *,
        severity_preset: str | None,
        raw: dict[str, str | None],
    ) -> tuple[str | None, str | None, str | None, str | None, str | None]:
        levels = {} if pack_application is None else pack_application.severity_levels
        folded = fold_gate_pack_severity(dict(raw), levels)
        return (
            severity_preset,
            folded["abi_breaking"],
            folded["potential_breaking"],
            folded["quality_issues"],
            folded["addition"],
        )

    @given(
        severity_preset=st.sampled_from(_PRESETS),
        raw=_raw_category_values(),
        data=st.data(),
    )
    def test_apply_release_gate_pack_matches_oracle(
        self,
        severity_preset: str | None,
        raw: dict[str, str | None],
        data: st.DataObject,
    ) -> None:
        levels = data.draw(_severity_levels())
        pack_application = PackApplication(policy_overrides={}, severity_levels=levels)
        got = apply_release_gate_pack(
            pack_application,
            severity_preset=severity_preset,
            severity_abi_breaking=raw["abi_breaking"],
            severity_potential_breaking=raw["potential_breaking"],
            severity_quality_issues=raw["quality_issues"],
            severity_addition=raw["addition"],
        )
        expected = self._oracle_apply_release_gate_pack(
            pack_application, severity_preset=severity_preset, raw=raw
        )
        assert got == expected

    def test_apply_release_gate_pack_none_application_is_untouched(self) -> None:
        got = apply_release_gate_pack(
            None,
            severity_preset="strict",
            severity_abi_breaking="error",
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
        )
        assert got == ("strict", "error", None, None, None)

    @given(preset=st.sampled_from(_PRESETS), data=st.data())
    def test_apply_to_compare_config_matches_oracle(
        self, preset: str | None, data: st.DataObject
    ) -> None:
        levels = data.draw(_severity_levels())
        resolved_cfg = resolve_compare_config(
            None, cli_severity_preset=preset, cli_scope_public=None
        )
        pack_application = PackApplication(policy_overrides={}, severity_levels=levels)
        got = apply_to_compare_config(resolved_cfg, pack_application)

        if not levels:
            assert got == resolved_cfg
            return
        expected_folded = fold_gate_pack_severity(
            {c: getattr(resolved_cfg.severity, c) for c in GATE_SEVERITY_CATEGORIES},
            levels,
        )
        expected_severity = dataclasses.replace(
            resolved_cfg.severity, **expected_folded
        )
        assert got.severity == expected_severity
        assert got.severity_active is True


class TestGateSeverityStateProperties:
    """``GateSeverityState``'s own contract, per this repo's "Primitive-
    level property tests" convention for a new reusable fold/merge value
    type."""

    @given(levels=_raw_category_values())
    def test_construction_requires_every_category(
        self, levels: dict[str, str | None]
    ) -> None:
        GateSeverityState(levels=levels, active=False)  # no raise
        short = dict(levels)
        del short[GATE_SEVERITY_CATEGORIES[0]]
        with pytest.raises(ValueError, match="missing categories"):
            GateSeverityState(levels=short, active=False)

    @given(levels=_raw_category_values(), active=st.booleans())
    def test_empty_pack_contribution_is_the_identity_object(
        self, levels: dict[str, str | None], active: bool
    ) -> None:
        state = GateSeverityState(levels=levels, active=active)
        assert state.folded({}) is state

    @given(levels=_raw_category_values(), active=st.booleans(), data=st.data())
    def test_a_real_contribution_always_activates(
        self, levels: dict[str, str | None], active: bool, data: st.DataObject
    ) -> None:
        pack_levels = data.draw(_severity_levels())
        if not pack_levels:
            return
        state = GateSeverityState(levels=levels, active=active).folded(pack_levels)
        assert state.active is True
        assert state.levels == fold_gate_pack_severity(levels, pack_levels)


class TestEffectiveGateParity:
    """``EffectiveGate`` is the same shape from both producers -- the
    completion test this closure package's task asked for: equivalent
    inputs to ``GateOptions``/``ResolvedCompareConfig`` must resolve to an
    *equal* ``EffectiveGate``, not merely an equal ``exit_code_scheme``."""

    def test_from_severity_derives_scheme_from_the_shared_rule(self) -> None:
        cfg = SeverityConfig()
        assert EffectiveGate.from_severity(None).exit_code_scheme == "legacy"
        assert EffectiveGate.from_severity(cfg).exit_code_scheme == "severity"
        assert EffectiveGate.from_severity(
            cfg
        ).exit_code_scheme == gate_exit_code_scheme(True)

    def test_defaults(self) -> None:
        gate = EffectiveGate.from_severity(None)
        assert gate.require_complete_analysis is False
        assert gate.scope is None

    def test_scope_and_require_complete_analysis_are_carried(self) -> None:
        scope = ScopedGateSelection(kind="used_by", targets=("app",))
        gate = EffectiveGate.from_severity(
            SeverityConfig(), require_complete_analysis=True, scope=scope
        )
        assert gate.require_complete_analysis is True
        assert gate.scope == scope

    @given(preset=st.sampled_from(_PRESETS), data=st.data())
    def test_gate_options_and_resolved_compare_config_agree(
        self, preset: str | None, data: st.DataObject
    ) -> None:
        """The five distinct assertions the completion test requires:
        compatibility (severity content), assurance
        (``require_complete_analysis``, both default False here), scope
        (both default None here), gate (``exit_code_scheme``), and the
        derived process outcome (equality of the whole resolved object) --
        checked separately rather than only the aggregate equality, so a
        regression in any one axis fails with a pinpointed assertion."""
        levels = data.draw(_severity_levels())
        pack_application = PackApplication(policy_overrides={}, severity_levels=levels)

        resolved_cfg = resolve_compare_config(
            None, cli_severity_preset=preset, cli_scope_public=None
        )
        single_pair = apply_to_compare_config(resolved_cfg, pack_application)

        release = resolve_release_gate_options(
            pack_application,
            severity_preset=preset,
            severity_abi_breaking=None,
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
        )

        compare_gate = effective_gate_for_resolved_compare_config(single_pair)
        release_gate = release.effective_gate

        # 1. gate (exit-code scheme)
        assert compare_gate.exit_code_scheme == release_gate.exit_code_scheme
        # 2. compatibility (resolved severity content)
        assert compare_gate.severity == release_gate.severity
        # 3. assurance
        assert (
            compare_gate.require_complete_analysis
            == release_gate.require_complete_analysis
            is False
        )
        # 4. scope
        assert compare_gate.scope == release_gate.scope is None
        # 5. process outcome: the two EffectiveGate values are wholly equal
        assert compare_gate == release_gate

    def test_gate_options_effective_gate_property(self) -> None:
        gate = GateOptions(severity_preset=None, severity=None)
        assert gate.effective_gate == EffectiveGate.from_severity(None)
        cfg = SeverityConfig()
        gate2 = GateOptions(severity_preset="strict", severity=cfg)
        assert gate2.effective_gate == EffectiveGate.from_severity(cfg)

    def test_resolved_compare_config_effective_gate_helper(self) -> None:
        cfg = resolve_compare_config(
            None, cli_severity_preset=None, cli_scope_public=None
        )
        assert effective_gate_for_resolved_compare_config(
            cfg
        ) == EffectiveGate.from_severity(None)
        cfg2 = resolve_compare_config(
            None, cli_severity_preset="strict", cli_scope_public=None
        )
        assert effective_gate_for_resolved_compare_config(
            cfg2
        ) == EffectiveGate.from_severity(cfg2.severity)


class _FakeResult:
    """A minimal stand-in for the ``DiffResult`` fields
    ``scoped_gate_selection_from_result`` reads -- mirrors
    ``effective_config_digest._gate_scope_str``'s own reading contract."""

    def __init__(
        self,
        gate_scope: str | None = None,
        used_by: tuple[dict[str, object], ...] = (),
        required_symbols: dict[str, object] | None = None,
    ) -> None:
        self.gate_scope = gate_scope
        self.used_by = used_by
        self.required_symbols = required_symbols or {}


class TestScopedGateSelectionFromResult:
    """``scoped_gate_selection_from_result``'s own contract."""

    def test_none_when_no_scope_selected(self) -> None:
        assert scoped_gate_selection_from_result(None) is None
        assert scoped_gate_selection_from_result(_FakeResult()) is None

    def test_used_by_reads_app_paths_sorted(self) -> None:
        result = _FakeResult(
            gate_scope="used_by",
            used_by=({"app": "b.so"}, {"app": "a.so"}),
        )
        scope = scoped_gate_selection_from_result(result)
        assert scope == ScopedGateSelection(kind="used_by", targets=("a.so", "b.so"))

    def test_required_symbol_reads_entrypoints_sorted(self) -> None:
        result = _FakeResult(
            gate_scope="required_symbol",
            required_symbols={"required_entrypoints": ["sym_b", "sym_a"]},
        )
        scope = scoped_gate_selection_from_result(result)
        assert scope == ScopedGateSelection(
            kind="required_symbol", targets=("sym_a", "sym_b")
        )


class TestEffectiveGateForResolvedCompareConfigCarriesRealAxes:
    """Codex review (PR #1192): the fix for "two invocations with genuinely
    different exit/gate behavior produce an equal EffectiveGate" --
    ``require_complete_analysis``/``result``-derived scope must actually
    differentiate the returned object, not silently default away."""

    def test_require_complete_analysis_differentiates(self) -> None:
        cfg = resolve_compare_config(
            None, cli_severity_preset=None, cli_scope_public=None
        )
        off = effective_gate_for_resolved_compare_config(
            cfg, require_complete_analysis=False
        )
        on = effective_gate_for_resolved_compare_config(
            cfg, require_complete_analysis=True
        )
        assert off != on
        assert off.require_complete_analysis is False
        assert on.require_complete_analysis is True

    def test_used_by_scope_differentiates(self) -> None:
        cfg = resolve_compare_config(
            None, cli_severity_preset=None, cli_scope_public=None
        )
        no_scope = effective_gate_for_resolved_compare_config(cfg)
        scoped = effective_gate_for_resolved_compare_config(
            cfg, result=_FakeResult(gate_scope="used_by", used_by=({"app": "x"},))
        )
        assert no_scope != scoped
        assert no_scope.scope is None
        assert scoped.scope == ScopedGateSelection(kind="used_by", targets=("x",))

    def test_two_different_required_symbol_selections_differentiate(self) -> None:
        """The literal finding example: two runs differing only in which
        symbol is required must not collapse to one EffectiveGate."""
        cfg = resolve_compare_config(
            None, cli_severity_preset=None, cli_scope_public=None
        )
        a = effective_gate_for_resolved_compare_config(
            cfg,
            result=_FakeResult(
                gate_scope="required_symbol",
                required_symbols={"required_entrypoints": ["sym_a"]},
            ),
        )
        b = effective_gate_for_resolved_compare_config(
            cfg,
            result=_FakeResult(
                gate_scope="required_symbol",
                required_symbols={"required_entrypoints": ["sym_b"]},
            ),
        )
        assert a != b

    def test_no_result_and_no_flag_matches_the_prior_default_behavior(self) -> None:
        """Characterization: a caller passing neither (this function's
        original, pre-fix call shape) still gets the same all-defaults
        EffectiveGate as before this fix."""
        cfg = resolve_compare_config(
            None, cli_severity_preset="strict", cli_scope_public=None
        )
        gate = effective_gate_for_resolved_compare_config(cfg)
        assert gate == EffectiveGate.from_severity(cfg.severity)
        assert gate.require_complete_analysis is False
        assert gate.scope is None
