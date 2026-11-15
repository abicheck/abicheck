"""Metamorphic relations between abiforge's edits and their seeds (plan §6.2).

Each relation relates two edits or two seeds whose expected result is known
from the edit's meaning, so the oracle is the relation, never abicheck. The
relations are checked on rendered text and on role mappings, with no
compiler; the compile-based relations live in ``test_abiforge_compile_properties.py``.

Not covered (plan §6.2 names them, and abiforge has no operator for them):
renaming unrelated entities (no rename operator on rendered text) and adding an
unrelated header (the expectation is a function of the configuration, which
the seed does not reach, so the relation would be tautological).
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from abiforge.model.seeds import SEEDS, Seed
from abiforge.operators import OP_BY_NAME, applicable
from tests.test_abiforge_properties import PAIRS

# ---------------------------------------------------------------------------
# Metamorphic: an edit does not depend on roles it does not declare
# ---------------------------------------------------------------------------


@settings(
    derandomize=True,
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(data=st.data())
def test_edit_ignores_roles_it_does_not_declare(data: st.DataObject) -> None:
    op, seed = data.draw(st.sampled_from(PAIRS), label="pair")
    unrelated = sorted(set(seed.roles) - set(op.roles))
    assume(unrelated)
    role = data.draw(st.sampled_from(unrelated), label="unrelated role")
    noise = data.draw(
        st.text(alphabet="abcxyz /*-_", min_size=1, max_size=12), label="noise"
    )
    original = dict(seed.roles)
    perturbed = dict(original)
    perturbed[role] = (original[role][0] + noise, original[role][1] + noise)
    assert op.edit(perturbed) == op.edit(original), (op.name, seed.name, role)


@settings(
    derandomize=True,
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(data=st.data())
def test_independent_edits_commute(data: st.DataObject) -> None:
    """Two edits with disjoint roles give the same seed in either order."""
    seed = data.draw(st.sampled_from(SEEDS), label="seed")
    candidates = [
        op for op in applicable(seed) if op.rebuild is None and op.new_evidence is None
    ]
    first = data.draw(st.sampled_from(candidates), label="first")
    others = [op for op in candidates if not set(op.roles) & set(first.roles)]
    assume(others)
    second = data.draw(st.sampled_from(others), label="second")
    roles = dict(seed.roles)
    after_first = {**roles, **first.edit(roles)}
    after_second = {**roles, **second.edit(roles)}
    ab = {**after_first, **second.edit(after_first)}
    ba = {**after_second, **first.edit(after_second)}
    assert seed.render(ab) == seed.render(ba), (first.name, second.name, seed.name)
    # Each edit's output is unchanged by the other edit's roles (they are disjoint).
    assert second.edit(after_first) == second.edit(roles), (first.name, second.name)
    assert first.edit(after_second) == first.edit(roles), (first.name, second.name)


REORDER = OP_BY_NAME["reorder_declarations"]
REORDER_SEEDS = [seed for seed in SEEDS if REORDER.applies_to(seed)]


@pytest.mark.parametrize("seed", REORDER_SEEDS, ids=lambda s: s.name)
def test_reorder_declarations_is_an_involution(seed: Seed) -> None:
    """Swapping two declarations twice restores the seed: the one self-inverse edit."""
    roles = dict(seed.roles)
    once = {**roles, **REORDER.edit(roles)}
    twice = {**once, **REORDER.edit(once)}
    assert seed.render(twice) == seed.render(roles), seed.name
    assert seed.render(once) != seed.render(roles), "the swap must change the header"


def test_reorder_declarations_applies_to_some_seed() -> None:
    assert REORDER_SEEDS, "the involution is vacuous without a seed"
