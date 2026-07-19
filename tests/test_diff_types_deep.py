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

    def test_field_became_const_with_type_spelling_change_also_reports_type_changed(self):
        """A real dumper (castxml) spells the qualifier into `type` too
        ("int" -> "const int"), not just the boolean — and unlike a
        pointer/reference cv change, a BY-VALUE field's own const/volatile
        change is a deliberate source-break escalation (case30_field_qualifiers
        ground truth; see test_top_level_field_const_is_not_neutralised in
        test_const_pointer_abi_neutral.py), so both the compatible
        FIELD_BECAME_CONST and the breaking TYPE_FIELD_TYPE_CHANGED are
        expected together here — a prior attempt to suppress the latter
        (Codex review, PR #582) was reverted because it silently regressed
        that ground truth."""
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "int", 0, is_const=False)])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("val", "const int", 0, is_const=True)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kinds = _kinds(r)
        assert ChangeKind.FIELD_BECAME_CONST in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds
        assert r.verdict == Verdict.BREAKING


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

    def test_field_became_volatile_with_type_spelling_change_also_reports_type_changed(self):
        """Same as the by-value const case above: a field changing from
        "int" to "volatile int" (castxml's real spelling) is a deliberate
        source-break escalation, so both FIELD_BECAME_VOLATILE and
        TYPE_FIELD_TYPE_CHANGED fire, and the verdict is BREAKING — not
        merely COMPATIBLE (a prior attempt to suppress the latter, per
        Codex review on PR #582, was reverted as an incorrect regression)."""
        t_old = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "int", 0, is_volatile=False)])
        t_new = RecordType(name="Reg", kind="struct", size_bits=32,
                           fields=[TypeField("status", "volatile int", 0, is_volatile=True)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kinds = _kinds(r)
        assert ChangeKind.FIELD_BECAME_VOLATILE in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds
        assert r.verdict == Verdict.BREAKING


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
        # A pure rename must not ALSO surface as a field removal/addition —
        # that would overstate a source-only break as BREAKING (case35).
        assert ChangeKind.TYPE_FIELD_REMOVED not in _kinds(r)
        assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE not in _kinds(r)
        assert r.verdict == Verdict.API_BREAK

    def test_field_renamed_reports_severity_not_verdict_breaking(self):
        """A rename-only diff resolves to API_BREAK, never BREAKING."""
        t_old = RecordType(name="Point", kind="struct", size_bits=64,
                           fields=[TypeField("x", "int", 0),
                                   TypeField("y", "int", 32)])
        t_new = RecordType(name="Point", kind="struct", size_bits=64,
                           fields=[TypeField("col", "int", 0),
                                   TypeField("row", "int", 32)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert r.verdict == Verdict.API_BREAK

    def test_field_renamed_with_differently_spelled_equal_type(self):
        """A rename is still reported even when the two sides spell the
        (canonically identical) field type differently.

        ``_diff_field_renames`` keys on the *raw* ``(offset, type)`` tuple, so
        it alone would not match "struct Foo" against "Foo" for the same
        field. The generic field-removed/-added path must report the rename
        itself rather than assuming the dedicated detector already covers
        it — otherwise the finding is silently dropped instead of merely
        duplicated (regression guard for a review finding on the case35 fix).
        """
        t_old = RecordType(name="Widget", kind="struct", size_bits=64,
                           fields=[TypeField("handle", "struct Foo *", 0)])
        t_new = RecordType(name="Widget", kind="struct", size_bits=64,
                           fields=[TypeField("ref", "Foo *", 0)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kinds = _kinds(r)
        assert ChangeKind.FIELD_RENAMED in kinds
        assert ChangeKind.TYPE_FIELD_REMOVED not in kinds

    def test_bitfield_width_change_is_not_masked_as_a_bare_rename(self) -> None:
        """A bit-field's width is a layout property the type spelling alone
        doesn't capture — two "unsigned int" bit-fields at the same offset
        can still differ in width. Renaming *and* widening a bit-field in
        the same edit must not collapse to a bare FIELD_RENAMED (regression
        guard for a review finding on the case35 fix).
        """
        t_old = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag_a", "unsigned int", 0,
                                             is_bitfield=True, bitfield_bits=1)])
        t_new = RecordType(name="Flags", kind="struct", size_bits=32,
                           fields=[TypeField("flag_b", "unsigned int", 0,
                                             is_bitfield=True, bitfield_bits=4)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kinds = _kinds(r)
        assert ChangeKind.FIELD_RENAMED not in kinds

    def test_two_same_offset_fields_collapsing_to_one_do_not_both_rename(
        self,
    ) -> None:
        """Two old fields sharing an offset (e.g. overlapping anonymous-union
        members) collapsing to a single new field at that offset must not
        both be reported as FIELD_RENAMED to the same target — only the
        first can genuinely be "the same field renamed"; the other was
        really removed. Without tracking which added field a rename has
        already consumed, both would silently claim it, hiding the real
        removal (regression guard for a review finding on the case35 fix).
        """
        t_old = RecordType(name="Overlay", kind="struct", size_bits=32,
                           fields=[TypeField("a", "int", 0),
                                   TypeField("b", "int", 0)])
        t_new = RecordType(name="Overlay", kind="struct", size_bits=32,
                           fields=[TypeField("x", "int", 0)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        renames = [c for c in r.changes if c.kind == ChangeKind.FIELD_RENAMED]
        removals = [c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_REMOVED]
        assert len(renames) == 1
        assert len(removals) == 1
        assert renames[0].new_value == "x"

    def test_exact_type_candidate_preferred_over_first_same_offset_candidate(
        self,
    ) -> None:
        """When several distinct added fields share an offset (anonymous-
        union/overlap layout), the exact-type rename candidate must be
        preferred over an arbitrary first-in-order one, even if that first
        one is a different, unrelated type. Old ``a: int`` becoming new
        ``x: float, y: int`` (both at offset 0) is really "a renamed to y";
        picking "x" first would report a false TYPE_FIELD_TYPE_CHANGED for
        "a" *and* leave the independent `_diff_field_renames` detector free
        to separately claim "y" as FIELD_RENAMED — two contradictory
        findings about the same old field (review finding on the
        collapsing-duplicate-rename fix).
        """
        t_old = RecordType(name="Overlay", kind="struct", size_bits=32,
                           fields=[TypeField("a", "int", 0)])
        t_new = RecordType(name="Overlay", kind="struct", size_bits=32,
                           fields=[TypeField("x", "float", 0),
                                   TypeField("y", "int", 0)])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        kinds = _kinds(r)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in kinds
        renames = [c for c in r.changes if c.kind == ChangeKind.FIELD_RENAMED]
        assert len(renames) == 1
        assert renames[0].old_value == "a"
        assert renames[0].new_value == "y"


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


# ── Field default initializer changes ───────────────────────────────────

class TestFieldDefaultInitializerChanged:
    """FIELD_DEFAULT_INITIALIZER_CHANGED's description must render the real
    old/new values, not the literal string "None" (Codex review, PR #582):
    make_change() only fills {old}/{new} in the registry description
    template from its ``old=``/``new=`` kwargs, not from ``old_value``/
    ``new_value`` — passing the latter alone left the template's
    placeholders unformatted."""

    def test_description_contains_real_old_and_new_values(self):
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("timeout", "int", 0, default="30")])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("timeout", "int", 0, default="60")])
        old = AbiSnapshot(library="libtest.so.1", version="1.0",
                          types=[t_old], ast_producer="castxml", from_headers=True)
        new = AbiSnapshot(library="libtest.so.1", version="2.0",
                          types=[t_new], ast_producer="castxml", from_headers=True)
        r = compare(old, new)
        changed = [c for c in r.changes
                   if c.kind == ChangeKind.FIELD_DEFAULT_INITIALIZER_CHANGED]
        assert len(changed) == 1
        assert changed[0].old_value == "30"
        assert changed[0].new_value == "60"
        assert "30" in changed[0].description
        assert "60" in changed[0].description
        assert "None" not in changed[0].description


class TestFieldDeprecated:
    """TypeField.deprecated was populated and serialized but nothing read
    it — a field changing from `int x;` to `[[deprecated]] int x;` (or
    losing the marker) went completely undetected (Codex review, PR #582).
    """

    def _snap(self, version, default=None):
        t = RecordType(name="Cfg", kind="struct", size_bits=32,
                       fields=[TypeField("count", "int", 0, deprecated=default)])
        return AbiSnapshot(library="libtest.so.1", version=version, types=[t],
                          from_headers=True, ast_producer="castxml")

    def test_field_gained_deprecated(self):
        old = self._snap("1.0", default=None)
        new = self._snap("2.0", default="use new_count instead")
        r = compare(old, new)
        changed = [c for c in r.changes if c.kind == ChangeKind.FIELD_DEPRECATED_ADDED]
        assert len(changed) == 1
        assert "use new_count instead" in changed[0].description
        assert r.verdict == Verdict.COMPATIBLE

    def test_field_lost_deprecated(self):
        old = self._snap("1.0", default="use new_count instead")
        new = self._snap("2.0", default=None)
        r = compare(old, new)
        assert ChangeKind.FIELD_DEPRECATED_REMOVED in _kinds(r)

    def test_bare_deprecated_with_no_message(self):
        old = self._snap("1.0", default=None)
        new = self._snap("2.0", default="")
        r = compare(old, new)
        assert ChangeKind.FIELD_DEPRECATED_ADDED in _kinds(r)

    def test_gated_on_both_castxml_backed(self):
        """clang doesn't populate TypeField.deprecated yet — a producer
        mismatch must not misread it as the field losing its marker."""
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("count", "int", 0, deprecated="msg")])
        t_new = RecordType(name="Cfg", kind="struct", size_bits=32,
                           fields=[TypeField("count", "int", 0, deprecated=None)])
        old = AbiSnapshot(library="libtest.so.1", version="1.0", types=[t_old],
                          from_headers=True, ast_producer="castxml")
        new = AbiSnapshot(library="libtest.so.1", version="2.0", types=[t_new],
                          from_headers=True, ast_producer="clang")
        r = compare(old, new)
        assert ChangeKind.FIELD_DEPRECATED_REMOVED not in _kinds(r)

    def test_union_variant_deprecated_detected(self):
        """Matches FIELD_DEFAULT_INITIALIZER_*'s union-inclusive precedent:
        a union variant can carry [[deprecated]] too."""
        u_old = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("as_int", "int", 0, deprecated=None)])
        u_new = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("as_int", "int", 0, deprecated="msg")])
        old = AbiSnapshot(library="libtest.so.1", version="1.0", types=[u_old],
                          from_headers=True, ast_producer="castxml")
        new = AbiSnapshot(library="libtest.so.1", version="2.0", types=[u_new],
                          from_headers=True, ast_producer="castxml")
        r = compare(old, new)
        assert ChangeKind.FIELD_DEPRECATED_ADDED in _kinds(r)


