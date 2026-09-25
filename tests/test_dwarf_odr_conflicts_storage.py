"""Schema v51: ``DwarfMetadata``'s ODR-conflict observation round-trips, and a
snapshot stored before it reads as "not looked for" (evidence-entity-model
Phase 2; the debug-type join reports a recorded conflict as ambiguous)."""

from __future__ import annotations

import json

from abicheck.model import AbiSnapshot
from abicheck.model.dwarf_facts import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION


def _layout(size: int) -> StructLayout:
    return StructLayout(
        name="Dup",
        byte_size=size,
        fields=[FieldInfo(name="a", type_name="int", byte_offset=0, byte_size=4)],
    )


def _snap(dwarf: DwarfMetadata) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version="1", dwarf=dwarf)


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


def test_schema_version_is_at_least_51() -> None:
    assert SCHEMA_VERSION >= 51


def test_conflicts_round_trip() -> None:
    dwarf = DwarfMetadata(
        structs={"Dup": _layout(4)},
        enums={"E": EnumInfo(name="E", underlying_byte_size=4, members={"A": 0})},
        has_dwarf=True,
        struct_odr_conflicts={"Dup": [_layout(16)]},
        enum_odr_conflicts={
            "E": [EnumInfo(name="E", underlying_byte_size=4, members={"A": 1})]
        },
        odr_conflicts_observed=True,
    )
    back = _round_trip(_snap(dwarf)).dwarf
    assert back is not None
    assert back.struct_odr_conflicts == {"Dup": [_layout(16)]}
    assert back.enum_odr_conflicts["E"][0].members == {"A": 1}
    assert back.odr_conflicts_observed is True


def test_observed_without_conflicts_round_trips_as_observed() -> None:
    dwarf = DwarfMetadata(has_dwarf=True, odr_conflicts_observed=True)
    back = _round_trip(_snap(dwarf)).dwarf
    assert back is not None and back.odr_conflicts_observed is True
    assert back.struct_odr_conflicts == {}


def test_unobserved_encodes_exactly_like_v50() -> None:
    # No ODR walk ran (BTF/CTF/PDB, a symbols-only dump): nothing new is
    # written, so such a snapshot's DWARF block is byte-identical to v50's.
    encoded = snapshot_to_dict(_snap(DwarfMetadata(has_dwarf=True)))["dwarf"]
    assert set(encoded) == {"structs", "enums", "base_types", "has_dwarf"}


def test_pre_v51_snapshot_reads_as_not_looked_for() -> None:
    d = snapshot_to_dict(
        _snap(DwarfMetadata(structs={"Dup": _layout(4)}, has_dwarf=True))
    )
    d["schema_version"] = 50
    for key in ("struct_odr_conflicts", "enum_odr_conflicts", "odr_conflicts_observed"):
        d["dwarf"].pop(key, None)
    back = snapshot_from_dict(d).dwarf
    assert back is not None
    assert back.odr_conflicts_observed is False
    assert back.struct_odr_conflicts == {}


def test_debug_join_on_a_pre_v51_snapshot_says_odr_was_not_observed() -> None:
    from abicheck.compare.debug_type_join import join_debug_types

    d = snapshot_to_dict(
        _snap(DwarfMetadata(structs={"Dup": _layout(4)}, has_dwarf=True))
    )
    d["schema_version"] = 50
    assert join_debug_types(snapshot_from_dict(d)).odr_observed is False
