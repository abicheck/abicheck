"""Primitive-level properties of the Phase 2 cross-layer joins
(evidence-entity-model plan; AGENTS.md "Primitive-level property tests").

Each world is generated from ground truth -- which entity exists, which is
exported, which has a debug definition and with what layout -- and the
expected state is derived from that ground truth, never from the join's own
helpers or ``model/export_index.py``'s projections.
"""

from __future__ import annotations

import pytest

import random

from hypothesis import given, settings, strategies as st

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.graph_join import JoinState


def join_exports(snap):
    from abicheck.compare.export_join import join_exports as _join

    return _join(snap)


def join_debug_types(snap):
    from abicheck.compare.debug_type_join import join_debug_types as _join

    return _join(snap)


_IDENT = st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=6)


# ---------------------------------------------------------------------------
# Export join worlds
# ---------------------------------------------------------------------------


@st.composite
def _export_world(draw):
    """``(declared, exported_declared, export_only)`` -- disjoint name sets;
    every declared entity is a C function whose linker name is its name."""
    names = draw(st.lists(_IDENT, unique=True, min_size=0, max_size=12))
    split = draw(st.integers(min_value=0, max_value=len(names)))
    declared = names[:split]
    export_only = [f"x_{n}" for n in names[split:]]
    exported = draw(st.sets(st.sampled_from(declared))) if declared else set()
    return declared, exported, export_only


def _export_snap(declared, exported, export_only, rng):
    fns = [
        Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
        for n in declared
    ]
    syms = [ElfSymbol(name=n) for n in (*sorted(exported), *export_only)]
    rng.shuffle(fns)
    rng.shuffle(syms)
    return AbiSnapshot(
        library="lib.so", version="1", functions=fns, elf=ElfMetadata(symbols=syms)
    )


@settings(max_examples=150, deadline=None)
@given(_export_world(), st.randoms(use_true_random=False))
def test_export_join_matches_ground_truth(world, rng):
    declared, exported, export_only = world
    j = join_exports(_export_snap(declared, exported, export_only, rng))
    for n in declared:
        rec = j.declaration(f"decl://{n}")
        if n in exported:
            assert rec.state is JoinState.MATCHED
            assert rec.candidates == (f"binary_symbol://elf/{n}",)
        else:
            assert rec.state is JoinState.UNMATCHED
    for n in export_only:
        assert j.export("elf", n).state is JoinState.UNMATCHED


@settings(max_examples=150, deadline=None)
@given(
    _export_world(),
    st.randoms(use_true_random=False),
    st.randoms(use_true_random=False),
)
def test_export_join_is_independent_of_input_order(world, rng_a, rng_b):
    a = join_exports(_export_snap(*world, rng_a))
    b = join_exports(_export_snap(*world, rng_b))
    assert dict(a.join.left) == dict(b.join.left)
    assert dict(a.join.right) == dict(b.join.right)


@settings(max_examples=150, deadline=None)
@given(_export_world(), st.randoms(use_true_random=False))
def test_no_export_join_without_shared_spelling(world, rng):
    """Every edge's export spelling is one the declaration itself recorded
    (ELF: no alias rule at all)."""
    j = join_exports(_export_snap(*world, rng))
    for export_id, decl_id in j.join.edges():
        assert export_id.removeprefix("binary_symbol://elf/") == decl_id.removeprefix(
            "decl://"
        )


@settings(max_examples=150, deadline=None)
@given(_export_world(), st.randoms(use_true_random=False), st.booleans())
def test_export_join_states_are_exhaustive(world, rng, with_table):
    snap = _export_snap(*world, rng)
    if not with_table:
        snap.elf = None
    j = join_exports(snap)
    declared, exported, export_only = world
    assert set(j.join.left) == {f"decl://{n}" for n in declared}
    expected_exports = (
        {f"binary_symbol://elf/{n}" for n in (*exported, *export_only)}
        if with_table
        else set()
    )
    assert set(j.join.right) == expected_exports
    for rec in (*j.join.left.values(), *j.join.right.values()):
        assert rec.state in JoinState
        if not with_table:
            assert rec.state is JoinState.UNKNOWN
    counts = j.join.state_counts()
    assert sum(counts["left"].values()) == len(declared)


# ---------------------------------------------------------------------------
# Debug-type join worlds
# ---------------------------------------------------------------------------


@st.composite
def _debug_world(draw):
    """Per type name: header present?, debug present?, header size, debug
    size, and an optional ODR variant size. Returned as a list of tuples."""
    names = draw(st.lists(_IDENT, unique=True, min_size=0, max_size=10))
    world = []
    for n in names:
        header = draw(st.booleans())
        debug = draw(st.booleans()) or not header
        hsize = draw(st.sampled_from([None, 4, 8]))
        dsize = draw(st.sampled_from([4, 8]))
        odr = draw(st.sampled_from([None, 4, 8, 16]))
        world.append((f"ns::{n}", header, debug, hsize, dsize, odr))
    return world


