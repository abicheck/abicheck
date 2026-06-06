"""Bidirectional symmetry tests — verify v1→v2 and v2→v1 produce symmetric results.

For every ABI change, comparing (old, new) and (new, old) must yield symmetric
ChangeKind pairs: e.g. FUNC_REMOVED ↔ FUNC_ADDED, TYPE_SIZE_CHANGED in both
directions, etc. This catches asymmetric detector bugs where a change is detected
in one direction but missed in the reverse.
"""
from __future__ import annotations

import copy

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
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
    """Extract set of ChangeKind values from a DiffResult."""
    return {c.kind for c in result.changes}


# ── Symmetric pairs: removal ↔ addition ────────────────────────────────────

class TestFunctionSymmetry:
    """func_removed(v1→v2) ↔ func_added(v2→v1) and vice versa."""

    def test_func_removed_vs_added(self):
        """Removing a function: forward = FUNC_REMOVED, reverse = FUNC_ADDED."""
        f = _pub_func("process", "_Z7processv")
        old = _snap(functions=[f])
        new = _snap()

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.FUNC_REMOVED in _kinds(fwd)
        assert ChangeKind.FUNC_ADDED in _kinds(rev)
        assert fwd.verdict == Verdict.BREAKING
        assert rev.verdict == Verdict.COMPATIBLE

    def test_func_added_vs_removed(self):
        """Adding a function: forward = FUNC_ADDED, reverse = FUNC_REMOVED."""
        f = _pub_func("cleanup", "_Z7cleanupv")
        old = _snap()
        new = _snap(functions=[f])

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.FUNC_ADDED in _kinds(fwd)
        assert ChangeKind.FUNC_REMOVED in _kinds(rev)


class TestVariableSymmetry:
    """var_removed ↔ var_added."""

    def test_var_removed_vs_added(self):
        v = _pub_var("config", "_Z6configv", "int")
        old = _snap(variables=[v])
        new = _snap()

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.VAR_REMOVED in _kinds(fwd)
        assert ChangeKind.VAR_ADDED in _kinds(rev)

    def test_var_added_vs_removed(self):
        v = _pub_var("config", "_Z6configv", "int")
        old = _snap()
        new = _snap(variables=[v])

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.VAR_ADDED in _kinds(fwd)
        assert ChangeKind.VAR_REMOVED in _kinds(rev)


class TestTypeSymmetry:
    """type_removed ↔ type_added."""

    def test_type_removed_vs_added(self):
        t = RecordType(name="Widget", kind="struct", size_bits=32)
        old = _snap(types=[t])
        new = _snap()

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.TYPE_REMOVED in _kinds(fwd)
        assert ChangeKind.TYPE_ADDED in _kinds(rev)


class TestEnumSymmetry:
    """enum_member_removed ↔ enum_member_added."""

    def test_enum_member_removed_vs_added(self):
        old_enum = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1), EnumMember("BLUE", 2),
        ])
        new_enum = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1),
        ])

        fwd = compare(_snap(enums=[old_enum]), _snap(enums=[new_enum]))
        rev = compare(_snap(enums=[new_enum]), _snap(enums=[old_enum]))

        assert ChangeKind.ENUM_MEMBER_REMOVED in _kinds(fwd)
        assert ChangeKind.ENUM_MEMBER_ADDED in _kinds(rev)

    def test_enum_member_added_vs_removed(self):
        old_enum = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1),
        ])
        new_enum = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1), EnumMember("BLUE", 2),
        ])

        fwd = compare(_snap(enums=[old_enum]), _snap(enums=[new_enum]))
        rev = compare(_snap(enums=[new_enum]), _snap(enums=[old_enum]))

        assert ChangeKind.ENUM_MEMBER_ADDED in _kinds(fwd)
        assert ChangeKind.ENUM_MEMBER_REMOVED in _kinds(rev)


class TestTypedefSymmetry:
    """typedef_removed in forward, no typedef_added (typedefs don't have added kind)."""

    def test_typedef_removed_in_forward(self):
        old = _snap(typedefs={"MyInt": "int"})
        new = _snap(typedefs={})

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.TYPEDEF_REMOVED in _kinds(fwd)
        # Reverse: adding a typedef has no separate kind, should be NO_CHANGE or compatible
        assert ChangeKind.TYPEDEF_REMOVED not in _kinds(rev)


# ── Symmetric mutation changes (both directions should detect) ──────────────

