"""False positive resistance tests — verify benign changes don't trigger alerts.

Systematically tests scenarios where the scanner should produce NO_CHANGE or
COMPATIBLE, ensuring no false positives for:
1. Source location changes only
2. Version string changes only
3. Comment/whitespace-equivalent changes
4. Identical snapshots with different object identity
5. Compatible additions that should not be BREAKING
6. Qualifier changes on hidden symbols
7. ELF metadata that should not cause breaks
"""
from __future__ import annotations

import copy

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, elf=None, constants=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf,
        constants=constants or {},
        from_headers=True,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _pub_var(name, mangled, type_, **kwargs):
    return Variable(name=name, mangled=mangled, type=type_,
                    visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


# ═══════════════════════════════════════════════════════════════════════════
# Identical Snapshots — NO False Positives
# ═══════════════════════════════════════════════════════════════════════════

class TestIdenticalSnapshots:
    """Identical snapshots must always produce NO_CHANGE."""

    def test_empty_vs_empty(self):
        r = compare(_snap(), _snap())
        assert r.verdict == Verdict.NO_CHANGE
        assert len(r.changes) == 0

    def test_single_func_identical(self):
        f = _pub_func("init", "_Z4initv", ret="int")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_complex_snapshot_identical(self):
        """Complex snapshot with all entity types."""
        f = _pub_func("api", "_Z3apiv", ret="int",
                       params=[Param(name="x", type="int")])
        v = _pub_var("ver", "_Z3verv", "const char *")
        t = RecordType(name="Config", kind="struct", size_bits=64,
                       fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)])
        e = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1), EnumMember("BLUE", 2)])

        snap = _snap(
            functions=[f], variables=[v], types=[t], enums=[e],
            typedefs={"ColorType": "enum Color"},
            constants={"VERSION": "1"},
        )
        r = compare(snap, copy.deepcopy(snap))
        assert r.verdict == Verdict.NO_CHANGE
        assert len(r.changes) == 0

    def test_deep_copy_no_change(self):
        """Deep copy of snapshot produces NO_CHANGE."""
        f = _pub_func("foo", "_Z3foov")
        snap = _snap(functions=[f])
        r = compare(snap, copy.deepcopy(snap))
        assert r.verdict == Verdict.NO_CHANGE


# ═══════════════════════════════════════════════════════════════════════════
# Non-ABI Changes — Should NOT be Breaking
# ═══════════════════════════════════════════════════════════════════════════

