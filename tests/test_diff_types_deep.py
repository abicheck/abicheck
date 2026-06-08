"""Deep detection tests for type-level ChangeKinds with shallow coverage.

Covers: union fields, field qualifiers, bitfields, enum renames, field renames,
type_kind_changed, reserved fields, const overloads, type_became_opaque, and
other type-related ChangeKinds that have minimal dedicated test coverage.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, constants=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, constants=constants or {},
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


def test_stdlib_type_filter_uses_elf_soname_for_runtime_snapshots() -> None:
    old = AbiSnapshot(
        library="old-renamed-copy.so",
        version="1.0",
        elf=ElfMetadata(soname="libstdc++.so.6"),
        types=[RecordType(name="std::runtime_surface", kind="class", size_bits=64)],
    )
    new = AbiSnapshot(
        library="new-renamed-copy.so",
        version="2.0",
        elf=ElfMetadata(soname="libstdc++.so.6"),
        types=[RecordType(name="std::runtime_surface", kind="class", size_bits=128)],
    )

    result = compare(old, new)

    assert ChangeKind.TYPE_SIZE_CHANGED in _kinds(result)


# ── Union field changes (2-3 refs each) ──────────────────────────────────

class TestUnionFieldAdded:
    """Adding a field to a union may change its size."""

    def test_union_field_added(self):
        u_old = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("i", "int", 0)])
        u_new = RecordType(name="Value", kind="union", size_bits=64, is_union=True,
                           fields=[TypeField("i", "int", 0), TypeField("d", "double", 0)])
        r = compare(_snap(types=[u_old]), _snap(types=[u_new]))
        assert ChangeKind.UNION_FIELD_ADDED in _kinds(r)

    def test_union_field_added_same_size(self):
        """Adding a union field that doesn't change the overall size."""
        u_old = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("i", "int", 0)])
        u_new = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("i", "int", 0), TypeField("f", "float", 0)])
        r = compare(_snap(types=[u_old]), _snap(types=[u_new]))
        assert ChangeKind.UNION_FIELD_ADDED in _kinds(r)


class TestUnionFieldRemoved:
    """Removing a union field breaks code accessing that alternative."""

    def test_union_field_removed(self):
        u_old = RecordType(name="Value", kind="union", size_bits=64, is_union=True,
                           fields=[TypeField("i", "int", 0), TypeField("d", "double", 0)])
        u_new = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("i", "int", 0)])
        r = compare(_snap(types=[u_old]), _snap(types=[u_new]))
        assert ChangeKind.UNION_FIELD_REMOVED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


class TestUnionFieldTypeChanged:
    """Changing the type of an existing union field."""

    def test_union_field_type_changed(self):
        u_old = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("val", "int", 0)])
        u_new = RecordType(name="Value", kind="union", size_bits=64, is_union=True,
                           fields=[TypeField("val", "double", 0)])
        r = compare(_snap(types=[u_old]), _snap(types=[u_new]))
        assert ChangeKind.UNION_FIELD_TYPE_CHANGED in _kinds(r)


# ── Field qualifier changes (3 refs each) ────────────────────────────────

class TestFieldBecameConst:
    """Field const qualifier added."""

    def test_field_became_const(self):
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "int", 0, is_const=False)])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "int", 0, is_const=True)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_BECAME_CONST in _kinds(r)


class TestFieldLostConst:
    """Field const qualifier removed."""

    def test_field_lost_const(self):
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "int", 0, is_const=True)])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "int", 0, is_const=False)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_LOST_CONST in _kinds(r)


class TestFieldVolatileChanged:
    """Field volatile qualifier added/removed."""

    def test_field_became_volatile(self):
        t_old = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "int", 0, is_volatile=False)])
        t_new = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "int", 0, is_volatile=True)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_BECAME_VOLATILE in _kinds(r)

    def test_field_lost_volatile(self):
        t_old = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "int", 0, is_volatile=True)])
        t_new = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "int", 0, is_volatile=False)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_LOST_VOLATILE in _kinds(r)


