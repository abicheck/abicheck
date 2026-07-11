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
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_platform_elf_dynamic import _kernel_version_tuple
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
    _decode_abi_tag_desc,
    _decode_gnu_property_desc,
    _parse_abi_tag,
    _parse_dynamic,
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


def _common(name: str, alignment: int = 0) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        binding=SymbolBinding.GLOBAL,
        sym_type=SymbolType.COMMON,
        size=8,
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

    def test_unparseable_floor_skipped(self):
        # A malformed captured floor must not crash or report.
        old = _elf(min_kernel_version="3.2.0")
        new = _elf(min_kernel_version="not.a.version")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED not in _kinds(r)

    def test_kernel_version_tuple(self):
        assert _kernel_version_tuple("3.2.0") == (3, 2, 0)
        assert _kernel_version_tuple("not.a.version") is None


class _FakeNoteElf:
    """Minimal stand-in for pyelftools' ELFFile: one .note.ABI-tag section."""

    little_endian = True

    def __init__(self, notes):
        self._notes = notes

    def get_section_by_name(self, name):
        return SimpleNamespace(iter_notes=lambda: iter(self._notes))


class TestParseAbiTag:
    def test_reads_floor_from_gnu_note(self):
        desc = struct.pack("<IIII", 0, 3, 2, 0)
        meta = ElfMetadata()
        notes = [
            {"n_type": 99, "n_name": "GNU"},  # wrong type → skipped
            {"n_type": 1, "n_name": "Linux"},  # wrong owner → skipped
            {"n_type": 1, "n_name": "GNU"},  # descriptor missing → skipped
            {"n_type": 1, "n_name": "GNU", "n_descdata": desc},
        ]
        _parse_abi_tag(_FakeNoteElf(notes), meta, Path("lib.so"))
        assert meta.min_kernel_version == "3.2.0"

    def test_string_desc_is_re_encoded(self):
        # pyelftools sometimes hands the descriptor back as a latin-1 string.
        desc = struct.pack("<IIII", 0, 2, 6, 32).decode("latin-1")
        meta = ElfMetadata()
        notes = [{"n_type": "NT_GNU_ABI_TAG", "n_name": "GNU", "n_descdata": desc}]
        _parse_abi_tag(_FakeNoteElf(notes), meta, Path("lib.so"))
        assert meta.min_kernel_version == "2.6.32"

    def test_non_linux_note_leaves_floor_unset(self):
        desc = struct.pack("<IIII", 1, 3, 2, 0)  # os_id 1 = not Linux
        meta = ElfMetadata()
        notes = [{"n_type": 1, "n_name": "GNU", "n_descdata": desc}]
        _parse_abi_tag(_FakeNoteElf(notes), meta, Path("lib.so"))
        assert meta.min_kernel_version == ""

    def test_missing_section_is_noop(self):
        elf = SimpleNamespace(
            little_endian=True, get_section_by_name=lambda name: None
        )
        meta = ElfMetadata()
        _parse_abi_tag(elf, meta, Path("lib.so"))
        assert meta.min_kernel_version == ""

    def test_note_read_error_is_swallowed(self):
        def _boom():
            raise OSError("bad note segment")

        elf = SimpleNamespace(
            little_endian=True,
            get_section_by_name=lambda name: SimpleNamespace(iter_notes=_boom),
        )
        meta = ElfMetadata()
        _parse_abi_tag(elf, meta, Path("lib.so"))  # must not raise
        assert meta.min_kernel_version == ""


class _FakeTag:
    def __init__(self, d_tag, d_val=0):
        self.entry = SimpleNamespace(d_tag=d_tag, d_val=d_val)


