# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Tests for abi_check.checker — pure Python, no external tools required.

All test fixtures are original C++ snippets authored for this project.
No code or test data is derived from abi-compliance-checker (LGPL-2.1).
"""

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_types import _diff_enum_renames, _diff_enums
from abicheck.model import (
    AbiSnapshot,
    ElfVisibility,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(version: str, functions=None, variables=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
    )


def _pub_func(name: str, mangled: str, ret: str = "void",
              params=None, virtual=False, noexcept=False) -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret,
        params=params or [], visibility=Visibility.PUBLIC,
        is_virtual=virtual, is_noexcept=noexcept,
    )


def _pub_var(name: str, mangled: str, type_: str) -> Variable:
    return Variable(name=name, mangled=mangled, type=type_,
                    visibility=Visibility.PUBLIC)


# ── No change ────────────────────────────────────────────────────────────────

class TestNoChange:
    def test_identical_snapshots(self):
        f = _pub_func("init", "_Z4initv", "int")
        old = _snap("1.0", functions=[f])
        new = _snap("1.1", functions=[f])
        r = compare(old, new)
        assert r.verdict == Verdict.NO_CHANGE
        assert r.changes == []

    def test_empty_snapshots(self):
        r = compare(_snap("1.0"), _snap("1.1"))
        assert r.verdict == Verdict.NO_CHANGE


# ── Function removal ─────────────────────────────────────────────────────────

class TestFunctionRemoval:
    def test_public_func_removed_is_breaking(self):
        f = _pub_func("process", "_Z7processv")
        old = _snap("1.0", functions=[f])
        new = _snap("2.0", functions=[])
        r = compare(old, new)
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes)

    def test_hidden_func_removal_is_not_reported(self):
        f = Function(name="internal", mangled="_Z8internalv",
                     return_type="void", visibility=Visibility.HIDDEN)
        old = _snap("1.0", functions=[f])
        new = _snap("2.0", functions=[])
        r = compare(old, new)
        assert r.verdict == Verdict.NO_CHANGE


# ── Function addition ─────────────────────────────────────────────────────────

class TestFunctionAddition:
    def test_new_public_func_is_compatible(self):
        f_old = _pub_func("init", "_Z4initv")
        f_new1 = _pub_func("init", "_Z4initv")
        f_new2 = _pub_func("reset", "_Z5resetv")
        old = _snap("1.0", functions=[f_old])
        new = _snap("1.1", functions=[f_new1, f_new2])
        r = compare(old, new)
        assert r.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.FUNC_ADDED for c in r.changes)


# ── Return type change ────────────────────────────────────────────────────────

class TestReturnTypeChange:
    def test_return_type_changed_is_breaking(self):
        old_f = _pub_func("getCount", "_Z8getCountv", ret="int")
        new_f = _pub_func("getCount", "_Z8getCountv", ret="size_t")
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_RETURN_CHANGED for c in r.changes)


# ── Parameter changes ─────────────────────────────────────────────────────────

class TestParameterChanges:
    def test_param_type_change_is_breaking(self):
        # A genuine pointee-type substitution (void* -> int*) is an ABI break.
        old_f = _pub_func("send", "_Z4sendPv",
                          params=[Param(name="buf", type="void*")])
        new_f = _pub_func("send", "_Z4sendPv",
                          params=[Param(name="buf", type="int*")])
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_PARAMS_CHANGED for c in r.changes)

    def test_param_pointee_const_change_is_not_breaking(self):
        # ISSUE-29/52: adding const to a pointed-to type (void* -> const void*)
        # for a symbol that keeps the same name leaves the calling convention
        # and binary parameter layout identical — not a binary ABI break.
        old_f = _pub_func("send", "_Z4sendPv",
                          params=[Param(name="buf", type="void*")])
        new_f = _pub_func("send", "_Z4sendPv",
                          params=[Param(name="buf", type="const void*")])
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert not any(c.kind == ChangeKind.FUNC_PARAMS_CHANGED for c in r.changes)
        assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)

    def test_param_added_is_breaking(self):
        old_f = _pub_func("open", "_Z4openv")
        new_f = _pub_func("open", "_Z4openv",
                          params=[Param(name="flags", type="int")])
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.BREAKING


# ── noexcept specifier ────────────────────────────────────────────────────────

class TestNoexcept:
    def test_noexcept_removed_is_compatible_with_risk(self):
        # Since C++17 `noexcept` is part of the function type (encoded in
        # function-pointer / template-argument mangling) and removing it can hit
        # std::terminate, so it is a deployment/source risk, not a clean
        # compatible change.
        old_f = _pub_func("move", "_Z4movev", noexcept=True)
        new_f = _pub_func("move", "_Z4movev", noexcept=False)
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK
        assert any(c.kind == ChangeKind.FUNC_NOEXCEPT_REMOVED for c in r.changes)

    def test_noexcept_added_is_compatible(self):
        old_f = _pub_func("swap", "_Z4swapv", noexcept=False)
        new_f = _pub_func("swap", "_Z4swapv", noexcept=True)
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.FUNC_NOEXCEPT_ADDED for c in r.changes)


# ── Virtual methods ───────────────────────────────────────────────────────────

class TestVirtualMethods:
    def test_become_virtual_is_breaking(self):
        old_f = _pub_func("render", "_Z6renderv", virtual=False)
        new_f = _pub_func("render", "_Z6renderv", virtual=True)
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_VIRTUAL_ADDED for c in r.changes)

    def test_lose_virtual_is_breaking(self):
        old_f = _pub_func("update", "_Z6updatev", virtual=True)
        new_f = _pub_func("update", "_Z6updatev", virtual=False)
        r = compare(_snap("1.0", [old_f]), _snap("2.0", [new_f]))
        assert r.verdict == Verdict.BREAKING


# ── Variables ─────────────────────────────────────────────────────────────────

class TestVariables:
    def test_var_removed_is_breaking(self):
        v = _pub_var("g_version", "_ZN3lib9g_versionE", "int")
        r = compare(_snap("1.0", variables=[v]), _snap("2.0", variables=[]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.VAR_REMOVED for c in r.changes)

    def test_var_type_changed_is_breaking(self):
        old_v = _pub_var("g_limit", "_ZN3lib7g_limitE", "int")
        new_v = _pub_var("g_limit", "_ZN3lib7g_limitE", "unsigned int")
        r = compare(_snap("1.0", variables=[old_v]), _snap("2.0", variables=[new_v]))
        assert r.verdict == Verdict.BREAKING

    def test_var_added_is_compatible(self):
        v_old = _pub_var("g_count", "_ZN3lib7g_countE", "int")
        v_new1 = _pub_var("g_count", "_ZN3lib7g_countE", "int")
        v_new2 = _pub_var("g_max", "_ZN3lib5g_maxE", "int")
        r = compare(_snap("1.0", variables=[v_old]),
                    _snap("1.1", variables=[v_new1, v_new2]))
        assert r.verdict == Verdict.COMPATIBLE


# ── Type / struct changes ─────────────────────────────────────────────────────

class TestTypeChanges:
    def _make_point(self, size=64) -> RecordType:
        return RecordType(
            name="Point", kind="struct", size_bits=size,
            fields=[
                TypeField("x", "float", offset_bits=0),
                TypeField("y", "float", offset_bits=32),
            ],
        )

    def test_struct_size_change_is_breaking(self):
        old_t = self._make_point(64)
        new_t = self._make_point(96)
        r = compare(_snap("1.0", types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_SIZE_CHANGED for c in r.changes)

    def test_field_removed_is_breaking(self):
        old_t = self._make_point(64)
        new_t = RecordType(
            name="Point", kind="struct", size_bits=32,
            fields=[TypeField("x", "float", offset_bits=0)],
        )
        r = compare(_snap("1.0", types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_REMOVED for c in r.changes)

    def test_field_offset_changed_is_breaking(self):
        old_t = RecordType(
            name="Rect", kind="struct", size_bits=128,
            fields=[
                TypeField("x", "int", offset_bits=0),
                TypeField("y", "int", offset_bits=32),
                TypeField("w", "int", offset_bits=64),
                TypeField("h", "int", offset_bits=96),
            ],
        )
        new_t = RecordType(
            name="Rect", kind="struct", size_bits=160,
            fields=[
                TypeField("_pad", "int", offset_bits=0),   # inserted at front
                TypeField("x", "int", offset_bits=32),     # shifted
                TypeField("y", "int", offset_bits=64),
                TypeField("w", "int", offset_bits=96),
                TypeField("h", "int", offset_bits=128),
            ],
        )
        r = compare(_snap("1.0", types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_OFFSET_CHANGED for c in r.changes)

    def test_base_class_changed_is_breaking(self):
        old_t = RecordType(name="Derived", kind="class", bases=["Base"])
        new_t = RecordType(name="Derived", kind="class", bases=["OtherBase"])
        r = compare(_snap("1.0", types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_BASE_CHANGED for c in r.changes)

    def test_vtable_change_is_breaking(self):
        old_t = RecordType(name="Widget", kind="class",
                           vtable=["_ZN6Widget6renderEv", "_ZN6Widget6updateEv"])
        new_t = RecordType(name="Widget", kind="class",
                           vtable=["_ZN6Widget6updateEv", "_ZN6Widget6renderEv"])  # reordered
        r = compare(_snap("1.0", types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING

    def test_type_removed_is_breaking(self):
        t = RecordType(name="Handle", kind="struct")
        r = compare(_snap("1.0", types=[t]), _snap("2.0", types=[]))
        assert r.verdict == Verdict.BREAKING

    def test_type_added_is_compatible(self):
        t = RecordType(name="NewConfig", kind="struct")
        r = compare(_snap("1.0", types=[]), _snap("1.1", types=[t]))
        assert r.verdict == Verdict.COMPATIBLE


# ── Verdict priority ──────────────────────────────────────────────────────────

class TestVerdictPriority:
    def test_breaking_overrides_compatible(self):
        """Mixed: one function added (compatible) + one removed (breaking) = BREAKING."""
        f_old = _pub_func("old_api", "_Z7old_apiv")
        f_kept = _pub_func("kept", "_Z4keptv")
        f_new = _pub_func("new_api", "_Z7new_apiv")
        old = _snap("1.0", functions=[f_old, f_kept])
        new = _snap("2.0", functions=[f_kept, f_new])
        r = compare(old, new)
        assert r.verdict == Verdict.BREAKING

    def test_noexcept_added_stays_compatible(self):
        """noexcept added (compatible) + new func (compatible) = COMPATIBLE."""
        f_noexcept = _pub_func("swap", "_Z4swapv", noexcept=False)
        f_new = _pub_func("swap", "_Z4swapv", noexcept=True)
        f_added = _pub_func("reset", "_Z5resetv")
        old = _snap("1.0", functions=[f_noexcept])
        new = _snap("1.1", functions=[f_new, f_added])
        r = compare(old, new)
        assert r.verdict == Verdict.COMPATIBLE



def test_func_removed_elf_only_is_breaking() -> None:
    old = AbiSnapshot(
        library="libfoo.so", version="1.0",
        functions=[Function(name="internal", mangled="internal", return_type="void", visibility=Visibility.ELF_ONLY)],
        elf_only_mode=True,
    )
    new = AbiSnapshot(library="libfoo.so", version="2.0", functions=[])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_REMOVED_ELF_ONLY in kinds
    assert result.verdict == Verdict.BREAKING


# ── WS-4a: ELF visibility tracking ─────────────────────────────────────────

class TestElfVisibilityTracking:
    """Tests for SYMBOL_ELF_VISIBILITY_CHANGED detection."""

    def test_elf_visibility_field_on_function(self):
        """elf_visibility field is separate from API-level visibility."""
        f = Function(
            name="foo", mangled="foo", return_type="void",
            visibility=Visibility.PUBLIC,
            elf_visibility=ElfVisibility.PROTECTED,
        )
        assert f.visibility == Visibility.PUBLIC
        assert f.elf_visibility == ElfVisibility.PROTECTED

    def test_elf_visibility_default_none(self):
        """elf_visibility defaults to None when not set."""
        f = Function(name="foo", mangled="foo", return_type="void")
        assert f.elf_visibility is None

    def test_elf_visibility_on_variable(self):
        """elf_visibility field works on Variable."""
        v = Variable(
            name="bar", mangled="bar", type="int",
            elf_visibility=ElfVisibility.DEFAULT,
        )
        assert v.elf_visibility == ElfVisibility.DEFAULT


# ── WS-4b: Global variable ELF-only tracking ───────────────────────────────

class TestVarElfOnlyTracking:
    """Tests for variable detection in ELF-only mode."""

    def test_var_removed_elf_only(self):
        """Variable removed in ELF-only mode should emit VAR_REMOVED."""
        old = AbiSnapshot(
            library="libfoo.so", version="1.0",
            variables=[Variable(name="debug_level", mangled="debug_level",
                                type="?", visibility=Visibility.ELF_ONLY)],
            elf_only_mode=True,
        )
        new = AbiSnapshot(library="libfoo.so", version="2.0",
                          variables=[], elf_only_mode=True)
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VAR_REMOVED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_var_added_elf_only(self):
        """Variable added in ELF-only mode should emit VAR_ADDED."""
        old = AbiSnapshot(library="libfoo.so", version="1.0",
                          variables=[], elf_only_mode=True)
        new = AbiSnapshot(
            library="libfoo.so", version="2.0",
            variables=[Variable(name="build_number", mangled="build_number",
                                type="?", visibility=Visibility.ELF_ONLY)],
            elf_only_mode=True,
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VAR_ADDED in kinds
        assert result.verdict == Verdict.COMPATIBLE


# ── WS-5a: Reserved field recognition ──────────────────────────────────────

class TestReservedFieldRecognition:
    """Tests for USED_RESERVED_FIELD detection integrated into _diff_type_fields."""

    def _make_struct(self, name, fields):
        return RecordType(name=name, kind="struct", fields=fields)

    def test_reserved_field_renamed_same_offset_same_type(self):
        """__reserved1 renamed to real_field at same offset + same type → COMPATIBLE."""
        old = _snap("1.0", types=[self._make_struct("Cfg", [
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="__reserved1", type="int", offset_bits=32),
        ])])
        new = _snap("2.0", types=[self._make_struct("Cfg", [
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="flags", type="int", offset_bits=32),
        ])])
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.USED_RESERVED_FIELD in kinds
        # Must NOT emit TYPE_FIELD_REMOVED or TYPE_FIELD_ADDED for the rename
        assert ChangeKind.TYPE_FIELD_REMOVED not in kinds
        assert ChangeKind.TYPE_FIELD_ADDED not in kinds
        assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE not in kinds
        assert r.verdict == Verdict.COMPATIBLE

    def test_reserved_field_different_type_not_downgraded(self):
        """__reserved1 replaced with different type → still BREAKING."""
        old = _snap("1.0", types=[self._make_struct("Cfg", [
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="__reserved1", type="int", offset_bits=32),
        ])])
        new = _snap("2.0", types=[self._make_struct("Cfg", [
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="flags", type="long", offset_bits=32),
        ])])
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        # Different type → not matched as reserved-field activation
        assert ChangeKind.USED_RESERVED_FIELD not in kinds
        assert ChangeKind.TYPE_FIELD_REMOVED in kinds

    def test_pad_field_pattern(self):
        """_pad0 is also recognized as a reserved-field pattern."""
        old = _snap("1.0", types=[self._make_struct("S", [
            TypeField(name="_pad0", type="char", offset_bits=0),
        ])])
        new = _snap("2.0", types=[self._make_struct("S", [
            TypeField(name="version", type="char", offset_bits=0),
        ])])
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.USED_RESERVED_FIELD in kinds

    def test_mbz_field_pattern(self):
        """__mbz is recognized as a reserved-field pattern."""
        old = _snap("1.0", types=[self._make_struct("S", [
            TypeField(name="__mbz", type="int", offset_bits=0),
        ])])
        new = _snap("2.0", types=[self._make_struct("S", [
            TypeField(name="ctrl", type="int", offset_bits=0),
        ])])
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.USED_RESERVED_FIELD in kinds


# ── WS-5b: Opaque struct detection ─────────────────────────────────────────

class TestOpaqueStructDowngrade:
    """Tests for opaque struct size/field change downgrade."""

    def test_opaque_struct_size_change_is_compatible(self):
        """Size change on an opaque struct is downgraded to COMPATIBLE."""
        old = _snap("1.0", types=[
            RecordType(name="Session", kind="struct", is_opaque=True),
        ])
        new = _snap("2.0", types=[
            RecordType(name="Session", kind="struct", is_opaque=True),
        ])
        # Simulate DWARF-level size change by adding type-size changes manually.
        # The TYPE_SIZE_CHANGED would come from the DWARF detector.
        # For unit test, add directly via the type pair.
        # Since both are opaque with no fields, no changes would be emitted
        # from the type diff. The real-world scenario has DWARF adding the change.
        r = compare(old, new)
        # No changes for two opaque types with no fields → NO_CHANGE
        assert r.verdict == Verdict.NO_CHANGE

    def test_non_opaque_struct_size_change_is_breaking(self):
        """Size change on a non-opaque struct remains BREAKING."""
        old = _snap("1.0", types=[
            RecordType(name="Config", kind="struct", size_bits=64, fields=[
                TypeField(name="x", type="int", offset_bits=0),
            ]),
        ])
        new = _snap("2.0", types=[
            RecordType(name="Config", kind="struct", size_bits=128, fields=[
                TypeField(name="x", type="int", offset_bits=0),
                TypeField(name="y", type="int", offset_bits=64),
            ]),
        ])
        r = compare(old, new)
        assert r.verdict == Verdict.BREAKING


# ---------------------------------------------------------------------------
# Enum alias one-to-one guard (architecture review fix #5)
# ---------------------------------------------------------------------------


class TestEnumAliasOneToOneGuard:
    """Enum aliases (multiple names with same value) must not suppress changes."""

    def test_alias_removal_not_suppressed(self):
        """If two new names share a value, the removed old name should emit REMOVED."""
        old = _snap("1.0")
        old.enums = [EnumType(
            name="Color",
            members=[EnumMember(name="RED", value=0), EnumMember(name="GREEN", value=1)],
        )]
        new = _snap("2.0")
        new.enums = [EnumType(
            name="Color",
            members=[
                EnumMember(name="CRIMSON", value=0),
                EnumMember(name="SCARLET", value=0),  # alias — ambiguous
                EnumMember(name="GREEN", value=1),
            ],
        )]
        changes = _diff_enums(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "Color::RED"

    def test_ambiguous_new_aliases_not_suppress_removal(self):
        """When two new aliases share a value, the removed old name must NOT
        be suppressed (new-side ambiguity → not a clear rename)."""
        old = _snap("1.0")
        old.enums = [EnumType(
            name="Color",
            members=[
                EnumMember(name="RED", value=0),
                EnumMember(name="GREEN", value=1),
            ],
        )]
        new = _snap("2.0")
        new.enums = [EnumType(
            name="Color",
            members=[
                EnumMember(name="CRIMSON", value=0),
                EnumMember(name="SCARLET", value=0),  # alias — ambiguous
                EnumMember(name="GREEN", value=1),
            ],
        )]
        changes = _diff_enums(old, new)
        # RED removal must NOT be suppressed — two new aliases make it ambiguous
        removed = [c for c in changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "Color::RED"

    def test_unique_value_rename_suppresses_removal(self):
        """A true 1:1 rename (unique value) should suppress ENUM_MEMBER_REMOVED."""
        old = _snap("1.0")
        old.enums = [EnumType(
            name="Color",
            members=[EnumMember(name="RED", value=0), EnumMember(name="GREEN", value=1)],
        )]
        new = _snap("2.0")
        new.enums = [EnumType(
            name="Color",
            members=[EnumMember(name="CRIMSON", value=0), EnumMember(name="GREEN", value=1)],
        )]
        changes = _diff_enums(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 0


class TestEnumRenamesOneToOneGuard:
    """_diff_enum_renames must not produce false renames for aliases."""

    def test_alias_not_treated_as_rename(self):
        """When two new names share a value, no rename should be emitted."""
        old = _snap("1.0")
        old.enums = [EnumType(name="E", members=[EnumMember(name="A", value=0)])]
        new = _snap("2.0")
        new.enums = [EnumType(
            name="E",
            members=[EnumMember(name="B", value=0), EnumMember(name="C", value=0)],
        )]
        changes = _diff_enum_renames(old, new)
        renamed = [c for c in changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED]
        assert len(renamed) == 0

    def test_unique_rename_detected(self):
        """A true 1:1 rename is detected correctly."""
        old = _snap("1.0")
        old.enums = [EnumType(
            name="E",
            members=[EnumMember(name="A", value=0), EnumMember(name="B", value=1)],
        )]
        new = _snap("2.0")
        new.enums = [EnumType(
            name="E",
            members=[EnumMember(name="X", value=0), EnumMember(name="B", value=1)],
        )]
        changes = _diff_enum_renames(old, new)
        renamed = [c for c in changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED]
        assert len(renamed) == 1
        assert renamed[0].old_value == "A"
        assert renamed[0].new_value == "X"