class TestFieldMutableChanged:
    """Field mutable qualifier added/removed."""

    def test_field_became_mutable(self):
        t_old = RecordType(name="Cache", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0, is_mutable=False)])
        t_new = RecordType(name="Cache", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0, is_mutable=True)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_BECAME_MUTABLE in _kinds(r)

    def test_field_lost_mutable(self):
        t_old = RecordType(name="Cache", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0, is_mutable=True)])
        t_new = RecordType(name="Cache", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0, is_mutable=False)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_LOST_MUTABLE in _kinds(r)


# ── field_bitfield_changed (3 refs) ──────────────────────────────────────

class TestFieldBitfieldChanged:
    """Bit-field width/position changes."""

    def test_bitfield_width_changed(self):
        t_old = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag", "unsigned int", 0,
                                             is_bitfield=True, bitfield_bits=1)])
        t_new = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag", "unsigned int", 0,
                                             is_bitfield=True, bitfield_bits=4)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_BITFIELD_CHANGED in _kinds(r)

    def test_became_bitfield(self):
        """Regular field became a bitfield."""
        t_old = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag", "unsigned int", 0,
                                             is_bitfield=False)])
        t_new = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag", "unsigned int", 0,
                                             is_bitfield=True, bitfield_bits=1)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_BITFIELD_CHANGED in _kinds(r)


# ── field_renamed (source-level break) ───────────────────────────────────

class TestFieldRenamed:
    """Field name changed but offset and type preserved."""

    def test_field_renamed(self):
        t_old = RecordType(name="Point", kind="struct", size_bits=64,
                           fields=[TypeField("x_pos", "int", 0),
                                   TypeField("y_pos", "int", 32)])
        t_new = RecordType(name="Point", kind="struct", size_bits=64,
                           fields=[TypeField("horizontal", "int", 0),
                                   TypeField("vertical", "int", 32)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_RENAMED in _kinds(r)


# ── enum_member_renamed (source-level break) ─────────────────────────────

class TestEnumMemberRenamed:
    """Enumerator name changed but value preserved."""

    def test_enum_member_renamed(self):
        e_old = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GRN", 1),
        ])
        e_new = EnumType(name="Color", members=[
            EnumMember("RED", 0), EnumMember("GREEN", 1),
        ])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_MEMBER_RENAMED in _kinds(r)


# ── enum_last_member_value_changed ───────────────────────────────────────

class TestEnumLastMemberValueChanged:
    """Sentinel/MAX enumerator value changes."""

    def test_last_member_value_changed(self):
        e_old = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 1), EnumMember("MAX", 2),
        ])
        e_new = EnumType(name="Status", members=[
            EnumMember("OK", 0), EnumMember("ERR", 1), EnumMember("MAX", 3),
        ])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in _kinds(r)


# ── type_became_opaque ───────────────────────────────────────────────────

class TestTypeBecameOpaque:
    """Complete type became forward-declaration only."""

    def test_type_became_opaque(self):
        t_old = RecordType(name="Handle", kind="struct", size_bits=64,
                           fields=[TypeField("ptr", "void *", 0)],
                           is_opaque=False)
        t_new = RecordType(name="Handle", kind="struct", size_bits=None,
                           fields=[], is_opaque=True)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_BECAME_OPAQUE in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ── type_alignment_changed (2 refs) ──────────────────────────────────────

class TestTypeAlignmentChanged:
    """Struct alignment change."""

    def test_alignment_changed(self):
        t_old = RecordType(name="AlignedData", kind="struct", size_bits=64,
                           alignment_bits=32)
        t_new = RecordType(name="AlignedData", kind="struct", size_bits=64,
                           alignment_bits=128)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_ALIGNMENT_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ── type_kind_changed ────────────────────────────────────────────────────

class TestTypeKindChanged:
    """struct → class or class → union etc."""

    def test_struct_to_class(self):
        t_old = RecordType(name="Widget", kind="struct", size_bits=32)
        t_new = RecordType(name="Widget", kind="class", size_bits=32)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.SOURCE_LEVEL_KIND_CHANGED in _kinds(r)


# ── removed_const_overload ──────────────────────────────────────────────

class TestRemovedConstOverload:
    """Const overload removed while non-const version remains."""

    def test_const_overload_removed(self):
        f_nc = _pub_func("Cls::get", "_ZN3Cls3getEv", ret="int", is_const=False)
        f_c = _pub_func("Cls::get", "_ZNK3Cls3getEv", ret="int", is_const=True)

        old = _snap(functions=[f_nc, f_c])
        new = _snap(functions=[f_nc])  # only non-const remains

        r = compare(old, new)
        kind_set = _kinds(r)
        assert ChangeKind.REMOVED_CONST_OVERLOAD in kind_set


