# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Bug class ``evidence.backfill_bare_name_match``: the clang-backend DWARF
layout backfill (``dumper_layout_backfill.backfill_dwarf_layout``) must take
a header record's layout only from the debug record the Phase 2 debug-type
join matches -- same qualified spelling, non-contradicting layout, mutually
unique -- never from a bare-name / last-``::``-segment neighbour.

The oracle (:func:`_expected_source`) is written from the rule's prose, not
from ``model.debug_type_match``: a header record's source is the one debug
record spelled exactly like the header's qualified name with the same
union-ness, provided no other header record shares that spelling and no
other debug record survives. Every debug record carries a distinct size, so
the backfilled ``size_bits`` names which record the layout came from.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.debug_type_join import join_debug_types
from abicheck.dumper_layout_backfill import backfill_dwarf_layout
from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.graph_join import JoinState

#: Qualified spellings a leaf ``Foo`` can take: global, two sibling
#: namespaces, an inline-namespace variant of one, a nested class scope.
SCOPES = ("", "a", "b", "a::v1", "a::Outer")


def _q(scope: str) -> str:
    return f"{scope}::Foo" if scope else "Foo"


def _header(scope: str, *, is_union: bool = False) -> RecordType:
    """A clang-backend header record: bare ``name``, qualified spelling
    only in ``qualified_name``, no layout."""
    return RecordType(
        name="Foo",
        kind="union" if is_union else "struct",
        qualified_name=_q(scope) if scope else None,
        size_bits=None,
        fields=[TypeField(name="x", type="int")],
        is_union=is_union,
    )


def _debug(scope: str, size_bits: int, *, is_union: bool = False) -> RecordType:
    """A DWARF-built record: qualified ``name``, real layout."""
    return RecordType(
        name=_q(scope),
        kind="union" if is_union else "struct",
        size_bits=size_bits,
        fields=[TypeField(name="x", type="int", offset_bits=0)],
        is_union=is_union,
    )


def _expected_source(
    header: list[RecordType], debug: list[RecordType], i: int
) -> int | None:
    h = header[i]
    key = h.qualified_name or h.name
    if sum(1 for o in header if (o.qualified_name or o.name) == key) != 1:
        return None
    hits = [
        j for j, d in enumerate(debug) if d.name == key and d.is_union == h.is_union
    ]
    return hits[0] if len(hits) == 1 else None


def _check(header: list[RecordType], debug: list[RecordType]) -> list[str]:
    out, _ = backfill_dwarf_layout(header, debug)
    errors = []
    for i, (before, after) in enumerate(zip(header, out)):
        src = _expected_source(header, debug, i)
        want = None if src is None else debug[src].size_bits
        if after.size_bits != want:
            errors.append(
                f"header {before.qualified_name or before.name!r} "
                f"(headers={[h.qualified_name or h.name for h in header]}, "
                f"debug={[d.name for d in debug]}): got {after.size_bits}, want {want}"
            )
    return errors


def _all_cases():
    for hn in range(1, 3):
        for hscopes in itertools.combinations(SCOPES, hn):
            for dn in range(0, 4):
                for dscopes in itertools.combinations(SCOPES, dn):
                    yield hscopes, dscopes


def test_backfill_takes_layout_only_from_the_joined_debug_record():
    """Exhaustive over every 1-2 header / 0-3 debug scope subset of SCOPES."""
    errors: list[str] = []
    n = 0
    for hscopes, dscopes in _all_cases():
        header = [_header(s) for s in hscopes]
        debug = [_debug(s, 64 * (k + 1)) for k, s in enumerate(dscopes)]
        errors += _check(header, debug)
        n += 1
    # vacuity guard on the oracle: some cases match, some do not
    matched = sum(
        _expected_source([_header(s) for s in hs], [_debug(s, 8) for s in ds], 0)
        is not None
        for hs, ds in _all_cases()
    )
    assert 0 < matched < n
    assert not errors, f"{len(errors)} of {n} cases:\n" + "\n".join(errors[:20])


@pytest.mark.parametrize("header_union", [False, True])
def test_layout_conflicting_candidate_is_never_the_source(header_union):
    """The same-spelled debug record disagrees on union-ness; a
    differently-scoped one agrees. Neither may supply the layout."""
    for hs, other in itertools.permutations(SCOPES, 2):
        header = [_header(hs, is_union=header_union)]
        debug = [
            _debug(hs, 96, is_union=not header_union),
            _debug(other, 128, is_union=header_union),
        ]
        out, coherence = backfill_dwarf_layout(header, debug)
        assert out[0].size_bits is None, (hs, other)
        assert coherence is not None and coherence.mismatched, (hs, other)