class TestParseDynamicFlags:
    def test_origin_and_flags1_bits_collected(self):
        meta = ElfMetadata()
        tags = [
            _FakeTag("DT_FLAGS", 0x1),  # DF_ORIGIN
            _FakeTag("DT_FLAGS_1", 0x8 | 0x10 | 0x80),  # NODELETE | NOOPEN | ORIGIN
        ]
        _parse_dynamic(SimpleNamespace(iter_tags=lambda: iter(tags)), meta)
        assert meta.dynamic_flags == frozenset({"ORIGIN", "NODELETE", "NOOPEN"})

    def test_no_flag_tags_captures_empty(self):
        meta = ElfMetadata()
        _parse_dynamic(SimpleNamespace(iter_tags=lambda: iter([])), meta)
        assert meta.dynamic_flags == frozenset()
        assert meta.has_init is False
        assert meta.has_fini is False


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

    def test_absent_old_property_is_baseline(self):
        # Both sides are captured ELF: an absent old ISA note is not
        # "unrecorded" but "no declared micro-arch floor" = baseline x86-64, so
        # the common baseline → v3 rebuild reports.
        old = _elf(gnu_properties=frozenset())
        new = _elf(gnu_properties=frozenset({"x86-64-v3"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.X86_ISA_BASELINE_RAISED in _kinds(r)

    def test_new_baseline_only_is_not_a_raise(self):
        # An added note that only declares plain x86-64 is not a raise.
        old = _elf(gnu_properties=frozenset())
        new = _elf(gnu_properties=frozenset({"x86-64-baseline"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.X86_ISA_BASELINE_RAISED not in _kinds(r)

    def test_legacy_side_without_elf_identity_skipped(self):
        # No captured identity → the note's absence is unknown, not baseline.
        old = ElfMetadata(gnu_properties=frozenset())
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

    def test_common_tentative_definition_reduced(self):
        # STT_COMMON exports are copy-relocation data, like OBJECT/TLS — an
        # alignment drop on a retained COMMON variable is still a hazard.
        old = _elf(symbols=[_common("g_pool", alignment=64)])
        new = _elf(symbols=[_common("g_pool", alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED in _kinds(r)

    # Compiler-emitted RTTI/vtable objects (_ZTV/_ZTI/_ZTS/_ZTT) carry an
    # st_value alignment that is a linker-placement artifact of the mangled
    # name-string length, not a declared data-object alignment. A drop there is
    # noise (observed: 21 spurious findings across a pvxs patch release), so it
    # must not fire — mirroring the _ZT* exemption in the size detector. One
    # representative of each of the four prefixes, plus the smallest possible
    # bare-prefix name, to pin the check to the prefix rather than a full mangling.
    @pytest.mark.parametrize("rtti", [
        "_ZTVN4pvxs6server6OpBaseE",              # vtable
        "_ZTIN4pvxs4impl6evbase3PvtE",            # typeinfo
        "_ZTSN4pvxs6client12SubscriptionE",       # typeinfo name
        "_ZTTN4pvxs3fooE",                        # VTT
        "_ZTV1A", "_ZTI1A", "_ZTS1A", "_ZTT1A",   # minimal manglings
    ])
    def test_rtti_symbols_are_exempt(self, rtti):
        old = _elf(symbols=[_obj(rtti, alignment=2048)])
        new = _elf(symbols=[_obj(rtti, alignment=32)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED not in _kinds(r), rtti

    def test_real_mangled_data_object_still_fires(self):
        # The exemption is by RTTI prefix, not "looks mangled": a genuine
        # namespace-scoped global variable (_ZN…E, not _ZT*) is real data whose
        # alignment drop IS an ABI hazard and must still be reported.
        sym = "_ZN4pvxs6detail13g_lookup_tableE"
        old = _elf(symbols=[_obj(sym, alignment=64)])
        new = _elf(symbols=[_obj(sym, alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED in _kinds(r)

    def test_rtti_exemption_is_per_symbol_not_global(self):
        # An RTTI object and a real object both drop alignment in the same diff.
        # The RTTI one is suppressed while the real one still fires — the exemption
        # must not blanket-suppress the whole finding kind for the comparison.
        old = _elf(symbols=[_obj("_ZTV1A", alignment=2048), _obj("g_real", alignment=64)])
        new = _elf(symbols=[_obj("_ZTV1A", alignment=32), _obj("g_real", alignment=8)])
        r = compare(_snap(old), _snap(new))
        hits = [c for c in r.changes
                if c.kind == ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED]
        assert len(hits) == 1
        assert (hits[0].symbol or hits[0].name) == "g_real"

    def test_rtti_increase_still_not_flagged(self):
        # Direction guard also holds for RTTI names (an increase never fires,
        # exempt or not).
        old = _elf(symbols=[_obj("_ZTI1A", alignment=32)])
        new = _elf(symbols=[_obj("_ZTI1A", alignment=2048)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED not in _kinds(r)

    def test_tls_object_reduced_fires(self):
        # TLS data participates in the ABI like OBJECT/COMMON; a real (non-RTTI)
        # TLS symbol's alignment drop is a hazard and must fire.
        def _tls(name, alignment):
            return ElfSymbol(name=name, binding=SymbolBinding.GLOBAL,
                             sym_type=SymbolType.TLS, size=8, value_alignment=alignment)
        old = _elf(symbols=[_tls("tls_buf", alignment=64)])
        new = _elf(symbols=[_tls("tls_buf", alignment=8)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED in _kinds(r)

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

    def test_weak_to_strong_import_is_added_obligation(self):
        # A persisting import going weak → strong becomes a hard requirement the
        # loader must satisfy (the weak form resolved to null); report it.
        old = _elf(imports=[_imp("optional_fn", binding=SymbolBinding.WEAK)])
        new = _elf(imports=[_imp("optional_fn", binding=SymbolBinding.GLOBAL)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.IMPORTED_SYMBOL_ADDED)
        assert "weak" in change.description

    def test_strong_to_weak_import_not_flagged(self):
        # The relaxing direction (strong → weak) is not a new obligation.
        old = _elf(imports=[_imp("optional_fn", binding=SymbolBinding.GLOBAL)])
        new = _elf(imports=[_imp("optional_fn", binding=SymbolBinding.WEAK)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED not in _kinds(r)

    def test_first_import_gained(self):
        # A parsed ELF with zero undefined symbols is real evidence of
        # "imports nothing" — gaining the first import must report.
        old = _elf(imports=[])
        new = _elf(imports=[_imp("memcpy")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED in _kinds(r)

    def test_last_import_dropped(self):
        old = _elf(imports=[_imp("memcpy")])
        new = _elf(imports=[])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_REMOVED in _kinds(r)

    def test_legacy_side_without_elf_identity_skipped(self):
        # A legacy/header-only baseline (machine never captured) is unknown,
        # not "imports nothing" — no fabricated finding.
        old = ElfMetadata(imports=[])
        new = _elf(imports=[_imp("memcpy")])
        r = compare(_snap(old), _snap(new))
        kinds = _kinds(r)
        assert ChangeKind.IMPORTED_SYMBOL_ADDED not in kinds
        assert ChangeKind.IMPORTED_SYMBOL_REMOVED not in kinds


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

    def test_empty_export_table_is_evidence(self):
        # Zero exports on the old side is still a parsed fact; gaining a
        # global operator new from nothing reports.
        old = _elf(symbols=[])
        new = _elf(symbols=[_func("_Znwm")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED in _kinds(r)

    def test_placement_operators_not_matched(self):
        # Placement new/delete (operator new(size,void*) / delete(void*,void*))
        # do not replace the global allocator — adding them is not a finding.
        old = _elf(symbols=[_func("api_fn")])
        new = _elf(
            symbols=[_func("api_fn"), _func("_ZnwmPv"), _func("_ZdlPvS_")]
        )
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED not in _kinds(r)

    def test_sized_and_aligned_delete_still_matched(self):
        # Sized delete(void*, size_t) and aligned new/delete ARE replaceable
        # global forms and must not be excluded by the placement filter.
        old = _elf(symbols=[_func("api_fn")])
        new = _elf(symbols=[_func("api_fn"), _func("_ZdlPvm")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED in _kinds(r)

    def test_legacy_side_without_elf_identity_skipped(self):
        old = ElfMetadata(symbols=[])
        new = _elf(symbols=[_func("_Znwm")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED not in _kinds(r)
