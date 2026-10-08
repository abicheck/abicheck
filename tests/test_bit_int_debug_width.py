"""Bit-precise integer (`_BitInt(N)`) widths survive every extraction path.

Regression for the GCC/Clang catalog audit (case115): Clang names every
bit-precise integer base type just ``_BitInt`` in DWARF and CastXML 0.7
emits it as an untyped ``<Unimplemented>`` node, so a 64 -> 128 width
change produced no ``bit_int_width_changed`` finding, and the header type
graph read the width as a referenced type (a bogus ``64`` -> ``128``
``declaration_renamed``).

The invariant is checked over the whole small domain of base-type DIE shapes
(name x bit_size x byte_size) against an independent oracle, not only the
reported 64/128 pair.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from abicheck.buildsource.type_graph import _resolve_nested_type_names
from abicheck.checker import ChangeKind, compare
from abicheck.diff_bit_int import _bit_int_width
from abicheck.dwarf_utils import base_type_name
from abicheck.model import AbiSnapshot, RecordType, ScopeOrigin, TypeField
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout


def _die(**attrs: object) -> SimpleNamespace:
    return SimpleNamespace(
        attributes={k: SimpleNamespace(value=v) for k, v in attrs.items()}
    )


_NAMES = ["_BitInt", "unsigned _BitInt", "int", "_BitInt(37)", "unsigned char"]
_BIT_SIZES = [None, 7, 64, 128]
_BYTE_SIZES = [None, 1, 8, 16]


def _oracle(name: str, bits: int | None, byte_size: int | None) -> str:
    if name not in ("_BitInt", "unsigned _BitInt"):
        return name
    if bits:
        return f"{name}({bits})"
    if byte_size:
        return f"{name}[{byte_size * 8}-bit storage]"
    return name


@pytest.mark.parametrize(
    ("name", "bits", "byte_size"),
    list(itertools.product(_NAMES, _BIT_SIZES, _BYTE_SIZES)),
)
def test_base_type_name_keeps_bit_precise_width(
    name: str, bits: int | None, byte_size: int | None
) -> None:
    attrs: dict[str, object] = {"DW_AT_name": name.encode()}
    if bits is not None:
        attrs["DW_AT_bit_size"] = bits
    if byte_size is not None:
        attrs["DW_AT_byte_size"] = byte_size
    spelled = base_type_name(_die(**attrs))
    assert spelled == _oracle(name, bits, byte_size)
    # Every width-bearing spelling must round-trip through the detector's
    # parser; distinct widths must stay distinct.
    if spelled != name or "(" in name:
        expected_width = bits or (byte_size * 8 if byte_size else None)
        if "(" in name:
            expected_width = int(name[name.index("(") + 1 : -1])
        assert _bit_int_width(spelled) == expected_width


def test_unnamed_base_type_keeps_placeholder() -> None:
    assert base_type_name(_die(DW_AT_byte_size=4)) == "base"


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("_BitInt(128)", ["_BitInt"]),
        ("unsigned _BitInt(7) *", ["unsigned _BitInt"]),
        ("const _BitInt( 64 ) &", ["_BitInt"]),
        ("std::function<void(_BitInt(9))>", None),
    ],
)
def test_type_graph_never_reads_a_width_as_a_type(
    spelling: str, expected: list[str] | None
) -> None:
    names = _resolve_nested_type_names(spelling)
    assert not any(n.strip().isdigit() for n in names), names
    if expected is not None:
        assert names == expected


def _snap(version: str, storage_bits: int) -> AbiSnapshot:
    # Header side as CastXML 0.7 reports it: the field type is unresolvable.
    record = RecordType(
        name="Accumulator",
        kind="struct",
        size_bits=storage_bits,
        fields=[TypeField(name="acc", type="Unimplemented")],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    layout = StructLayout(
        name="Accumulator",
        byte_size=storage_bits // 8,
        fields=[
            FieldInfo(
                name="acc",
                type_name=f"_BitInt[{storage_bits}-bit storage]",
                byte_offset=0,
                byte_size=storage_bits // 8,
            )
        ],
    )
    return AbiSnapshot(
        library="lib",
        version=version,
        types=[record],
        from_headers=True,
        dwarf=DwarfMetadata(has_dwarf=True, structs={"Accumulator": layout}),
    )


@pytest.mark.parametrize(("old_bits", "new_bits"), [(64, 128), (128, 64), (8, 16)])
def test_debug_layout_reports_bit_int_width_change(
    old_bits: int, new_bits: int
) -> None:
    result = compare(_snap("1", old_bits), _snap("2", new_bits))
    hits = [c for c in result.changes if c.kind == ChangeKind.BIT_INT_WIDTH_CHANGED]
    assert len(hits) == 1, [c.kind for c in result.changes]
    assert hits[0].symbol == "Accumulator"


def test_same_storage_width_is_not_a_bit_int_change() -> None:
    result = compare(_snap("1", 64), _snap("2", 64))
    assert not any(c.kind == ChangeKind.BIT_INT_WIDTH_CHANGED for c in result.changes)


def _layout(name: str, field_type: str, nbytes: int) -> StructLayout:
    return StructLayout(
        name=name,
        byte_size=nbytes,
        fields=[
            FieldInfo(name="acc", type_name=field_type, byte_offset=0, byte_size=nbytes)
        ],
    )


def _hits(result: object) -> list:
    return [c for c in result.changes if c.kind == ChangeKind.BIT_INT_WIDTH_CHANGED]  # type: ignore[attr-defined]


def test_header_and_debug_views_of_one_slot_report_once() -> None:
    """The header AST and the debug layout both see the field: one finding."""

    def snap(version: str, bits: int) -> AbiSnapshot:
        record = RecordType(
            name="Accumulator",
            kind="struct",
            size_bits=bits,
            fields=[TypeField(name="acc", type=f"_BitInt({bits})")],
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
        return AbiSnapshot(
            library="lib",
            version=version,
            types=[record],
            from_headers=True,
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "Accumulator": _layout("Accumulator", f"_BitInt({bits})", bits // 8)
                },
            ),
        )

    assert len(_hits(compare(snap("1", 64), snap("2", 128)))) == 1


def test_debug_only_struct_outside_the_public_scope_is_not_reported() -> None:
    """A width change on a struct no public header declares stays out."""
    old, new = _snap("1", 64), _snap("2", 64)
    old.declarations.debug_layout.structs["Internal"] = _layout(
        "Internal", "_BitInt[64-bit storage]", 8
    )
    new.declarations.debug_layout.structs["Internal"] = _layout(
        "Internal", "_BitInt[128-bit storage]", 16
    )
    assert not any(h.symbol == "Internal" for h in _hits(compare(old, new)))


def test_struct_missing_on_the_new_side_is_not_a_width_change() -> None:
    old, new = _snap("1", 64), _snap("2", 64)
    old.declarations.debug_layout.structs["Gone"] = _layout(
        "Gone", "_BitInt[64-bit storage]", 8
    )
    assert not any(h.symbol == "Gone" for h in _hits(compare(old, new)))


def test_mutating_the_debug_layout_reaches_the_detector() -> None:
    """Positive control for the two negatives above: the same mutation on
    the public struct is reported."""
    old, new = _snap("1", 64), _snap("2", 64)
    new.declarations.debug_layout.structs["Accumulator"] = _layout(
        "Accumulator", "_BitInt[128-bit storage]", 16
    )
    assert [h.symbol for h in _hits(compare(old, new))] == ["Accumulator"]
