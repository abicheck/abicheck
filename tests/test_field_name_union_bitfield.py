"""``field_name`` on union-member and bit-field findings (report schema 7.7).

Bug class: a field-level finding whose member was readable only from its
``description`` prose. ``UNION_FIELD_ADDED``, ``UNION_FIELD_REMOVED`` and
``FIELD_BITFIELD_CHANGED`` left ``field_name`` unset, so an exact judge
(abiforge's ``model/exact``) or any report consumer could not tell which
member moved without parsing text. The oracle is the member each edit
declares; each kind is checked over several unions/records with several
members changing at once, so a positional or first-member answer fails.
"""

from __future__ import annotations

from collections import Counter

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.report.change_json import change_to_dict


def _snap(types):
    return AbiSnapshot(
        library="libt.so.1", version="1", functions=[], variables=[], types=types
    )


def _union(name, members):
    return RecordType(
        name=name,
        kind="union",
        size_bits=64,
        is_union=True,
        fields=[TypeField(n, t, 0) for n, t in members],
    )


def _bits(name, widths):
    fields, off = [], 0
    for n, w in widths:
        fields.append(
            TypeField(n, "unsigned int", off, is_bitfield=True, bitfield_bits=w)
        )
        off += w
    return RecordType(name=name, kind="struct", size_bits=64, fields=fields)


CASES = [
    # (old types, new types, kind, the members the edit declares)
    (
        [_union("U", [("a", "int"), ("b", "float")])],
        [_union("U", [("a", "int"), ("b", "float"), ("c", "double"), ("d", "long")])],
        ChangeKind.UNION_FIELD_ADDED,
        {("U", "c"), ("U", "d")},
    ),
    (
        [_union("U", [("a", "int"), ("b", "float"), ("c", "double")])],
        [_union("U", [("b", "float")])],
        ChangeKind.UNION_FIELD_REMOVED,
        {("U", "a"), ("U", "c")},
    ),
    (
        [_union("U", [("a", "int")]), _union("V", [("x", "int"), ("y", "int")])],
        [
            _union("U", [("a", "int"), ("z", "int")]),
            _union("V", [("x", "int"), ("y", "int"), ("w", "int")]),
        ],
        ChangeKind.UNION_FIELD_ADDED,
        {("U", "z"), ("V", "w")},
    ),
    (
        [_bits("B", [("p", 1), ("q", 7), ("r", 8)])],
        [_bits("B", [("p", 1), ("q", 8), ("r", 9)])],
        ChangeKind.FIELD_BITFIELD_CHANGED,
        {("B", "q"), ("B", "r")},
    ),
    (
        [_bits("B", [("p", 16), ("q", 16)]), _bits("C", [("s", 3)])],
        [_bits("B", [("p", 17), ("q", 16)]), _bits("C", [("s", 4)])],
        ChangeKind.FIELD_BITFIELD_CHANGED,
        {("B", "p"), ("C", "s")},
    ),
]


@pytest.mark.parametrize("old,new,kind,members", CASES)
def test_each_finding_names_its_own_member(old, new, kind, members) -> None:
    got = [c for c in compare(_snap(old), _snap(new)).changes if c.kind == kind]
    named = Counter((c.symbol, c.field_name) for c in got)
    assert named == Counter(members)  # each member exactly once, none unnamed
    for c in got:
        assert change_to_dict(c)["field_name"] == c.field_name
