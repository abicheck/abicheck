"""Properties of abiforge's edit operators, checked without a compiler (plan §6.1).

Fast and stdlib-only, so this module is in the default unit lane. The
compile-based properties (both sides build for each standard) live in
``test_abiforge_compile_properties.py`` (``slow``).

The oracle for every property is the operator's own declared meaning (its
roles, its ``touches`` and ``new_evidence`` fields, the ABI verdict it states),
never ``abicheck``'s answer (plan §1.2 principle 5). Applicability in
particular is checked against a re-statement of the ``EditOp`` docstring, not
against ``applies_to`` itself.

Minimality is checked on rendered text, not on a parsed AST: abiforge carries
no C/C++ parser, and adding one for this check is not justified. The text
check is weaker (it sees lines, not tokens) and says so in its name.
"""

from __future__ import annotations

import difflib
from collections import Counter

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abiforge.model.config import Flags
from abiforge.model.expect import VERDICT_RANK
from abiforge.model.opkit import EditOp
from abiforge.model.seeds import SEEDS, RoleText, Seed
from abiforge.operators import OP_BY_NAME, OPERATORS, applicable

# ---------------------------------------------------------------------------
# Oracles stated from the operator docstring (abiforge/operators/__init__.py)
# ---------------------------------------------------------------------------


def _oracle_applies(op: EditOp, seed: Seed) -> bool:
    """``applies_to`` as the docstring words it: every role present, gate respected."""
    if not all(role in seed.roles for role in op.roles):
        return False
    return seed.gate_role is None or seed.gate_role in op.roles


def _applicable_pairs() -> list[tuple[EditOp, Seed]]:
    """Every (operator, seed) pair the oracle says applies, in table order."""
    return [
        (op, seed) for op in OPERATORS for seed in SEEDS if _oracle_applies(op, seed)
    ]


PAIRS = _applicable_pairs()
TEXT_PAIRS = [
    (op, seed) for op, seed in PAIRS if op.rebuild is None and op.new_evidence is None
]


def _pair_id(pair: object) -> str:
    if isinstance(pair, EditOp | Seed):
        return pair.name
    return str(pair)


def _changed_roles(op_out: dict[str, RoleText], seed: Seed) -> set[str]:
    return {role for role, text in op_out.items() if text != seed.roles[role]}


# ---------------------------------------------------------------------------
# (a) an edit applies to every compatible seed
# ---------------------------------------------------------------------------


def test_applicability_agrees_with_docstring_oracle() -> None:
    mismatches = [
        (op.name, seed.name)
        for op in OPERATORS
        for seed in SEEDS
        if op.applies_to(seed) != _oracle_applies(op, seed)
    ]
    assert mismatches == [], (
        f"applies_to disagrees with the docstring rule: {mismatches}"
    )


def test_applicable_helper_is_the_filter_of_applies_to() -> None:
    for seed in SEEDS:
        expected = tuple(op for op in OPERATORS if _oracle_applies(op, seed))
        assert applicable(seed) == expected, seed.name


def test_every_operator_applies_to_some_seed() -> None:
    # An operator no seed provides is dead: its ground truth is never checked.
    dead = [
        op.name for op in OPERATORS if not any(_oracle_applies(op, s) for s in SEEDS)
    ]
    assert dead == []


def test_every_seed_has_operators() -> None:
    empty = [seed.name for seed in SEEDS if not applicable(seed)]
    assert empty == []


