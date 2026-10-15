"""Unit tests for ``TypeMapPair.counterpart`` (compare/type_map_pair.py).

Expectations are written out by hand from the matching rules, not computed
with the implementation's own helpers, so a regression in the fallback shows
up as a failed assertion here rather than agreeing with itself.
"""

from __future__ import annotations

import pytest

from abicheck.compare.type_map_pair import Side, TypeMap, TypeMapPair
from abicheck.diff_helpers import lookup_matched_type
from abicheck.model import RecordType


def _rec(bare: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=bare, kind="class", qualified_name=qualified, size_bits=64)


def _pair(old: list[RecordType], new: list[RecordType]) -> TypeMapPair[RecordType]:
    return TypeMapPair(old=TypeMap(old), new=TypeMap(new))


def test_exact_qualified_match_resolves_in_both_directions() -> None:
    old_foo = _rec("Foo", "ns::Foo")
    new_foo = _rec("Foo", "ns::Foo")
    pair = _pair([old_foo], [new_foo])
    assert pair.counterpart(Side.NEW, new_foo) is old_foo
    assert pair.counterpart(Side.OLD, old_foo) is new_foo


def test_side_selects_which_map_is_probed() -> None:
    # Same bare name, different namespaces: the side argument alone decides
    # which map t is looked up in, so the pair must not swap them.
    old_a = _rec("A", "ns::A")
    new_b = _rec("B", "ns::B")
    pair = _pair([old_a], [new_b])
    assert pair.counterpart(Side.NEW, new_b) is None
    assert pair.counterpart(Side.OLD, old_a) is None


def test_legacy_bare_keyed_side_matches_qualified_side_both_ways() -> None:
    # A legacy snapshot that never recorded qualified_name keys by bare name.
    old_legacy = _rec("Foo")
    new_foo = _rec("Foo", "ns::Foo")
    pair = _pair([old_legacy], [new_foo])
    assert pair.counterpart(Side.NEW, new_foo) is old_legacy
    assert pair.counterpart(Side.OLD, old_legacy) is new_foo


def test_namespace_move_between_distinct_identities_is_unmatched() -> None:
    # tbb::d1::graph -> tbb::d2::graph: distinct identities, no legacy side.
    old_graph = _rec("graph", "tbb::detail::d1::graph")
    new_graph = _rec("graph", "tbb::detail::d2::graph")
    pair = _pair([old_graph], [new_graph])
    assert pair.counterpart(Side.NEW, new_graph) is None
    assert pair.counterpart(Side.OLD, old_graph) is None


def test_inline_abi_namespace_difference_matches() -> None:
    # libstdc++ dual-ABI spelling vs. the plain spelling name one entity.
    old_str = _rec("basic_string", "std::__cxx11::basic_string")
    new_str = _rec("basic_string", "std::basic_string")
    pair = _pair([old_str], [new_str])
    assert pair.counterpart(Side.NEW, new_str) is old_str


def test_ambiguous_bare_name_on_probing_side_never_retries_by_bare_name() -> None:
    # ns1::Impl and ns2::Impl both exist on the new side, so the bare name
    # "Impl" is not a safe retry key and ns1::Impl stays unmatched even
    # though the old side's surviving Impl shares the bare spelling.
    new_ns1 = _rec("Impl", "ns1::Impl")
    new_ns2 = _rec("Impl", "ns2::Impl")
    old_ns2 = _rec("Impl", "ns2::Impl")
    pair = _pair([old_ns2], [new_ns1, new_ns2])
    assert pair.counterpart(Side.NEW, new_ns1) is None
    assert pair.counterpart(Side.NEW, new_ns2) is old_ns2


@pytest.mark.parametrize("name", ["Alpha", "Beta", "Gamma"])
def test_exact_match_is_symmetric_across_names(name: str) -> None:
    old_t = _rec(name, f"ns::{name}")
    new_t = _rec(name, f"ns::{name}")
    pair = _pair([old_t], [new_t])
    assert pair.counterpart(Side.NEW, new_t) is old_t
    assert pair.counterpart(Side.OLD, old_t) is new_t


def test_lookup_matched_type_delegates_to_the_same_answer() -> None:
    # The helper kept for the other callers must agree with the pair for
    # each direction it is called in.
    old_legacy = _rec("Foo")
    new_foo = _rec("Foo", "ns::Foo")
    old_map, new_map = TypeMap([old_legacy]), TypeMap([new_foo])
    assert lookup_matched_type(new_map, old_map, new_foo) is old_legacy
    assert lookup_matched_type(old_map, new_map, old_legacy) is new_foo


def test_type_map_mapping_surface_lists_each_qualified_type_once() -> None:
    # items/values/iteration expose one entry per qualified identity; the
    # bare-name alias never appears there (see TypeMap's docstring).
    a = _rec("A", "ns::A")
    b = _rec("B", "ns::B")
    m = TypeMap([a, b])
    assert len(m) == 2
    assert set(m) == {"ns::A", "ns::B"}
    assert dict(m.items()) == {"ns::A": a, "ns::B": b}
    assert {id(v) for v in m.values()} == {id(a), id(b)}


def test_repeated_same_identity_keeps_bare_name_unambiguous() -> None:
    # Two records with one identity are one type, not a bare-name collision.
    first = _rec("Foo", "ns::Foo")
    second = _rec("Foo", "ns::Foo")
    m = TypeMap([first, second])
    assert len(m) == 1
    assert m["ns::Foo"] is second
    assert m.bare_name_is_unambiguous("Foo")
