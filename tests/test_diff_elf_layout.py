# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the binary-only (no-DWARF / L0) vtable & RTTI layout detector.

The fast cases build ``AbiSnapshot``s entirely in memory (no compiler) and
exercise ``_diff_elf_layout`` directly.  One ``integration``-marked case
compiles a real ``.so`` with g++ to prove the ``_ZTV`` / ``_ZTI`` size math
matches what the Itanium C++ ABI actually emits.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_elf_layout import (
    _class_name,
    _diff_elf_layout,
    _inheritance_shape,
    _sized_rtti,
    _vtable_slots,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot


def _snap(*symbols: ElfSymbol, pointer_size: int = 8) -> AbiSnapshot:
    """Build a snapshot carrying only an ELF symbol table."""
    return AbiSnapshot(
        library="lib.so",
        version="1",
        elf=ElfMetadata(symbols=list(symbols), pointer_size=pointer_size),
    )


def _obj(name: str, size: int) -> ElfSymbol:
    return ElfSymbol(name=name, sym_type=SymbolType.OBJECT, size=size)


def _kinds(changes: list) -> list[ChangeKind]:
    return [c.kind for c in changes]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_vtable_slots_lp64(self) -> None:
        # [offset-to-top, typeinfo, slot0, slot1] = 4 words → 2 slots
        assert _vtable_slots(32, 8) == 2
        assert _vtable_slots(48, 8) == 4

    def test_vtable_slots_floor_at_zero(self) -> None:
        assert _vtable_slots(8, 8) == 0

    def test_inheritance_shape_categories(self) -> None:
        assert "no base" in _inheritance_shape(16, 8)
        assert "single base" in _inheritance_shape(24, 8)
        assert "2 base" in _inheritance_shape(56, 8)

    def test_inheritance_shape_32bit(self) -> None:
        # 2 words on ILP32 = 8 bytes → still "no base"
        assert "no base" in _inheritance_shape(8, 4)
        assert "single base" in _inheritance_shape(12, 4)

    def test_slot_and_shape_fall_back_on_bad_pointer_size(self) -> None:
        # A zero/negative pointer width must not divide-by-zero; fall back to 8.
        assert _vtable_slots(48, 0) == 4
        assert _vtable_slots(48, -1) == 4
        assert "no base" in _inheritance_shape(16, 0)

    # The demangler is monkeypatched so these branch tests are deterministic
    # across platforms (macOS CI ships no Itanium demangler, so the real
    # demangle() returns the input unchanged there).
    def test_class_name_extracts_after_for_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "abicheck.diff_elf_layout.demangle", lambda s: "vtable for Widget"
        )
        assert _class_name("_ZTV6Widget") == "Widget"

    def test_class_name_without_for_marker_returns_demangled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A demanglable symbol that is not a "X for Y" form → return as demangled.
        monkeypatch.setattr("abicheck.diff_elf_layout.demangle", lambda s: "foo()")
        assert _class_name("_Z3foov") == "foo()"

    def test_class_name_non_mangled_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Non-C++ name: demangle yields nothing → fall back to the raw symbol.
        monkeypatch.setattr("abicheck.diff_elf_layout.demangle", lambda s: None)
        assert _class_name("plain_c_symbol") == "plain_c_symbol"

    def test_sized_rtti_without_elf_is_empty(self) -> None:
        snap = AbiSnapshot(library="lib.so", version="1")  # elf is None
        assert _sized_rtti(snap, "_ZTV", skip_runtime=True) == {}


# ---------------------------------------------------------------------------
# Vtable slot count
# ---------------------------------------------------------------------------
class TestVtableSlotCount:
    def test_virtual_added_grows_vtable(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 40))
        new = _snap(_obj("_ZTV6Widget", 48))
        changes = _diff_elf_layout(old, new)
        assert _kinds(changes) == [ChangeKind.VTABLE_SLOT_COUNT_CHANGED]
        c = changes[0]
        assert c.symbol == "_ZTV6Widget"
        assert c.old_value == "40" and c.new_value == "48"
        assert "Widget" in c.description

    def test_virtual_removed_shrinks_vtable(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 48))
        new = _snap(_obj("_ZTV6Widget", 40))
        assert _kinds(_diff_elf_layout(old, new)) == [
            ChangeKind.VTABLE_SLOT_COUNT_CHANGED
        ]

    def test_stable_vtable_is_silent(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 48))
        new = _snap(_obj("_ZTV6Widget", 48))
        assert _diff_elf_layout(old, new) == []

    def test_added_only_vtable_not_reported(self) -> None:
        # Present only on the new side → owned by symbol add/remove, not here.
        old = _snap()
        new = _snap(_obj("_ZTV6Widget", 48))
        assert _diff_elf_layout(old, new) == []

    def test_removed_only_vtable_not_reported(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 48))
        new = _snap()
        assert _diff_elf_layout(old, new) == []

    def test_runtime_vtable_ignored(self) -> None:
        # libstdc++/cxxabi vtables must never be flagged.
        old = _snap(_obj("_ZTVSt13runtime_error", 40))
        new = _snap(_obj("_ZTVSt13runtime_error", 48))
        assert _diff_elf_layout(old, new) == []

    def test_zero_size_vtable_ignored(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 0))
        new = _snap(_obj("_ZTV6Widget", 48))
        assert _diff_elf_layout(old, new) == []