@settings(
    derandomize=True,
    max_examples=400,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(data=st.data())
def test_applicability_property_over_random_pairs(data: st.DataObject) -> None:
    op = data.draw(st.sampled_from(OPERATORS), label="op")
    seed = data.draw(st.sampled_from(SEEDS), label="seed")
    assert op.applies_to(seed) == _oracle_applies(op, seed), (op.name, seed.name)
    if op.applies_to(seed):
        # Applicable means the edit runs on this seed's roles.
        out = op.edit(dict(seed.roles))
        assert set(out) <= set(op.roles), (
            op.name,
            seed.name,
            sorted(set(out) - set(op.roles)),
        )


# ---------------------------------------------------------------------------
# An edit changes exactly the construct it declares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("op", "seed"), TEXT_PAIRS, ids=_pair_id)
def test_text_edit_changes_a_declared_role(op: EditOp, seed: Seed) -> None:
    """A text edit that changes nothing is a generator defect (plan §6.4)."""
    out = op.edit(dict(seed.roles))
    changed = _changed_roles(out, seed)
    assert changed, f"{op.name} on {seed.name} re-spells no role: a no-op edit"
    if op.touches:
        assert changed <= set(op.touches), (
            op.name,
            seed.name,
            sorted(changed),
            op.touches,
        )


@pytest.mark.parametrize(
    "op", [op for op in OPERATORS if op.rebuild is not None], ids=lambda o: o.name
)
def test_rebuild_operator_changes_the_build_flags(op: EditOp) -> None:
    before = Flags()
    after = op.new_flags(before)
    assert after != before, f"{op.name} rebuilds with the same flags: a no-op rebuild"


# ---------------------------------------------------------------------------
# (c) minimality: the edit touches only the declared construct
# ---------------------------------------------------------------------------


def _line_diff(before: str, after: str) -> tuple[Counter[str], Counter[str]]:
    """(removed, added) lines of a textual diff of two renders."""
    a, b = before.splitlines(), after.splitlines()
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.update(a[i1:i2])
        if tag in ("replace", "insert"):
            added.update(b[j1:j2])
    return removed, added


def _render_header(seed: Seed, overrides: dict[str, RoleText] | None = None) -> str:
    return seed.render(overrides)[0]


SINGLE_ROLE_TEXT_PAIRS = [(op, seed) for op, seed in TEXT_PAIRS if len(op.roles) == 1]


@pytest.mark.parametrize(("op", "seed"), SINGLE_ROLE_TEXT_PAIRS, ids=_pair_id)
def test_edit_diff_is_the_sum_of_its_role_diffs(op: EditOp, seed: Seed) -> None:
    """Rendering the full edit changes exactly the lines each changed role changes alone.

    If one role's edit altered a line that belongs to another role, the full
    render's diff would carry a line no single-role diff accounts for, so the
    multiset sums would disagree. Only single-role edits are decomposed: a
    joint edit (a reordering of two declarations) changes no line on its own,
    so its per-role diffs are not its effect. ``touches`` bounds those.
    """
    out = op.edit(dict(seed.roles))
    changed = sorted(_changed_roles(out, seed))
    assert changed, f"{op.name} on {seed.name} re-spells no role"
    base = _render_header(seed)
    full_removed, full_added = _line_diff(
        base, _render_header(seed, {r: out[r] for r in changed})
    )
    sum_removed: Counter[str] = Counter()
    sum_added: Counter[str] = Counter()
    for role in changed:
        removed, added = _line_diff(base, _render_header(seed, {role: out[role]}))
        sum_removed.update(removed)
        sum_added.update(added)
    assert full_removed == sum_removed, (op.name, seed.name, changed)
    assert full_added == sum_added, (op.name, seed.name, changed)


# ---------------------------------------------------------------------------
# (d) reversibility: counterpart verdicts mirror
# ---------------------------------------------------------------------------
#
# plan §6.1(d) asks for ``inverse(op)`` with ``apply(inv, apply(op, s)) ~ s``.
# abiforge has no inverse yet, and the add/remove operators here are not
# inverses: each writes absolute text from the base seed, so a removal after
# an addition does not restore the base (``struct_remove_trailing_field``
# drops ``char tag[8]`` as well). The round trip is therefore not asserted
# here. The pairs below are counterparts with the same role and one mirrored
# ABI meaning, and their verdicts must mirror.

# (addition, removal) counterpart pairs whose addition is benign by ABI meaning,
# so its removal must be breaking. A struct or union addition is breaking in
# both directions and does not mirror.
MIRRORED_PAIRS: tuple[tuple[str, str], ...] = (
    ("enum_append_member", "enum_remove_member"),
    ("enum_class_append", "enum_class_remove"),
    ("hidden_friend_added", "hidden_friend_removed"),
)


@pytest.mark.parametrize(("add_name", "rem_name"), MIRRORED_PAIRS, ids=lambda p: p)
def test_counterpart_verdicts_mirror_addition_and_removal(
    add_name: str, rem_name: str
) -> None:
    add, rem = OP_BY_NAME[add_name], OP_BY_NAME[rem_name]
    assert add.roles == rem.roles, "counterparts edit the same role"
    assert add.verdict_max is not None, (
        f"{add_name} is declared benign, so it needs a ceiling"
    )
    assert VERDICT_RANK[add.verdict_max] <= VERDICT_RANK["COMPATIBLE_WITH_RISK"], (
        add_name
    )
    assert VERDICT_RANK[rem.verdict_min] >= VERDICT_RANK["API_BREAK"], rem_name
