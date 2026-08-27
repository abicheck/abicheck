"""Multi-detector interaction tests — deduplication and cross-detector behavior.

Verifies that:
1. AST + DWARF duplicate changes are properly deduplicated
2. Multiple detectors firing on the same symbol produce correct results
3. Redundancy filtering doesn't lose unique changes
4. Post-processing preserves root cause information
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, elf=None, dwarf=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf, dwarf=dwarf,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


def _all_kinds(result):
    """All kinds including redundant changes."""
    return {c.kind for c in result.changes + result.redundant_changes}


# ═══════════════════════════════════════════════════════════════════════════
# AST + DWARF Deduplication
# ═══════════════════════════════════════════════════════════════════════════

class TestAstDwarfDedup:
    """When both header AST and DWARF report the same type change, dedup."""

    def test_type_size_change_deduped_with_dwarf(self):
        """TYPE_SIZE_CHANGED from AST + STRUCT_SIZE_CHANGED from DWARF → one root."""
        t_old = RecordType(name="Config", kind="struct", size_bits=64,
                           fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)])
        t_new = RecordType(name="Config", kind="struct", size_bits=128,
                           fields=[TypeField("a", "int", 0), TypeField("b", "int", 32),
                                   TypeField("c", "long", 64)])

        old_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=8)},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=16)},
            has_dwarf=True,
        )

        r = compare(
            _snap(types=[t_old], dwarf=old_dwarf),
            _snap(types=[t_new], dwarf=new_dwarf),
        )

        # Both AST and DWARF detect the size change
        all_k = _all_kinds(r)
        # At least one should survive dedup; the redundant one may be filtered
        assert (ChangeKind.TYPE_SIZE_CHANGED in all_k or
                ChangeKind.STRUCT_SIZE_CHANGED in all_k)
        # Verdict should be BREAKING regardless
        assert r.verdict == Verdict.BREAKING

    def test_type_size_change_still_caught_when_new_side_lacks_dwarf(self):
        """DWARF_INFO_MISSING does not mean layout coverage dropped to zero.

        Codex review, PR #882, fresh evidence: _diff_types() compares
        struct/enum layout from header-AST evidence (AbiSnapshot.types)
        independently of DWARF, and still runs when both sides carry that
        evidence — so a real layout change is still caught even when the
        new binary lacks DWARF (only the DWARF-derived comparison itself
        is skipped, and DWARF_INFO_MISSING discloses that). Pins the
        counterexample the dwarf_info_missing impact text now names,
        rather than only asserting it in prose.
        """
        t_old = RecordType(name="Config", kind="struct", size_bits=64,
                           fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)])
        t_new = RecordType(name="Config", kind="struct", size_bits=128,
                           fields=[TypeField("a", "int", 0), TypeField("b", "int", 32),
                                   TypeField("c", "long", 64)])

        old_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=8)},
            has_dwarf=True,
        )
        # New side has header-AST type evidence but no DWARF at all.
        r = compare(
            _snap(types=[t_old], dwarf=old_dwarf),
            _snap(types=[t_new]),
        )

        kinds = _kinds(r)
        assert ChangeKind.DWARF_INFO_MISSING in kinds
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds
        assert r.verdict == Verdict.BREAKING

    def test_field_offset_deduped_with_dwarf(self):
        """TYPE_FIELD_OFFSET_CHANGED + STRUCT_FIELD_OFFSET_CHANGED → dedup."""
        t_old = RecordType(name="Data", kind="struct", size_bits=96,
                           fields=[TypeField("x", "int", 0), TypeField("y", "int", 32),
                                   TypeField("z", "int", 64)])
        t_new = RecordType(name="Data", kind="struct", size_bits=96,
                           fields=[TypeField("x", "int", 0), TypeField("y", "int", 48),
                                   TypeField("z", "int", 64)])

        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=12,
                fields=[FieldInfo("x", "int", 0, 4),
                        FieldInfo("y", "int", 4, 4),
                        FieldInfo("z", "int", 8, 4)])},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=12,
                fields=[FieldInfo("x", "int", 0, 4),
                        FieldInfo("y", "int", 6, 4),
                        FieldInfo("z", "int", 8, 4)])},
            has_dwarf=True,
        )

        r = compare(
            _snap(types=[t_old], dwarf=old_dwarf),
            _snap(types=[t_new], dwarf=new_dwarf),
        )

        all_k = _all_kinds(r)
        assert (ChangeKind.TYPE_FIELD_OFFSET_CHANGED in all_k or
                ChangeKind.STRUCT_FIELD_OFFSET_CHANGED in all_k)
        assert r.verdict == Verdict.BREAKING


# ═══════════════════════════════════════════════════════════════════════════
# Redundancy Filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestRedundancyFiltering:
    """Root-cause filtering: derived changes should reference root type."""

    def test_type_size_change_is_root_for_field_changes(self):
        """When type size changes, individual field changes are redundant."""
        t_old = RecordType(name="Cfg", kind="struct", size_bits=64,
                           fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=96,
                           fields=[TypeField("a", "long", 0), TypeField("b", "int", 64)])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        # TYPE_SIZE_CHANGED should be the root; field changes may be redundant
        visible = _kinds(r)

        # Root change should be visible (not relegated to redundant_changes)
        assert ChangeKind.TYPE_SIZE_CHANGED in visible
        # Verdict should reflect the worst change
        assert r.verdict == Verdict.BREAKING

    def test_different_types_not_redundant(self):
        """Changes to different types should NOT be collapsed."""
        t1_old = RecordType(name="TypeA", kind="struct", size_bits=32,
                            fields=[TypeField("x", "int", 0)])
        t1_new = RecordType(name="TypeA", kind="struct", size_bits=64,
                            fields=[TypeField("x", "long", 0)])

        t2_old = RecordType(name="TypeB", kind="struct", size_bits=32,
                            fields=[TypeField("y", "int", 0)])
        t2_new = RecordType(name="TypeB", kind="struct", size_bits=64,
                            fields=[TypeField("y", "long", 0)])

        r = compare(
            _snap(types=[t1_old, t2_old]),
            _snap(types=[t1_new, t2_new]),
        )

        # Both type changes should survive (different root types)
        type_size_changes = [c for c in r.changes + r.redundant_changes
                             if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(type_size_changes) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Multiple Detectors on Same Symbol
# ═══════════════════════════════════════════════════════════════════════════

class TestMultipleDetectorsSameSymbol:
    """Multiple detectors report changes for the same symbol."""

    def test_func_return_and_virtual_changed(self):
        """Return type changed + virtual status changed on same function."""
        f_old = _pub_func("Base::render", "_ZN4Base6renderEv",
                          ret="void", is_virtual=False)
        f_new = _pub_func("Base::render", "_ZN4Base6renderEv",
                          ret="int", is_virtual=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        kind_set = _kinds(r)
        assert ChangeKind.FUNC_RETURN_CHANGED in kind_set
        assert ChangeKind.FUNC_VIRTUAL_ADDED in kind_set

    def test_var_type_and_elf_size_changed(self):
        """Variable type change detected at header level + ELF size change."""
        v_old = Variable(name="data", mangled="_Z4datav", type="int",
                         visibility=Visibility.PUBLIC)
        v_new = Variable(name="data", mangled="_Z4datav", type="long",
                         visibility=Visibility.PUBLIC)

        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="_Z4datav", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.OBJECT, size=4)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="_Z4datav", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.OBJECT, size=8)])

        r = compare(
            _snap(variables=[v_old], elf=old_elf),
            _snap(variables=[v_new], elf=new_elf),
        )

        all_k = _all_kinds(r)
        assert ChangeKind.VAR_TYPE_CHANGED in all_k
        assert ChangeKind.SYMBOL_SIZE_CHANGED in all_k


# ═══════════════════════════════════════════════════════════════════════════
# ELF + Header Consistency
# ═══════════════════════════════════════════════════════════════════════════

class TestElfHeaderConsistency:
    """Verify ELF and header detectors agree."""

    def test_func_in_headers_and_elf_both_removed(self):
        """Function removed from both headers and ELF → single FUNC_REMOVED."""
        f = _pub_func("old_api", "_Z7old_apiv")
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z7old_apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[])

        r = compare(
            _snap(functions=[f], elf=old_elf),
            _snap(functions=[], elf=new_elf),
        )

        # Should not double-count the removal
        removed = [c for c in r.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 1

    def test_no_duplicate_var_removal(self):
        """Variable removed from headers and ELF → single report."""
        v = Variable(name="global", mangled="_Z6globalv", type="int",
                     visibility=Visibility.PUBLIC)
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z6globalv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.OBJECT, size=4)])
        new_elf = ElfMetadata(symbols=[])

        r = compare(
            _snap(variables=[v], elf=old_elf),
            _snap(variables=[], elf=new_elf),
        )

        removed = [c for c in r.changes if c.kind == ChangeKind.VAR_REMOVED]
        assert len(removed) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Suppressed Changes Don't Affect Visible Changes
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionInteraction:
    """Suppression should not interfere with other unsuppressed changes."""

    def test_multiple_changes_suppression_leaves_others(self):
        """Even if we don't suppress here, verify baseline: all changes visible."""
        f1 = _pub_func("keep", "_Z4keepv")
        f2 = _pub_func("remove", "_Z6removev")
        v = Variable(name="g", mangled="_Z1gv", type="int",
                     visibility=Visibility.PUBLIC)

        r = compare(
            _snap(functions=[f1, f2], variables=[v]),
            _snap(functions=[f1]),
        )

        kind_set = _kinds(r)
        assert ChangeKind.FUNC_REMOVED in kind_set
        assert ChangeKind.VAR_REMOVED in kind_set


# ═══════════════════════════════════════════════════════════════════════════
# Edge Case: Empty/Missing Metadata
# ═══════════════════════════════════════════════════════════════════════════

class TestMissingMetadata:
    """Detectors handle missing metadata gracefully."""

    def test_elf_present_old_missing_new(self):
        """Old has ELF metadata, new does not → no crash."""
        old_elf = ElfMetadata(
            soname="libfoo.so.1",
            symbols=[ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(_snap(elf=old_elf), _snap())
        assert r.verdict is not None  # Should not crash

    def test_dwarf_present_old_missing_new(self):
        """Old has DWARF, new does not → DWARF_INFO_MISSING."""
        old_dwarf = DwarfMetadata(
            structs={"Foo": StructLayout(name="Foo", byte_size=4)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap())
        assert ChangeKind.DWARF_INFO_MISSING in _kinds(r)

    def test_both_missing_all_metadata(self):
        """Neither snapshot has any metadata → NO_CHANGE."""
        r = compare(_snap(), _snap())
        assert r.verdict == Verdict.NO_CHANGE