# ---------------------------------------------------------------------------
# RTTI inheritance shape
# ---------------------------------------------------------------------------
class TestRttiInheritance:
    def test_gained_base_class(self) -> None:
        # __class_type_info (16) → __si_class_type_info (24): no base → single base
        old = _snap(_obj("_ZTI3Foo", 16))
        new = _snap(_obj("_ZTI3Foo", 24))
        changes = _diff_elf_layout(old, new)
        assert _kinds(changes) == [ChangeKind.RTTI_INHERITANCE_CHANGED]
        assert "Foo" in changes[0].description

    def test_single_to_multiple_inheritance(self) -> None:
        old = _snap(_obj("_ZTI5Panel", 24))
        new = _snap(_obj("_ZTI5Panel", 56))
        changes = _diff_elf_layout(old, new)
        assert _kinds(changes) == [ChangeKind.RTTI_INHERITANCE_CHANGED]
        assert "single base" in changes[0].description
        assert "2 base" in changes[0].description

    def test_stable_typeinfo_is_silent(self) -> None:
        old = _snap(_obj("_ZTI3Foo", 24))
        new = _snap(_obj("_ZTI3Foo", 24))
        assert _diff_elf_layout(old, new) == []

    def test_runtime_typeinfo_ignored(self) -> None:
        old = _snap(_obj("_ZTISt13runtime_error", 16))
        new = _snap(_obj("_ZTISt13runtime_error", 24))
        assert _diff_elf_layout(old, new) == []

    @pytest.mark.parametrize(
        "sym",
        [
            "_ZTIi",     # int
            "_ZTIc",     # char
            "_ZTId",     # double
            "_ZTIPc",    # char*
            "_ZTIPKc",   # const char*
            "_ZTIKi",    # const int
            "_ZTIRi",    # int&
            "_ZTIDn",    # std::nullptr_t
        ],
    )
    def test_fundamental_and_compound_rtti_ignored(self, sym: str) -> None:
        # _ZTIi/_ZTIPc/etc. are NOT class types — decoding their size as
        # inheritance ("int gained a base") would be a false positive.
        old = _snap(_obj(sym, 16))
        new = _snap(_obj(sym, 24))
        assert _diff_elf_layout(old, new) == []

    def test_nested_class_rtti_still_decoded(self) -> None:
        # Sanity: a genuine nested class typeinfo (N…E) is still reported.
        old = _snap(_obj("_ZTIN4sycl6deviceE", 16))
        new = _snap(_obj("_ZTIN4sycl6deviceE", 24))
        assert {c.kind for c in _diff_elf_layout(old, new)} == {
            ChangeKind.RTTI_INHERITANCE_CHANGED
        }

    def test_runtime_rtti_kept_when_comparing_runtime_itself(self) -> None:
        # When the library under test IS the C++ runtime (libstdc++/libc++), its
        # own std:: vtables/typeinfo are the surface under test, not noise — so
        # their size changes must be reported (mirrors stdlib_namespaces_excluded).
        def _rt(*syms: ElfSymbol) -> AbiSnapshot:
            return AbiSnapshot(
                library="libstdc++.so.6",
                version="1",
                elf=ElfMetadata(symbols=list(syms)),
            )

        old = _rt(_obj("_ZTVSt13runtime_error", 40), _obj("_ZTISt13runtime_error", 16))
        new = _rt(_obj("_ZTVSt13runtime_error", 48), _obj("_ZTISt13runtime_error", 24))
        kinds = {c.kind for c in _diff_elf_layout(old, new)}
        assert ChangeKind.VTABLE_SLOT_COUNT_CHANGED in kinds
        assert ChangeKind.RTTI_INHERITANCE_CHANGED in kinds


