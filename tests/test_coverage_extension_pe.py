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

"""Coverage-extension PE/COFF detectors and PDB calling-convention wiring.

Covers DllCharacteristics hardening drift, delay-load dependency drift,
per-DLL imported-function drift, file-version downgrade, subsystem-version
floor, and the LF_ONEMETHOD → AdvancedDwarfMetadata.calling_conventions
bridge. All tests use synthetic metadata — no real binaries required.
"""
from __future__ import annotations

import struct

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.model import AbiSnapshot
from abicheck.pdb_metadata import _extract_method_calling_conventions
from abicheck.pdb_parser import (
    LF_FIELDLIST,
    LF_MFUNCTION,
    LF_ONEMETHOD,
    LF_PROCEDURE,
    CvOneMethod,
    CvStruct,
    TpiRecord,
    TpiStream,
    TypeDatabase,
)
from abicheck.pe_metadata import PeMetadata


def _snap(pe: PeMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="test.dll",
        version="1.0",
        functions=[],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        pe=pe,
        elf_only_mode=True,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _pe(**kwargs) -> PeMetadata:
    # The hardening detector is gated on both sides carrying PE identity
    # (a real parse always sets machine).
    kwargs.setdefault("machine", "IMAGE_FILE_MACHINE_AMD64")
    return PeMetadata(**kwargs)


_NX = 0x0100
_ASLR = 0x0040
_HEVA = 0x0020
_CFG = 0x4000


# ── DllCharacteristics hardening ─────────────────────────────────────────────

class TestPeHardening:
    def test_weakened(self):
        old = _pe(dll_characteristics=_NX | _ASLR | _CFG)
        new = _pe(dll_characteristics=_NX)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.PE_HARDENING_WEAKENED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.PE_HARDENING_WEAKENED)
        assert "DYNAMIC_BASE" in change.description
        assert "GUARD_CF" in change.description

    def test_improved_is_compatible(self):
        old = _pe(dll_characteristics=_NX)
        new = _pe(dll_characteristics=_NX | _HEVA)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.PE_HARDENING_IMPROVED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE

    def test_unrelated_bits_ignored(self):
        # Only the mitigation bits are diffed; e.g. WDM_DRIVER (0x2000) churn
        # emits neither kind.
        old = _pe(dll_characteristics=_NX | 0x2000)
        new = _pe(dll_characteristics=_NX)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.PE_HARDENING_WEAKENED not in _kinds(r)
        assert ChangeKind.PE_HARDENING_IMPROVED not in _kinds(r)

    def test_parse_failed_side_skipped(self):
        old = PeMetadata(machine="", dll_characteristics=_NX | _ASLR)
        new = _pe(dll_characteristics=0)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.PE_HARDENING_WEAKENED not in _kinds(r)


# ── Delay-load imports ───────────────────────────────────────────────────────

