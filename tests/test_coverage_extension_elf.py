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

"""Coverage-extension ELF detectors: loader contract, identity, import surface.

Covers EI_DATA endianness, PT_INTERP, BIND_NOW, DT_FLAGS_1 loading flags,
init/fini presence, NT_GNU_ABI_TAG kernel floor, x86-64 ISA-needed baseline,
exported-object alignment, undefined-symbol (import) set, and global
allocator-replacement detection. All tests use synthetic ``ElfMetadata`` —
no real binaries required.
"""
from __future__ import annotations

import struct

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
    _decode_abi_tag_desc,
    _decode_gnu_property_desc,
    _value_alignment,
)
from abicheck.model import AbiSnapshot


def _snap(elf: ElfMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=[],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        elf=elf,
        elf_only_mode=True,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _elf(**kwargs) -> ElfMetadata:
    # Loader-contract detectors are gated on both sides having captured ELF
    # identity (a real parse always sets machine); default it so these
    # fixtures aren't mistaken for legacy/empty snapshots.
    kwargs.setdefault("machine", "EM_X86_64")
    return ElfMetadata(**kwargs)


def _obj(name: str, alignment: int = 0) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        binding=SymbolBinding.GLOBAL,
        sym_type=SymbolType.OBJECT,
        size=8,
        value_alignment=alignment,
    )


def _func(name: str, alignment: int = 0) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        binding=SymbolBinding.GLOBAL,
        sym_type=SymbolType.FUNC,
        value_alignment=alignment,
    )


def _imp(name: str, binding: SymbolBinding = SymbolBinding.GLOBAL, version: str = "") -> ElfImport:
    return ElfImport(name=name, binding=binding, version=version)


# ── EI_DATA endianness ───────────────────────────────────────────────────────

class TestEndianness:
    def test_flip_is_breaking(self):
        r = compare(_snap(_elf(ei_data="LSB")), _snap(_elf(ei_data="MSB")))
        assert ChangeKind.ELF_ENDIANNESS_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_uncaptured_side_is_skipped(self):
        r = compare(_snap(_elf(ei_data="")), _snap(_elf(ei_data="MSB")))
        assert ChangeKind.ELF_ENDIANNESS_CHANGED not in _kinds(r)

    def test_stable_no_finding(self):
        r = compare(_snap(_elf(ei_data="LSB")), _snap(_elf(ei_data="LSB")))
        assert ChangeKind.ELF_ENDIANNESS_CHANGED not in _kinds(r)


# ── PT_INTERP ────────────────────────────────────────────────────────────────

class TestInterpreter:
    def test_changed(self):
        old = _elf(interpreter="/lib64/ld-linux-x86-64.so.2")
        new = _elf(interpreter="/lib64/ld-musl-x86_64.so.1")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.INTERPRETER_CHANGED in _kinds(r)

    def test_absent_side_skipped(self):
        # Shared libraries usually carry no PT_INTERP; absence is not a change.
        r = compare(_snap(_elf(interpreter="")), _snap(_elf(interpreter="/lib64/ld.so")))
        assert ChangeKind.INTERPRETER_CHANGED not in _kinds(r)


# ── BIND_NOW ─────────────────────────────────────────────────────────────────

class TestBindNow:
    def test_disabled_with_stable_relro(self):
        old = _elf(bind_now=True, relro="none")
        new = _elf(bind_now=False, relro="none")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.BIND_NOW_DISABLED in _kinds(r)

    def test_suppressed_when_relro_weakened_reports(self):
        # full→partial RELRO is the same underlying event; RELRO_WEAKENED owns it.
        old = _elf(bind_now=True, relro="full")
        new = _elf(bind_now=False, relro="partial")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.RELRO_WEAKENED in _kinds(r)
        assert ChangeKind.BIND_NOW_DISABLED not in _kinds(r)

    def test_enabling_is_not_a_finding(self):
        r = compare(_snap(_elf(bind_now=False)), _snap(_elf(bind_now=True)))
        assert ChangeKind.BIND_NOW_DISABLED not in _kinds(r)


# ── DT_FLAGS_1 loading flags ─────────────────────────────────────────────────