def test_backfill_agrees_with_the_snapshot_level_join():
    """For every generated case, the records the backfill fills are exactly
    the header records the snapshot-level ``join_debug_types`` matches
    one-to-one against the equivalent ``DwarfMetadata``."""
    disagreements = []
    for hscopes, dscopes in _all_cases():
        header = [_header(s) for s in hscopes]
        debug = [_debug(s, 64 * (k + 1)) for k, s in enumerate(dscopes)]
        out, _ = backfill_dwarf_layout(header, debug)
        filled = {
            h.qualified_name or h.name
            for h, o in zip(header, out)
            if o.size_bits is not None
        }
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=header,
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    d.name: StructLayout(
                        name=d.name,
                        byte_size=d.size_bits // 8,
                        fields=[FieldInfo("x", "int", 0, 4)],
                        is_union=d.is_union,
                    )
                    for d in debug
                },
            ),
        )
        join = join_debug_types(snap).join
        joined = set()
        for h, ident in zip(header, join_debug_types(snap).identities.records):
            rec = join.left[ident.node_id]
            if rec.state is JoinState.MATCHED:
                (occ,) = rec.candidates
                if join.right[occ].state is JoinState.MATCHED:
                    joined.add(h.qualified_name or h.name)
        if filled != joined:
            disagreements.append((hscopes, dscopes, filled, joined))
    assert not disagreements, disagreements[:10]


def test_match_is_independent_of_input_order():
    """``match_header_records`` is a pure function of the two *sets*: every
    permutation of either side pairs the same (header, debug) records."""
    from abicheck.model.debug_type_match import DebugRecordFacts, match_header_records

    checked = 0
    for hscopes, dscopes in _all_cases():
        header = [_header(s) for s in hscopes]
        debug = [_debug(s, 64 * (k + 1)) for k, s in enumerate(dscopes)]
        facts = [DebugRecordFacts.from_record_type(d) for d in debug]

        def pairs(hs, fs):
            return {
                (h.qualified_name or h.name, fs[m.debug_index].size_bits)
                for h, m in zip(hs, match_header_records(hs, fs))
                if m.debug_index is not None
            }

        want = pairs(header, facts)
        for hp in itertools.permutations(header):
            for fp in itertools.permutations(facts):
                assert pairs(list(hp), list(fp)) == want, (hscopes, dscopes)
                checked += 1
    assert checked > 1000


# ---------------------------------------------------------------------------
# ODR variants (bug class ``evidence.backfill_bare_name_match``, sibling:
# the backfill saw only the first DWARF definition of a name while the
# snapshot-level join also sees ``DwarfMetadata.struct_odr_conflicts``).
# ---------------------------------------------------------------------------

#: Layout-distinct definitions a name can get across CUs; the header record
#: carries a field ``x`` whose offset (when known) may corroborate one.
_VARIANT_OFFSETS = (0, 32, 64)


def _odr_layout(name: str, x_offset_bits: int, size_bits: int) -> StructLayout:
    return StructLayout(
        name=name,
        byte_size=size_bits // 8,
        fields=[FieldInfo("x", "int", x_offset_bits // 8, 4)],
    )


def _layout_record(layout: StructLayout) -> RecordType:
    return RecordType(
        name=layout.name,
        kind="struct",
        size_bits=layout.byte_size * 8,
        fields=[
            TypeField(name=f.name, type=f.type_name, offset_bits=f.byte_offset * 8)
            for f in layout.fields
        ],
    )


def _odr_expected(header_x: int | None, variants: list[int]) -> str:
    """Independent oracle: the definitions that do not contradict the
    header's known ``x`` offset; exactly one -> matched, several ->
    ambiguous, none -> mismatch."""
    alive = [v for v in variants if header_x is None or v == header_x]
    if len(alive) == 1:
        return "matched"
    return "ambiguous" if alive else "mismatch"


def _odr_cases():
    for n in range(1, 4):
        for variants in itertools.permutations(_VARIANT_OFFSETS, n):
            for header_x in (None, *_VARIANT_OFFSETS):
                yield header_x, list(variants)


def _odr_header(header_x: int | None) -> RecordType:
    return RecordType(
        name="Foo",
        kind="struct",
        qualified_name="ns::Foo",
        size_bits=None,
        fields=[TypeField(name="x", type="int", offset_bits=header_x)],
    )


def test_backfill_state_agrees_with_join_including_odr_variants():
    """Backfill resolves a header record to the same state the snapshot join
    does when the name has layout-distinct ODR definitions, and fills only
    from the one definition both agree on."""
    errors = []
    oracle_states = set()
    for header_x, variants in _odr_cases():
        layouts = [
            _odr_layout("ns::Foo", off, 128 + 32 * k) for k, off in enumerate(variants)
        ]
        header = [_odr_header(header_x)]
        out, coherence = backfill_dwarf_layout(
            header, [_layout_record(layouts[0])], {"ns::Foo": layouts[1:]}
        )
        assert coherence is not None
        got = (
            "matched"
            if coherence.matched
            else "ambiguous"
            if coherence.ambiguous
            else "mismatch"
            if coherence.mismatched
            else "unavailable"
        )
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=header,
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={"ns::Foo": layouts[0]},
                struct_odr_conflicts=(
                    {"ns::Foo": layouts[1:]} if len(layouts) > 1 else {}
                ),
                odr_conflicts_observed=True,
            ),
        )
        dj = join_debug_types(snap)
        rec = dj.join.left[dj.identities.records[0].node_id]
        join_state = {
            JoinState.MATCHED: "matched",
            JoinState.AMBIGUOUS: "ambiguous",
        }.get(rec.state, "other")
        want = _odr_expected(header_x, variants)
        oracle_states.add(want)
        want_size = (
            layouts[
                variants.index(header_x if header_x is not None else variants[0])
            ].byte_size
            * 8
            if want == "matched"
            else None
        )
        if got != want or out[0].size_bits != want_size:
            errors.append((header_x, variants, got, want, out[0].size_bits))
        if want in ("matched", "ambiguous") and join_state != want:
            errors.append(("join", header_x, variants, join_state, want))
    assert oracle_states == {"matched", "ambiguous", "mismatch"}  # vacuity
    assert not errors, errors[:10]


