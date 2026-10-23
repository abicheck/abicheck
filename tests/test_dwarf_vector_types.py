"""GCC/Clang vector types (``vector_size``) in the DWARF-derived snapshot.

A ``DW_AT_GNU_vector`` array is a SIMD vector, not a plain array: its lane
count and vector-ness are identity, and a plain ``T[]`` spelling hid both.
Oracles are hand-computed from the source declarations: ``float x8`` is
8 * 4 = 32 bytes, ``char x32`` is 32 * 1 = 32 bytes, ``short x16`` is
16 * 2 = 32 bytes, so each is ``vector_size(32)``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_unified import parse_dwarf
from abicheck.dwarf_utils import array_type_spelling
from abicheck.elf_metadata import parse_elf_metadata
from abicheck.model import AbiSnapshot
from abicheck.workflows.dwarf_snapshot_assembly import build_snapshot_from_dwarf

_GCC = "gcc"
_F8 = "float __attribute__((vector_size(32)))"
_C32 = "char __attribute__((vector_size(32)))"


# ── spelling helper (unit) ───────────────────────────────────────────────────


def test_vector_spelling_names_element_and_total_bytes() -> None:
    assert array_type_spelling("float", 32, is_vector=True) == _F8


def test_plain_array_keeps_unbounded_spelling() -> None:
    assert array_type_spelling("float", 32, is_vector=False) == "float[]"


def test_unresolved_element_keeps_legacy_fallbacks() -> None:
    assert array_type_spelling(None, 32, is_vector=False) == "array"
    assert array_type_spelling(None, 32, is_vector=True) == (
        "? __attribute__((vector_size(32)))"
    )


# ── DWARF snapshot + comparison (integration: needs gcc) ─────────────────────


def _build(side_dir: Path, name: str, src: str) -> AbiSnapshot:
    side_dir.mkdir(parents=True, exist_ok=True)
    c_src = side_dir / f"{name}.c"
    c_src.write_text(src)
    so_path = side_dir / f"lib{name}.so"
    result = subprocess.run(
        [
            _GCC,
            "-shared",
            "-fPIC",
            "-g",
            "-w",
            "-Wno-psabi",
            "-o",
            str(so_path),
            str(c_src),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    dwarf_meta, dwarf_adv = parse_dwarf(so_path)
    return build_snapshot_from_dwarf(
        so_path, parse_elf_metadata(so_path), dwarf_meta, dwarf_adv
    )


def _pair(tmp_path: Path, name: str, old_src: str, new_src: str):  # type: ignore[no-untyped-def]
    old = _build(tmp_path / "old", name, old_src)
    new = _build(tmp_path / "new", name, new_src)
    return compare(old, new)


def _field_type(snap: AbiSnapshot, struct: str, field: str) -> str:
    rec = next(t for t in snap.declarations.types if t.name == struct)
    return next(f.type for f in rec.fields if f.name == field)


@pytest.mark.integration
class TestDwarfVectorTypes:
    def test_vector_fields_spelled_with_element_and_bytes(self, tmp_path: Path) -> None:
        snap = _build(
            tmp_path,
            "spell",
            "typedef float f8 __attribute__((vector_size(32)));\n"
            "typedef char c32 __attribute__((vector_size(32)));\n"
            "struct S { f8 a; c32 b; };\n"
            "struct S g_s;\n",
        )
        # Fields keep the typedef name; the resolved spelling is in the typedef table.
        assert _field_type(snap, "S", "a") == "f8"
        assert snap.declarations.typedefs["f8"] == _F8
        assert snap.declarations.typedefs["c32"] == _C32

    def test_identical_vector_sources_report_no_change(self, tmp_path: Path) -> None:
        src = (
            "typedef float f8 __attribute__((vector_size(32)));\n"
            "struct S { f8 a; int n; };\n"
            "struct S g_s;\n"
        )
        result = _pair(tmp_path, "same", src, src)
        assert result.verdict == Verdict.NO_CHANGE
        assert not result.changes

    def test_same_size_element_change_is_breaking(self, tmp_path: Path) -> None:
        # float x8 -> int x8: both 32 bytes, element type differs.
        result = _pair(
            tmp_path,
            "elem",
            "typedef float f8 __attribute__((vector_size(32)));\n"
            "struct S { f8 a; int n; };\nstruct S g_s;\n",
            "typedef int f8 __attribute__((vector_size(32)));\n"
            "struct S { f8 a; int n; };\nstruct S g_s;\n",
        )
        assert ChangeKind.TYPEDEF_BASE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_lane_count_change_at_same_size_is_breaking(self, tmp_path: Path) -> None:
        # char x32 -> short x16: both 32 bytes, lane count and element size swap.
        result = _pair(
            tmp_path,
            "lanes",
            "typedef char c32 __attribute__((vector_size(32)));\n"
            "struct C { c32 a; int n; };\nstruct C g_c;\n",
            "typedef short c32 __attribute__((vector_size(32)));\n"
            "struct C { c32 a; int n; };\nstruct C g_c;\n",
        )
        assert ChangeKind.TYPEDEF_BASE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_array_to_vector_field_swap_is_breaking(self, tmp_path: Path) -> None:
        # struct { float a[8]; } -> struct { f8 a; }: both 32 bytes at offset 0,
        # but the vector is 32-aligned and the array only 4-aligned.
        result = _pair(
            tmp_path,
            "arrvec",
            "struct W { float a[8]; };\nstruct W g_w;\n",
            "typedef float f8 __attribute__((vector_size(32)));\n"
            "struct W { f8 a; };\nstruct W g_w;\n",
        )
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_by_value_vector_param_change_is_breaking(self, tmp_path: Path) -> None:
        # A vector parameter travels in a vector register; a struct parameter
        # travels in memory. Both are 32 bytes, yet the ABI differs.
        result = _pair(
            tmp_path,
            "pv",
            "typedef float f8 __attribute__((vector_size(32)));\n"
            "void take(f8 x) { (void)x; }\n",
            "struct W8 { float a[8]; };\nvoid take(struct W8 x) { (void)x; }\n",
        )
        assert ChangeKind.FUNC_PARAMS_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_inline_vector_swap_without_typedef_is_breaking(
        self, tmp_path: Path
    ) -> None:
        # No typedef: base spelled both sides as a plain float[] and reported
        # NO_CHANGE for a real array -> vector break.
        result = _pair(
            tmp_path,
            "inline",
            "struct W { float a[8]; };\nstruct W g_w;\n",
            "struct W { float __attribute__((vector_size(32))) a; };\nstruct W g_w;\n",
        )
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING
