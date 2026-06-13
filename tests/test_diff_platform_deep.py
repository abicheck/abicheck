"""Deep detection tests for platform-specific ChangeKinds with exhaustive coverage.

Covers: ELF symbol metadata (binding, type, size, visibility, IFUNC, versioning),
DWARF layout cross-checks, Mach-O compat_version, and advanced DWARF detectors.
All tests use synthetic metadata — no real binaries required.
"""
from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    Visibility,
)

try:
    from abicheck.macho_metadata import MachoMetadata
    HAS_MACHO = True
except ImportError:
    HAS_MACHO = False



def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, elf=None, dwarf=None,
          dwarf_advanced=None, macho=None, pe=None, elf_only_mode=False):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf, dwarf=dwarf,
        dwarf_advanced=dwarf_advanced, macho=macho, pe=pe,
        elf_only_mode=elf_only_mode,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


# ═══════════════════════════════════════════════════════════════════════════
# ELF Symbol Metadata
# ═══════════════════════════════════════════════════════════════════════════

class TestSymbolBindingChanged:
    """GLOBAL → WEAK binding change."""

    def test_global_to_weak(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="api", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="api", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_BINDING_CHANGED in _kinds(r)

    def test_weak_to_global_strengthened(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="api", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="api", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED in _kinds(r)


class TestSymbolTypeChanged:
    """Symbol type changes: FUNC → OBJECT etc."""

    def test_func_to_object(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_TYPE_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


class TestSymbolSizeChanged:
    """Symbol size changes (for OBJECT symbols — copy relocations)."""

    def test_object_size_changed(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="data", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.OBJECT, size=4)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="data", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.OBJECT, size=8)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_SIZE_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


class TestIfuncChanges:
    """STT_GNU_IFUNC introduced/removed."""

    def test_ifunc_introduced(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="resolve", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="resolve", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.IFUNC)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.IFUNC_INTRODUCED in _kinds(r)

    def test_ifunc_removed(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="resolve", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.IFUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="resolve", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.IFUNC_REMOVED in _kinds(r)


class TestElfVisibilityChanged:
    """ELF st_other visibility transitions (1 ref!)."""

    def test_default_to_protected(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, visibility="default")])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, visibility="protected")])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        assert ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED in kind_set