class TestLegacyCvFactsReliableGating:
    """A persisted pre-CV-fact-fix CastXML snapshot must not misreport a
    false FIELD_BECAME_CONST/VOLATILE/MUTABLE or TYPE_FIELD_TYPE_CHANGED
    purely from a tool upgrade, when compared against a fresh dump of
    genuinely unchanged headers (Codex review, PR #582 — the two open
    findings following the destructor/namespace-qualification fixes).

    The pre-fix parser left TypeField.is_const/is_volatile/is_mutable
    permanently False and never included the qualifier in the field's type
    spelling; a legacy snapshot's False/qualifier-less values are
    real-but-wrong data, not absent data, so only a snapshot-level
    ``header_cv_facts_reliable`` marker (derived from schema_version on
    deserialization) can distinguish them from a genuine "not const" fact.
    """

    def _legacy_snap(self, version, **type_kwargs):
        t = RecordType(name="Cfg", kind="struct", size_bits=32,
                       fields=[TypeField("flag", **type_kwargs)])
        return AbiSnapshot(
            library="libtest.so.1", version=version, types=[t],
            from_headers=True, ast_producer="castxml",
            header_cv_facts_reliable=False,
        )

    def _fresh_snap(self, version, **type_kwargs):
        t = RecordType(name="Cfg", kind="struct", size_bits=32,
                       fields=[TypeField("flag", **type_kwargs)])
        return AbiSnapshot(
            library="libtest.so.1", version=version, types=[t],
            from_headers=True, ast_producer="castxml",
            header_cv_facts_reliable=True,
        )

    def test_legacy_vs_fresh_suppresses_false_field_became_volatile(self):
        old = self._legacy_snap("1.0", type="int", offset_bits=0,
                                is_const=False, is_volatile=False)
        new = self._fresh_snap("2.0", type="volatile int", offset_bits=0,
                               is_const=False, is_volatile=True)
        r = compare(old, new)
        kinds = _kinds(r)
        assert ChangeKind.FIELD_BECAME_VOLATILE not in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in kinds
        assert r.verdict == Verdict.NO_CHANGE

    def test_legacy_vs_fresh_suppresses_false_field_became_const(self):
        old = self._legacy_snap("1.0", type="int", offset_bits=0,
                                is_const=False, is_volatile=False)
        new = self._fresh_snap("2.0", type="const int", offset_bits=0,
                               is_const=True, is_volatile=False)
        r = compare(old, new)
        kinds = _kinds(r)
        assert ChangeKind.FIELD_BECAME_CONST not in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in kinds

    def test_both_reliable_still_detects_genuine_volatile_addition(self):
        """The gate must not neutralize a REAL change between two
        current-generation snapshots — only the mixed legacy/fresh pairing."""
        old = self._fresh_snap("1.0", type="int", offset_bits=0,
                               is_const=False, is_volatile=False)
        new = self._fresh_snap("2.0", type="volatile int", offset_bits=0,
                               is_const=False, is_volatile=True)
        r = compare(old, new)
        kinds = _kinds(r)
        assert ChangeKind.FIELD_BECAME_VOLATILE in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds
        assert r.verdict == Verdict.BREAKING

    def test_unrelated_type_change_still_detected_when_legacy(self):
        """A genuine non-cv type change (int -> double) must still fire
        even when the pair is legacy/unreliable for cv facts specifically —
        the gate only neutralizes the cv axis, not the whole detector."""
        old = self._legacy_snap("1.0", type="int", offset_bits=0)
        new = self._fresh_snap("2.0", type="double", offset_bits=0)
        r = compare(old, new)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(r)

    def test_union_variant_legacy_vs_fresh_suppresses_false_positive(self):
        u_old = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("val", "int", 0)])
        u_new = RecordType(name="Value", kind="union", size_bits=32, is_union=True,
                           fields=[TypeField("val", "volatile int", 0)])
        old = AbiSnapshot(library="libtest.so.1", version="1.0", types=[u_old],
                          from_headers=True, ast_producer="castxml",
                          header_cv_facts_reliable=False)
        new = AbiSnapshot(library="libtest.so.1", version="2.0", types=[u_new],
                          from_headers=True, ast_producer="castxml",
                          header_cv_facts_reliable=True)
        r = compare(old, new)
        assert ChangeKind.UNION_FIELD_TYPE_CHANGED not in _kinds(r)


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


