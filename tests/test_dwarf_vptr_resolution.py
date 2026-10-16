"""Unit tests for ``extract.dwarf_vptr_resolution.finalize_vptr_offsets``.

The pass is a pure function over the record list and the base-edge indexes
the DWARF builder collects, so every resolution tier is exercised here
directly with hand-built ``RecordType``s. The real-binary counterparts live
in ``test_dwarf_vptr_offset.py`` (needs g++).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.extract.dwarf_vptr_resolution import finalize_vptr_offsets
from abicheck.model import RecordType


def _rec(name: str, **kw: object) -> RecordType:
    return RecordType(name=name, kind="struct", **kw)  # type: ignore[arg-type]


def _run(
    types: list[RecordType],
    *,
    edges: dict[str, list[tuple[str, int | None, tuple[int, int] | None]]]
    | None = None,
    vedges: dict[str, list[tuple[str, tuple[int, int] | None]]] | None = None,
    die_index: dict[tuple[int, int], RecordType] | None = None,
    aliases: dict[tuple[int, int], str] | None = None,
    by_qualified: dict[str, RecordType] | None = None,
) -> None:
    by_name = {t.name: t for t in types}
    finalize_vptr_offsets(
        types,
        record_die_index=die_index or {},
        die_key_to_qualified_name=aliases or {},
        record_by_qualified_name=by_qualified or {},
        base_edges_by_record={id(by_name[k]): v for k, v in (edges or {}).items()},
        virtual_base_edges_by_record={
            id(by_name[k]): v for k, v in (vedges or {}).items()
        },
    )


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_primary_base_chain_resolves_in_any_emission_order(
    order: tuple[int, ...],
) -> None:
    # M : N : A, A owns its vptr at bit 64; oracle: every class in the chain
    # shares A's absolute vptr offset, whatever order DWARF emitted them in.
    a = _rec("A", vptr_offset_bits=64)
    n = _rec("N", bases=["A"], vtable=["n"])
    m = _rec("M", bases=["N"], vtable=["m"])
    recs = [a, n, m]
    _run(
        [recs[i] for i in order],
        edges={"N": [("A", 0, None)], "M": [("N", 0, None)]},
    )
    assert [r.resolved_vptr_offset_bits() for r in recs] == [64, 64, 64]


def test_non_primary_base_is_not_used_and_vtable_fallback_gives_zero() -> None:
    a = _rec("A", vptr_offset_bits=64)
    d = _rec("D", bases=["A"], vtable=["d"])
    _run([a, d], edges={"D": [("A", 128, None)]})
    assert d.resolved_vptr_offset_bits() == 0


def test_die_identity_beats_bare_name_collision() -> None:
    # Two bases named "A" in different namespaces; the DIE key picks two::A.
    one_a = _rec("A", vptr_offset_bits=32)
    two_a = _rec("two::A", vptr_offset_bits=96)
    d = _rec("D", bases=["A"], vtable=["d"])
    _run(
        [one_a, two_a, d],
        edges={"D": [("A", 0, (1, 2))]},
        die_index={(1, 2): two_a},
    )
    assert d.resolved_vptr_offset_bits() == 96


def test_aliased_stub_die_resolves_through_qualified_name() -> None:
    base = _rec("ns::A", vptr_offset_bits=128)
    d = _rec("D", bases=["A"], vtable=["d"])
    _run(
        [base, d],
        edges={"D": [("A", 0, (7, 9))]},
        aliases={(7, 9): "ns::A"},
        by_qualified={"ns::A": base},
    )
    assert d.resolved_vptr_offset_bits() == 128


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_virtual_only_chain_resolves_to_zero_in_any_order(
    order: tuple[int, ...],
) -> None:
    # struct A { virtual void a(); }; struct E : virtual A {}; struct F : virtual E {};
    a = _rec("A", vptr_offset_bits=0)
    e = _rec("E", virtual_bases=["A"])
    f = _rec("F", virtual_bases=["E"])
    recs = [a, e, f]
    _run(
        [recs[i] for i in order],
        vedges={"E": [("A", None)], "F": [("E", None)]},
    )
    assert [r.resolved_vptr_offset_bits() for r in recs] == [0, 0, 0]


def test_unrelated_and_unresolvable_records_stay_unknown() -> None:
    plain = _rec("Plain")
    orphan = _rec("Orphan", virtual_bases=["Missing"])
    _run([plain, orphan], vedges={"Orphan": [("Missing", None)]})
    assert plain.resolved_vptr_offset_bits() is None
    assert orphan.resolved_vptr_offset_bits() is None
