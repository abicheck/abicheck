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

"""Binary-only (no PDB / no DWARF) detector coverage for PE and Mach-O.

These detectors run from header / export-table metadata alone:
  * PE  : ordinal reassignment, forwarder repoint, machine/arch drift
  * Mach-O: CPU type / architecture drift

Plus a confidence-labelling check: the L0 vtable / RTTI size inferences are
``MEDIUM`` confidence (derived from symbol size, not authoritative).

All snapshots are built in memory — no real binaries required.
"""
from __future__ import annotations

import pytest

from abicheck.checker import _diff_macho, _diff_pe
from abicheck.checker_policy import BREAKING_KINDS, ChangeKind, Confidence
from abicheck.diff_elf_layout import _diff_elf_layout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.pe_metadata import PeExport, PeMetadata, PeSymbolType

try:
    from abicheck.macho_metadata import MachoMetadata
    HAS_MACHO = True
except ImportError:  # pragma: no cover - macholib always present in dev
    HAS_MACHO = False


def _pe_snap(exports=None, machine="", imports=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="test.dll", version="1.0",
        pe=PeMetadata(exports=exports or [], machine=machine, imports=imports or {}),
    )


def _kinds(changes) -> set[ChangeKind]:
    return {c.kind for c in changes}


def _by_kind(changes, kind):
    return [c for c in changes if c.kind == kind]


# ═══════════════════════════════════════════════════════════════════════════
# PE ordinal churn must NOT be flagged
# ═══════════════════════════════════════════════════════════════════════════

class TestPeOrdinalChurnIsBenign:
    """PE ordinals are auto-assigned sequentially: adding/removing an export
    renumbers everything after it. That benign churn must never be a finding —
    name-bound clients (the common case) are unaffected, and ordinal-only
    exports are keyed by ordinal so a genuine reorder is already a remove+add."""

    def test_named_export_ordinal_shift_is_not_flagged(self) -> None:
        old = _pe_snap([PeExport(name="foo", ordinal=3)])
        new = _pe_snap([PeExport(name="foo", ordinal=7)])
        # No PE-specific finding at all; certainly nothing breaking.
        assert _diff_pe(old, new) == []

    def test_insertion_shifting_ordinals_is_only_an_addition(self) -> None:
        old = _pe_snap([PeExport(name="get_version", ordinal=1)])
        new = _pe_snap([PeExport(name="get_build", ordinal=1),
                        PeExport(name="get_version", ordinal=2)])
        kinds = _kinds(_diff_pe(old, new))
        assert kinds == {ChangeKind.FUNC_ADDED}


# ═══════════════════════════════════════════════════════════════════════════
# PE forwarder stability
# ═══════════════════════════════════════════════════════════════════════════

class TestPeForwarderChanged:
    def test_forwarder_retargeted_is_breaking(self) -> None:
        old = _pe_snap([PeExport(name="alloc", ordinal=1,
                                 sym_type=PeSymbolType.FORWARDED,
                                 forwarder="NTDLL.RtlAllocateHeap")])
        new = _pe_snap([PeExport(name="alloc", ordinal=1,
                                 sym_type=PeSymbolType.FORWARDED,
                                 forwarder="KERNEL32.HeapAlloc")])
        hits = _by_kind(_diff_pe(old, new), ChangeKind.PE_FORWARDER_CHANGED)
        assert len(hits) == 1
        assert hits[0].old_value == "NTDLL.RtlAllocateHeap"
        assert hits[0].new_value == "KERNEL32.HeapAlloc"
        assert ChangeKind.PE_FORWARDER_CHANGED in BREAKING_KINDS

    def test_direct_export_becomes_forwarder(self) -> None:
        old = _pe_snap([PeExport(name="alloc", ordinal=1)])
        new = _pe_snap([PeExport(name="alloc", ordinal=1,
                                 sym_type=PeSymbolType.FORWARDED,
                                 forwarder="NTDLL.RtlAllocateHeap")])
        hits = _by_kind(_diff_pe(old, new), ChangeKind.PE_FORWARDER_CHANGED)
        assert len(hits) == 1
        assert hits[0].new_value == "NTDLL.RtlAllocateHeap"

    def test_unchanged_forwarder_is_not_flagged(self) -> None:
        e = dict(name="alloc", ordinal=1, sym_type=PeSymbolType.FORWARDED,
                 forwarder="NTDLL.RtlAllocateHeap")
        old = _pe_snap([PeExport(**e)])
        new = _pe_snap([PeExport(**e)])
        assert ChangeKind.PE_FORWARDER_CHANGED not in _kinds(_diff_pe(old, new))

    def test_ordinal_only_forwarder_repoint_is_caught(self) -> None:
        # Nameless export at the SAME ordinal whose forwarder target is silently
        # redirected — keyed by ordinal so the retained-export loop still sees it.
        old = _pe_snap([PeExport(name="", ordinal=5,
                                 sym_type=PeSymbolType.FORWARDED,
                                 forwarder="NTDLL.RtlAllocateHeap")])
        new = _pe_snap([PeExport(name="", ordinal=5,
                                 sym_type=PeSymbolType.FORWARDED,
                                 forwarder="KERNEL32.HeapAlloc")])
        hits = _by_kind(_diff_pe(old, new), ChangeKind.PE_FORWARDER_CHANGED)
        assert len(hits) == 1
        assert hits[0].symbol == "ordinal:5"
        assert hits[0].old_value == "NTDLL.RtlAllocateHeap"
        assert hits[0].new_value == "KERNEL32.HeapAlloc"


# ═══════════════════════════════════════════════════════════════════════════
# PE machine / architecture drift
# ═══════════════════════════════════════════════════════════════════════════