# ── Bare-name collision across distinct qualified scopes (pvxs FP) ────────

class TestQualifiedNameMatching:
    """Two unrelated types that only share a short/leaf name (e.g. two
    distinct ``std::*::_Impl`` template internals pulled in transitively via
    different headers) must not be diffed against each other. Header-mode
    dumpers set ``RecordType.qualified_name`` alongside the deliberately-bare
    ``name`` (see model.py); matching must prefer it.
    """

    def test_unrelated_same_leaf_name_types_not_cross_matched(self):
        # old: two distinct scopes' "_Impl", each losing/gaining an unrelated field.
        old_a = RecordType(name="_Impl", qualified_name="std::locale::_Impl",
                           kind="class", size_bits=64,
                           fields=[TypeField("_M_refcount", "int", 0)])
        old_b = RecordType(name="_Impl", qualified_name="ns::Other::_Impl",
                           kind="class", size_bits=32,
                           fields=[TypeField("_M_storage", "int", 0)])
        # new: each scope's own _Impl is unchanged; only cross-matching by the
        # bare "_Impl" name would fabricate a field-removed/field-added diff.
        new_a = RecordType(name="_Impl", qualified_name="std::locale::_Impl",
                           kind="class", size_bits=64,
                           fields=[TypeField("_M_refcount", "int", 0)])
        new_b = RecordType(name="_Impl", qualified_name="ns::Other::_Impl",
                           kind="class", size_bits=32,
                           fields=[TypeField("_M_storage", "int", 0)])

        r = compare(_snap(types=[old_a, old_b]), _snap(types=[new_a, new_b]))

        assert ChangeKind.TYPE_FIELD_REMOVED not in _kinds(r)
        assert ChangeKind.TYPE_FIELD_ADDED not in _kinds(r)

    def test_genuine_change_still_detected_when_qualified(self):
        t_old = RecordType(name="_Impl", qualified_name="ns::Scope::_Impl",
                           kind="class", size_bits=64,
                           fields=[TypeField("count", "int", 0)])
        t_new = RecordType(name="_Impl", qualified_name="ns::Scope::_Impl",
                           kind="class", size_bits=32, fields=[])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        assert ChangeKind.TYPE_FIELD_REMOVED in _kinds(r)

    def test_legacy_snapshot_without_qualified_name_still_matches(self):
        """Codex review, PR #608: an older serialized/header snapshot that
        predates ``qualified_name`` (so it's unset — ``None``, the dataclass
        default) must still match against a freshly-dumped snapshot side
        where the same namespaced type now populates it, instead of
        manufacturing a false TYPE_REMOVED + TYPE_ADDED pair.
        """
        # old side: simulates a pre-qualified_name snapshot — unset even
        # though the type is genuinely namespaced.
        t_old = RecordType(name="Handle", qualified_name=None,
                           kind="class", size_bits=64,
                           fields=[TypeField("count", "int", 0)])
        # new side: freshly dumped, qualified_name populated as usual.
        t_new = RecordType(name="Handle", qualified_name="ns::Handle",
                           kind="class", size_bits=32, fields=[])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        kinds = _kinds(r)
        assert ChangeKind.TYPE_REMOVED not in kinds
        assert ChangeKind.TYPE_ADDED not in kinds
        assert ChangeKind.TYPE_FIELD_REMOVED in kinds

    def test_legacy_snapshot_on_new_side_still_matches(self):
        """Codex review, PR #608 (second round): the mirror of the case
        above — a FRESH old side (qualified_name populated) compared against
        a LEGACY new side (qualified_name unset) — must also match, not just
        the legacy-old/fresh-new direction. ``TypeMap``'s bare-name alias
        only maps bare -> qualified, so a naive ``new_map.get(old_side's_own_
        qualified_key)`` lookup misses when the *new* side is the legacy one;
        ``diff_helpers.lookup_matched_type`` retries with the bare name to
        make this symmetric.
        """
        t_old = RecordType(name="Handle", qualified_name="ns::Handle",
                           kind="class", size_bits=64,
                           fields=[TypeField("count", "int", 0)])
        t_new = RecordType(name="Handle", qualified_name=None,
                           kind="class", size_bits=32, fields=[])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        kinds = _kinds(r)
        assert ChangeKind.TYPE_REMOVED not in kinds
        assert ChangeKind.TYPE_ADDED not in kinds
        assert ChangeKind.TYPE_FIELD_REMOVED in kinds

    def test_namespaced_type_diff_not_doubled_by_alias(self):
        """A normal namespaced type (both sides populate ``qualified_name``,
        the common case, no legacy mismatch at all) must still produce each
        finding exactly once. The bare-name alias exists purely for
        cross-schema lookups and must never leak into iteration, or every
        namespaced-type finding in the codebase would double-report.
        """
        t_old = RecordType(name="Widget", qualified_name="ns::Widget",
                           kind="class", size_bits=64,
                           fields=[TypeField("count", "int", 0)])
        t_new = RecordType(name="Widget", qualified_name="ns::Widget",
                           kind="class", size_bits=32, fields=[])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        field_removed = [c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_REMOVED]
        assert len(field_removed) == 1
        assert ChangeKind.TYPE_ADDED not in _kinds(r)
        assert ChangeKind.TYPE_REMOVED not in _kinds(r)

    def test_legacy_alias_does_not_reopen_leaf_name_collision(self):
        """The bare-name compatibility alias must stay collision-safe: a
        legacy (``qualified_name=None``) type must not accidentally match an
        unrelated, differently-scoped type on the other side purely because
        that side also has an ambiguous/collided bare name.
        """
        old_a = RecordType(name="_Impl", qualified_name=None,
                           kind="class", size_bits=64,
                           fields=[TypeField("_M_refcount", "int", 0)])
        new_a = RecordType(name="_Impl", qualified_name="std::locale::_Impl",
                           kind="class", size_bits=64,
                           fields=[TypeField("_M_refcount", "int", 0)])
        new_b = RecordType(name="_Impl", qualified_name="ns::Other::_Impl",
                           kind="class", size_bits=32,
                           fields=[TypeField("_M_storage", "int", 0)])

        r = compare(_snap(types=[old_a]), _snap(types=[new_a, new_b]))

        # old_a's bare "_Impl" is ambiguous on the new side (two distinct
        # qualified identities), so no alias is registered there — old_a
        # must not cross-match either new_a or new_b.
        assert ChangeKind.TYPE_FIELD_REMOVED not in _kinds(r)
        assert ChangeKind.TYPE_FIELD_ADDED not in _kinds(r)


