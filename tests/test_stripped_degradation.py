"""Stripped binary graceful degradation tests.

Validates scanner behavior when DWARF debug info is absent (as in production
binaries stripped with `strip -g`). Uses synthetic metadata to simulate
stripped vs unstripped scenarios without requiring a compiler.

Tests:
1. No DWARF → DWARF_INFO_MISSING reported
2. Confidence degrades when DWARF is absent
3. Header-only analysis still detects symbol-level changes correctly
4. ELF-only mode (no headers) still produces valid results
5. Graceful handling of partial metadata (ELF present, DWARF absent)
6. Degraded evidence tiers reflect available data sources
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import Confidence
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, elf=None, dwarf=None, elf_only_mode=False):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs={}, elf=elf, dwarf=dwarf,
        elf_only_mode=elf_only_mode,
    )


def _pub_func(name, mangled, ret="void", **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    visibility=Visibility.PUBLIC, **kwargs)


def _elf_func(name, mangled):
    """Function visible only via ELF symbol table (no header)."""
    return Function(name=name, mangled=mangled, return_type="void",
                    visibility=Visibility.ELF_ONLY)


def _kinds(result):
    return {c.kind for c in result.changes}


# ═══════════════════════════════════════════════════════════════════════════
# DWARF Present → Absent (Stripping)
# ═══════════════════════════════════════════════════════════════════════════

class TestDwarfStripped:
    """Old binary has DWARF, new binary stripped — DWARF_INFO_MISSING."""

    def test_dwarf_missing_reported(self):
        """When old has DWARF and new doesn't, report DWARF_INFO_MISSING."""
        old_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=8)},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(has_dwarf=False)

        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.DWARF_INFO_MISSING in _kinds(r)

    def test_dwarf_missing_when_new_has_none(self):
        """Old has DWARF, new has no dwarf field at all."""
        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(name="Data", byte_size=4)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap())
        assert ChangeKind.DWARF_INFO_MISSING in _kinds(r)

    def test_both_no_dwarf_no_missing_report(self):
        """Neither snapshot has DWARF → no DWARF_INFO_MISSING."""
        r = compare(_snap(), _snap())
        assert ChangeKind.DWARF_INFO_MISSING not in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# Confidence Degradation
# ═══════════════════════════════════════════════════════════════════════════

class TestConfidenceDegradation:
    """Confidence should degrade when evidence sources are missing."""

    def test_full_metadata_higher_confidence(self):
        """Headers + ELF + DWARF → higher confidence than headers alone."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(has_dwarf=True)

        full = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        header_only = compare(
            _snap(functions=[f]),
            _snap(functions=[f]),
        )

        conf_rank = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        assert conf_rank[full.confidence] >= conf_rank[header_only.confidence]

    def test_elf_only_lower_than_elf_plus_dwarf(self):
        """ELF without DWARF → lower confidence than ELF + DWARF."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(has_dwarf=True)

        elf_dwarf = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        elf_only = compare(
            _snap(functions=[f], elf=elf),
            _snap(functions=[f], elf=elf),
        )

        conf_rank = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        assert conf_rank[elf_dwarf.confidence] >= conf_rank[elf_only.confidence]


# ═══════════════════════════════════════════════════════════════════════════
# Header-Only Still Detects Symbol Changes
# ═══════════════════════════════════════════════════════════════════════════

class TestHeaderOnlyDetection:
    """Without DWARF/ELF, header analysis should still catch symbol changes."""

    def test_func_removed_detected_without_elf(self):
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap())
        assert ChangeKind.FUNC_REMOVED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_return_type_change_detected_without_elf(self):
        f_old = _pub_func("api", "_Z3apiv", ret="int")
        f_new = _pub_func("api", "_Z3apiv", ret="long")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(r)

    def test_type_size_change_detected_without_dwarf(self):
        t_old = RecordType(name="Config", kind="struct", size_bits=64)
        t_new = RecordType(name="Config", kind="struct", size_bits=128)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_SIZE_CHANGED in _kinds(r)

    def test_enum_change_detected_without_dwarf(self):
        e_old = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 1)])
        e_new = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 42)])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# ELF-Only Mode (No Headers)
# ═══════════════════════════════════════════════════════════════════════════

