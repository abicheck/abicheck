"""The L1 debug-type join (evidence-entity-model Phase 2, I2).

Expectations are stated by hand from each fixture; the oracle never calls
the join's own layout comparison.
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot, EnumType, RecordType, TypeField
from abicheck.model.dwarf_facts import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.model.entities import EnumMember
from abicheck.model.graph_evidence_class import EdgeEvidenceClass
from abicheck.model.graph_join import JOIN_SPECS, JoinState


def _join(snap):
    from abicheck.compare.debug_type_join import join_debug_types

    return join_debug_types(snap)


def _rec(name, qualified=None, size_bits=64, fields=(), is_union=False):
    return RecordType(
        name=name,
        kind="union" if is_union else "struct",
        qualified_name=qualified,
        size_bits=size_bits,
        is_union=is_union,
        fields=[TypeField(name=n, type="int", offset_bits=o) for n, o in fields],
    )


def _layout(name, byte_size=8, fields=(), is_union=False):
    return StructLayout(
        name=name,
        byte_size=byte_size,
        is_union=is_union,
        fields=[
            FieldInfo(name=n, type_name="int", byte_offset=o, byte_size=4)
            for n, o in fields
        ],
    )


def _snap(types=(), enums=(), dwarf=None, typedefs=None):
    return AbiSnapshot(
        library="libx.so",
        version="1",
        types=list(types),
        enums=list(enums),
        typedefs=dict(typedefs or {}),
        dwarf=dwarf,
    )


def _dwarf(*layouts, enums=(), odr=None, observed=True):
    return DwarfMetadata(
        structs={lay.name: lay for lay in layouts},
        enums={e.name: e for e in enums},
        has_dwarf=True,
        struct_odr_conflicts=dict(odr or {}),
        odr_conflicts_observed=observed,
    )


def _debug(name, n=1, kind="record"):
    base = f"debug_type://debug/{kind}/{name}"
    return base if n == 1 else f"{base}#{n}"


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
class TestDebugTypeJoinStates:
    def test_struct_joins_header_record_by_qualified_name_and_layout(self):
        j = _join(
            _snap(
                [_rec("Foo", "api::Foo", 64, [("a", 0), ("b", 32)])],
                dwarf=_dwarf(_layout("api::Foo", 8, [("a", 0), ("b", 4)])),
            )
        )
        rec = j.join.right[_debug("api::Foo")]
        assert rec.state is JoinState.MATCHED
        assert rec.candidates == ("type://api::Foo",)
        assert rec.reason == "layout_corroborated"
        assert j.header("type://api::Foo").state is JoinState.MATCHED

    def test_layout_contradiction_rejects_the_name_match(self):
        j = _join(
            _snap(
                [_rec("Foo", "api::Foo", 64, [("a", 0)])],
                dwarf=_dwarf(_layout("api::Foo", 16, [("a", 0)])),
            )
        )
        rec = j.join.right[_debug("api::Foo")]
        assert rec.state is JoinState.UNMATCHED
        assert rec.rejected == ("type://api::Foo",)
        assert rec.reason == "layout_conflict"
        assert j.header("type://api::Foo").state is JoinState.UNMATCHED

    def test_union_never_joins_a_struct(self):
        j = _join(
            _snap(
                [_rec("U", size_bits=None)], dwarf=_dwarf(_layout("U", is_union=True))
            )
        )
        assert j.join.right[_debug("U")].state is JoinState.UNMATCHED

    def test_layout_unavailable_on_the_header_side_still_joins_by_name(self):
        j = _join(_snap([_rec("Foo", size_bits=None)], dwarf=_dwarf(_layout("Foo"))))
        rec = j.join.right[_debug("Foo")]
        assert rec.state is JoinState.MATCHED
        assert rec.reason == "layout_unavailable"

    def test_typedef_named_anonymous_struct_joins(self):
        # `typedef struct { int x; } Anon;`: castxml names the record after
        # the typedef, DWARF registers the anonymous struct under it too.
        j = _join(
            _snap(
                [_rec("Anon", None, 32, [("x", 0)])],
                typedefs={"Anon": ""},
                dwarf=_dwarf(_layout("Anon", 4, [("x", 0)])),
            )
        )
        assert j.join.right[_debug("Anon")].state is JoinState.MATCHED

    def test_inline_namespace_stays_separate_without_evidence(self):
        # castxml drops `v1`; DWARF keeps it. G15: no join on a guess.
        j = _join(
            _snap([_rec("S", "api::S", 32)], dwarf=_dwarf(_layout("api::v1::S", 4)))
        )
        assert j.join.right[_debug("api::v1::S")].state is JoinState.UNMATCHED
        assert j.header("type://api::S").state is JoinState.UNMATCHED
        assert j.header("type://api::S").reason == "no_debug_type"

    def test_bare_name_of_another_scope_never_joins(self):
        j = _join(
            _snap([_rec("Foo", "api::Foo", 64)], dwarf=_dwarf(_layout("impl::Foo", 8)))
        )
        assert j.join.right[_debug("impl::Foo")].state is JoinState.UNMATCHED

    def test_odr_duplicates_across_cus_are_ambiguous_never_merged(self):
        # Two CUs define `Dup` differently; the header carries no layout that
        # could tell which one it means.
        first = _layout("Dup", 4, [("a", 0)])
        second = _layout("Dup", 16, [("a", 0), ("b", 8)])
        j = _join(
            _snap(
                [_rec("Dup", size_bits=None)],
                dwarf=_dwarf(first, odr={"Dup": [second]}),
            )
        )
        head = j.header("type://Dup")
        assert head.state is JoinState.AMBIGUOUS
        assert head.candidates == (_debug("Dup"), _debug("Dup", 2))
        assert head.reason == "odr_conflict"
        assert (
            j.join.right[_debug("Dup")].subject
            != j.join.right[_debug("Dup", 2)].subject
        )

    def test_odr_conflict_resolved_by_layout_keeps_the_conflict_on_record(self):
        first = _layout("Dup", 4, [("a", 0)])
        second = _layout("Dup", 16, [("a", 0), ("b", 8)])
        j = _join(
            _snap(
                [_rec("Dup", None, 32, [("a", 0)])],
                dwarf=_dwarf(first, odr={"Dup": [second]}),
            )
        )
        head = j.header("type://Dup")
        assert head.state is JoinState.MATCHED
        assert head.candidates == (_debug("Dup"),)
        assert head.reason == "odr_conflict"
        assert j.join.right[_debug("Dup", 2)].rejected == ("type://Dup",)

    def test_two_header_records_sharing_a_spelling_are_ambiguous(self):
        # Phase 1 keeps them as two unresolved nodes; the debug name cannot
        # choose.
        j = _join(
            _snap(
                [_rec("Impl", size_bits=None), _rec("Impl", size_bits=None)],
                dwarf=_dwarf(_layout("Impl")),
            )
        )
        rec = j.join.right[_debug("Impl")]
        assert rec.state is JoinState.AMBIGUOUS
        assert all(c.startswith("unresolved://") for c in rec.candidates)

    def test_enum_joins_and_enumerator_values_corroborate(self):
        en = EnumType(
            name="Color", members=[EnumMember("RED", 0), EnumMember("BLUE", 2)]
        )
        info = EnumInfo(
            name="Color", underlying_byte_size=4, members={"RED": 0, "BLUE": 2}
        )
        j = _join(_snap(enums=[en], dwarf=_dwarf(enums=[info])))
        rec = j.join.right[_debug("Color", kind="enum")]
        assert rec.state is JoinState.MATCHED
        assert rec.reason == "layout_corroborated"

    def test_enum_value_conflict_rejects(self):
        en = EnumType(name="Color", members=[EnumMember("RED", 0)])
        info = EnumInfo(name="Color", underlying_byte_size=4, members={"RED": 1})
        j = _join(_snap(enums=[en], dwarf=_dwarf(enums=[info])))
        assert j.join.right[_debug("Color", kind="enum")].state is JoinState.UNMATCHED

    def test_record_never_joins_an_enum_of_the_same_name(self):
        info = EnumInfo(name="Kind", underlying_byte_size=4)
        j = _join(_snap([_rec("Kind", size_bits=None)], dwarf=_dwarf(enums=[info])))
        assert j.join.right[_debug("Kind", kind="enum")].state is JoinState.UNMATCHED


@pytest.mark.xfail(strict=True, reason="evidence-entity-model Phase 2: producer not landed yet")
class TestDebugTypeJoinIncompleteEvidence:
    def test_no_debug_info_is_unknown_not_unmatched(self):
        j = _join(_snap([_rec("Foo")]))
        assert not j.complete
        assert j.header("type://Foo").state is JoinState.UNKNOWN

    def test_debug_section_absent_is_unknown(self):
        j = _join(_snap([_rec("Foo")], dwarf=DwarfMetadata(has_dwarf=False)))
        assert j.header("type://Foo").state is JoinState.UNKNOWN

    def test_odr_not_looked_for_is_recorded(self):
        # BTF/CTF/PDB shapes and pre-v51 snapshots never looked for ODR
        # conflicts; the join says so instead of implying there were none.
        j = _join(_snap([_rec("Foo")], dwarf=_dwarf(_layout("Foo"), observed=False)))
        assert j.odr_observed is False


def test_debug_type_edge_is_a_resolved_join():
    spec = JOIN_SPECS["debug_type_of"]
    assert spec.evidence_class is EdgeEvidenceClass.RESOLVED_JOIN
    assert spec.producer == "compare.debug_type_join.join_debug_types"
    assert spec.inputs and spec.recompute_rule
