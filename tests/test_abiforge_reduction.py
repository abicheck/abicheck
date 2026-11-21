"""The bit-field reduction certificate (``abiforge/model/reduction``).

The certificate is exhaustive arithmetic over the layout model, so these
tests pin its claims and check that the table of selected edits is the
generator's, not a copy that can drift from it.
"""

from __future__ import annotations

import pytest

from abiforge.model.fragments import bitfield_record, role_text
from abiforge.model.reduction import (
    BARRIERS,
    CONFIGS,
    MUTANTS,
    SELECTED,
    State,
    Transition,
    certify,
    dense_domain,
    retention,
    rows,
    signature,
)
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators.fragments import FRAGMENT_OPERATORS

_SEED = SEED_BY_NAME["c_layout"]
_OPS = {op.name: op for op in FRAGMENT_OPERATORS}


def _bitfield_text(fid: str, state: State) -> str:
    return role_text(fid, (bitfield_record(fid, state.width, state.barrier),))[0]


@pytest.mark.parametrize("name", sorted(SELECTED))
def test_selected_transition_is_the_operators_edit(name: str) -> None:
    """Each table row's old and new states are the operator's fixture and edit."""
    op = _OPS[name]
    changed = op.edit(dict(_SEED.roles))
    (role,) = changed
    fid = role.removeprefix("lf.")
    t = SELECTED[name]
    assert _SEED.roles[role][0] == _bitfield_text(fid, t.old)
    assert changed[role][0] == _bitfield_text(fid, t.new)


def test_every_bitfield_shape_edit_is_in_the_table() -> None:
    """A new width or barrier operator must enter the certificate."""
    shaped = {n for n in _OPS if n.startswith(("bitfield_width_", "bitfield_barrier_"))}
    assert shaped == set(SELECTED)


def test_dense_domain_is_every_width_and_barrier_edit() -> None:
    dense = list(dense_domain())
    assert len(dense) == len(set(dense))
    widths = len(BARRIERS) * 32 * 31  # ordered width pairs per barrier
    barriers = 32 * 2 * 2  # insert/remove x before/after, per width
    assert len(dense) == widths + barriers
    assert certify()["dense_transitions"] == len(dense) * len(CONFIGS)


def test_selected_edits_cover_every_row_shape_class() -> None:
    per = certify()["configs"]
    assert {name: v["uncovered"] for name, v in per.items()} == dict.fromkeys(per, {})
    # Classes differ by packing: the byte-rounded gcc barrier absorbs edits
    # the unit-rounded one moves. Pinned so a model change is noticed.
    assert {name: v["classes"] for name, v in per.items()} == {
        "gcc/pack=default": 10,
        "clang/pack=default": 10,
        "gcc/pack=1": 9,
        "clang/pack=1": 9,
    }


@pytest.mark.parametrize(
    "name",
    [
        "bitfield_width_1_to_7",
        "bitfield_barrier_removed_after_6",
        "bitfield_barrier_removed_before_1",
        "bitfield_barrier_removed_after_14",
        "bitfield_barrier_removed_after_16",
        "bitfield_barrier_removed_before_30",
        "bitfield_width_1_to_2_after_barrier",
        "bitfield_width_1_to_2_before_barrier",
    ],
)
def test_each_class_closing_edit_is_needed(name: str) -> None:
    """Dropping any of these reopens a class: the certificate notices."""
    rest = {k: v for k, v in SELECTED.items() if k != name}
    assert any(v["uncovered"] for v in certify(rest)["configs"].values())


def test_an_empty_selection_covers_nothing() -> None:
    per = certify({})["configs"]
    assert all(v["covered"] == 0 for v in per.values())


def test_every_mutant_is_detected_densely_and_by_the_selection() -> None:
    got = retention()
    assert set(got) == set(MUTANTS)
    for name, v in got.items():
        assert v["dense_detecting_cells"] > 0, name
        assert v["selected_detecting_cells"] > 0, name


def test_a_mutant_the_selection_misses_is_reported_as_missed() -> None:
    """Retention is measured, not assumed: a selection of only unpacked width
    edits without barriers cannot see a barrier rule error."""
    no_barrier = {
        k: v
        for k, v in SELECTED.items()
        if v.kind == "width" and v.old.barrier == "none"
    }
    got = retention(no_barrier)
    assert got["barrier_ignored"]["selected_detecting_cells"] == 0
    assert got["barrier_rule_swapped"]["selected_detecting_cells"] == 0
    assert got["barrier_ignored"]["dense_detecting_cells"] > 0


def test_signature_excludes_values_but_rows_keep_them() -> None:
    """Two width edits with the same moved members are one class even when
    the offsets differ; their exact rows still differ."""
    cfg = CONFIGS[0]
    a = Transition(State(1, "after"), State(2, "after"))
    b = Transition(State(1, "after"), State(3, "after"))
    assert signature(a, cfg) == signature(b, cfg)
    assert rows(a, cfg) != rows(b, cfg)
