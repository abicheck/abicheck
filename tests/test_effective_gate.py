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
import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from test_release_selection import _invoke, _snap, _write_snap

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

    def test_release_scope_axes_stay_not_applicable_at_this_scope(self) -> None:
        """Codex review, PR #1192, fourth follow-up round (finding 6): a
        bare single-pair ``compare`` never resolves ADR-065's scope-
        completeness policy for itself -- ``cfg.on_incomplete_scope``/
        ``cfg.fail_on_removed_library`` exist only to be *forwarded* to a
        release fan-out this run might dispatch to, so this function must
        not read them onto its own returned ``EffectiveGate`` (see this
        function's own docstring)."""
        cfg = resolve_compare_config(
            None, cli_severity_preset=None, cli_scope_public=None
        )
        assert cfg.on_incomplete_scope == "warn"  # cfg itself does carry it
        gate = effective_gate_for_resolved_compare_config(cfg)
        assert gate.on_incomplete_scope is None
        assert gate.fail_on_removed_library is None


class TestReleaseScopeAxesOnEffectiveGate:
    """Codex review, PR #1192, fourth follow-up round (finding 6): the same
    "EffectiveGate claims completeness it doesn't have" gap findings 1/4/5
    closed for require_complete_analysis/scope/severity-completeness, for
    ADR-065's two directory/package release gate axes --
    ``--on-incomplete-scope`` and ``--fail-on-removed-library``. Two
    otherwise-identical release runs differing only in one of these exit
    differently (``0``/``1``, or ``0``/``8``); before this fix
    ``EffectiveGate`` stayed silent about both, so it could claim two such
    runs "gate identically" when they provably do not."""

    def test_two_different_on_incomplete_scope_values_differentiate(self) -> None:
        warn = EffectiveGate.from_severity(None, on_incomplete_scope="warn")
        block = EffectiveGate.from_severity(None, on_incomplete_scope="block")
        assert warn != block
        assert warn.on_incomplete_scope == "warn"
        assert block.on_incomplete_scope == "block"

    def test_two_different_fail_on_removed_library_values_differentiate(self) -> None:
        off = EffectiveGate.from_severity(None, fail_on_removed_library=False)
        on = EffectiveGate.from_severity(None, fail_on_removed_library=True)
        assert off != on
        assert off.fail_on_removed_library is False
        assert on.fail_on_removed_library is True

    def test_unset_release_axes_default_to_not_applicable(self) -> None:
        gate = EffectiveGate.from_severity(None)
        assert gate.on_incomplete_scope is None
        assert gate.fail_on_removed_library is None

    def test_gate_options_effective_gate_carries_release_scope_axes(self) -> None:
        warn = GateOptions(
            severity_preset=None, severity=None, on_incomplete_scope="warn"
        )
        block = GateOptions(
            severity_preset=None, severity=None, on_incomplete_scope="block"
        )
        assert warn.effective_gate != block.effective_gate
        assert warn.effective_gate.on_incomplete_scope == "warn"
        assert block.effective_gate.on_incomplete_scope == "block"

        off = GateOptions(
            severity_preset=None, severity=None, fail_on_removed_library=False
        )
        on = GateOptions(
            severity_preset=None, severity=None, fail_on_removed_library=True
        )
        assert off.effective_gate != on.effective_gate
        assert off.effective_gate.fail_on_removed_library is False
        assert on.effective_gate.fail_on_removed_library is True

    def test_resolve_release_gate_options_threads_axes_through(self) -> None:
        """The real production resolver (``cli_compare_release.py``'s own
        call site) must pass these through unchanged -- not merely
        ``GateOptions``'s own constructor accepting them in a test."""
        opts = resolve_release_gate_options(
            None,
            severity_preset=None,
            severity_abi_breaking=None,
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
            on_incomplete_scope="block",
            fail_on_removed_library=True,
        )
        assert opts.on_incomplete_scope == "block"
        assert opts.fail_on_removed_library is True
        assert opts.effective_gate.on_incomplete_scope == "block"
        assert opts.effective_gate.fail_on_removed_library is True

    def test_resolve_release_gate_options_defaults_stay_not_applicable(self) -> None:
        """Every pre-existing caller of ``resolve_release_gate_options`` that
        does not know about these two axes yet gets the identical "not
        applicable" default it always did -- adding the parameters cannot
        change behavior for a caller that omits them."""
        opts = resolve_release_gate_options(
            None,
            severity_preset=None,
            severity_abi_breaking=None,
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
        )
        assert opts.on_incomplete_scope is None
        assert opts.fail_on_removed_library is None
        assert opts.effective_gate.on_incomplete_scope is None
        assert opts.effective_gate.fail_on_removed_library is None


class TestReleaseScopeAxesReachTheRealDigest:
    """Codex review, PR #1192, fourth follow-up round (finding 6):
    production-wiring proof, not just the type's own unit tests -- two real
    ``compare <dir> <dir>`` release-fan-out runs differing only in
    ``.abicheck.yml``'s ``scope.on_incomplete``/``gate.fail_on_removed_
    library`` must report a genuinely different ``effective_config_digest``/
    ``effective_config_fields``, the same way findings 1/4/5 required for
    ``--require-complete-analysis``/``--used-by``/severity.

    Asymmetric pre-fix status between the two axes, confirmed by stashing
    the production fix and re-running both: ``gate.fail_on_removed_library``
    was previously *absent from the digest entirely* (a real ``KeyError``
    pre-fix) -- the field genuinely did not exist. ``gate.on_incomplete_
    scope`` already carried the right *value* pre-fix (read off ``result``
    rather than off ``EffectiveGate``, so its own dedicated unit tests above
    are what falsify that half of the gap -- the type itself could not
    distinguish two configs differing only in this axis, even though the
    digest happened to read the value from elsewhere); this CLI-level test
    is its non-regression proof that routing the read through ``gate``
    instead changes nothing observable."""

    @staticmethod
    def _run(tmp_path: Path, name: str, config_yaml: str) -> dict[str, object]:
        old_dir, new_dir = tmp_path / f"{name}_old", tmp_path / f"{name}_new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        cfg = tmp_path / f"{name}.abicheck.yml"
        cfg.write_text(config_yaml, encoding="utf-8")
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(cfg),
            "-o",
            "json=-",
        )
        assert code == 0
        data: dict[str, object] = json.loads(out)
        return data

    def test_on_incomplete_scope_changes_the_release_digest(
        self, tmp_path: Path
    ) -> None:
        warn = self._run(tmp_path, "warn", "scope:\n  on_incomplete: warn\n")
        block = self._run(tmp_path, "block", "scope:\n  on_incomplete: block\n")
        warn_fields = warn["effective_config_fields"]
        block_fields = block["effective_config_fields"]
        assert isinstance(warn_fields, dict) and isinstance(block_fields, dict)
        assert warn_fields["gate.on_incomplete_scope"] == "warn"
        assert block_fields["gate.on_incomplete_scope"] == "block"
        assert warn["effective_config_digest"] != block["effective_config_digest"]

    def test_fail_on_removed_library_changes_the_release_digest(
        self, tmp_path: Path
    ) -> None:
        off = self._run(
            tmp_path, "off", "gate:\n  fail_on_removed_library: false\n"
        )
        on = self._run(tmp_path, "on", "gate:\n  fail_on_removed_library: true\n")
        off_fields = off["effective_config_fields"]
        on_fields = on["effective_config_fields"]
        assert isinstance(off_fields, dict) and isinstance(on_fields, dict)
        assert off_fields["gate.fail_on_removed_library"] == "False"
        assert on_fields["gate.fail_on_removed_library"] == "True"
        assert off["effective_config_digest"] != on["effective_config_digest"]