class TestHybridProvenanceKeys:
    """Codex review, PR #608: ``dumper_hybrid`` records per-fact provenance
    under the bare ``RecordType.name`` (``type_fact_key``/``field_fact_key``),
    not the namespace-qualified matching key ``_type_map_key`` introduced for
    FP-1. The type-level detectors that gate on
    ``fact_provenance.both_castxml_backed_fact`` per declaration must look the
    provenance key up by the bare name, or a hybrid snapshot's castxml-only
    facts (``is_abstract``, ``deprecated``, field ``default``) silently stop
    firing for every namespaced type.
    """

    def _hybrid_snap(self, version, types):
        return AbiSnapshot(
            library="libtest.so.1", version=version, types=types,
            from_headers=True, ast_producer="hybrid",
            fact_provenance={"type:Impl:is_abstract": "castxml"},
        )

    def test_became_abstract_detected_for_namespaced_type_in_hybrid_snapshot(self):
        t_old = RecordType(name="Impl", qualified_name="ns::Impl", kind="class",
                           size_bits=64, is_abstract=False)
        t_new = RecordType(name="Impl", qualified_name="ns::Impl", kind="class",
                           size_bits=64, is_abstract=True)

        r = compare(self._hybrid_snap("1.0", [t_old]), self._hybrid_snap("2.0", [t_new]))

        assert ChangeKind.TYPE_BECAME_ABSTRACT in _kinds(r)