# ---------------------------------------------------------------------------
# requires_support gating + full-compare wiring
# ---------------------------------------------------------------------------
class TestWiring:
    def test_no_elf_metadata_is_silent(self) -> None:
        old = AbiSnapshot(library="lib.so", version="1")
        new = AbiSnapshot(library="lib.so", version="2")
        # requires_support is False → detector skipped, compare() succeeds.
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE

    def test_vtable_growth_makes_compare_breaking(self) -> None:
        old = _snap(_obj("_ZTV6Widget", 40))
        new = _snap(_obj("_ZTV6Widget", 48))
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(
            c.kind == ChangeKind.VTABLE_SLOT_COUNT_CHANGED for c in result.changes
        )

    def test_no_generic_symbol_size_changed_for_vtable(self) -> None:
        # The vtable size change must surface ONLY as the specialized kind, not
        # also as the generic SYMBOL_SIZE_CHANGED (no double-emit).
        old = _snap(_obj("_ZTV6Widget", 40))
        new = _snap(_obj("_ZTV6Widget", 48))
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VTABLE_SLOT_COUNT_CHANGED in kinds
        assert ChangeKind.SYMBOL_SIZE_CHANGED not in kinds

    def test_pointer_size_survives_serialization_roundtrip(self) -> None:
        # A 32-bit ELF snapshot must reload with pointer_size=4, else
        # diff_elf_layout would decode ILP32 _ZTV/_ZTI sizes as LP64.
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap(_obj("_ZTV3Foo", 24), pointer_size=4)
        restored = snapshot_from_dict(snapshot_to_dict(snap))
        assert restored.elf is not None
        assert restored.elf.pointer_size == 4

    def test_vtt_size_change_keeps_generic_coverage(self) -> None:
        # VTT (_ZTT, emitted for virtual-base classes) has no dedicated detector
        # and is part of the construction ABI, so a size change must still surface
        # as the generic SYMBOL_SIZE_CHANGED — it must NOT be silently suppressed.
        old = _snap(_obj("_ZTT1B", 16))
        new = _snap(_obj("_ZTT1B", 32))
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.SYMBOL_SIZE_CHANGED for c in result.changes)


# ---------------------------------------------------------------------------
# Real-binary proof (needs g++; not in the fast lane)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF-only: g++ emits Mach-O/PE off Linux, so _ZTV/_ZTI parsing N/A",
)
@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_real_binary_vtable_and_rtti(tmp_path: Path) -> None:
    from abicheck.elf_metadata import parse_elf_metadata

    v1 = tmp_path / "v1.cpp"
    v1.write_text(
        "struct Widget { virtual void draw(); virtual ~Widget(); int x; };\n"
        "void Widget::draw() {} Widget::~Widget() {}\n"
        "struct Panel : Widget { void draw() override; int y; };\n"
        "void Panel::draw() {}\n"
    )
    v2 = tmp_path / "v2.cpp"
    v2.write_text(
        "struct Mixin { virtual void m(); };\n"
        "void Mixin::m() {}\n"
        "struct Widget { virtual void draw(); virtual void resize();"
        " virtual ~Widget(); int x; };\n"
        "void Widget::draw() {} void Widget::resize() {} Widget::~Widget() {}\n"
        "struct Panel : Widget, Mixin { void draw() override; int y; };\n"
        "void Panel::draw() {}\n"
    )
    lib1 = tmp_path / "libw_v1.so"
    lib2 = tmp_path / "libw_v2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-O0", str(v1), "-o", str(lib1)], check=True
    )
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-O0", str(v2), "-o", str(lib2)], check=True
    )

    s1 = AbiSnapshot(library="libw.so", version="1", elf=parse_elf_metadata(lib1))
    s2 = AbiSnapshot(library="libw.so", version="2", elf=parse_elf_metadata(lib2))
    changes = _diff_elf_layout(s1, s2)
    kinds_by_sym = {(c.kind, c.symbol) for c in changes}

    # Widget gained resize() → vtable grew.
    assert (ChangeKind.VTABLE_SLOT_COUNT_CHANGED, "_ZTV6Widget") in kinds_by_sym
    # Panel gained a second base (Mixin) → vtable grew AND typeinfo reshaped.
    assert (ChangeKind.VTABLE_SLOT_COUNT_CHANGED, "_ZTV5Panel") in kinds_by_sym
    assert (ChangeKind.RTTI_INHERITANCE_CHANGED, "_ZTI5Panel") in kinds_by_sym