class TestNonAbiChanges:
    """Changes that don't affect ABI should not be flagged as breaking."""

    def test_version_string_only(self):
        """Different version strings, identical API → NO_CHANGE."""
        f = _pub_func("api", "_Z3apiv")
        old = _snap(version="1.0", functions=[f])
        new = _snap(version="2.0", functions=[f])
        r = compare(old, new)
        assert r.verdict == Verdict.NO_CHANGE

    def test_source_location_change(self):
        """Moving a function to a different header line → NO_CHANGE."""
        f1 = _pub_func("foo", "_Z3foov")
        f1.source_location = "foo.h:10"
        f2 = _pub_func("foo", "_Z3foov")
        f2.source_location = "foo.h:42"
        r = compare(_snap(functions=[f1]), _snap(functions=[f2]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_library_name_difference(self):
        """Different library names but same API → NO_CHANGE."""
        f = _pub_func("api", "_Z3apiv")
        old = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        new = AbiSnapshot(library="libfoo.so.2", version="1.0", functions=[f])
        r = compare(old, new)
        assert r.verdict == Verdict.NO_CHANGE


# ═══════════════════════════════════════════════════════════════════════════
# Hidden Symbol Changes — Should NOT be Reported
# ═══════════════════════════════════════════════════════════════════════════

class TestHiddenSymbolFP:
    """Changes to hidden/internal symbols must not produce false positives."""

    def test_hidden_func_return_type_changed(self):
        """Hidden function return type change → NO_CHANGE."""
        f_old = Function(name="internal", mangled="_Z8internalv",
                         return_type="int", visibility=Visibility.HIDDEN)
        f_new = Function(name="internal", mangled="_Z8internalv",
                         return_type="long", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_func_params_changed(self):
        """Hidden function parameter change → NO_CHANGE."""
        f_old = Function(name="internal", mangled="_Z8internalv",
                         return_type="void", visibility=Visibility.HIDDEN,
                         params=[Param(name="x", type="int")])
        f_new = Function(name="internal", mangled="_Z8internalv",
                         return_type="void", visibility=Visibility.HIDDEN,
                         params=[Param(name="x", type="long")])
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_var_type_changed(self):
        """Hidden variable type change → NO_CHANGE."""
        v_old = Variable(name="priv", mangled="_Z4privv", type="int",
                         visibility=Visibility.HIDDEN)
        v_new = Variable(name="priv", mangled="_Z4privv", type="long",
                         visibility=Visibility.HIDDEN)
        r = compare(_snap(variables=[v_old]), _snap(variables=[v_new]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_func_added_and_removed(self):
        """Adding AND removing hidden functions → NO_CHANGE."""
        f_old = Function(name="old_internal", mangled="_Z12old_internalv",
                         return_type="void", visibility=Visibility.HIDDEN)
        f_new = Function(name="new_internal", mangled="_Z12new_internalv",
                         return_type="void", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert r.verdict == Verdict.NO_CHANGE


# ═══════════════════════════════════════════════════════════════════════════
# Compatible Additions — Should NOT be Breaking
# ═══════════════════════════════════════════════════════════════════════════

class TestCompatibleAdditions:
    """New entities should be COMPATIBLE, not BREAKING."""

    def test_new_function_compatible(self):
        f_old = _pub_func("init", "_Z4initv")
        f_new = _pub_func("cleanup", "_Z7cleanupv")
        r = compare(_snap(functions=[f_old]),
                     _snap(functions=[f_old, f_new]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_new_variable_compatible(self):
        v_old = _pub_var("a", "_Z1av", "int")
        v_new = _pub_var("b", "_Z1bv", "int")
        r = compare(_snap(variables=[v_old]),
                     _snap(variables=[v_old, v_new]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_new_type_compatible(self):
        t_old = RecordType(name="OldType", kind="struct", size_bits=32)
        t_new = RecordType(name="NewType", kind="struct", size_bits=64)
        r = compare(_snap(types=[t_old]),
                     _snap(types=[t_old, t_new]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_new_enum_member_compatible(self):
        e_old = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1)])
        e_new = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1), EnumMember("BLUE", 2)])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_new_typedef_no_change(self):
        """Adding a new typedef is not even tracked as a change."""
        r = compare(_snap(typedefs={}), _snap(typedefs={"NewAlias": "int"}))
        assert r.verdict == Verdict.NO_CHANGE

    def test_new_constant_compatible(self):
        r = compare(_snap(constants={}), _snap(constants={"NEW_FLAG": "1"}))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking


# ═══════════════════════════════════════════════════════════════════════════
# ELF Metadata — Compatible Changes
# ═══════════════════════════════════════════════════════════════════════════

class TestElfCompatibleChanges:
    """ELF metadata changes classified as COMPATIBLE should not be BREAKING."""

    def test_needed_added_compatible(self):
        old_elf = ElfMetadata(needed=["libc.so.6"])
        new_elf = ElfMetadata(needed=["libc.so.6", "libm.so.6"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not r.breaking

    def test_needed_removed_compatible(self):
        old_elf = ElfMetadata(needed=["libc.so.6", "libdl.so.2"])
        new_elf = ElfMetadata(needed=["libc.so.6"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not r.breaking

    def test_weak_to_global_compatible(self):
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.WEAK,
                      sym_type=SymbolType.FUNC)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not r.breaking

    def test_soname_change_not_breaking(self):
        """SONAME change without API changes → COMPATIBLE."""
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not r.breaking

    def test_func_code_size_change_not_breaking(self):
        """Function code size changed (optimization) → not a break."""
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="fn", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, size=100)])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="fn", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, size=200)])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        # STT_FUNC size changes should NOT produce SYMBOL_SIZE_CHANGED
        size_changes = [c for c in r.changes if c.kind == ChangeKind.SYMBOL_SIZE_CHANGED]
        assert len(size_changes) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Type Changes That Are NOT Breaking
# ═══════════════════════════════════════════════════════════════════════════

class TestTypeCompatibleChanges:
    """Type changes that should not be flagged as breaking."""

    def test_same_type_identical_layout(self):
        t = RecordType(name="Point", kind="struct", size_bits=64,
                       fields=[TypeField("x", "float", 0), TypeField("y", "float", 32)])
        r = compare(_snap(types=[t]), _snap(types=[t]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_same_enum_values(self):
        e = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1)])
        r = compare(_snap(enums=[e]), _snap(enums=[e]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_same_typedef(self):
        r = compare(_snap(typedefs={"Foo": "int"}),
                     _snap(typedefs={"Foo": "int"}))
        assert r.verdict == Verdict.NO_CHANGE


# ═══════════════════════════════════════════════════════════════════════════
# Multiple Entities — No Spurious Cross-Talk
# ═══════════════════════════════════════════════════════════════════════════

class TestNoCrossTalk:
    """Changes to one entity should not affect unrelated entities."""

    def test_func_change_doesnt_affect_var(self):
        """Changing function return type shouldn't flag variable changes."""
        f_old = _pub_func("api", "_Z3apiv", ret="int")
        f_new = _pub_func("api", "_Z3apiv", ret="long")
        v = _pub_var("data", "_Z4datav", "int")

        r = compare(
            _snap(functions=[f_old], variables=[v]),
            _snap(functions=[f_new], variables=[v]),
        )

        # Only function should have changes, not variable
        var_changes = [c for c in r.changes if c.symbol == "_Z4datav"]
        assert len(var_changes) == 0

    def test_type_change_doesnt_affect_enum(self):
        """Changing struct layout shouldn't flag enum changes."""
        t_old = RecordType(name="Config", kind="struct", size_bits=32)
        t_new = RecordType(name="Config", kind="struct", size_bits=64)
        e = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1)])

        r = compare(
            _snap(types=[t_old], enums=[e]),
            _snap(types=[t_new], enums=[e]),
        )

        # Only type changes, no enum changes
        enum_changes = [c for c in r.changes
                        if c.kind in (ChangeKind.ENUM_MEMBER_REMOVED,
                                      ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
                                      ChangeKind.ENUM_MEMBER_ADDED)]
        assert len(enum_changes) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Large Snapshot — No Performance False Positives
# ═══════════════════════════════════════════════════════════════════════════

class TestLargeSnapshotNoFP:
    """Large identical snapshots should still produce NO_CHANGE."""

    def test_100_functions_identical(self):
        funcs = [_pub_func(f"func{i}", f"_Z4func{i}v", ret="void")
                 for i in range(100)]
        snap = _snap(functions=funcs)
        r = compare(snap, copy.deepcopy(snap))
        assert r.verdict == Verdict.NO_CHANGE
        assert len(r.changes) == 0

    def test_50_types_identical(self):
        types = [RecordType(name=f"Type{i}", kind="struct", size_bits=32 * (i + 1))
                 for i in range(50)]
        snap = _snap(types=types)
        r = compare(snap, copy.deepcopy(snap))
        assert r.verdict == Verdict.NO_CHANGE

    def test_100_enum_members_identical(self):
        e = EnumType(name="BigEnum", members=[
            EnumMember(f"VAL_{i}", i) for i in range(100)])
        snap = _snap(enums=[e])
        r = compare(snap, copy.deepcopy(snap))
        assert r.verdict == Verdict.NO_CHANGE