class TestEmittedSymbolStaysBare:
    """The qualified matching key introduced for FP-1 must stay internal to
    old/new type matching. Every emitted ``Change.symbol`` for a namespaced
    type must still be the bare declaration name — the identity every other
    consumer already keys on: ``diff_filtering._dedup_cross_kind``'s DWARF<->
    AST redundancy correlation matches a DWARF field symbol's *bare* parent
    type name against the AST-level type symbol (FIX-F), so a qualified
    symbol here would silently defeat that correlation for any namespaced
    type — exactly the regression a real castxml-backed integration test
    (case187/191 in ``tests/test_header_graph_examples.py``) caught (Codex
    review, PR #608).
    """

    def test_field_type_changed_symbol_is_bare_for_namespaced_type(self):
        t_old = RecordType(name="Public", qualified_name="demo::Public", kind="struct",
                           size_bits=96, fields=[TypeField("reserved", "void *", 64)])
        t_new = RecordType(name="Public", qualified_name="demo::Public", kind="struct",
                           size_bits=96, fields=[TypeField("reserved", "detail::PrivateType *", 64)])

        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))

        field_changes = [c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED]
        assert len(field_changes) == 1
        assert field_changes[0].symbol == "Public"

    def test_type_added_symbol_is_bare_for_namespaced_type(self):
        t_new = RecordType(name="Widget", qualified_name="ns::Widget", kind="class", size_bits=32)

        r = compare(_snap(types=[]), _snap(types=[t_new]))

        added = [c for c in r.changes if c.kind == ChangeKind.TYPE_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "Widget"