# ── Multiple type changes at once ────────────────────────────────────────

class TestMultipleTypeChanges:
    """Multiple type-level changes in a single comparison."""

    def test_field_removed_and_type_changed(self):
        t_old = RecordType(name="Config", kind="struct", size_bits=96,
                           fields=[
                               TypeField("a", "int", 0),
                               TypeField("b", "int", 32),
                               TypeField("c", "int", 64),
                           ])
        t_new = RecordType(name="Config", kind="struct", size_bits=64,
                           fields=[
                               TypeField("a", "long", 0),   # type changed
                               TypeField("b", "int", 64),   # offset changed (c removed)
                           ])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kind_set = _kinds(r)
        assert ChangeKind.TYPE_FIELD_REMOVED in kind_set
        # At least one type/offset change should be detected
        assert kind_set & {
            ChangeKind.TYPE_FIELD_TYPE_CHANGED,
            ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
            ChangeKind.TYPE_SIZE_CHANGED,
        }

    def test_enum_member_added_and_value_changed(self):
        """Add a new enum member while changing an existing value."""
        e_old = EnumType(name="Priority", members=[
            EnumMember("LOW", 0), EnumMember("HIGH", 1),
        ])
        e_new = EnumType(name="Priority", members=[
            EnumMember("LOW", 0), EnumMember("HIGH", 10),
            EnumMember("URGENT", 100),
        ])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        kind_set = _kinds(r)
        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kind_set
        assert ChangeKind.ENUM_MEMBER_ADDED in kind_set


# ── Typedef changes ─────────────────────────────────────────────────────

class TestTypedefChanges:
    """Typedef removal and base change edge cases."""

    def test_typedef_removed_multiple(self):
        """Multiple typedefs removed at once."""
        old = _snap(typedefs={"IntPtr": "int*", "CharPtr": "char*", "VoidPtr": "void*"})
        new = _snap(typedefs={"IntPtr": "int*"})
        r = compare(old, new)
        removed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED]
        assert len(removed) == 2

    def test_typedef_base_changed(self):
        """Typedef underlying type changed."""
        old = _snap(typedefs={"Size": "unsigned int"})
        new = _snap(typedefs={"Size": "unsigned long"})
        r = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(r)


# ── Field access changes ────────────────────────────────────────────────

class TestFieldAccessChanged:
    """Field access level narrowing."""

    def test_field_public_to_private(self):
        t_old = RecordType(name="Cls", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0,
                                             access=AccessLevel.PUBLIC)])
        t_new = RecordType(name="Cls", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0,
                                             access=AccessLevel.PRIVATE)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_ACCESS_CHANGED in _kinds(r)

    def test_field_public_to_protected(self):
        t_old = RecordType(name="Cls", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0,
                                             access=AccessLevel.PUBLIC)])
        t_new = RecordType(name="Cls", kind="class", size_bits=32,
                           fields=[TypeField("data", "int", 0,
                                             access=AccessLevel.PROTECTED)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_ACCESS_CHANGED in _kinds(r)


# ── Base class changes ──────────────────────────────────────────────────

class TestBaseClassChanges:
    """Base class addition, removal, and reordering."""

    def test_base_added(self):
        t_old = RecordType(name="Derived", kind="class", size_bits=32, bases=[])
        t_new = RecordType(name="Derived", kind="class", size_bits=64, bases=["Base"])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(r)

    def test_base_removed(self):
        t_old = RecordType(name="Derived", kind="class", size_bits=64, bases=["Base"])
        t_new = RecordType(name="Derived", kind="class", size_bits=32, bases=[])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(r)

    def test_base_reordered(self):
        """Multiple inheritance base order changed — affects this-pointer layout."""
        t_old = RecordType(name="Multi", kind="class", size_bits=128,
                           bases=["BaseA", "BaseB"])
        t_new = RecordType(name="Multi", kind="class", size_bits=128,
                           bases=["BaseB", "BaseA"])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING
