# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Primitive-level property tests for the one gate-pack severity fold and
the one gate-algorithm derivation (`abicheck/policy/gate_pack_fold.py`,
duplication-and-convergence-assessment T6).

Written per this repository's own AGENTS.md "Primitive-level property
tests" guidance, which asks for exactly this treatment when a reusable
merge/fold primitive is added: a standalone class stating the primitive's
contract as invariants, decoupled from either caller's domain logic, rather
than only the two callers' own example-shaped tests. That guidance exists
because the bugs a fold like this actually ships are order-dependence,
input mutation, a silently-dropped key, and an asymmetry between the two
sides -- none of which a test written to confirm the fold's first caller
would search for.

The second half of the file pins T6's other half as a *structural*
invariant rather than a documented one: neither `GateOptions` nor
`ResolvedCompareConfig` may carry `exit_code_scheme` as a settable field
beside the predicate it is derived from. Both were documented as "purely
derived" while remaining independently constructible, and two unit-test
helpers were in fact constructing an inconsistent one -- so the property
worth testing is that the model can no longer express the disagreement, not
that today's resolver happens to avoid it.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.cli_helpers_compare import ResolvedCompareConfig, resolve_compare_config
from abicheck.policy.gate_pack_fold import (
    GATE_SEVERITY_CATEGORIES,
    LEGACY_SCHEME,
    SEVERITY_SCHEME,
    fold_gate_pack_severity,
    gate_exit_code_scheme,
)
from abicheck.policy.release_gate_options import GateOptions
from abicheck.policy.severity import SeverityConfig, SeverityLevel

# Deliberately not `SeverityLevel` members: the fold is generic over the
# per-category value type on purpose (its two production callers pass
# resolved `SeverityLevel`s and raw `str | None` CLI strings respectively),
# so the property tests use opaque values that cannot accidentally satisfy
# an assertion through a `SeverityLevel`-specific coincidence.
_VALUES = st.one_of(st.none(), st.integers(), st.text(max_size=4))


@st.composite
def _current(draw: st.DrawFn) -> dict[str, object]:
    return {c: draw(_VALUES) for c in GATE_SEVERITY_CATEGORIES}


@st.composite
def _levels(draw: st.DrawFn) -> dict[str, object]:
    return {c: draw(_VALUES) for c in GATE_SEVERITY_CATEGORIES if draw(st.booleans())}