class TestReturnTypeSymmetry:
    """Return type changes should be detected in both directions."""

    def test_return_type_changed_both_directions(self):
        f_v1 = _pub_func("getValue", "_Z8getValuev", ret="int")
        f_v2 = _pub_func("getValue", "_Z8getValuev", ret="long")

        fwd = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        rev = compare(_snap(functions=[f_v2]), _snap(functions=[f_v1]))

        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(fwd)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(rev)


class TestParamTypeSymmetry:
    """Parameter type changes should be detected in both directions."""

    def test_param_type_changed_both_directions(self):
        f_v1 = _pub_func("send", "_Z4sendi",
                          params=[Param(name="x", type="int")])
        f_v2 = _pub_func("send", "_Z4sendi",
                          params=[Param(name="x", type="long")])

        fwd = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        rev = compare(_snap(functions=[f_v2]), _snap(functions=[f_v1]))

        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(fwd)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(rev)


class TestTypeSizeSymmetry:
    """Type size changes should be detected in both directions."""

    def test_type_size_changed_both_directions(self):
        t_v1 = RecordType(name="Config", kind="struct", size_bits=64,
                          fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)])
        t_v2 = RecordType(name="Config", kind="struct", size_bits=128,
                          fields=[TypeField("x", "int", 0), TypeField("y", "int", 32),
                                  TypeField("z", "long", 64)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.TYPE_SIZE_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPE_SIZE_CHANGED in _kinds(rev)


class TestEnumValueSymmetry:
    """Enum value changes detected in both directions."""

    def test_enum_value_changed_both_directions(self):
        e_v1 = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 1),
        ])
        e_v2 = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 42),
        ])

        fwd = compare(_snap(enums=[e_v1]), _snap(enums=[e_v2]))
        rev = compare(_snap(enums=[e_v2]), _snap(enums=[e_v1]))

        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in _kinds(fwd)
        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in _kinds(rev)


class TestVarTypeSymmetry:
    """Variable type changes detected in both directions."""

    def test_var_type_changed_both_directions(self):
        v_v1 = _pub_var("counter", "_Z7counterv", "int")
        v_v2 = _pub_var("counter", "_Z7counterv", "size_t")

        fwd = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        rev = compare(_snap(variables=[v_v2]), _snap(variables=[v_v1]))

        assert ChangeKind.VAR_TYPE_CHANGED in _kinds(fwd)
        assert ChangeKind.VAR_TYPE_CHANGED in _kinds(rev)


