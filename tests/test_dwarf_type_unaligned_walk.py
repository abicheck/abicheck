"""``_type_unaligned_at`` terminates on cyclic and deeply nested DIE graphs.

Oracles are the layout arithmetic written out in each test, not the walker's own
helpers. DIEs are fakes: ``_resolve_type_die`` is patched to follow ``target``.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import abicheck.extract.dwarf_type_alignment as adv


class Die:
    def __init__(self, tag, offset, *, byte_size=None, members=(), target=None):
        self.tag = tag
        self.offset = offset
        self.attributes = {}
        if byte_size is not None:
            self.attributes["DW_AT_byte_size"] = SimpleNamespace(value=byte_size)
        self._members = list(members)
        self.target = target

    def iter_children(self):
        return iter(self._members)


def member(offset, type_die):
    m = Die("DW_TAG_member", -1, target=type_die)
    m.attributes["DW_AT_data_member_location"] = SimpleNamespace(value=offset)
    return m


@pytest.fixture(autouse=True)
def _follow_targets(monkeypatch):
    monkeypatch.setattr(
        adv, "_resolve_type_die", lambda die, cu: getattr(die, "target", None)
    )


def _int(offset):
    return Die("DW_TAG_base_type", offset, byte_size=4)


def _char(offset):
    return Die("DW_TAG_base_type", offset, byte_size=1)


def test_self_referential_struct_through_a_pointer_terminates() -> None:
    # struct N { N *next; char c; }  -- the pointer is a leaf; no infinite walk.
    node = Die("DW_TAG_structure_type", 100)
    ptr = Die("DW_TAG_pointer_type", 101, byte_size=8, target=node)
    node._members = [member(0, ptr), member(8, _char(102))]
    # Base 0: pointer aligned, char at 8 aligned for a 1-byte leaf -> nothing misaligned.
    assert adv._type_unaligned_at(node, None, 0, None) is False
    # Base 2: the pointer lands at 2, not 8-aligned -> misaligned.
    assert adv._type_unaligned_at(node, None, 2, None) is True


def test_a_by_value_cycle_terminates_with_the_same_answer() -> None:
    # struct A { B b; char x; }  struct B { A a; int y; }  -- a cycle in the DIE
    # graph (as declaration/definition references can produce). A real layout
    # cannot contain it by value, so the oracle is only the leaves reachable.
    a = Die("DW_TAG_structure_type", 200)
    b = Die("DW_TAG_structure_type", 300)
    a._members = [member(0, b), member(8, _char(201))]
    b._members = [member(0, a), member(4, _int(301))]
    # Base 0: int at 4 is aligned; char at 8 is aligned -> no misaligned leaf.
    assert adv._type_unaligned_at(a, None, 0, None) is False
    # Base 2: int lands at 2+4=6, not 4-aligned -> misaligned, found before the loop.
    assert adv._type_unaligned_at(a, None, 2, None) is True


def test_a_typedef_chain_is_followed_to_its_leaf() -> None:
    leaf = _int(400)
    chain = leaf
    for i in range(50):  # typedef chain: each DIE targets the previous one
        chain = Die("DW_TAG_typedef", 401 + i, target=chain)
    # Base 0 is aligned for an int leaf; base 1 is not.
    assert adv._type_unaligned_at(chain, None, 0, None) is False
    assert adv._type_unaligned_at(chain, None, 1, None) is True


@pytest.mark.parametrize("start", [0, 1, 2, 3])
def test_two_thousand_levels_of_nesting_terminate_with_the_arithmetic_answer(
    start: int,
) -> None:
    # struct L_k { char pad; L_{k-1} inner; } at offset 1: each level pushes the
    # next one 1 byte deeper. The innermost int therefore sits at start + 2000.
    depth = 2000
    assert depth > sys.getrecursionlimit()  # would overflow a recursive walk
    cur = _int(9000)
    for k in range(depth):
        cur = Die("DW_TAG_structure_type", 10_000 + k, members=[member(1, cur)])
    expected = (start + depth) % 4 != 0
    assert adv._type_unaligned_at(cur, None, start, None) is expected


def test_a_typedef_cycle_with_no_leaf_terminates_as_no_misalignment() -> None:
    # typedef A -> typedef B -> typedef A: no non-qualifier is reachable.
    a = Die("DW_TAG_typedef", 500)
    b = Die("DW_TAG_typedef", 501, target=a)
    a.target = b
    assert adv._type_unaligned_at(a, None, 0, None) is False
    assert adv._type_unaligned_at(a, None, 1, None) is False