def _debug_snap(world, rng):
    types = []
    structs = {}
    odr = {}
    for name, header, debug, hsize, dsize, odr_size in world:
        if header:
            types.append(
                RecordType(
                    name=name.split("::")[-1],
                    kind="struct",
                    qualified_name=name,
                    size_bits=None if hsize is None else hsize * 8,
                    fields=[TypeField(name="a", type="int", offset_bits=0)],
                )
            )
        if debug:
            structs[name] = StructLayout(
                name=name,
                byte_size=dsize,
                fields=[
                    FieldInfo(name="a", type_name="int", byte_offset=0, byte_size=4)
                ],
            )
            if odr_size is not None and odr_size != dsize:
                odr[name] = [
                    StructLayout(
                        name=name,
                        byte_size=odr_size,
                        fields=[
                            FieldInfo(
                                name="a", type_name="int", byte_offset=0, byte_size=4
                            )
                        ],
                    )
                ]
    rng.shuffle(types)
    items = list(structs.items())
    rng.shuffle(items)
    dwarf = DwarfMetadata(
        structs=dict(items),
        has_dwarf=True,
        struct_odr_conflicts=odr,
        odr_conflicts_observed=True,
    )
    return AbiSnapshot(library="lib.so", version="1", types=types, dwarf=dwarf)


def _expected_header_state(header, debug, hsize, dsize, odr_size):
    """The ground-truth oracle: which debug definitions of the name are
    compatible with the header's own size (``None`` = unknown)."""
    if not debug:
        return JoinState.UNMATCHED
    sizes = [dsize] + ([odr_size] if odr_size is not None and odr_size != dsize else [])
    compatible = [s for s in sizes if hsize is None or s == hsize]
    if not compatible:
        return JoinState.UNMATCHED
    return JoinState.MATCHED if len(compatible) == 1 else JoinState.AMBIGUOUS


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
@settings(max_examples=150, deadline=None)
@given(_debug_world(), st.randoms(use_true_random=False))
def test_debug_join_matches_ground_truth(world, rng):
    j = join_debug_types(_debug_snap(world, rng))
    for name, header, debug, hsize, dsize, odr_size in world:
        if header:
            assert j.header(f"type://{name}").state is _expected_header_state(
                header, debug, hsize, dsize, odr_size
            ), name
        elif debug:
            rec = j.join.right[f"debug_type://debug/record/{name}"]
            assert rec.state is JoinState.UNMATCHED


@settings(max_examples=150, deadline=None)
@given(
    _debug_world(), st.randoms(use_true_random=False), st.randoms(use_true_random=False)
)
@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
def test_debug_join_is_independent_of_input_order(world, rng_a, rng_b):
    a = join_debug_types(_debug_snap(world, rng_a))
    b = join_debug_types(_debug_snap(world, rng_b))
    assert dict(a.join.left) == dict(b.join.left)
    assert dict(a.join.right) == dict(b.join.right)


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
@settings(max_examples=150, deadline=None)
@given(_debug_world(), st.randoms(use_true_random=False))
def test_no_debug_join_without_identical_qualified_spelling(world, rng):
    j = join_debug_types(_debug_snap(world, rng))
    for debug_id, header_id in j.join.edges():
        debug_name = j.occurrences[debug_id].name
        assert header_id == f"type://{debug_name}"


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
@settings(max_examples=150, deadline=None)
@given(_debug_world(), st.randoms(use_true_random=False))
def test_debug_join_states_are_exhaustive_and_odr_never_merges(world, rng):
    j = join_debug_types(_debug_snap(world, rng))
    headers = {f"type://{n}" for n, h, *_ in world if h}
    assert set(j.join.left) == headers
    for rec in (*j.join.left.values(), *j.join.right.values()):
        assert rec.state in (
            JoinState.MATCHED,
            JoinState.AMBIGUOUS,
            JoinState.UNMATCHED,
        )
    # Every ODR variant is its own occurrence node.
    for name, _h, debug, _hs, dsize, odr_size in world:
        if debug and odr_size is not None and odr_size != dsize:
            assert f"debug_type://debug/record/{name}#2" in j.join.right


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
def test_generated_worlds_are_not_vacuous():
    """Guard the oracle itself: across a fixed sample the generators reach
    every state on both joins."""
    rng = random.Random(0)
    seen: set[JoinState] = set()
    for i in range(200):
        world = [
            (
                f"ns::t{i}_{k}",
                rng.random() < 0.8,
                rng.random() < 0.8,
                rng.choice([None, 4, 8]),
                rng.choice([4, 8]),
                rng.choice([None, 4, 8, 16]),
            )
            for k in range(4)
        ]
        world = [(n, h, d or not h, hs, ds, o) for n, h, d, hs, ds, o in world]
        j = join_debug_types(_debug_snap(world, rng))
        seen |= {r.state for r in j.join.left.values()}
    assert {JoinState.MATCHED, JoinState.AMBIGUOUS, JoinState.UNMATCHED} <= seen
