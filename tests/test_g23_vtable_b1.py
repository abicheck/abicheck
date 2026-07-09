# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""G23 Phase B1 — L0 Itanium thunk / VTT surface diff.

Recovers multi-inheritance / virtual-base vtable breaks from .dynsym thunk and
VTT symbol names + sizes alone — no DWARF, no headers, works on stripped
binaries. All tests use synthetic ``ElfMetadata``.
"""
from __future__ import annotations

import sys

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_elf_layout import _parse_thunk
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot


def _sym(name: str, size: int = 0, sym_type: SymbolType = SymbolType.FUNC) -> ElfSymbol:
    return ElfSymbol(name=name, binding=SymbolBinding.GLOBAL, sym_type=sym_type, size=size)


def _snap(*syms: ElfSymbol) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1", version="1.0", functions=[], variables=[],
        types=[], enums=[], typedefs={},
        elf=ElfMetadata(symbols=list(syms), machine="EM_X86_64"),
        elf_only_mode=True,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── thunk symbol parsing ────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("_ZThn16_N7Derived2fbEv", ("N7Derived2fbEv", "h:n16")),
    ("_ZTh8_N3Foo3barEv", ("N3Foo3barEv", "h:8")),
    ("_ZTv0_n24_N7Derived3fooEv", ("N7Derived3fooEv", "v:0_n24")),
    # Covariant-return thunks: each of the two call-offsets carries its own
    # h/v adjustment-kind letter (e.g. _ZTch0_h8_...).
    ("_ZTch0_h8_N1D5cloneEv", ("N1D5cloneEv", "c:h0_h8")),
    ("_ZTchn8_h8_N1D5cloneEv", ("N1D5cloneEv", "c:hn8_h8")),
    ("_ZThn16_7Foo3barEv", ("7Foo3barEv", "h:n16")),   # unqualified base name
    ("_ZN7Derived2fbEv", None),                          # a plain method, not a thunk
    ("some_c_function", None),
])
def test_parse_thunk(name, expected):
    assert _parse_thunk(name) == expected


# ── VTABLE_THUNK_OFFSET_CHANGED ─────────────────────────────────────────────

class TestThunkOffsetChanged:
    def test_offset_shift_is_breaking(self):
        # Base reorder moves a secondary subobject → thunk this-adjustment offset
        # shifts (n16 → n72) while the primary _ZTV size is unchanged.
        old = _snap(_sym("_ZThn16_N7Derived2fbEv"))
        new = _snap(_sym("_ZThn72_N7Derived2fbEv"))
        r = compare(old, new)
        assert ChangeKind.VTABLE_THUNK_OFFSET_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_stable_offset_no_change(self):
        old = _snap(_sym("_ZThn16_N7Derived2fbEv"))
        new = _snap(_sym("_ZThn16_N7Derived2fbEv"))
        r = compare(old, new)
        assert ChangeKind.VTABLE_THUNK_OFFSET_CHANGED not in _kinds(r)

    def test_covariant_thunk_offset_shift_is_breaking(self):
        # A covariant-return override thunk whose adjustment offset shifts under
        # a base reorder must be caught (the _ZTc offset encoding carries h/v).
        old = _snap(_sym("_ZTch0_h8_N1D5cloneEv"))
        new = _snap(_sym("_ZTch0_h16_N1D5cloneEv"))
        r = compare(old, new)
        assert ChangeKind.VTABLE_THUNK_OFFSET_CHANGED in _kinds(r)

    def test_thunk_does_not_double_count_as_func_rename(self):
        # The thunk-offset change must be the *only* finding — the underlying
        # thunk symbols must not also surface as func_added/removed/renamed.
        old = _snap(_sym("_ZThn16_N7Derived2fbEv"))
        new = _snap(_sym("_ZThn72_N7Derived2fbEv"))
        ks = _kinds(compare(old, new))
        assert ks == {ChangeKind.VTABLE_THUNK_OFFSET_CHANGED}


# ── VTABLE_THUNK_SET_CHANGED ────────────────────────────────────────────────

class TestThunkSetChanged:
    def test_thunk_added_for_persisting_method(self):
        # The method symbol persists; it gains a thunk → a secondary-base
        # override was added.
        method = _sym("_ZN7Derived2fbEv")
        old = _snap(method)
        new = _snap(method, _sym("_ZThn16_N7Derived2fbEv"))
        r = compare(old, new)
        assert ChangeKind.VTABLE_THUNK_SET_CHANGED in _kinds(r)

    def test_thunk_removed_for_persisting_method(self):
        method = _sym("_ZN7Derived2fbEv")
        old = _snap(method, _sym("_ZThn16_N7Derived2fbEv"))
        new = _snap(method)
        r = compare(old, new)
        assert ChangeKind.VTABLE_THUNK_SET_CHANGED in _kinds(r)

    def test_no_set_change_when_method_absent_on_a_side(self):
        # If the plain method symbol is not present on both sides, the thunk
        # add/remove is part of a normal symbol add/remove, not a set change.
        old = _snap()
        new = _snap(_sym("_ZThn16_N7Derived2fbEv"))
        assert ChangeKind.VTABLE_THUNK_SET_CHANGED not in _kinds(compare(old, new))


# ── VTT_SLOT_COUNT_CHANGED ──────────────────────────────────────────────────

class TestVttSlotCount:
    def test_vtt_size_change_is_breaking(self):
        old = _snap(_sym("_ZTT7Derived", size=24, sym_type=SymbolType.OBJECT))
        new = _snap(_sym("_ZTT7Derived", size=40, sym_type=SymbolType.OBJECT))
        r = compare(old, new)
        assert ChangeKind.VTT_SLOT_COUNT_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_vtt_stable_no_change(self):
        old = _snap(_sym("_ZTT7Derived", size=24, sym_type=SymbolType.OBJECT))
        new = _snap(_sym("_ZTT7Derived", size=24, sym_type=SymbolType.OBJECT))
        assert ChangeKind.VTT_SLOT_COUNT_CHANGED not in _kinds(compare(old, new))

    def test_vtt_size_change_not_double_reported(self):
        # The dedicated vtt_slot_count_changed owns _ZTT size; the generic
        # symbol_size_changed path must skip it so one fact isn't two findings.
        old = _snap(_sym("_ZTT7Derived", size=24, sym_type=SymbolType.OBJECT))
        new = _snap(_sym("_ZTT7Derived", size=40, sym_type=SymbolType.OBJECT))
        ks = _kinds(compare(old, new))
        assert ChangeKind.VTT_SLOT_COUNT_CHANGED in ks
        assert ChangeKind.SYMBOL_SIZE_CHANGED not in ks
        assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL not in ks


def test_stdlib_thunks_are_skipped():
    # A thunk whose target is a std:: type is transitive runtime noise, not the
    # library's own ABI — it must not be reported.
    old = _snap(_sym("_ZThn16_NSt7ostream4fooEv"))
    new = _snap(_sym("_ZThn72_NSt7ostream4fooEv"))
    assert ChangeKind.VTABLE_THUNK_OFFSET_CHANGED not in _kinds(compare(old, new))


def test_b1_kinds_are_breaking():
    from abicheck.checker_policy import BREAKING_KINDS
    for k in (
        ChangeKind.VTABLE_THUNK_OFFSET_CHANGED,
        ChangeKind.VTABLE_THUNK_SET_CHANGED,
        ChangeKind.VTT_SLOT_COUNT_CHANGED,
    ):
        assert k in BREAKING_KINDS


# ── real-binary acceptance (multi-inheritance base reorder, stripped) ────────

@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="builds ELF thunks with g++ (produces Mach-O on macOS, PE on Windows)",
)
def test_multi_inheritance_base_reorder_stripped_is_breaking(tmp_path):
    """Acceptance fixture: growing a primary base shifts a secondary-base
    override's thunk offset while the primary _ZTV size is unchanged. This is
    BREAKING and detected on a fully *stripped* pair — the case a symbol-only or
    slot-count-only diff misses."""
    import subprocess

    v1 = "struct A { virtual void fa(); char pad[8]; };\n" \
         "struct B { virtual void fb(); };\n" \
         "struct D : A, B { void fb() override; };\n" \
         "void A::fa() {} void B::fb() {} void D::fb() {}\n"
    # A grows → B subobject (and its override thunk offset) moves; D's vtable
    # keeps the same number of slots.
    v2 = v1.replace("char pad[8];", "char pad[64];")

    def build(src: str, name: str):
        so = tmp_path / name
        r = subprocess.run(
            ["g++", "-shared", "-fPIC", "-o", str(so), "-x", "c++", "-"],
            input=src.encode(), capture_output=True,
        )
        if r.returncode != 0:
            pytest.skip(f"g++ failed: {r.stderr.decode()[:200]}")
        subprocess.run(["strip", "-s", str(so)], capture_output=True)
        return so

    from abicheck.elf_metadata import parse_elf_metadata

    old_so, new_so = build(v1, "v1.so"), build(v2, "v2.so")
    old = AbiSnapshot(library="v1.so", version="1", functions=[], variables=[],
                      types=[], enums=[], typedefs={},
                      elf=parse_elf_metadata(old_so), elf_only_mode=True)
    new = AbiSnapshot(library="v2.so", version="2", functions=[], variables=[],
                      types=[], enums=[], typedefs={},
                      elf=parse_elf_metadata(new_so), elf_only_mode=True)
    r = compare(old, new)
    assert ChangeKind.VTABLE_THUNK_OFFSET_CHANGED in _kinds(r)
    assert r.verdict == Verdict.BREAKING