class TestSonameChanges:
    """SONAME metadata changes."""

    def test_soname_changed(self):
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SONAME_CHANGED in _kinds(r)

    def test_soname_missing(self):
        """Old has no SONAME, new defines one → SONAME_MISSING (bad practice in old)."""
        old_elf = ElfMetadata(soname="")
        new_elf = ElfMetadata(soname="libfoo.so.1")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SONAME_MISSING in _kinds(r)

    def test_soname_lost_is_changed(self):
        """Old has SONAME, new doesn't → SONAME_CHANGED (not MISSING)."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SONAME_CHANGED in _kinds(r)


class TestRpathRunpathChanged:
    """RPATH/RUNPATH changes (3 refs each)."""

    def test_rpath_changed(self):
        old_elf = ElfMetadata(rpath="/usr/lib")
        new_elf = ElfMetadata(rpath="/opt/lib")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.RPATH_CHANGED in _kinds(r)

    def test_runpath_changed(self):
        old_elf = ElfMetadata(runpath="/usr/lib")
        new_elf = ElfMetadata(runpath="/opt/lib")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.RUNPATH_CHANGED in _kinds(r)


class TestVisibilityLeak:
    """Internal symbols exported without -fvisibility=hidden."""

    def test_visibility_leak_does_not_crash(self):
        """New ELF has way more symbols than headers declare → no crash."""
        f = _pub_func("api", "_Z3apiv")
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC),
        ])
        # New version leaks many internal symbols
        new_syms = [
            ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC),
        ]
        for i in range(50):
            new_syms.append(
                ElfSymbol(name=f"_ZN8internal{i}Ev", binding=SymbolBinding.GLOBAL,
                          sym_type=SymbolType.FUNC))
        new_elf = ElfMetadata(symbols=new_syms)
        r = compare(_snap(functions=[f], elf=old_elf),
                     _snap(functions=[f], elf=new_elf))
        # The leak detector might fire depending on threshold
        # At minimum, no crash and valid result
        assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE,
                              Verdict.COMPATIBLE_WITH_RISK)


class TestExecutableStack:
    """PT_GNU_STACK with RWE flags (3 refs)."""

    def test_executable_stack_detected(self):
        old_elf = ElfMetadata(has_executable_stack=False)
        new_elf = ElfMetadata(has_executable_stack=True)
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.EXECUTABLE_STACK in _kinds(r)

    def test_executable_stack_removal_uses_distinct_kind(self):
        """Removing an executable stack is a hardening *improvement*: it emits
        the distinct EXECUTABLE_STACK_REMOVED kind (COMPATIBLE), never the
        regression kind — so the shipped `security` policy can't fail it
        (Codex P2). The default verdict stays COMPATIBLE (matches case49)."""
        old_elf = ElfMetadata(has_executable_stack=True)
        new_elf = ElfMetadata(has_executable_stack=False)
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kinds = _kinds(r)
        assert ChangeKind.EXECUTABLE_STACK_REMOVED in kinds
        assert ChangeKind.EXECUTABLE_STACK not in kinds
        assert r.verdict == Verdict.COMPATIBLE


class TestSecurityHardeningDrift:
    """checksec-equivalent hardening regressions (G12).

    All hardening kinds are COMPATIBLE_WITH_RISK by default and only fire on a
    *weakening* transition; an improvement is never a finding.
    """

    def test_relro_weakened_full_to_partial(self):
        old_elf = ElfMetadata(relro="full", bind_now=True)
        new_elf = ElfMetadata(relro="partial", bind_now=False)
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.RELRO_WEAKENED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_relro_weakened_partial_to_none(self):
        r = compare(_snap(elf=ElfMetadata(relro="partial")),
                    _snap(elf=ElfMetadata(relro="none")))
        assert ChangeKind.RELRO_WEAKENED in _kinds(r)

    def test_relro_strengthened_is_not_a_finding(self):
        r = compare(_snap(elf=ElfMetadata(relro="partial")),
                    _snap(elf=ElfMetadata(relro="full", bind_now=True)))
        assert ChangeKind.RELRO_WEAKENED not in _kinds(r)

    def test_pie_disabled(self):
        r = compare(_snap(elf=ElfMetadata(is_pie=True)),
                    _snap(elf=ElfMetadata(is_pie=False)))
        assert ChangeKind.PIE_DISABLED in _kinds(r)

    def test_pie_enabled_is_not_a_finding(self):
        r = compare(_snap(elf=ElfMetadata(is_pie=False)),
                    _snap(elf=ElfMetadata(is_pie=True)))
        assert ChangeKind.PIE_DISABLED not in _kinds(r)

    def test_stack_canary_removed(self):
        r = compare(_snap(elf=ElfMetadata(has_stack_canary=True)),
                    _snap(elf=ElfMetadata(has_stack_canary=False)))
        assert ChangeKind.STACK_CANARY_REMOVED in _kinds(r)

    def test_fortify_source_weakened(self):
        r = compare(_snap(elf=ElfMetadata(has_fortify_source=True)),
                    _snap(elf=ElfMetadata(has_fortify_source=False)))
        assert ChangeKind.FORTIFY_SOURCE_WEAKENED in _kinds(r)

    def test_writable_executable_segment_introduced(self):
        r = compare(_snap(elf=ElfMetadata(has_writable_executable_segment=False)),
                    _snap(elf=ElfMetadata(has_writable_executable_segment=True)))
        assert ChangeKind.WRITABLE_EXECUTABLE_SEGMENT in _kinds(r)

    def test_unchanged_hardening_is_silent(self):
        elf = ElfMetadata(relro="full", bind_now=True, is_pie=True,
                          has_stack_canary=True, has_fortify_source=True)
        r = compare(_snap(elf=elf), _snap(elf=elf))
        hardening = {
            ChangeKind.RELRO_WEAKENED, ChangeKind.PIE_DISABLED,
            ChangeKind.STACK_CANARY_REMOVED, ChangeKind.FORTIFY_SOURCE_WEAKENED,
            ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
        }
        assert not (hardening & _kinds(r))

    def test_security_policy_gates_relro_to_breaking(self):
        from pathlib import Path

        from abicheck.policy_file import PolicyFile
        pf = PolicyFile.load(Path("security"))
        old_elf = ElfMetadata(relro="full", bind_now=True)
        new_elf = ElfMetadata(relro="none")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        relro_changes = [c for c in r.changes if c.kind == ChangeKind.RELRO_WEAKENED]
        assert relro_changes, "expected a relro_weakened change"
        # Default verdict is RISK; the shipped security policy promotes to break.
        assert pf.compute_verdict(relro_changes) == Verdict.BREAKING


class TestCommonSymbolRisk:
    """STT_COMMON symbol detection (3 refs)."""

    def test_common_symbol_introduced(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="data", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.OBJECT)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="data", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.COMMON)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        kind_set = _kinds(r)
        assert ChangeKind.SYMBOL_TYPE_CHANGED in kind_set
        assert ChangeKind.COMMON_SYMBOL_RISK in kind_set


# ═══════════════════════════════════════════════════════════════════════════
# Symbol Versioning
# ═══════════════════════════════════════════════════════════════════════════

class TestSymbolVersionChanges:
    """ELF symbol version changes."""

    def test_version_defined_removed(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"])
        new_elf = ElfMetadata(versions_defined=["LIBFOO_2.0"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED in _kinds(r)

    def test_version_defined_added(self):
        old_elf = ElfMetadata(versions_defined=["LIBFOO_1.0"])
        new_elf = ElfMetadata(versions_defined=["LIBFOO_1.0", "LIBFOO_2.0"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_DEFINED_ADDED in _kinds(r)

    def test_version_required_added_compat(self):
        """New lib not previously required → COMPAT (not a new runtime constraint)."""
        old_elf = ElfMetadata(versions_required={})
        new_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.34"]})
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in _kinds(r)

    def test_version_required_added_breaking(self):
        """Genuinely newer version requirement on existing lib → BREAKING."""
        old_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})
        new_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]})
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in _kinds(r)

    def test_version_required_removed(self):
        old_elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.28"]})
        new_elf = ElfMetadata(versions_required={})
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED in _kinds(r)


class TestNeededChanges:
    """DT_NEEDED library dependency changes."""

    def test_needed_added(self):
        old_elf = ElfMetadata(needed=["libc.so.6"])
        new_elf = ElfMetadata(needed=["libc.so.6", "libm.so.6"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.NEEDED_ADDED in _kinds(r)

    def test_needed_removed(self):
        old_elf = ElfMetadata(needed=["libc.so.6", "libm.so.6"])
        new_elf = ElfMetadata(needed=["libc.so.6"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.NEEDED_REMOVED in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# DWARF Layout Cross-Check
# ═══════════════════════════════════════════════════════════════════════════

class TestDwarfStructSizeChanged:
    """DWARF-level struct size change cross-check."""

    def test_struct_size_changed(self):
        old_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=8)},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Config": StructLayout(name="Config", byte_size=16)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.STRUCT_SIZE_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


class TestDwarfStructFieldOffset:
    """DWARF-level field offset change."""

    def test_field_offset_changed(self):
        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=8,
                fields=[FieldInfo("a", "int", 0, 4), FieldInfo("b", "int", 4, 4)])},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=12,
                fields=[FieldInfo("a", "int", 0, 4), FieldInfo("b", "int", 8, 4)])},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.STRUCT_FIELD_OFFSET_CHANGED in _kinds(r)


class TestDwarfStructFieldRemoved:
    """DWARF-level field removal."""

    def test_field_removed(self):
        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=8,
                fields=[FieldInfo("a", "int", 0, 4), FieldInfo("b", "int", 4, 4)])},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=4,
                fields=[FieldInfo("a", "int", 0, 4)])},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.STRUCT_FIELD_REMOVED in _kinds(r)


class TestDwarfStructFieldTypeChanged:
    """DWARF-level field type change."""

    def test_field_type_changed(self):
        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=8,
                fields=[FieldInfo("x", "int", 0, 4)])},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(
                name="Data", byte_size=8,
                fields=[FieldInfo("x", "long", 0, 8)])},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in _kinds(r)


class TestDwarfStructAlignmentChanged:
    """DWARF-level struct alignment change (2 refs)."""

    def test_alignment_changed(self):
        old_dwarf = DwarfMetadata(
            structs={"AlignedData": StructLayout(
                name="AlignedData", byte_size=8, alignment=4)},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            structs={"AlignedData": StructLayout(
                name="AlignedData", byte_size=8, alignment=16)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.STRUCT_ALIGNMENT_CHANGED in _kinds(r)


class TestDwarfEnumUnderlyingSizeChanged:
    """DWARF enum underlying type size change (4 refs)."""

    def test_enum_underlying_size_changed(self):
        old_dwarf = DwarfMetadata(
            enums={"Status": EnumInfo(name="Status", underlying_byte_size=4,
                                      members={"OK": 0, "ERR": 1})},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(
            enums={"Status": EnumInfo(name="Status", underlying_byte_size=8,
                                      members={"OK": 0, "ERR": 1})},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED in _kinds(r)


class TestDwarfInfoMissing:
    """DWARF present in old, missing in new (3 refs)."""

    def test_dwarf_present_then_missing(self):
        old_dwarf = DwarfMetadata(
            structs={"Data": StructLayout(name="Data", byte_size=8)},
            has_dwarf=True,
        )
        new_dwarf = DwarfMetadata(has_dwarf=False)
        r = compare(_snap(dwarf=old_dwarf), _snap(dwarf=new_dwarf))
        assert ChangeKind.DWARF_INFO_MISSING in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# Advanced DWARF (Sprint 4)
# ═══════════════════════════════════════════════════════════════════════════

class TestCallingConventionChanged:
    """Calling convention change detected via DWARF."""

    def test_calling_convention_changed(self):
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            calling_conventions={"_Z3foov": "normal"},
        )
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            calling_conventions={"_Z3foov": "stdcall"},
        )
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.CALLING_CONVENTION_CHANGED in _kinds(r)


class TestToolchainFlagDrift:
    """Compiler flag ABI drift (4 refs)."""

    def test_flag_added(self):
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            toolchain=ToolchainInfo(producer_string="gcc 13.2.0", compiler="GCC",
                                    version="13.2.0", abi_flags=set()),
        )
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            toolchain=ToolchainInfo(producer_string="gcc 13.2.0 -fshort-enums",
                                    compiler="GCC", version="13.2.0",
                                    abi_flags={"-fshort-enums"}),
        )
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.TOOLCHAIN_FLAG_DRIFT in _kinds(r)


class TestStructPackingChanged:
    """Packing attribute change detected via DWARF."""

    def test_packing_added(self):
        """Struct became packed — both sides must have the struct in all_struct_names."""
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True, packed_structs=set(),
            all_struct_names={"Config"})
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True, packed_structs={"Config"},
            all_struct_names={"Config"})
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.STRUCT_PACKING_CHANGED in _kinds(r)

    def test_packing_removed(self):
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True, packed_structs={"Config"},
            all_struct_names={"Config"})
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True, packed_structs=set(),
            all_struct_names={"Config"})
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.STRUCT_PACKING_CHANGED in _kinds(r)


class TestValueAbiTraitChanged:
    """Value ABI trait fingerprint changed."""

    def test_abi_trait_changed(self):
        # Parameter-position triviality flip → generic value-ABI trait change.
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"_Z3fooP1S": "ret:v(trivial)|p0:trivial"},
        )
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"_Z3fooP1S": "ret:v(trivial)|p0:nontrivial"},
        )
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in _kinds(r)

    def test_return_trait_flip_is_struct_return_convention(self):
        # Return-position triviality flip → struct_return_convention_changed.
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"_Z3foov": "ret:v(trivial)"},
        )
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"_Z3foov": "ret:v(nontrivial)"},
        )
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in _kinds(r)


class TestFrameRegisterChanged:
    """Frame pointer register usage change."""

    def test_frame_register_changed(self):
        old_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            frame_registers={"_Z3foov": "rbp"},
        )
        new_adv = AdvancedDwarfMetadata(
            has_dwarf=True,
            frame_registers={"_Z3foov": "rsp"},
        )
        r = compare(_snap(dwarf_advanced=old_adv), _snap(dwarf_advanced=new_adv))
        assert ChangeKind.FRAME_REGISTER_CHANGED in _kinds(r)


# ═══════════════════════════════════════════════════════════════════════════
# Mach-O Compatibility Version
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_MACHO, reason="macholib not available")
class TestMachoCompatVersionChanged:
    """Mach-O LC_ID_DYLIB compatibility version change (7 refs)."""

    def test_compat_version_changed(self):
        old_macho = MachoMetadata(
            compat_version="1.0.0", current_version="1.2.0",
            install_name="/usr/lib/libfoo.dylib",
        )
        new_macho = MachoMetadata(
            compat_version="2.0.0", current_version="2.0.0",
            install_name="/usr/lib/libfoo.dylib",
        )
        r = compare(_snap(macho=old_macho), _snap(macho=new_macho))
        assert ChangeKind.COMPAT_VERSION_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ═══════════════════════════════════════════════════════════════════════════
# Multi-detector: ELF + header changes together
# ═══════════════════════════════════════════════════════════════════════════

class TestElfAndHeaderCombined:
    """Verify detectors work when both ELF and header data are present."""

    def test_func_removed_with_elf_confirmation(self):
        """Function removed from both headers and ELF."""
        f = _pub_func("api", "_Z3apiv")
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[])

        r = compare(
            _snap(functions=[f], elf=old_elf),
            _snap(functions=[], elf=new_elf),
        )
        assert ChangeKind.FUNC_REMOVED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_elf_soname_and_type_change_together(self):
        """SONAME change combined with a type size change."""
        t_old = RecordType(name="Data", kind="struct", size_bits=32)
        t_new = RecordType(name="Data", kind="struct", size_bits=64)
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")

        r = compare(
            _snap(types=[t_old], elf=old_elf),
            _snap(types=[t_new], elf=new_elf),
        )
        kind_set = _kinds(r)
        assert ChangeKind.SONAME_CHANGED in kind_set
        assert ChangeKind.TYPE_SIZE_CHANGED in kind_set
