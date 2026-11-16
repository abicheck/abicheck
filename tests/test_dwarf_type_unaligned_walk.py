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


def test_scalar_alignment_prefers_the_declared_alignment_then_the_size() -> None:
    declared = Die("DW_TAG_base_type", 600)
    declared.attributes["DW_AT_alignment"] = SimpleNamespace(value=16)
    assert adv._scalar_leaf_align(declared) == 16
    bad = Die("DW_TAG_base_type", 601, byte_size=8)
    bad.attributes["DW_AT_alignment"] = SimpleNamespace(value="not-a-number")
    assert adv._scalar_leaf_align(bad) == 8  # falls back to the byte size
    unsized = Die("DW_TAG_base_type", 602)
    assert adv._scalar_leaf_align(unsized) == 1


def _refers_to(type_die):
    """A variable-like DIE whose DW_AT_type reaches *type_die*."""
    return Die("DW_TAG_variable", 9_999, target=type_die)


def test_a_pointer_member_is_a_leaf_not_followed() -> None:
    # A by-value struct whose only member is a pointer: the pointer is the leaf,
    # its referent is never entered, so a pointee's layout cannot matter.
    target = Die("DW_TAG_structure_type", 700, members=[member(3, _int(701))])
    ptr = Die("DW_TAG_pointer_type", 702, byte_size=8, target=target)
    outer = Die("DW_TAG_structure_type", 703, members=[member(0, ptr)])
    assert adv._aggregate_has_unaligned_member(_refers_to(outer), None) is False


def test_an_aggregate_with_an_unaligned_member_is_reported() -> None:
    inner = Die("DW_TAG_structure_type", 710, members=[member(1, _int(711))])
    outer = Die("DW_TAG_structure_type", 712, members=[member(0, inner)])
    assert adv._aggregate_has_unaligned_member(_refers_to(outer), None) is True


def test_unresolvable_and_non_aggregate_returns_are_not_unaligned() -> None:
    unresolved = Die("DW_TAG_typedef", 720)  # no target: resolves to None
    assert adv._aggregate_has_unaligned_member(unresolved, None) is False
    assert adv._aggregate_has_unaligned_member(_int(721), None) is False


def test_bit_fields_and_unlocatable_members_are_skipped() -> None:
    bitfield = member(1, _int(730))
    bitfield.attributes["DW_AT_bit_size"] = SimpleNamespace(value=3)
    untyped = Die("DW_TAG_member", 731)  # resolves to no type
    outer = Die("DW_TAG_structure_type", 732, members=[bitfield, untyped])
    assert adv._type_unaligned_at(outer, None, 0, None) is False


def test_an_array_member_shares_its_elements_alignment() -> None:
    element = _int(740)
    array = Die("DW_TAG_array_type", 741, target=element)
    assert adv._type_unaligned_at(array, None, 0, None) is False
    assert adv._type_unaligned_at(array, None, 2, None) is True
    headless = Die("DW_TAG_array_type", 742)  # element type unresolved
    assert adv._type_unaligned_at(headless, None, 2, None) is False


def test_a_member_location_defaults_to_zero_when_absent() -> None:
    plain = Die("DW_TAG_member", 750)
    assert adv._decode_member_location(plain) == 0


def test_the_qualifier_cache_is_consulted_and_filled() -> None:
    leaf = _int(760)
    typedef = Die("DW_TAG_typedef", 761, target=leaf)
    cache = SimpleNamespace(unwrap={})
    assert adv._unwrap_qualifiers(typedef, None, cache=cache) is leaf
    assert cache.unwrap[761] is leaf
    cache.unwrap[761] = "cached"  # a later call returns the cached answer
    assert adv._unwrap_qualifiers(typedef, None, cache=cache) == "cached"
