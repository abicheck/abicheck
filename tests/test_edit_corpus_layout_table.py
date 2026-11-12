"""The padding-absorbed record-layout operators agree with the Lean layout table.

``formal/lean/layout_table.json`` is exported from the Lean model
(``AbiLean.Layout``): for a base record of scalar fields, an insertion at
position ``insert_at`` of one field of type ``inserted``, the field offsets
before and after, and sizeof before and after (SysV x86-64). The same row
also answers a removal, read backwards: the layout without the member is the
base and the layout with it is the insertion.

Each operator's before and after layout is read from the operator's own seed
text, the row for that pair is looked up, and the operator's expectation must
say the same thing about offsets and sizeof. Only the default (unpacked)
configuration is modelled by the table; packed cells have their own rules.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._edit_corpus_config import Toolchain, default_config
from tests._edit_corpus_coverage import expectation_kinds
from tests._edit_corpus_operators import OP_BY_NAME
from tests._edit_corpus_opkit import _OFFSET, _SIZE
from tests._edit_corpus_seeds import XCXX_SEED

TABLE_PATH = (
    Path(__file__).resolve().parents[1] / "formal" / "lean" / "layout_table.json"
)
GCC = Toolchain("gcc", 13, "gcc", "g++")
_STRUCT = re.compile(r"struct\s+\w+\s*\{([^}]*)\}")


@dataclass(frozen=True)
class Member:
    type: str
    name: str


def _members(header: str) -> list[Member]:
    """The members of the first struct in *header*, in declaration order."""
    match = _STRUCT.search(header)
    assert match is not None, header
    out = []
    for decl in match.group(1).split(";"):
        tokens = decl.split()
        if tokens:
            *ty, name = tokens
            out.append(Member(" ".join(ty), name))
    return out


def _table_rows() -> list[dict]:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))["rows"]


def _row_for(before: list[Member], after: list[Member]) -> tuple[dict, int]:
    """The table row for the one-member change between *before* and *after*.

    Returns the row and the index of the member the change adds or removes.
    The base is the smaller layout, as the table is keyed by it.
    """
    if len(after) == len(before) + 1:
        base, with_member = before, after
    elif len(before) == len(after) + 1:
        base, with_member = after, before
    else:
        raise AssertionError(f"not a one-member change: {before} -> {after}")
    names = [m.name for m in base]
    (k,) = [i for i, m in enumerate(with_member) if m.name not in names]
    assert [m.name for m in with_member[:k] + with_member[k + 1 :]] == names
    rows = [
        r
        for r in _table_rows()
        if r["base"] == [m.type for m in base]
        and r["insert_at"] == k
        and r["inserted"] == with_member[k].type
    ]
    assert len(rows) == 1, (base, k, with_member[k].type, len(rows))
    return rows[0], k


def _offsets_moved(row: dict, k: int) -> bool:
    """Does any member other than the inserted one change offset?"""
    new = row["new_offsets"][:k] + row["new_offsets"][k + 1 :]
    return new != row["old_offsets"]


def _mentions(exp, group: frozenset[str]) -> bool:
    return bool(expectation_kinds(exp) & group)


# --- the table's composition ---------------------------------------------------
#
# The axes, written here from ``AbiLean/LayoutExport.lean``'s definitions
# (``types``, ``bases``, ``List.range (b.length + 1)``), not read back from the
# table or from any enumerator: four scalar types, bases of one to three
# fields, every insertion position 0..len(base), every inserted type.
TABLE_TYPES: tuple[str, ...] = ("char", "short", "int", "double")
TABLE_BASE_LENGTHS: tuple[int, ...] = (1, 2, 3)
# SysV x86-64 size (= alignment) of each scalar, for the independent oracle.
SCALAR_SIZE: dict[str, int] = {"char": 1, "short": 2, "int": 4, "double": 8}


def _row_key(r: dict) -> tuple[tuple[str, ...], int, str]:
    return (tuple(r["base"]), r["insert_at"], r["inserted"])


def _expected_keys() -> list[tuple[tuple[str, ...], int, str]]:
    keys = []
    for n in TABLE_BASE_LENGTHS:
        for base in _cartesian(TABLE_TYPES, n):
            for k in range(n + 1):
                for t in TABLE_TYPES:
                    keys.append((base, k, t))
    return keys


def _cartesian(values: tuple[str, ...], n: int) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = [()]
    for _ in range(n):
        out = [prefix + (v,) for prefix in out for v in values]
    return out


def _layout(fields: list[str]) -> tuple[list[int], int]:
    """Offsets and sizeof of a record of scalar *fields* (natural alignment)."""
    off, offsets, align = 0, [], 1
    for f in fields:
        a = SCALAR_SIZE[f]
        off = (off + a - 1) // a * a
        offsets.append(off)
        off += a
        align = max(align, a)
    return offsets, (off + align - 1) // align * align


def _composition_problems(rows: list[dict]) -> list[str]:
    keys = [_row_key(r) for r in rows]
    want = _expected_keys()
    problems = [f"repeated key {k}" for k, n in Counter(keys).items() if n > 1]
    problems += [f"missing key {k}" for k in sorted(set(want) - set(keys))]
    problems += [f"unexpected key {k}" for k in sorted(set(keys) - set(want))]
    return problems


def test_the_table_is_exactly_the_cartesian_product_of_its_axes() -> None:
    rows = _table_rows()
    assert _composition_problems(rows) == []
    # The product itself has no repeats (it is the oracle; pin its size).
    want = _expected_keys()
    assert (
        len(set(want))
        == len(want)
        == sum(4**n * (n + 1) * 4 for n in TABLE_BASE_LENGTHS)
    )


def test_a_duplicated_neighbour_row_is_caught() -> None:
    """Mutation check: replacing one row with a copy of its neighbour keeps the
    row count and must still fail the composition check, twice over (a
    repeated key and a missing one)."""
    rows = _table_rows()
    for i in (0, len(rows) // 2, len(rows) - 1):
        j = i - 1 if i else 1
        mutated = [*rows[:i], rows[j], *rows[i + 1 :]]
        assert len(mutated) == len(rows)
        problems = _composition_problems(mutated)
        assert f"repeated key {_row_key(rows[j])}" in problems
        assert f"missing key {_row_key(rows[i])}" in problems


def test_every_row_matches_an_independent_layout_oracle() -> None:
    for r in _table_rows():
        base = list(r["base"])
        new = base[: r["insert_at"]] + [r["inserted"]] + base[r["insert_at"] :]
        assert _layout(base) == (r["old_offsets"], r["old_size"]), r
        assert _layout(new) == (r["new_offsets"], r["new_size"]), r


def test_helper_reads_a_moving_insertion_as_moving() -> None:
    # Control for the helper: an int inserted at the front of {char a; int b}
    # pushes a to 4 and b from 4 to 8, so the reader must report a move. A
    # helper that always answered "not moved" would pass the padding cases.
    before = [Member("char", "a"), Member("int", "b")]
    after = [Member("int", "x"), Member("char", "a"), Member("int", "b")]
    row, k = _row_for(before, after)
    assert (k, row["old_offsets"], row["new_offsets"]) == (0, [0, 4], [0, 4, 8])
    assert _offsets_moved(row, k) is True
    assert (row["old_size"], row["new_size"]) == (8, 12)


@pytest.mark.parametrize(
    ("op_name", "role", "table_moves", "table_resizes"),
    [
        # {char a; int b} -> insert char between them: b stays at 4, sizeof 8.
        ("field_inserted_in_padding", "x.inspad", False, False),
        # {char a; char x; int b} -> remove x: the same row read backwards.
        ("field_removed_without_shrink", "x.rmpad", False, False),
        # Control, already landed: {int a; char b} + char in tail padding.
        ("field_added_in_tail_padding", "x.tailpad", False, False),
    ],
)
def test_expectation_matches_the_layout_table(
    op_name: str, role: str, table_moves: bool, table_resizes: bool
) -> None:
    op = OP_BY_NAME[op_name]
    before_text = XCXX_SEED.roles[role][0]
    after_text = op.edit(XCXX_SEED.roles)[role][0]
    row, k = _row_for(_members(before_text), _members(after_text))

    # The table itself says what this layout pair does.
    assert _offsets_moved(row, k) is table_moves, row
    assert (row["old_size"] != row["new_size"]) is table_resizes, row

    # The operator's expectation says the same, in its own kinds.
    exp = op.expect(default_config("c++", GCC))
    assert _mentions(exp, _OFFSET) is table_moves, (
        op_name,
        sorted(expectation_kinds(exp)),
    )
    assert _mentions(exp, _SIZE) is table_resizes, (
        op_name,
        sorted(expectation_kinds(exp)),
    )


def test_the_removal_row_is_the_insertion_row() -> None:
    # The two padding operators share one table row: the removal's base is the
    # layout the insertion starts from. Pins the reading used above.
    ins_row, ins_k = _row_for(
        _members(XCXX_SEED.roles["x.inspad"][0]),
        _members(
            OP_BY_NAME["field_inserted_in_padding"].edit(XCXX_SEED.roles)["x.inspad"][0]
        ),
    )
    rm_row, rm_k = _row_for(
        _members(
            OP_BY_NAME["field_removed_without_shrink"].edit(XCXX_SEED.roles)["x.rmpad"][
                0
            ]
        ),
        _members(XCXX_SEED.roles["x.rmpad"][0]),
    )
    assert ins_row == rm_row and ins_k == rm_k == 1