class TestFieldOffsetSymmetry:
    """Field offset changes detected in both directions."""

    def test_field_offset_changed_both_directions(self):
        t_v1 = RecordType(name="Data", kind="struct", size_bits=64,
                          fields=[TypeField("a", "int", 0), TypeField("b", "int", 32)])
        t_v2 = RecordType(name="Data", kind="struct", size_bits=64,
                          fields=[TypeField("a", "int", 0), TypeField("b", "int", 48)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.TYPE_FIELD_OFFSET_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPE_FIELD_OFFSET_CHANGED in _kinds(rev)


class TestFieldTypeSymmetry:
    """Field type changes detected in both directions."""

    def test_field_type_changed_both_directions(self):
        t_v1 = RecordType(name="Cfg", kind="struct", size_bits=64,
                          fields=[TypeField("val", "int", 0)])
        t_v2 = RecordType(name="Cfg", kind="struct", size_bits=64,
                          fields=[TypeField("val", "long", 0)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(rev)


class TestTypedefBaseSymmetry:
    """Typedef underlying type changes detected in both directions."""

    def test_typedef_base_changed_both_directions(self):
        old = _snap(typedefs={"Handle": "int"})
        new = _snap(typedefs={"Handle": "void *"})

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(rev)


class TestVtableSymmetry:
    """Vtable changes detected in both directions."""

    def test_vtable_changed_both_directions(self):
        t_v1 = RecordType(name="Base", kind="class", size_bits=64,
                          vtable=["_ZN4Base3fooEv", "_ZN4Base3barEv"])
        t_v2 = RecordType(name="Base", kind="class", size_bits=64,
                          vtable=["_ZN4Base3barEv", "_ZN4Base3fooEv"])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(rev)


class TestBaseClassSymmetry:
    """Base class changes detected in both directions."""

    def test_base_changed_both_directions(self):
        t_v1 = RecordType(name="Derived", kind="class", size_bits=64,
                          bases=["BaseA"])
        t_v2 = RecordType(name="Derived", kind="class", size_bits=64,
                          bases=["BaseB"])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(fwd)
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(rev)


# ── ELF metadata symmetry ──────────────────────────────────────────────────

class TestElfNeededSymmetry:
    """needed_added ↔ needed_removed."""

    def test_needed_added_vs_removed(self):
        old_elf = ElfMetadata(needed=["libm.so.6"])
        new_elf = ElfMetadata(needed=["libm.so.6", "libz.so.1"])

        fwd = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        rev = compare(_snap(elf=new_elf), _snap(elf=old_elf))

        assert ChangeKind.NEEDED_ADDED in _kinds(fwd)
        assert ChangeKind.NEEDED_REMOVED in _kinds(rev)


class TestElfSonameSymmetry:
    """SONAME changes detected in both directions."""

    def test_soname_changed_both_directions(self):
        old_elf = ElfMetadata(soname="libfoo.so.1")
        new_elf = ElfMetadata(soname="libfoo.so.2")

        fwd = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        rev = compare(_snap(elf=new_elf), _snap(elf=old_elf))

        assert ChangeKind.SONAME_CHANGED in _kinds(fwd)
        assert ChangeKind.SONAME_CHANGED in _kinds(rev)


# ── Virtual function symmetry ──────────────────────────────────────────────

class TestVirtualFuncSymmetry:
    """Virtual function add/remove produce symmetric results."""

    def test_virtual_added_vs_removed(self):
        f_nonvirt = _pub_func("doWork", "_ZN4Base6doWorkEv", is_virtual=False)
        f_virt = _pub_func("doWork", "_ZN4Base6doWorkEv", is_virtual=True)

        fwd = compare(_snap(functions=[f_nonvirt]), _snap(functions=[f_virt]))
        rev = compare(_snap(functions=[f_virt]), _snap(functions=[f_nonvirt]))

        assert ChangeKind.FUNC_VIRTUAL_ADDED in _kinds(fwd)
        assert ChangeKind.FUNC_VIRTUAL_REMOVED in _kinds(rev)


# ── Noexcept symmetry ─────────────────────────────────────────────────────

class TestNoexceptSymmetry:
    """noexcept add/remove produce symmetric results."""

    def test_noexcept_added_vs_removed(self):
        f_v1 = _pub_func("safe", "_Z4safev", is_noexcept=False)
        f_v2 = _pub_func("safe", "_Z4safev", is_noexcept=True)

        fwd = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        rev = compare(_snap(functions=[f_v2]), _snap(functions=[f_v1]))

        assert ChangeKind.FUNC_NOEXCEPT_ADDED in _kinds(fwd)
        assert ChangeKind.FUNC_NOEXCEPT_REMOVED in _kinds(rev)


# ── Union field symmetry ──────────────────────────────────────────────────

class TestUnionFieldSymmetry:
    """Union field add ↔ remove."""

    def test_union_field_added_vs_removed(self):
        u_v1 = RecordType(name="Data", kind="union", size_bits=32, is_union=True,
                          fields=[TypeField("i", "int", 0)])
        u_v2 = RecordType(name="Data", kind="union", size_bits=64, is_union=True,
                          fields=[TypeField("i", "int", 0), TypeField("d", "double", 0)])

        fwd = compare(_snap(types=[u_v1]), _snap(types=[u_v2]))
        rev = compare(_snap(types=[u_v2]), _snap(types=[u_v1]))

        assert ChangeKind.UNION_FIELD_ADDED in _kinds(fwd)
        assert ChangeKind.UNION_FIELD_REMOVED in _kinds(rev)


# ── Constant symmetry ─────────────────────────────────────────────────────

class TestConstantSymmetry:
    """constant_added ↔ constant_removed."""

    def test_constant_added_vs_removed(self):
        old = _snap(constants={})
        new = _snap(constants={"MAX_SIZE": "1024"})

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.CONSTANT_ADDED in _kinds(fwd)
        assert ChangeKind.CONSTANT_REMOVED in _kinds(rev)


# ── Field qualifier symmetry ──────────────────────────────────────────────

class TestFieldQualifierSymmetry:
    """field_became_const ↔ field_lost_const."""

    def test_const_qualifier_symmetry(self):
        t_v1 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_const=False)])
        t_v2 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_const=True)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.FIELD_BECAME_CONST in _kinds(fwd)
        assert ChangeKind.FIELD_LOST_CONST in _kinds(rev)

    def test_volatile_qualifier_symmetry(self):
        t_v1 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_volatile=False)])
        t_v2 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_volatile=True)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.FIELD_BECAME_VOLATILE in _kinds(fwd)
        assert ChangeKind.FIELD_LOST_VOLATILE in _kinds(rev)

    def test_mutable_qualifier_symmetry(self):
        t_v1 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_mutable=False)])
        t_v2 = RecordType(name="Cfg", kind="struct", size_bits=32,
                          fields=[TypeField("val", "int", 0, is_mutable=True)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        rev = compare(_snap(types=[t_v2]), _snap(types=[t_v1]))

        assert ChangeKind.FIELD_BECAME_MUTABLE in _kinds(fwd)
        assert ChangeKind.FIELD_LOST_MUTABLE in _kinds(rev)


# ── Access level symmetry ─────────────────────────────────────────────────

class TestAccessLevelSymmetry:
    """Access level changes are detected in both directions."""

    def test_method_access_narrowed(self):
        """Narrowing access (public → private) is detected as API_BREAK."""
        f_v1 = _pub_func("helper", "_ZN3Cls6helperEv", access=AccessLevel.PUBLIC)
        f_v2 = _pub_func("helper", "_ZN3Cls6helperEv", access=AccessLevel.PRIVATE)

        fwd = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.METHOD_ACCESS_CHANGED in _kinds(fwd)

    def test_method_access_widened_not_breaking(self):
        """Widening access (private → public) is not an ABI/API break."""
        f_v1 = _pub_func("helper", "_ZN3Cls6helperEv", access=AccessLevel.PRIVATE)
        f_v2 = _pub_func("helper", "_ZN3Cls6helperEv", access=AccessLevel.PUBLIC)

        rev = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        # Widening is not a break — either no change or compatible
        assert not rev.breaking

    def test_field_access_narrowed(self):
        """Narrowing field access (public → private) is detected."""
        t_v1 = RecordType(name="Cls", kind="class", size_bits=32,
                          fields=[TypeField("val", "int", 0, access=AccessLevel.PUBLIC)])
        t_v2 = RecordType(name="Cls", kind="class", size_bits=32,
                          fields=[TypeField("val", "int", 0, access=AccessLevel.PRIVATE)])

        fwd = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        assert ChangeKind.FIELD_ACCESS_CHANGED in _kinds(fwd)

    def test_field_access_widened_not_breaking(self):
        """Widening field access (private → public) is not a break."""
        t_v1 = RecordType(name="Cls", kind="class", size_bits=32,
                          fields=[TypeField("val", "int", 0, access=AccessLevel.PRIVATE)])
        t_v2 = RecordType(name="Cls", kind="class", size_bits=32,
                          fields=[TypeField("val", "int", 0, access=AccessLevel.PUBLIC)])

        rev = compare(_snap(types=[t_v1]), _snap(types=[t_v2]))
        assert not rev.breaking


# ── Var const symmetry ────────────────────────────────────────────────────

class TestVarConstSymmetry:
    """var_became_const ↔ var_lost_const."""

    def test_var_const_symmetry(self):
        v_v1 = _pub_var("setting", "_Z7settingv", "int", is_const=False)
        v_v2 = _pub_var("setting", "_Z7settingv", "int", is_const=True)

        fwd = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        rev = compare(_snap(variables=[v_v2]), _snap(variables=[v_v1]))

        assert ChangeKind.VAR_BECAME_CONST in _kinds(fwd)
        assert ChangeKind.VAR_LOST_CONST in _kinds(rev)


# ── Multi-change symmetry ────────────────────────────────────────────────

class TestMultiChangeSymmetry:
    """Multiple simultaneous changes should all be symmetric."""

    def test_func_and_var_removed_vs_added(self):
        """Remove func + var together; reverse should add both."""
        f = _pub_func("api", "_Z3apiv")
        v = _pub_var("ver", "_Z3verv", "int")
        old = _snap(functions=[f], variables=[v])
        new = _snap()

        fwd = compare(old, new)
        rev = compare(new, old)

        assert ChangeKind.FUNC_REMOVED in _kinds(fwd)
        assert ChangeKind.VAR_REMOVED in _kinds(fwd)
        assert ChangeKind.FUNC_ADDED in _kinds(rev)
        assert ChangeKind.VAR_ADDED in _kinds(rev)

    def test_identity_both_directions(self):
        """Identical snapshots → NO_CHANGE in both directions."""
        f = _pub_func("foo", "_Z3foov")
        snap = _snap(functions=[f])

        fwd = compare(snap, copy.deepcopy(snap))
        rev = compare(copy.deepcopy(snap), snap)

        assert fwd.verdict == Verdict.NO_CHANGE
        assert rev.verdict == Verdict.NO_CHANGE
        assert len(fwd.changes) == 0
        assert len(rev.changes) == 0
