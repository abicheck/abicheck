# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Negative tests -- verify that benign changes are NOT flagged as breaking.

These tests ensure abicheck does not produce false positives for changes that
are ABI-compatible: internal-only modifications, hidden symbol changes,
same-type additions, re-exports, etc.
"""

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


def _snap(
    version="1.0",
    functions=None,
    variables=None,
    types=None,
    enums=None,
    typedefs=None,
    elf=None,
):
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        typedefs=typedefs or {},
        elf=elf,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=params or [],
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


class TestHiddenSymbolChanges:
    """Changes to hidden/internal symbols should not be reported."""

    def test_hidden_func_added_not_reported(self):
        f = Function(
            name="internal",
            mangled="_Z8internalv",
            return_type="void",
            visibility=Visibility.HIDDEN,
        )
        r = compare(_snap(), _snap(functions=[f]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_func_removed_not_reported(self):
        f = Function(
            name="internal",
            mangled="_Z8internalv",
            return_type="void",
            visibility=Visibility.HIDDEN,
        )
        r = compare(_snap(functions=[f]), _snap())
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_var_removed_not_reported(self):
        v = Variable(
            name="priv", mangled="_Z4privv", type="int", visibility=Visibility.HIDDEN
        )
        r = compare(_snap(variables=[v]), _snap())
        assert r.verdict == Verdict.NO_CHANGE

    def test_hidden_var_type_change_not_reported(self):
        v_old = Variable(
            name="priv", mangled="_Z4privv", type="int", visibility=Visibility.HIDDEN
        )
        v_new = Variable(
            name="priv", mangled="_Z4privv", type="long", visibility=Visibility.HIDDEN
        )
        r = compare(_snap(variables=[v_old]), _snap(variables=[v_new]))
        assert r.verdict == Verdict.NO_CHANGE


class TestBenignFunctionChanges:
    """Function changes that don't affect ABI should not be breaking."""

    def test_same_function_different_source_location(self):
        """Moving a function to a different header line is not an ABI break."""
        f_old = _pub_func("foo", "_Z3foov")
        f_old.source_location = "foo.h:10"
        f_new = _pub_func("foo", "_Z3foov")
        f_new.source_location = "foo.h:42"
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_adding_new_function_is_not_breaking(self):
        """Adding a new public function is backward compatible."""
        f_old = _pub_func("init", "_Z4initv")
        f_new1 = _pub_func("init", "_Z4initv")
        f_new2 = _pub_func("cleanup", "_Z7cleanupv")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new1, f_new2]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_adding_new_variable_is_not_breaking(self):
        v_old = Variable(
            name="g", mangled="_Z1gv", type="int", visibility=Visibility.PUBLIC
        )
        v_new1 = Variable(
            name="g", mangled="_Z1gv", type="int", visibility=Visibility.PUBLIC
        )
        v_new2 = Variable(
            name="h", mangled="_Z1hv", type="int", visibility=Visibility.PUBLIC
        )
        r = compare(_snap(variables=[v_old]), _snap(variables=[v_new1, v_new2]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking


class TestBenignTypeChanges:
    """Type changes that don't affect ABI should not be breaking."""

    def test_same_type_same_layout(self):
        """Identical type definitions should produce no changes."""
        t = RecordType(
            name="Point",
            kind="struct",
            size_bits=64,
            fields=[TypeField("x", "float", 0), TypeField("y", "float", 32)],
        )
        r = compare(_snap(types=[t]), _snap(types=[t]))
        assert r.verdict == Verdict.NO_CHANGE

    def test_new_type_added_is_compatible(self):
        t = RecordType(name="NewStruct", kind="struct", size_bits=32)
        r = compare(_snap(), _snap(types=[t]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_field_added_to_standard_layout_struct_is_compatible(self):
        """Adding a field to a non-polymorphic standard-layout struct is compatible."""
        t_old = RecordType(
            name="Config",
            kind="struct",
            size_bits=32,
            fields=[TypeField("x", "int", 0)],
        )
        t_new = RecordType(
            name="Config",
            kind="struct",
            size_bits=64,
            fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)],
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        # Size change IS breaking, but the field addition itself is compatible
        field_add_changes = [
            c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
        ]
        assert len(field_add_changes) == 1

    def test_same_enum_values_no_change(self):
        e = EnumType(
            name="Color",
            members=[
                EnumMember("RED", 0),
                EnumMember("GREEN", 1),
                EnumMember("BLUE", 2),
            ],
        )
        r = compare(_snap(enums=[e]), _snap(enums=[e]))
        assert r.verdict == Verdict.NO_CHANGE


class TestBenignElfChanges:
    """ELF metadata changes that are classified as compatible, not breaking."""

    def test_needed_added_is_compatible(self):
        old_elf = ElfMetadata(needed=["libm.so.6"])
        new_elf = ElfMetadata(needed=["libm.so.6", "libpthread.so.0"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_needed_removed_is_compatible(self):
        old_elf = ElfMetadata(needed=["libm.so.6", "libdl.so.2"])
        new_elf = ElfMetadata(needed=["libm.so.6"])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_weak_to_global_is_compatible(self):
        """Strengthening a symbol from WEAK to GLOBAL is backward-compatible."""
        old_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="sym", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
                )
            ]
        )
        new_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="sym", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                )
            ]
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert r.verdict == Verdict.COMPATIBLE
        assert not r.breaking

    def test_func_size_change_not_breaking(self):
        """Function code size changes (different optimization) are not ABI breaks."""
        old_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="fn",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    size=100,
                )
            ]
        )
        new_elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="fn",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    size=200,
                )
            ]
        )
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        # STT_FUNC size changes should NOT produce SYMBOL_SIZE_CHANGED
        size_changes = [
            c for c in r.changes if c.kind == ChangeKind.SYMBOL_SIZE_CHANGED
        ]
        assert len(size_changes) == 0


class TestEmptySnapshots:
    """Edge cases with empty or minimal snapshots."""

    def test_empty_vs_empty(self):
        r = compare(_snap(), _snap())
        assert r.verdict == Verdict.NO_CHANGE
        assert r.changes == []

    def test_identical_complex_snapshot(self):
        f = _pub_func("api", "_Z3apiv", ret="int", params=[Param(name="x", type="int")])
        v = Variable(
            name="ver",
            mangled="_Z3verv",
            type="const char*",
            visibility=Visibility.PUBLIC,
        )
        t = RecordType(
            name="Data",
            kind="struct",
            size_bits=64,
            fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)],
        )
        e = EnumType(name="Status", members=[EnumMember("OK", 0), EnumMember("ERR", 1)])
        snap = _snap(
            functions=[f],
            variables=[v],
            types=[t],
            enums=[e],
            typedefs={"DataPtr": "Data*"},
        )
        r = compare(snap, snap)
        assert r.verdict == Verdict.NO_CHANGE
        assert r.changes == []