def test_odr_negative_control_single_definition_still_fills():
    header = [_odr_header(None)]
    out, coherence = backfill_dwarf_layout(
        header, [_layout_record(_odr_layout("ns::Foo", 0, 64))], {}
    )
    assert out[0].size_bits == 64
    assert coherence is not None and coherence.matched == ("Foo",)


# ---------------------------------------------------------------------------
# Base classes: a fieldless header record whose known bases contradict the
# DWARF record's known bases must never MATCH (and never receive its layout).
# ---------------------------------------------------------------------------

_BASE_SPELLINGS = {
    # (header spelling, DWARF spelling) -- the same class, spelled differently
    "same": [
        ("BaseA", "BaseA"),
        ("ns::BaseA", "BaseA"),
        ("BaseA", "ns::BaseA"),
        ("::ns::BaseA", "ns::BaseA"),
    ],
    # two different classes
    "different": [
        ("BaseA", "BaseB"),
        ("ns::BaseA", "ns::BaseB"),
        ("a::Base", "b::Base"),
        ("BaseA", "XBaseA"),
    ],
}


def _based(name, bases, virtual_bases, *, size_bits, dwarf):
    from abicheck.model.fact import Fact

    kw = {}
    if bases is not None:
        kw["bases"] = list(bases)
        kw["bases_fact"] = Fact.present(list(bases))
    if virtual_bases is not None:
        kw["virtual_bases"] = list(virtual_bases)
        kw["virtual_bases_fact"] = Fact.present(list(virtual_bases))
    return RecordType(
        name="ns::Foo" if dwarf else "Foo",
        qualified_name=None if dwarf else "ns::Foo",
        kind="class",
        size_bits=size_bits,
        fields=[],
        **kw,
    )


def _base_oracle(hb, db) -> bool:
    """Independent statement of 'same classes': equal after dropping a
    leading ``::`` and comparing on the shorter spelling's full ``::``
    segments."""
    if hb is None or db is None:
        return True
    if len(hb) != len(db):
        return False

    def segs(s):
        return s.lstrip(":").split("::")

    def same(a, b):
        sa, sb = segs(a), segs(b)
        k = min(len(sa), len(sb))
        return sa[-k:] == sb[-k:]

    remaining = list(db)
    for h in hb:
        hit = next((d for d in remaining if same(h, d)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def _base_cases():
    for kind, pairs in _BASE_SPELLINGS.items():
        for h, d in pairs:
            for virtual in (False, True):
                yield kind, h, d, virtual
    # unknown on either side
    for h, d in _BASE_SPELLINGS["different"]:
        yield "unknown_header", h, d, False
        yield "unknown_debug", h, d, False
    # count mismatch
    yield "different", "BaseA", "BaseA+BaseC", False


def test_contradicting_known_bases_never_match():
    errors = []
    seen = set()
    for kind, h, d, virtual in _base_cases():
        hb = None if kind == "unknown_header" else h.split("+")
        db = None if kind == "unknown_debug" else d.split("+")
        header = _based(
            "Foo",
            *(
                (None if hb is None else [], hb)
                if virtual
                else (hb, None if hb is None else [])
            ),
            size_bits=None,
            dwarf=False,
        )
        debug = _based(
            "Foo",
            *(
                (None if db is None else [], db)
                if virtual
                else (db, None if db is None else [])
            ),
            size_bits=64,
            dwarf=True,
        )
        out, coherence = backfill_dwarf_layout([header], [debug])
        want = _base_oracle(hb, db)
        seen.add(want)
        filled = out[0].size_bits == 64
        if filled != want:
            errors.append((kind, h, d, virtual, filled, want))
        if not want and not (coherence and coherence.mismatched):
            errors.append(("no mismatch recorded", kind, h, d, virtual))
    assert seen == {True, False}  # vacuity
    assert not errors, errors


def test_virtual_vs_nonvirtual_same_base_is_a_contradiction():
    header = _based("Foo", ["BaseA"], [], size_bits=None, dwarf=False)
    debug = _based("Foo", [], ["BaseA"], size_bits=64, dwarf=True)
    out, _ = backfill_dwarf_layout([header], [debug])
    assert out[0].size_bits is None


def test_unknown_bases_on_both_sides_still_match():
    header = _based("Foo", None, None, size_bits=None, dwarf=False)
    debug = _based("Foo", None, None, size_bits=64, dwarf=True)
    out, _ = backfill_dwarf_layout([header], [debug])
    assert out[0].size_bits == 64