class TestPeMachineChanged:
    def test_machine_drift_is_breaking(self) -> None:
        old = _pe_snap([PeExport(name="foo", ordinal=1)],
                       machine="IMAGE_FILE_MACHINE_AMD64")
        new = _pe_snap([PeExport(name="foo", ordinal=1)],
                       machine="IMAGE_FILE_MACHINE_ARM64")
        hits = _by_kind(_diff_pe(old, new), ChangeKind.PE_MACHINE_CHANGED)
        assert len(hits) == 1
        assert hits[0].old_value == "IMAGE_FILE_MACHINE_AMD64"
        assert hits[0].new_value == "IMAGE_FILE_MACHINE_ARM64"
        assert ChangeKind.PE_MACHINE_CHANGED in BREAKING_KINDS

    def test_same_machine_is_not_flagged(self) -> None:
        old = _pe_snap(machine="IMAGE_FILE_MACHINE_AMD64")
        new = _pe_snap(machine="IMAGE_FILE_MACHINE_AMD64")
        assert ChangeKind.PE_MACHINE_CHANGED not in _kinds(_diff_pe(old, new))

    def test_unknown_machine_either_side_is_not_flagged(self) -> None:
        old = _pe_snap(machine="")
        new = _pe_snap(machine="IMAGE_FILE_MACHINE_ARM64")
        assert ChangeKind.PE_MACHINE_CHANGED not in _kinds(_diff_pe(old, new))


# ═══════════════════════════════════════════════════════════════════════════
# Mach-O CPU type / architecture drift
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_MACHO, reason="macholib not available")
class TestMachoCpuTypeChanged:
    def _snap(self, cpu_type) -> AbiSnapshot:
        return AbiSnapshot(library="lib.dylib", version="1.0",
                           macho=MachoMetadata(cpu_type=cpu_type))

    def test_cpu_drift_is_breaking(self) -> None:
        changes = _diff_macho(self._snap("X86_64"), self._snap("ARM64"))
        hits = _by_kind(changes, ChangeKind.MACHO_CPU_TYPE_CHANGED)
        assert len(hits) == 1
        assert hits[0].old_value == "X86_64"
        assert hits[0].new_value == "ARM64"
        assert ChangeKind.MACHO_CPU_TYPE_CHANGED in BREAKING_KINDS

    def test_same_cpu_is_not_flagged(self) -> None:
        changes = _diff_macho(self._snap("ARM64"), self._snap("ARM64"))
        assert ChangeKind.MACHO_CPU_TYPE_CHANGED not in _kinds(changes)

    def test_unknown_cpu_either_side_is_not_flagged(self) -> None:
        changes = _diff_macho(self._snap(""), self._snap("ARM64"))
        assert ChangeKind.MACHO_CPU_TYPE_CHANGED not in _kinds(changes)

    def _fat(self, *arches: str) -> AbiSnapshot:
        return AbiSnapshot(library="lib.dylib", version="1.0",
                           macho=MachoMetadata(cpu_type=arches[0],
                                               cpu_types=list(arches)))

    def test_single_to_universal_is_not_flagged(self) -> None:
        # x86_64 dylib replaced by a universal x86_64+ARM64 dylib: the original
        # slice is still present, so no architecture was removed.
        changes = _diff_macho(self._fat("X86_64"), self._fat("ARM64", "X86_64"))
        assert ChangeKind.MACHO_CPU_TYPE_CHANGED not in _kinds(changes)

    def test_universal_dropping_a_slice_is_breaking(self) -> None:
        changes = _diff_macho(self._fat("X86_64", "ARM64"), self._fat("ARM64"))
        hits = _by_kind(changes, ChangeKind.MACHO_CPU_TYPE_CHANGED)
        assert len(hits) == 1
        assert "X86_64" in hits[0].description

    def test_cpu_types_survive_serialization_round_trip(self) -> None:
        # Regression: the slice list must round-trip, or a reloaded universal
        # snapshot would fall back to the single selected slice and falsely
        # report a removed architecture.
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
        snap = self._fat("ARM64", "X86_64")
        restored = snapshot_from_dict(snapshot_to_dict(snap))
        assert restored.macho is not None
        assert set(restored.macho.cpu_types) == {"ARM64", "X86_64"}


# ═══════════════════════════════════════════════════════════════════════════
# Confidence labelling on L0 (size-derived) C++ inferences
# ═══════════════════════════════════════════════════════════════════════════

def _elf_snap(*symbols: ElfSymbol) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version="1",
                       elf=ElfMetadata(symbols=list(symbols), pointer_size=8))


def _rtti(name: str, size: int) -> ElfSymbol:
    return ElfSymbol(name=name, sym_type=SymbolType.OBJECT, size=size)


class TestL0Confidence:
    def test_vtable_slot_change_is_medium_confidence(self) -> None:
        old = _elf_snap(_rtti("_ZTV3Foo", 32))
        new = _elf_snap(_rtti("_ZTV3Foo", 48))
        hits = _by_kind(_diff_elf_layout(old, new),
                        ChangeKind.VTABLE_SLOT_COUNT_CHANGED)
        assert len(hits) == 1
        # Inferred from symbol size alone — not authoritative.
        assert hits[0].confidence == Confidence.MEDIUM

    def test_rtti_inheritance_change_is_medium_confidence(self) -> None:
        old = _elf_snap(_rtti("_ZTI3Foo", 16))
        new = _elf_snap(_rtti("_ZTI3Foo", 24))
        hits = _by_kind(_diff_elf_layout(old, new),
                        ChangeKind.RTTI_INHERITANCE_CHANGED)
        assert len(hits) == 1
        assert hits[0].confidence == Confidence.MEDIUM
