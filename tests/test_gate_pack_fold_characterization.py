# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Characterization tests for the *pre*-``EffectiveGate`` behavior of
``apply_release_gate_pack`` (``abicheck.policy.release_gate_options``) and
``apply_to_compare_config`` (``abicheck.pack_application``) -- ADR-061 gap D
/ Definition-of-Done item 8, closure package 4.

Written and run against today's implementation, *before* introducing
``abicheck.policy.effective_gate.GateSeverityState`` underneath both
functions (this repo's own bug-class regression-testing convention: capture
current behavior first, in its own commit, then refactor). Each oracle below
is reimplemented independently from each function's own current docstring/
body rather than imported from it, so a regression introduced by the
refactor cannot also be silently baked into the oracle that is supposed to
catch it.

``tests/test_effective_gate.py`` (added alongside the refactor itself)
restates these same two properties again, post-refactor, plus the actual
``EffectiveGate``/``GateSeverityState`` convergence the refactor introduces --
this file stays as the durable pre-refactor pin.
"""

from __future__ import annotations

import dataclasses

from hypothesis import given, strategies as st

from abicheck.cli_helpers_compare import resolve_compare_config
from abicheck.pack_application import PackApplication, apply_to_compare_config
from abicheck.policy.gate_pack_fold import (
    GATE_SEVERITY_CATEGORIES,
    fold_gate_pack_severity,
)
from abicheck.policy.release_gate_options import apply_release_gate_pack
from abicheck.policy.severity import SeverityLevel

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


class TestApplyReleaseGatePackCharacterization:
    @given(
        severity_preset=st.sampled_from(_PRESETS),
        raw=_raw_category_values(),
        data=st.data(),
    )
    def test_matches_independent_oracle(
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
        expected = _oracle_apply_release_gate_pack(
            pack_application, severity_preset=severity_preset, raw=raw
        )
        assert got == expected

    def test_none_application_returns_inputs_unchanged(self) -> None:
        got = apply_release_gate_pack(
            None,
            severity_preset="strict",
            severity_abi_breaking="error",
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
        )
        assert got == ("strict", "error", None, None, None)

    def test_empty_pack_levels_is_a_no_op(self) -> None:
        pack_application = PackApplication(policy_overrides={}, severity_levels={})
        got = apply_release_gate_pack(
            pack_application,
            severity_preset="default",
            severity_abi_breaking="warning",
            severity_potential_breaking="warning",
            severity_quality_issues=None,
            severity_addition=None,
        )
        assert got == ("default", "warning", "warning", None, None)


class TestApplyToCompareConfigCharacterization:
    @given(preset=st.sampled_from(_PRESETS), data=st.data())
    def test_matches_independent_oracle(
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

    def test_inert_pack_is_a_true_no_op(self) -> None:
        resolved_cfg = resolve_compare_config(
            None, cli_severity_preset="strict", cli_scope_public=None
        )
        pack_application = PackApplication(policy_overrides={})
        assert apply_to_compare_config(resolved_cfg, pack_application) == resolved_cfg