class TestDynamicLoadingFlags:
    def test_nodelete_dropped(self):
        old = _elf(dynamic_flags=frozenset({"NODELETE"}))
        new = _elf(dynamic_flags=frozenset())
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED)
        assert "-NODELETE" in change.description

    def test_noopen_gained(self):
        old = _elf(dynamic_flags=frozenset())
        new = _elf(dynamic_flags=frozenset({"NOOPEN"}))
        r = compare(_snap(old), _snap(new))
        change = next(c for c in r.changes if c.kind == ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED)
        assert "+NOOPEN" in change.description

    def test_legacy_uncaptured_side_skipped(self):
        old = _elf(dynamic_flags=None)
        new = _elf(dynamic_flags=frozenset({"NODELETE"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED not in _kinds(r)

    def test_stable_no_finding(self):
        flags = frozenset({"NODELETE"})
        r = compare(_snap(_elf(dynamic_flags=flags)), _snap(_elf(dynamic_flags=flags)))
        assert ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED not in _kinds(r)


# ── init/fini presence ───────────────────────────────────────────────────────

class TestInitFini:
    def test_init_gained(self):
        r = compare(_snap(_elf(has_init=False)), _snap(_elf(has_init=True)))
        assert ChangeKind.ELF_INIT_FINI_CHANGED in _kinds(r)

    def test_fini_removed(self):
        r = compare(_snap(_elf(has_fini=True)), _snap(_elf(has_fini=False)))
        assert ChangeKind.ELF_INIT_FINI_CHANGED in _kinds(r)

    def test_legacy_uncaptured_skipped(self):
        r = compare(_snap(_elf(has_init=None)), _snap(_elf(has_init=True)))
        assert ChangeKind.ELF_INIT_FINI_CHANGED not in _kinds(r)


# ── NT_GNU_ABI_TAG kernel floor ──────────────────────────────────────────────

class TestKernelFloor:
    def test_raised(self):
        old = _elf(min_kernel_version="3.2.0")
        new = _elf(min_kernel_version="4.4.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED in _kinds(r)

    def test_lowered_is_not_a_finding(self):
        old = _elf(min_kernel_version="4.4.0")
        new = _elf(min_kernel_version="3.2.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED not in _kinds(r)

    def test_absent_note_skipped(self):
        r = compare(_snap(_elf()), _snap(_elf(min_kernel_version="4.4.0")))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED not in _kinds(r)

    def test_decode_abi_tag_desc(self):
        desc = struct.pack("<IIII", 0, 3, 2, 0)
        assert _decode_abi_tag_desc(desc, little_endian=True) == "3.2.0"
        # Non-Linux OS id → no floor.
        assert _decode_abi_tag_desc(struct.pack("<IIII", 1, 3, 2, 0), True) == ""
        # Truncated description → no floor.
        assert _decode_abi_tag_desc(b"\x00" * 8, True) == ""


# ── GNU_PROPERTY_X86_ISA_1_NEEDED baseline ───────────────────────────────────

class TestX86IsaBaseline:
    def test_raised(self):
        old = _elf(gnu_properties=frozenset({"x86-64-v2"}))
        new = _elf(gnu_properties=frozenset({"x86-64-v3"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.X86_ISA_BASELINE_RAISED in _kinds(r)

    def test_lowered_is_not_a_finding(self):
        old = _elf(gnu_properties=frozenset({"x86-64-v3"}))
        new = _elf(gnu_properties=frozenset({"x86-64-v2"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.X86_ISA_BASELINE_RAISED not in _kinds(r)

    def test_absent_old_property_skipped(self):
        # Most toolchains never emit the property; absence means unrecorded.
        old = _elf(gnu_properties=frozenset())
        new = _elf(gnu_properties=frozenset({"x86-64-v3"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.X86_ISA_BASELINE_RAISED not in _kinds(r)

    def test_isa_tokens_do_not_disturb_cet(self):
        old = _elf(gnu_properties=frozenset({"IBT", "SHSTK", "x86-64-v2"}))
        new = _elf(gnu_properties=frozenset({"IBT", "SHSTK", "x86-64-v3"}))
        r = compare(_snap(old), _snap(new))
        kinds = _kinds(r)
        assert ChangeKind.X86_ISA_BASELINE_RAISED in kinds
        assert ChangeKind.CET_PROTECTION_WEAKENED not in kinds
        assert ChangeKind.CET_PROTECTION_IMPROVED not in kinds

    def test_decode_isa_needed_property(self):
        # pr_type=GNU_PROPERTY_X86_ISA_1_NEEDED, datasz=4, bits=v2|v3.
        desc = struct.pack("<III", 0xC0008002, 4, 0x2 | 0x4) + b"\x00" * 4
        tokens = _decode_gnu_property_desc(desc, little_endian=True, align=8)
        assert tokens == frozenset({"x86-64-v2", "x86-64-v3"})


# ── Exported-object alignment ────────────────────────────────────────────────

class TestObjectAlignmentReduced:
    def test_reduced(self):
        old = _elf(symbols=[_obj("g_table", alignment=64)])
        new = _elf(symbols=[_obj("g_table", alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED in _kinds(r)

    def test_increase_is_not_a_finding(self):
        old = _elf(symbols=[_obj("g_table", alignment=8)])
        new = _elf(symbols=[_obj("g_table", alignment=64)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED not in _kinds(r)

    def test_unknown_alignment_skipped(self):
        old = _elf(symbols=[_obj("g_table", alignment=0)])
        new = _elf(symbols=[_obj("g_table", alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED not in _kinds(r)

    def test_functions_are_exempt(self):
        old = _elf(symbols=[_func("do_work", alignment=64)])
        new = _elf(symbols=[_func("do_work", alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED not in _kinds(r)

    def test_value_alignment_helper(self):
        assert _value_alignment(0) == 0
        assert _value_alignment(0x1008) == 8
        assert _value_alignment(0x2000) == 4096  # page cap
        assert _value_alignment(0x3) == 1


# ── Undefined-symbol (import) surface ────────────────────────────────────────

class TestImportSet:
    def test_added_import(self):
        old = _elf(imports=[_imp("memcpy")])
        new = _elf(imports=[_imp("memcpy"), _imp("pthread_create", version="GLIBC_2.34")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.IMPORTED_SYMBOL_ADDED)
        assert "pthread_create" in change.description
        assert "GLIBC_2.34" in change.description

    def test_removed_import_is_compatible(self):
        old = _elf(imports=[_imp("memcpy"), _imp("obsolete_fn")])
        new = _elf(imports=[_imp("memcpy")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_REMOVED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE

    def test_weak_import_added_is_skipped(self):
        old = _elf(imports=[_imp("memcpy")])
        new = _elf(imports=[_imp("memcpy"), _imp("optional_fn", binding=SymbolBinding.WEAK)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED not in _kinds(r)

    def test_empty_side_skipped(self):
        # A side with no captured imports (header-only / legacy) is unknown,
        # not "imports nothing".
        old = _elf(imports=[])
        new = _elf(imports=[_imp("memcpy")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED not in _kinds(r)


# ── Global allocator replacement ─────────────────────────────────────────────

class TestAllocatorReplacement:
    def test_added(self):
        old = _elf(symbols=[_func("api_fn")])
        new = _elf(symbols=[_func("api_fn"), _func("_Znwm"), _func("_ZdlPv")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED in _kinds(r)

    def test_removed(self):
        old = _elf(symbols=[_func("api_fn"), _func("_Znwm")])
        new = _elf(symbols=[_func("api_fn")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED in _kinds(r)

    def test_stable_replacement_no_finding(self):
        old = _elf(symbols=[_func("_Znwm")])
        new = _elf(symbols=[_func("_Znwm"), _func("_ZdaPv")])
        r = compare(_snap(old), _snap(new))
        kinds = _kinds(r)
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED not in kinds
        assert ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED not in kinds

    def test_member_operator_not_matched(self):
        # In-class operator new mangles as _ZN...nwEm — not a global replacement.
        old = _elf(symbols=[_func("api_fn")])
        new = _elf(symbols=[_func("api_fn"), _func("_ZN3Foo3nwEm")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED not in _kinds(r)