class TestFoldGatePackSeverityProperties:
    """`fold_gate_pack_severity`'s contract, stated as invariants."""

    def test_categories_match_severity_config_fields(self) -> None:
        """The category vocabulary is not an independent list.

        Mechanical exhaustiveness, the same shape as the `ChangeKind`
        partition gate: a fifth `SeverityConfig` category added without a
        matching entry here would otherwise be silently unfoldable -- a pack
        could assign it, the resolver would record it, and the fold would
        drop it, which is the decorative-pack failure `pack_application.py`
        exists to prevent.
        """
        assert set(GATE_SEVERITY_CATEGORIES) == {
            f.name for f in dataclasses.fields(SeverityConfig)
        }

    @given(current=_current(), levels=_levels())
    def test_result_covers_exactly_the_categories(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        assert set(fold_gate_pack_severity(current, levels)) == set(
            GATE_SEVERITY_CATEGORIES
        )

    @given(current=_current(), levels=_levels())
    def test_pack_wins_exactly_where_it_supplied_a_level(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        """The whole rule, checked per category against an oracle that is not
        the implementation's own expression of it."""
        folded = fold_gate_pack_severity(current, levels)
        for category in GATE_SEVERITY_CATEGORIES:
            if category in levels:
                assert folded[category] == levels[category]
            else:
                assert folded[category] == current[category]

    @given(current=_current())
    def test_no_contribution_is_the_identity(self, current: dict[str, object]) -> None:
        assert fold_gate_pack_severity(current, {}) == current

    @given(current=_current(), levels=_levels())
    def test_a_full_contribution_ignores_the_pre_pack_values(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        """A pack supplying every category makes the pre-pack values
        irrelevant -- the boundary case of the rule above, which also fails
        loudly if the fold ever started *combining* the two sides rather
        than choosing between them."""
        full = dict.fromkeys(GATE_SEVERITY_CATEGORIES, "pack")
        assert fold_gate_pack_severity(current, full) == full
        assert fold_gate_pack_severity(levels | full, full) == full

    @given(current=_current(), levels=_levels())
    def test_idempotent(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        once = fold_gate_pack_severity(current, levels)
        assert fold_gate_pack_severity(once, levels) == once

    @given(current=_current(), levels=_levels(), data=st.data())
    def test_independent_of_key_insertion_order(
        self, current: dict[str, object], levels: dict[str, object], data: st.DataObject
    ) -> None:
        """Neither mapping's own iteration order may reach the result.

        `PackApplication.severity_levels` is built by iterating a mapping
        whose order follows the resolver's field table, and a release run
        builds `current` positionally -- so an order-sensitive fold would be
        invisible in production and would surface only once one of those two
        orders changed.
        """
        shuffled_levels = dict(data.draw(st.permutations(list(levels.items()))))
        shuffled_current = dict(data.draw(st.permutations(list(current.items()))))
        assert fold_gate_pack_severity(
            shuffled_current, shuffled_levels
        ) == fold_gate_pack_severity(current, levels)

    @given(current=_current(), levels=_levels())
    def test_does_not_mutate_its_inputs(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        """Both callers reuse their input mappings after folding (the release
        fan-out returns the pre-pack preset alongside the folded values)."""
        before_current, before_levels = dict(current), dict(levels)
        fold_gate_pack_severity(current, levels)
        assert current == before_current
        assert levels == before_levels

    @given(
        current=_current(),
        unknown=st.text(min_size=1, max_size=8).filter(
            lambda s: s not in GATE_SEVERITY_CATEGORIES
        ),
    )
    def test_an_unknown_category_is_rejected_not_ignored(
        self, current: dict[str, object], unknown: str
    ) -> None:
        with pytest.raises(ValueError, match="unknown categories"):
            fold_gate_pack_severity(current, {unknown: "x"})

    @given(dropped=st.sampled_from(GATE_SEVERITY_CATEGORIES))
    def test_a_category_missing_from_both_sides_is_an_error(self, dropped: str) -> None:
        """A short `current` is a caller bug, not a category to omit: the
        result must never come back missing a key a `SeverityConfig`
        splat then fails on far from the cause."""
        current = {c: "x" for c in GATE_SEVERITY_CATEGORIES if c != dropped}
        with pytest.raises(KeyError):
            fold_gate_pack_severity(current, {})
        # ... unless the pack itself supplies exactly that category.
        assert fold_gate_pack_severity(current, {dropped: "p"})[dropped] == "p"

    @given(current=_current(), levels=_levels())
    def test_result_always_splats_into_a_severity_config(
        self, current: dict[str, object], levels: dict[str, object]
    ) -> None:
        """The single-pair caller's real use: `replace(cfg.severity,
        **folded)`. Exercised against the real `SeverityConfig` rather than
        asserted structurally, since that class validates its own fields."""
        typed_current = dict.fromkeys(GATE_SEVERITY_CATEGORIES, SeverityLevel.WARNING)
        typed_levels = {c: SeverityLevel.ERROR for c in levels}
        folded = fold_gate_pack_severity(typed_current, typed_levels)
        config = SeverityConfig(**folded)
        for category in GATE_SEVERITY_CATEGORIES:
            expected = (
                SeverityLevel.ERROR if category in levels else SeverityLevel.WARNING
            )
            assert getattr(config, category) is expected


class TestGateExitCodeScheme:
    """`gate_exit_code_scheme` over its whole (two-point) domain."""

    def test_exhaustive(self) -> None:
        assert gate_exit_code_scheme(True) == SEVERITY_SCHEME == "severity"
        assert gate_exit_code_scheme(False) == LEGACY_SCHEME == "legacy"

    @given(active=st.booleans())
    def test_total_and_deterministic(self, active: bool) -> None:
        assert gate_exit_code_scheme(active) == gate_exit_code_scheme(active)
        assert gate_exit_code_scheme(active) in {SEVERITY_SCHEME, LEGACY_SCHEME}


class TestExitCodeSchemeIsDerivedNotSettable:
    """T6: the *model* may no longer express a scheme/severity disagreement."""

    def test_gate_options_has_no_exit_code_scheme_field(self) -> None:
        assert "exit_code_scheme" not in {
            f.name for f in dataclasses.fields(GateOptions)
        }
        with pytest.raises(TypeError):
            GateOptions(  # type: ignore[call-arg]
                exit_code_scheme="legacy", severity_preset=None, severity=None
            )

    def test_resolved_compare_config_has_no_exit_code_scheme_field(self) -> None:
        assert "exit_code_scheme" not in {
            f.name for f in dataclasses.fields(ResolvedCompareConfig)
        }

    @given(preset=st.sampled_from([None, "default", "strict", "info-only"]))
    @settings(deadline=None)
    def test_both_objects_agree_with_the_shared_rule(self, preset: str | None) -> None:
        """Whatever a real resolution produces, each object's published
        scheme is exactly the shared rule applied to that object's own
        "severity in effect" predicate -- so neither can drift from the
        other, or from `compatibility_evaluation_frontend`'s receipt."""
        gate = GateOptions(
            severity_preset=preset,
            severity=None if preset is None else SeverityConfig(),
        )
        assert gate.exit_code_scheme == gate_exit_code_scheme(gate.severity is not None)

        cfg = resolve_compare_config(
            None, cli_severity_preset=preset, cli_scope_public=None
        )
        assert cfg.exit_code_scheme == gate_exit_code_scheme(cfg.severity_active)
        assert cfg.exit_code_scheme == gate.exit_code_scheme