class TestElfOnlyMode:
    """Snapshots with elf_only_mode=True (no headers, only ELF symbols)."""

    def test_elf_only_mode_basic_detection(self):
        """ELF-only mode still detects symbol-level changes."""
        f = _elf_func("api", "_Z3apiv")
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        new_elf = ElfMetadata(symbols=[])

        r = compare(
            _snap(functions=[f], elf=old_elf, elf_only_mode=True),
            _snap(elf=new_elf, elf_only_mode=True),
        )
        # In elf_only mode, removal produces FUNC_REMOVED_ELF_ONLY
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_elf_only_lower_confidence(self):
        """ELF-only mode should have lower confidence than header+ELF."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )

        with_headers = compare(
            _snap(functions=[f], elf=elf),
            _snap(functions=[f], elf=elf),
        )
        elf_f = _elf_func("api", "_Z3apiv")
        without_headers = compare(
            _snap(functions=[elf_f], elf=elf, elf_only_mode=True),
            _snap(functions=[elf_f], elf=elf, elf_only_mode=True),
        )

        conf_rank = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        assert conf_rank[with_headers.confidence] >= conf_rank[without_headers.confidence]


# ═══════════════════════════════════════════════════════════════════════════
# Partial Metadata Handling
# ═══════════════════════════════════════════════════════════════════════════

class TestPartialMetadata:
    """Graceful handling when metadata is partially available."""

    def test_elf_present_dwarf_absent(self):
        """ELF metadata present, DWARF absent — should not crash."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            soname="libtest.so.1",
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(
            _snap(functions=[f], elf=elf),
            _snap(functions=[f], elf=elf),
        )
        assert r.verdict == Verdict.NO_CHANGE
        assert "elf" in r.evidence_tiers
        assert "dwarf" not in r.evidence_tiers

    def test_dwarf_present_elf_absent(self):
        """DWARF metadata present, ELF absent — should not crash."""
        dwarf = DwarfMetadata(
            structs={"Data": StructLayout(name="Data", byte_size=8)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=dwarf), _snap(dwarf=dwarf))
        assert r.verdict == Verdict.NO_CHANGE
        assert "dwarf" in r.evidence_tiers

    def test_old_has_elf_new_has_none(self):
        """Old snapshot has ELF, new has nothing — graceful degradation."""
        f = _pub_func("api", "_Z3apiv")
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(
            _snap(functions=[f], elf=old_elf),
            _snap(functions=[f]),
        )
        # Asymmetric metadata should degrade gracefully, not crash or break
        assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE,
                              Verdict.COMPATIBLE_WITH_RISK)


# ═══════════════════════════════════════════════════════════════════════════
# Evidence Tiers Reflect Available Data
# ═══════════════════════════════════════════════════════════════════════════

class TestEvidenceTiersReflection:
    """Evidence tiers should accurately reflect what data was available."""

    def test_empty_snapshot_minimal_tiers(self):
        r = compare(_snap(), _snap())
        assert isinstance(r.evidence_tiers, list)

    def test_header_tier_present_with_functions(self):
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert "header" in r.evidence_tiers

    def test_elf_tier_present_with_elf_metadata(self):
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(_snap(elf=elf), _snap(elf=elf))
        assert "elf" in r.evidence_tiers

    def test_dwarf_tier_present_with_dwarf_metadata(self):
        dwarf = DwarfMetadata(
            structs={"S": StructLayout(name="S", byte_size=4)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=dwarf), _snap(dwarf=dwarf))
        assert "dwarf" in r.evidence_tiers


# ═══════════════════════════════════════════════════════════════════════════
# Coverage Warnings
# ═══════════════════════════════════════════════════════════════════════════

class TestCoverageWarnings:
    """Coverage warnings should inform users about detection limitations."""

    def test_no_binary_metadata_warns(self):
        """Header-only analysis should warn about missing binary data."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        # Should have warnings about missing metadata
        assert len(r.coverage_warnings) > 0

    def test_elf_plus_dwarf_fewer_warnings(self):
        """With ELF + DWARF, fewer warnings expected."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(has_dwarf=True)

        full = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        header_only = compare(
            _snap(functions=[f]),
            _snap(functions=[f]),
        )

        # Full metadata should have fewer or equal warnings
        assert len(full.coverage_warnings) <= len(header_only.coverage_warnings)
