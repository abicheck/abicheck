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


class _Bounded:
    """Resolver that fails fast if the walk keeps going: an unbounded walk would hang."""

    def __init__(self, limit: int = 500) -> None:
        self.calls = 0
        self.limit = limit

    def __call__(self, die, cu):
        self.calls += 1
        if self.calls > self.limit:
            raise AssertionError("type walk did not terminate")
        return getattr(die, "target", None)


def test_a_nonzero_offset_self_member_terminates_without_a_leaf(monkeypatch) -> None:
    # struct A { int a @0; A self @4; } -- malformed (a type cannot hold itself by
    # value), but the walk must still end. Every revisit lands at a new base, so
    # only the ancestor chain stops it.
    a = Die("DW_TAG_structure_type", 800)
    a._members = [member(0, _int(801)), member(4, a)]
    bounded = _Bounded()
    monkeypatch.setattr(adv, "_resolve_type_die", bounded)
    assert adv._type_unaligned_at(a, None, 0, None) is False
    assert bounded.calls < 20


def test_a_nonzero_offset_self_member_still_reports_a_misaligned_leaf(
    monkeypatch,
) -> None:
    # struct A { int a @1; A self @4; } -- the int is misaligned at base 0 already,
    # so the answer is True before the cycle is ever revisited.
    a = Die("DW_TAG_structure_type", 810)
    a._members = [member(1, _int(811)), member(4, a)]
    bounded = _Bounded()
    monkeypatch.setattr(adv, "_resolve_type_die", bounded)
    assert adv._type_unaligned_at(a, None, 0, None) is True
    assert bounded.calls < 20


def test_sibling_members_of_one_type_at_different_offsets_are_each_checked() -> None:
    # struct T { int i @0; }   struct S { T first @0; T second @2; }
    # `first` is aligned at base 0; `second` puts T's int at 2 -- misaligned. A walk
    # that skipped the second member because its type was already seen would say False.
    t = Die("DW_TAG_structure_type", 820, members=[member(0, _int(821))])
    s = Die("DW_TAG_structure_type", 822, members=[member(0, t), member(2, t)])
    assert adv._type_unaligned_at(s, None, 0, None) is True


@pytest.mark.parametrize(
    "cycle_first", [True, False], ids=["cycle-first", "sibling-first"]
)
def test_a_cycle_cut_does_not_hide_an_independent_misaligned_sibling(
    cycle_first: bool,
) -> None:
    # struct A { int i @0; A self @2; }   -- a by-value cycle; A at base 2 is cut
    # when reached from inside A's own chain.
    # struct O { A x @2; A y @0; }  -- O's two members visit A at base 2 and base 0.
    # Oracle: A at base 2 puts its int at 2, which is not 4-aligned, so the answer
    # is True whichever order O's members are walked in.
    a = Die("DW_TAG_structure_type", 830)
    a._members = [member(0, _int(831)), member(2, a)]
    members = [member(2, a), member(0, a)]
    if cycle_first:
        members.reverse()
    o = Die("DW_TAG_structure_type", 832, members=members)
    assert adv._type_unaligned_at(o, None, 0, None) is True


def test_a_repeated_type_at_one_base_is_checked_once_and_keeps_its_answer() -> None:
    # struct S { int x @0; }   struct O { S a @0; S b @0; }
    # The second S at base 0 is the same (type, base) pair: deduplicated, and
    # its answer is the one already computed (aligned -> False). A misaligned
    # S at base 2 is still reported via its own pair.
    s = Die("DW_TAG_structure_type", 840, members=[member(0, _int(841))])
    aligned = Die("DW_TAG_structure_type", 842, members=[member(0, s), member(0, s)])
    assert adv._type_unaligned_at(aligned, None, 0, None) is False
    shifted = Die("DW_TAG_structure_type", 843, members=[member(0, s), member(2, s)])
    assert adv._type_unaligned_at(shifted, None, 0, None) is True