class TestDelayImports:
    def test_added(self):
        old = _pe(delay_imports={})
        new = _pe(delay_imports={"DBGHELP.dll": ["MiniDumpWriteDump"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_ADDED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.NEEDED_ADDED)
        assert "delay-load" in change.description

    def test_removed(self):
        old = _pe(delay_imports={"DBGHELP.dll": ["MiniDumpWriteDump"]})
        new = _pe(delay_imports={})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_REMOVED in _kinds(r)

    def test_uncaptured_legacy_side_skipped(self):
        # A legacy snapshot (delay_imports never captured → None) must not
        # read as "verified no delay imports".
        old = _pe()  # delay_imports defaults to None
        new = _pe(delay_imports={"DBGHELP.dll": ["MiniDumpWriteDump"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_ADDED not in _kinds(r)


# ── Per-DLL imported functions ───────────────────────────────────────────────

class TestPeImportFunctions:
    def test_added_function(self):
        old = _pe(imports={"KERNEL32.dll": ["CreateFileW"]})
        new = _pe(imports={"KERNEL32.dll": ["CreateFileW", "VirtualAlloc2"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.IMPORTED_SYMBOL_ADDED)
        assert "KERNEL32.dll" in change.description

    def test_removed_function(self):
        old = _pe(imports={"KERNEL32.dll": ["CreateFileW", "VirtualAlloc2"]})
        new = _pe(imports={"KERNEL32.dll": ["CreateFileW"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_REMOVED in _kinds(r)

    def test_ordinal_import_added(self):
        old = _pe(imports={"MFC42.dll": ["ordinal:1000"]})
        new = _pe(imports={"MFC42.dll": ["ordinal:1000", "ordinal:1001"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.IMPORTED_SYMBOL_ADDED in _kinds(r)

    def test_new_dll_functions_not_itemized(self):
        # A wholly-new DLL is NEEDED_ADDED; its functions are not itemized.
        old = _pe(imports={"KERNEL32.dll": ["CreateFileW"]})
        new = _pe(imports={"KERNEL32.dll": ["CreateFileW"], "USER32.dll": ["MessageBoxW"]})
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_ADDED in _kinds(r)
        assert ChangeKind.IMPORTED_SYMBOL_ADDED not in _kinds(r)


# ── Version drift ────────────────────────────────────────────────────────────

class TestPeVersions:
    def test_file_version_downgraded(self):
        old = _pe(file_version="2.5.0.0")
        new = _pe(file_version="2.4.9.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.LIBRARY_VERSION_DOWNGRADED in _kinds(r)

    def test_file_version_upgrade_is_fine(self):
        old = _pe(file_version="2.5.0.0")
        new = _pe(file_version="2.6.0.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.LIBRARY_VERSION_DOWNGRADED not in _kinds(r)

    def test_subsystem_floor_raised(self):
        old = _pe(subsystem_version="6.1")
        new = _pe(subsystem_version="10.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED in _kinds(r)

    def test_subsystem_floor_lowered_is_fine(self):
        old = _pe(subsystem_version="10.0")
        new = _pe(subsystem_version="6.1")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED not in _kinds(r)

    def test_uncaptured_versions_skipped(self):
        r = compare(_snap(_pe(file_version="")), _snap(_pe(file_version="1.0.0.0")))
        assert ChangeKind.LIBRARY_VERSION_DOWNGRADED not in _kinds(r)


# ── PDB → calling-convention bridge ─────────────────────────────────────────

def _mfunction_record(ti: int, calling_convention: int) -> TpiRecord:
    # rvtype, classtype, thistype, calltype, funcattr, parmcount, arglist, thisadjust
    payload = struct.pack("<IIIBBHIi", 0x74, 0x1000, 0x1001, calling_convention, 0, 0, 0, 0)
    return TpiRecord(type_index=ti, leaf=LF_MFUNCTION, data=payload)


def _fieldlist_record(ti: int, method_name: bytes, method_ti: int) -> TpiRecord:
    # LF_ONEMETHOD sub-record: leaf(2) + attr(2) + type_ti(4) + name + NUL.
    payload = struct.pack("<HHI", LF_ONEMETHOD, 0, method_ti) + method_name + b"\x00"
    return TpiRecord(type_index=ti, leaf=LF_FIELDLIST, data=payload)


class TestPdbCallingConventionBridge:
    def _typedb(self) -> TypeDatabase:
        tpi = TpiStream(
            type_index_begin=0x1000,
            type_index_end=0x2000,
            records=[
                _mfunction_record(0x1002, 0x0B),  # thiscall
                _fieldlist_record(0x1003, b"Frobnicate", 0x1002),
            ],
        )
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_onemethod_parsed_from_fieldlist(self):
        db = self._typedb()
        members = db.get_fieldlist(0x1003)
        methods = [m for m in members if isinstance(m, CvOneMethod)]
        assert methods and methods[0].name == "Frobnicate"
        assert methods[0].type_ti == 0x1002

    def test_function_calling_convention_lookup(self):
        db = self._typedb()
        assert db.function_calling_convention(0x1002) == 0x0B
        assert db.function_calling_convention(0x9999) is None

    def test_method_cc_lands_in_advanced_metadata(self):
        db = self._typedb()
        cv_struct = CvStruct(
            type_index=0x1004,
            name="Widget",
            field_list_ti=0x1003,
            byte_size=8,
            is_forward_ref=False,
            is_packed=False,
            is_union=False,
            count=1,
        )
        adv = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_method_calling_conventions(db, cv_struct, adv)
        assert adv.calling_conventions == {"Widget::Frobnicate": "thiscall"}

    def test_cc_change_fires_calling_convention_changed(self):
        # End-to-end: two snapshots whose dwarf_advanced metadata came from
        # PDBs where Widget::Frobnicate flipped thiscall → stdcall.
        old_adv = AdvancedDwarfMetadata(has_dwarf=True)
        old_adv.calling_conventions["Widget::Frobnicate"] = "thiscall"
        new_adv = AdvancedDwarfMetadata(has_dwarf=True)
        new_adv.calling_conventions["Widget::Frobnicate"] = "stdcall"

        def snap(adv: AdvancedDwarfMetadata) -> AbiSnapshot:
            return AbiSnapshot(
                library="test.dll",
                version="1.0",
                functions=[],
                variables=[],
                types=[],
                enums=[],
                typedefs={},
                dwarf_advanced=adv,
            )

        r = compare(snap(old_adv), snap(new_adv))
        assert ChangeKind.CALLING_CONVENTION_CHANGED in _kinds(r)

    def test_procedure_calling_convention_lookup(self):
        # LF_PROCEDURE (free-function type): rvtype, calltype, funcattr,
        # parmcount, arglist.
        proc = TpiRecord(
            type_index=0x1005,
            leaf=LF_PROCEDURE,
            data=struct.pack("<IBBHI", 0x74, 0x07, 0, 0, 0),
        )
        db = TypeDatabase(
            TpiStream(type_index_begin=0x1000, type_index_end=0x2000, records=[proc])
        )
        db.parse_all()
        assert db.function_calling_convention(0x1005) == 0x07

    def test_truncated_onemethod_subrecord_is_dropped(self):
        # LF_ONEMETHOD header cut short (only 2 of the 6 attr+type bytes) —
        # the fieldlist parse must bail out, not crash.
        rec = TpiRecord(
            type_index=0x1003,
            leaf=LF_FIELDLIST,
            data=struct.pack("<HH", LF_ONEMETHOD, 0),
        )
        db = TypeDatabase(
            TpiStream(type_index_begin=0x1000, type_index_end=0x2000, records=[rec])
        )
        db.parse_all()
        assert db.get_fieldlist(0x1003) == []

    def test_nameless_onemethod_is_dropped(self):
        rec = _fieldlist_record(0x1003, b"", 0x1002)
        db = TypeDatabase(
            TpiStream(type_index_begin=0x1000, type_index_end=0x2000, records=[rec])
        )
        db.parse_all()
        assert db.get_fieldlist(0x1003) == []

    def test_intro_virtual_onemethod_skips_vbaseoff(self):
        # mprop=4 (intro virtual) carries a 4-byte vbaseoff before the name.
        attr = 4 << 2
        payload = (
            struct.pack("<HHI", LF_ONEMETHOD, attr, 0x1002)
            + struct.pack("<I", 8)
            + b"VirtualOne\x00"
        )
        rec = TpiRecord(type_index=0x1003, leaf=LF_FIELDLIST, data=payload)
        db = TypeDatabase(
            TpiStream(type_index_begin=0x1000, type_index_end=0x2000, records=[rec])
        )
        db.parse_all()
        members = db.get_fieldlist(0x1003)
        assert [m.name for m in members if isinstance(m, CvOneMethod)] == ["VirtualOne"]

    def test_unresolvable_method_type_is_skipped(self):
        # The method's type index resolves to nothing → no CC recorded.
        rec = _fieldlist_record(0x1003, b"Mystery", 0x1FFF)
        db = TypeDatabase(
            TpiStream(type_index_begin=0x1000, type_index_end=0x2000, records=[rec])
        )
        db.parse_all()
        cv_struct = CvStruct(
            type_index=0x1004,
            name="Widget",
            field_list_ti=0x1003,
            byte_size=8,
            is_forward_ref=False,
            is_packed=False,
            is_union=False,
            count=1,
        )
        adv = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_method_calling_conventions(db, cv_struct, adv)
        assert adv.calling_conventions == {}
