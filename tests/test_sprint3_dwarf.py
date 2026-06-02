"""Unit tests for Sprint 3 DWARF-aware layout diff."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_metadata import (
    DwarfMetadata,
    EnumInfo,
    FieldInfo,
    StructLayout,
    parse_dwarf_metadata,
)
from abicheck.model import AbiSnapshot

# ── helpers ──────────────────────────────────────────────────────────────────

def _snap(dwarf: DwarfMetadata | None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libtest.so", version="v1")
    snap.dwarf = dwarf  # type: ignore[attr-defined]
    return snap


def _meta(**kwargs: object) -> DwarfMetadata:
    m = DwarfMetadata(has_dwarf=True)
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _struct(
    name: str,
    size: int,
    fields: list[FieldInfo] | None = None,
    alignment: int = 0,
    is_union: bool = False,
) -> StructLayout:
    return StructLayout(
        name=name,
        byte_size=size,
        alignment=alignment,
        fields=fields or [],
        is_union=is_union,
    )


def _field(name: str, type_name: str, offset: int, size: int) -> FieldInfo:
    return FieldInfo(name=name, type_name=type_name, byte_offset=offset, byte_size=size)


def _enum(name: str, byte_size: int, members: dict[str, int] | None = None) -> EnumInfo:
    return EnumInfo(name=name, underlying_byte_size=byte_size, members=members or {})


# ── no-DWARF graceful degradation ────────────────────────────────────────────

def test_no_dwarf_both_produces_no_changes() -> None:
    """If neither snapshot has DWARF, no DWARF changes produced."""
    old = _snap(None)
    new = _snap(None)
    result = compare(old, new)
    dwarf_kinds = {
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
    }
    kinds = {c.kind for c in result.changes}
    assert kinds & dwarf_kinds == set()


def test_no_dwarf_old_produces_no_changes() -> None:
    """Old has no DWARF, new has → no DWARF changes (can't diff without old baseline)."""
    old = _snap(None)
    new = _snap(_meta(structs={"Foo": _struct("Foo", 8)}))
    result = compare(old, new)
    assert not any(c.kind == ChangeKind.STRUCT_SIZE_CHANGED for c in result.changes)


def test_stripped_new_emits_dwarf_info_missing() -> None:
    """old.has_dwarf=True, new.has_dwarf=False → DWARF_INFO_MISSING diagnostic, not silent COMPATIBLE."""
    old = _snap(_meta(structs={"Foo": _struct("Foo", 16)}))
    new_meta = DwarfMetadata(has_dwarf=False)   # stripped binary
    new = _snap(new_meta)
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.DWARF_INFO_MISSING in kinds
    # Must NOT silently produce NO_CHANGE or COMPATIBLE with zero dwarf changes
    assert ChangeKind.STRUCT_SIZE_CHANGED not in kinds  # can't diff without new info


# ── struct size ───────────────────────────────────────────────────────────────

def test_struct_size_changed() -> None:
    old = _snap(_meta(structs={"Foo": _struct("Foo", 16)}))
    new = _snap(_meta(structs={"Foo": _struct("Foo", 24)}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


def test_struct_size_unchanged_no_change() -> None:
    s = _struct("Bar", 32)
    old = _snap(_meta(structs={"Bar": s}))
    new = _snap(_meta(structs={"Bar": s}))
    result = compare(old, new)
    assert not any(c.kind == ChangeKind.STRUCT_SIZE_CHANGED for c in result.changes)


# ── field offset ──────────────────────────────────────────────────────────────

def test_field_offset_changed() -> None:
    old_s = _struct("Foo", 16, fields=[
        _field("x", "int", offset=0, size=4),
        _field("y", "int", offset=4, size=4),
    ])
    # padding inserted before y → offset shifts
    new_s = _struct("Foo", 24, fields=[
        _field("x", "int", offset=0, size=4),
        _field("y", "int", offset=8, size=4),
    ])
    old = _snap(_meta(structs={"Foo": old_s}))
    new = _snap(_meta(structs={"Foo": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_OFFSET_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


# ── field removed ─────────────────────────────────────────────────────────────

def test_field_removed() -> None:
    old_s = _struct("Foo", 16, fields=[
        _field("x", "int", 0, 4),
        _field("secret", "int", 4, 4),
    ])
    new_s = _struct("Foo", 8, fields=[
        _field("x", "int", 0, 4),
    ])
    old = _snap(_meta(structs={"Foo": old_s}))
    new = _snap(_meta(structs={"Foo": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_REMOVED in kinds
    assert result.verdict == Verdict.BREAKING


# ── field type size changed ───────────────────────────────────────────────────

def test_stdlib_struct_layout_filtered_in_dwarf_only_mode() -> None:
    """std::/__gnu_cxx::/anonymous types leaked into DWARF (no header model)
    must not be diffed as the inspected library's own struct layout.

    Regression for validation RD2-1: the std::/anonymous surface filter was
    wired into the type/enum/typedef detectors but NOT the DWARF struct-layout
    detector, so e.g. ``std::__cxx11::basic_string<...>::npos`` and
    ``std::integral_constant<...>::value`` still scored STRUCT_FIELD_REMOVED and
    drove a false BREAKING verdict on an ABI-compatible bump.
    """
    leaked = {
        "std::__cxx11::basic_string<char>": _struct(
            "std::__cxx11::basic_string<char>", 32, fields=[
                _field("npos", "unsigned long", 0, 8),
                _field("_M_p", "char*", 8, 8),
            ]),
        "std::integral_constant<bool, false>": _struct(
            "std::integral_constant<bool, false>", 1, fields=[_field("value", "bool", 0, 1)]),
        "__gnu_cxx::__ops::_Iter_less_iter": _struct(
            "__gnu_cxx::__ops::_Iter_less_iter", 8, fields=[_field("_M_x", "int", 0, 4)]),
    }
    # New side drops fields from every leaked std:: type (toolchain DWARF churn).
    leaked_new = {
        "std::__cxx11::basic_string<char>": _struct(
            "std::__cxx11::basic_string<char>", 32, fields=[_field("_M_p", "char*", 8, 8)]),
        "std::integral_constant<bool, false>": _struct(
            "std::integral_constant<bool, false>", 1, fields=[]),
        "__gnu_cxx::__ops::_Iter_less_iter": _struct(
            "__gnu_cxx::__ops::_Iter_less_iter", 8, fields=[]),
    }
    old = _snap(_meta(structs=leaked))
    new = _snap(_meta(structs=leaked_new))
    result = compare(old, new)
    offenders = [
        c for c in result.changes
        if c.kind in {
            ChangeKind.STRUCT_FIELD_REMOVED,
            ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
            ChangeKind.STRUCT_SIZE_CHANGED,
        }
        and c.symbol.startswith(("std::", "__gnu_cxx::"))
    ]
    assert offenders == [], f"std:: layout changes leaked as findings: {[c.symbol for c in offenders]}"
    assert result.verdict in (Verdict.COMPATIBLE, Verdict.NO_CHANGE)


def test_stdlib_struct_layout_kept_when_target_is_cxx_runtime() -> None:
    """When the inspected DSO *is* the C++ runtime, std:: layout IS its own ABI
    surface and must still be diffed (the filter must not hide real breaks)."""
    old_s = _struct("std::__cxx11::basic_string<char>", 32, fields=[
        _field("npos", "unsigned long", 0, 8),
        _field("_M_p", "char*", 8, 8),
    ])
    new_s = _struct("std::__cxx11::basic_string<char>", 32, fields=[
        _field("_M_p", "char*", 8, 8),
    ])
    old = AbiSnapshot(library="libstdc++.so.6", version="v1")
    new = AbiSnapshot(library="libstdc++.so.6", version="v2")
    old.dwarf = _meta(structs={"std::__cxx11::basic_string<char>": old_s})  # type: ignore[attr-defined]
    new.dwarf = _meta(structs={"std::__cxx11::basic_string<char>": new_s})  # type: ignore[attr-defined]
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_REMOVED in kinds


def test_anonymous_dwarf_types_filtered_even_for_cxx_runtime() -> None:
    """Even when the target IS the C++ runtime (std:: kept), anonymous/lambda
    types are never stable ABI and must still be filtered from the DWARF layout
    diff (regression for the Codex review: the filter must run for runtimes too,
    just with std:: preserved)."""
    old = AbiSnapshot(library="libstdc++.so.6", version="v1")
    new = AbiSnapshot(library="libstdc++.so.6", version="v2")
    old.dwarf = _meta(structs={  # type: ignore[attr-defined]
        "std::vector<int>": _struct("std::vector<int>", 24, fields=[_field("_M_start", "int*", 0, 8)]),
        "<lambda(int)>": _struct("<lambda(int)>", 8, fields=[_field("__captured", "int", 0, 4)]),
    })
    new.dwarf = _meta(structs={  # type: ignore[attr-defined]
        # std:: kept and unchanged; the lambda closure type "lost" its field.
        "std::vector<int>": _struct("std::vector<int>", 24, fields=[_field("_M_start", "int*", 0, 8)]),
        "<lambda(int)>": _struct("<lambda(int)>", 8, fields=[]),
    })
    result = compare(old, new)
    offenders = [
        c for c in result.changes
        if c.kind == ChangeKind.STRUCT_FIELD_REMOVED and "lambda" in c.symbol
    ]
    assert offenders == [], f"anonymous/lambda DWARF churn must be filtered for runtimes too: {[c.symbol for c in offenders]}"


def test_field_type_size_changed() -> None:
    # int → long (size 4 → 8)
    old_s = _struct("Foo", 8, fields=[_field("n", "int", 0, 4)])
    new_s = _struct("Foo", 16, fields=[_field("n", "long", 0, 8)])
    old = _snap(_meta(structs={"Foo": old_s}))
    new = _snap(_meta(structs={"Foo": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds


def test_field_type_name_changed_same_size() -> None:
    """int → float: same size (4B) but different type — should still be BREAKING."""
    old_s = _struct("Ctx", 8, fields=[_field("val", "int", 0, 4)])
    new_s = _struct("Ctx", 8, fields=[_field("val", "float", 0, 4)])
    old = _snap(_meta(structs={"Ctx": old_s}))
    new = _snap(_meta(structs={"Ctx": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


# ── struct alignment ──────────────────────────────────────────────────────────

def test_struct_alignment_changed() -> None:
    old_s = _struct("Vec", 16, alignment=4)
    new_s = _struct("Vec", 16, alignment=8)
    old = _snap(_meta(structs={"Vec": old_s}))
    new = _snap(_meta(structs={"Vec": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_ALIGNMENT_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


# ── enum underlying size ──────────────────────────────────────────────────────

def test_enum_underlying_size_changed() -> None:
    old = _snap(_meta(enums={"Color": _enum("Color", byte_size=1)}))
    new = _snap(_meta(enums={"Color": _enum("Color", byte_size=4)}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


def test_enum_underlying_size_unchanged_no_change() -> None:
    e = _enum("Status", byte_size=4, members={"OK": 0, "ERR": 1})
    old = _snap(_meta(enums={"Status": e}))
    new = _snap(_meta(enums={"Status": e}))
    result = compare(old, new)
    assert not any(c.kind == ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED for c in result.changes)


# ── enum member removed ───────────────────────────────────────────────────────

def test_enum_member_removed() -> None:
    old = _snap(_meta(enums={
        "Flags": _enum("Flags", 4, members={"A": 1, "B": 2, "C": 4})
    }))
    new = _snap(_meta(enums={
        "Flags": _enum("Flags", 4, members={"A": 1, "C": 4})  # B removed
    }))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_REMOVED in kinds
    assert result.verdict == Verdict.BREAKING


# ── enum member value changed ─────────────────────────────────────────────────

def test_enum_member_value_changed() -> None:
    old = _snap(_meta(enums={"Code": _enum("Code", 4, members={"OK": 0, "FAIL": 1, "LAST": 2})}))
    new = _snap(_meta(enums={"Code": _enum("Code", 4, members={"OK": 0, "FAIL": 3, "LAST": 2})}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


def test_enum_negative_member_value() -> None:
    """Negative enum constants (DW_FORM_sdata) must be handled correctly."""
    old = _snap(_meta(enums={"Err": _enum("Err", 4, members={"OK": 0, "ERR": -1})}))
    new = _snap(_meta(enums={"Err": _enum("Err", 4, members={"OK": 0, "ERR": -2})}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kinds


# ── union ─────────────────────────────────────────────────────────────────────

def test_union_size_changed() -> None:
    old = _snap(_meta(structs={"U": _struct("U", 4, is_union=True)}))
    new = _snap(_meta(structs={"U": _struct("U", 8, is_union=True)}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


def test_union_field_offset_always_zero() -> None:
    """Union members all start at offset 0 — change in member types detected via type_name."""
    old_s = _struct("U", 4, fields=[_field("i", "int", 0, 4)], is_union=True)
    new_s = _struct("U", 4, fields=[_field("i", "float", 0, 4)], is_union=True)
    old = _snap(_meta(structs={"U": old_s}))
    new = _snap(_meta(structs={"U": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds


# ── mid-struct insert ─────────────────────────────────────────────────────────

def test_field_inserted_mid_struct_shifts_offsets() -> None:
    """Inserting a field between existing fields shifts later fields — detected via offsets."""
    old_s = _struct("S", 12, fields=[
        _field("a", "int", 0, 4),
        _field("b", "int", 4, 4),
        _field("c", "int", 8, 4),
    ])
    new_s = _struct("S", 16, fields=[
        _field("a", "int", 0, 4),
        _field("pad", "int", 4, 4),   # new field inserted
        _field("b", "int", 8, 4),     # b shifted
        _field("c", "int", 12, 4),    # c shifted
    ])
    old = _snap(_meta(structs={"S": old_s}))
    new = _snap(_meta(structs={"S": new_s}))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    # Size changed AND b/c offsets changed
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds
    assert ChangeKind.STRUCT_FIELD_OFFSET_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


# ── end-to-end AbiSnapshot pipeline ──────────────────────────────────────────

def test_full_snapshot_pipeline_dwarf_only() -> None:
    """Full AbiSnapshot with dwarf field set — verifies compare() integration path."""
    from abicheck.model import AbiSnapshot

    old_snap = AbiSnapshot(library="libfoo.so", version="1.0")
    old_snap.dwarf = _meta(structs={"Ctx": _struct("Ctx", 8, fields=[_field("n", "int", 0, 4)])})  # type: ignore[attr-defined]

    new_snap = AbiSnapshot(library="libfoo.so", version="2.0")
    new_snap.dwarf = _meta(structs={"Ctx": _struct("Ctx", 16, fields=[_field("n", "long", 0, 8)])})  # type: ignore[attr-defined]

    result = compare(old_snap, new_snap)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING


# ── integration: real .so with -g ─────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_parse_dwarf_real_so() -> None:
    """Compile a .so with debug info and verify DWARF layout extracted correctly."""
    src = """
    typedef struct { int x; double y; char z; } MyStruct;
    typedef enum { RED=0, GREEN=1, BLUE=2 } Color;
    int use(MyStruct *s, Color c) { return s->x + c; }
    """
    with tempfile.TemporaryDirectory() as td:
        so = Path(td) / "libtest.so"
        result = subprocess.run(
            ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), "-x", "c", "-"],
            input=src.encode(), capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")

        meta = parse_dwarf_metadata(so)

    assert meta.has_dwarf
    assert "MyStruct" in meta.structs
    s = meta.structs["MyStruct"]
    # x=int(4), y=double(8 with align), z=char(1) + padding
    # x86-64: 24 bytes; 32-bit (arm): 16 bytes
    assert s.byte_size in (16, 24), f"unexpected byte_size={s.byte_size}"
    field_names = {f.name for f in s.fields}
    assert {"x", "y", "z"} <= field_names

    assert "Color" in meta.enums
    e = meta.enums["Color"]
    assert e.underlying_byte_size == 4
    assert e.members == {"RED": 0, "GREEN": 1, "BLUE": 2}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_parse_dwarf_struct_size_regression() -> None:
    """int → long field: DWARF detects struct size change as BREAKING."""
    src_v1 = "typedef struct { int n; } Ctx; int use(Ctx *c) { return c->n; }"
    src_v2 = "typedef struct { long n; } Ctx; int use(Ctx *c) { return (int)c->n; }"

    with tempfile.TemporaryDirectory() as td:
        for src, name in [(src_v1, "v1.so"), (src_v2, "v2.so")]:
            r = subprocess.run(
                ["gcc", "-g", "-shared", "-fPIC", "-o", str(Path(td) / name), "-x", "c", "-"],
                input=src.encode(), capture_output=True,
            )
            if r.returncode != 0:
                pytest.skip(f"gcc failed: {r.stderr.decode()[:200]}")

        meta1 = parse_dwarf_metadata(Path(td) / "v1.so")
        meta2 = parse_dwarf_metadata(Path(td) / "v2.so")

    old = _snap(meta1)
    new = _snap(meta2)
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING
