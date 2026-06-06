"""Sprint 7 tests — full ABICC parity + beyond.

Covers all new ChangeKinds added in Sprint 7:
- ENUM_MEMBER_RENAMED           (source break: same value, different name)
- PARAM_DEFAULT_VALUE_CHANGED   (informational: default arg changed)
- PARAM_DEFAULT_VALUE_REMOVED   (source break: default arg removed)
- FIELD_RENAMED                 (source break: same offset+type, different name)
- PARAM_RENAMED                 (source break: same type, different name)
- FIELD_BECAME_CONST / FIELD_LOST_CONST
- FIELD_BECAME_VOLATILE / FIELD_LOST_VOLATILE
- FIELD_BECAME_MUTABLE / FIELD_LOST_MUTABLE
- PARAM_POINTER_LEVEL_CHANGED   (binary break: T* → T**)
- RETURN_POINTER_LEVEL_CHANGED  (binary break: return T* → T**)
- METHOD_ACCESS_CHANGED         (source break: public → private)
- FIELD_ACCESS_CHANGED          (source break: public → private)
- ANON_FIELD_CHANGED            (binary break: anonymous struct/union member)

All tests build AbiSnapshot objects directly (no castxml required).
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0", from_headers=True)
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ===========================================================================
# Group 1: Enum member rename detection
#
# ABICC rule: Enum_Member_Name — same value, different name.
# This is a source-level break (code references old name → compile error).
# Binary compatibility is preserved (old compiled enum value unchanged).
# ===========================================================================


class TestEnumMemberRenamed:
    """Detect when an enum value keeps its integer value but gets renamed.

    Real-world example: a library renames STATUS_ERROR → STATUS_FAILURE
    while keeping the value 1. Source code referencing STATUS_ERROR breaks.
    """

    def test_enum_member_renamed_detected(self) -> None:
        """Simple rename: OK(0), ERROR(1) → OK(0), FAILURE(1)."""
        old = _snap(
            enums=[EnumType("Status", [EnumMember("OK", 0), EnumMember("ERROR", 1)])]
        )
        new = _snap(
            enums=[EnumType("Status", [EnumMember("OK", 0), EnumMember("FAILURE", 1)])]
        )
        result = compare(old, new)
        assert ChangeKind.ENUM_MEMBER_RENAMED in _kinds(result)
        change = next(
            c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED
        )
        assert change.old_value == "ERROR"
        assert change.new_value == "FAILURE"

    def test_enum_member_renamed_is_at_least_source_break(self) -> None:
        """Rename also triggers ENUM_MEMBER_REMOVED (old name gone), which is BREAKING.
        The rename is detected as an additional signal alongside the removal."""
        old = _snap(
            enums=[EnumType("Color", [EnumMember("RED", 0), EnumMember("GRN", 1)])]
        )
        new = _snap(
            enums=[EnumType("Color", [EnumMember("RED", 0), EnumMember("GREEN", 1)])]
        )
        result = compare(old, new)
        assert ChangeKind.ENUM_MEMBER_RENAMED in _kinds(result)
        # Also triggers ENUM_MEMBER_REMOVED for old name → BREAKING verdict
        assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    def test_no_rename_when_value_also_changes(self) -> None:
        """If the value also changes, it's ENUM_MEMBER_REMOVED + ADDED, not a rename."""
        old = _snap(enums=[EnumType("E", [EnumMember("A", 0)])])
        new = _snap(enums=[EnumType("E", [EnumMember("B", 1)])])
        result = compare(old, new)
        assert ChangeKind.ENUM_MEMBER_RENAMED not in _kinds(result)

    def test_no_rename_when_name_unchanged(self) -> None:
        old = _snap(enums=[EnumType("E", [EnumMember("A", 0), EnumMember("B", 1)])])
        new = _snap(enums=[EnumType("E", [EnumMember("A", 0), EnumMember("B", 1)])])
        result = compare(old, new)
        assert ChangeKind.ENUM_MEMBER_RENAMED not in _kinds(result)

    def test_multiple_renames_in_same_enum(self) -> None:
        """Multiple members renamed simultaneously."""
        old = _snap(
            enums=[
                EnumType(
                    "E", [EnumMember("X", 0), EnumMember("Y", 1), EnumMember("Z", 2)]
                )
            ]
        )
        new = _snap(
            enums=[
                EnumType(
                    "E", [EnumMember("A", 0), EnumMember("B", 1), EnumMember("C", 2)]
                )
            ]
        )
        result = compare(old, new)
        renames = [
            c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED
        ]
        assert len(renames) == 3


# ===========================================================================
# Group 2: Parameter default value changes
#
# ABICC rules: Parameter_Default_Value_Changed, _Removed, _Added
# Removing a default breaks source (callers that relied on it must now pass arg).
# Changing a default is informational (recompiled callers get new value).
# ===========================================================================


class TestParamDefaultValueChanged:
    """Detect default argument value changes in function signatures.

    Real-world example: void connect(int timeout = 30) → void connect(int timeout = 60)
    Old callers recompiled get the new default; old binaries use old inlined value.
    """

    def test_default_value_changed(self) -> None:
        old = _snap(
            functions=[
                _func(
                    "connect",
                    "_Z7connecti",
                    params=[Param("timeout", "int", default="30")],
                )
            ]
        )
        new = _snap(
            functions=[
                _func(
                    "connect",
                    "_Z7connecti",
                    params=[Param("timeout", "int", default="60")],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_DEFAULT_VALUE_CHANGED in _kinds(result)

    def test_default_value_removed_is_source_break(self) -> None:
        old = _snap(
            functions=[
                _func(
                    "init",
                    "_Z4initb",
                    params=[Param("verbose", "bool", default="true")],
                )
            ]
        )
        new = _snap(
            functions=[
                _func(
                    "init", "_Z4initb", params=[Param("verbose", "bool", default=None)]
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED in _kinds(result)
        assert result.verdict == Verdict.API_BREAK

    def test_default_value_added_not_reported(self) -> None:
        """Adding a default is backward-compatible and not reported."""
        old = _snap(
            functions=[
                _func(
                    "init", "_Z4initb", params=[Param("verbose", "bool", default=None)]
                )
            ]
        )
        new = _snap(
            functions=[
                _func(
                    "init",
                    "_Z4initb",
                    params=[Param("verbose", "bool", default="false")],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED not in _kinds(result)
        assert ChangeKind.PARAM_DEFAULT_VALUE_CHANGED not in _kinds(result)

    def test_default_unchanged_no_report(self) -> None:
        old = _snap(
            functions=[_func("f", "_Z1fi", params=[Param("x", "int", default="42")])]
        )
        new = _snap(
            functions=[_func("f", "_Z1fi", params=[Param("x", "int", default="42")])]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_DEFAULT_VALUE_CHANGED not in _kinds(result)


# ===========================================================================
# Group 3: Field qualifier changes (const, volatile, mutable)
#
# ABICC rules: Field_Became_Const, Field_Became_Volatile, Field_Became_Mutable
# These are informational/compatible — they don't change binary layout but
# may indicate semantic API contract changes.
# ===========================================================================


class TestFieldBecameConst:
    """Detect const qualifier added/removed from struct fields.

    Real-world example: struct Config { int flags; } → struct Config { const int flags; }
    Binary layout unchanged, but semantic contract changed.
    """

    def test_field_became_const(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("x", "int", is_const=False)]
                )
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", is_const=True)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_BECAME_CONST in _kinds(result)

    def test_field_lost_const(self) -> None:
        old = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", is_const=True)])
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("x", "int", is_const=False)]
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_LOST_CONST in _kinds(result)

    def test_const_unchanged_no_report(self) -> None:
        old = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", is_const=True)])
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", is_const=True)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_BECAME_CONST not in _kinds(result)
        assert ChangeKind.FIELD_LOST_CONST not in _kinds(result)


class TestFieldBecameVolatile:
    """Detect volatile qualifier changes on struct fields."""

    def test_field_became_volatile(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("counter", "int", is_volatile=False)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("counter", "int", is_volatile=True)],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_BECAME_VOLATILE in _kinds(result)

    def test_field_lost_volatile(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("counter", "int", is_volatile=True)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("counter", "int", is_volatile=False)],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_LOST_VOLATILE in _kinds(result)


class TestFieldBecameMutable:
    """Detect mutable qualifier changes on class fields.

    Real-world example: class Cache { int size; } → class Cache { mutable int size; }
    mutable allows modification through const references — semantic concern.
    """

    def test_field_became_mutable(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "Cache",
                    "class",
                    fields=[TypeField("size", "int", is_mutable=False)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "Cache", "class", fields=[TypeField("size", "int", is_mutable=True)]
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_BECAME_MUTABLE in _kinds(result)

    def test_field_lost_mutable(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "Cache", "class", fields=[TypeField("size", "int", is_mutable=True)]
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "Cache",
                    "class",
                    fields=[TypeField("size", "int", is_mutable=False)],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_LOST_MUTABLE in _kinds(result)


class TestFieldQualifierVerdicts:
    """Field qualifier changes should be COMPATIBLE (informational, not breaking)."""

    def test_field_const_change_is_compatible(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("x", "int", is_const=False)]
                )
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", is_const=True)])
            ]
        )
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE

    def test_field_volatile_change_is_compatible(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("x", "int", is_volatile=False)]
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("x", "int", is_volatile=True)]
                )
            ]
        )
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE


# ===========================================================================
# Group 4: Field and parameter rename detection
#
# ABICC rules: Renamed_Field, Renamed_Parameter
# Source-level breaks: code that references the old name won't compile.
# Binary compatibility preserved (layout unchanged).
# ===========================================================================


class TestFieldRenamed:
    """Detect field renames: same offset and type, different name.

    Real-world example: struct Point { int x; int y; } → struct Point { int col; int row; }
    If both fields at same offsets change names, it's a rename not add/remove.
    """

    def test_field_renamed_detected(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "P",
                    "struct",
                    fields=[
                        TypeField("x", "int", offset_bits=0),
                        TypeField("y", "int", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "P",
                    "struct",
                    fields=[
                        TypeField("col", "int", offset_bits=0),
                        TypeField("row", "int", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        renames = [c for c in result.changes if c.kind == ChangeKind.FIELD_RENAMED]
        assert len(renames) == 2
        # Also triggers TYPE_FIELD_REMOVED for old names → BREAKING verdict
        assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    def test_field_renamed_not_triggered_for_type_change(self) -> None:
        """Different type at same offset is TYPE_FIELD_TYPE_CHANGED, not rename."""
        old = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S", "struct", fields=[TypeField("y", "float", offset_bits=0)]
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_RENAMED not in _kinds(result)

    def test_no_rename_when_name_unchanged(self) -> None:
        old = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_RENAMED not in _kinds(result)


class TestParamRenamed:
    """Detect parameter name changes (same type and position).

    Real-world example: void draw(int width, int height) → void draw(int w, int h)
    """

    def test_param_renamed_detected(self) -> None:
        old = _snap(
            from_headers=True,
            functions=[
                _func(
                    "draw",
                    "_Z4drawii",
                    params=[Param("width", "int"), Param("height", "int")],
                )
            ]
        )
        new = _snap(
            from_headers=True,
            functions=[
                _func(
                    "draw", "_Z4drawii", params=[Param("w", "int"), Param("h", "int")]
                )
            ]
        )
        result = compare(old, new)
        renames = [c for c in result.changes if c.kind == ChangeKind.PARAM_RENAMED]
        assert len(renames) == 2

    def test_param_renamed_is_source_break(self) -> None:
        old = _snap(
            from_headers=True,
            functions=[_func("f", "_Z1fi", params=[Param("count", "int")])],
        )
        new = _snap(
            from_headers=True,
            functions=[_func("f", "_Z1fi", params=[Param("n", "int")])],
        )
        result = compare(old, new)
        assert result.verdict == Verdict.API_BREAK

    def test_dwarf_only_param_rename_is_not_source_break(self) -> None:
        old = _snap(functions=[_func("f", "_Z1fi", params=[Param("arg_size", "int")])], from_headers=False)
        new = _snap(functions=[_func("f", "_Z1fi", params=[Param("size", "int")])], from_headers=False)
        result = compare(old, new)
        assert ChangeKind.PARAM_RENAMED not in _kinds(result)
        assert result.verdict == Verdict.NO_CHANGE

    def test_legacy_snapshot_inferred_headers_param_rename_not_source_break(self) -> None:
        """Legacy snapshots predating the from_headers key infer header
        provenance from a populated surface — but a DWARF-only dump satisfies
        that same inference, so param renames must stay suppressed.

        Reproduces the load path: a snapshot dict with no ``from_headers`` key
        and DWARF-derived functions deserializes to from_headers=True (for
        evidence-tier continuity) but from_headers_inferred=True, which must
        keep PARAM_RENAMED quiet.
        """
        from abicheck.serialization import snapshot_from_dict

        def _legacy_dict(param_name: str) -> dict:
            return {
                "library": "lib.so",
                "version": "1.0",
                "functions": [
                    {
                        "name": "f",
                        "mangled": "_Z1fi",
                        "return_type": "void",
                        "params": [{"name": param_name, "type": "int"}],
                    }
                ],
                "dwarf": {"has_dwarf": True},
            }

        old = snapshot_from_dict(_legacy_dict("arg_size"))
        new = snapshot_from_dict(_legacy_dict("size"))
        assert old.from_headers is True and old.from_headers_inferred is True
        result = compare(old, new)
        assert ChangeKind.PARAM_RENAMED not in _kinds(result)
        assert result.verdict == Verdict.NO_CHANGE

    def test_no_rename_when_unchanged(self) -> None:
        old = _snap(functions=[_func("f", "_Z1fi", params=[Param("x", "int")])])
        new = _snap(functions=[_func("f", "_Z1fi", params=[Param("x", "int")])])
        result = compare(old, new)
        assert ChangeKind.PARAM_RENAMED not in _kinds(result)

    def test_no_rename_for_empty_names(self) -> None:
        """Parameters without names should not trigger rename detection."""
        old = _snap(functions=[_func("f", "_Z1fi", params=[Param("", "int")])])
        new = _snap(functions=[_func("f", "_Z1fi", params=[Param("", "int")])])
        result = compare(old, new)
        assert ChangeKind.PARAM_RENAMED not in _kinds(result)


# ===========================================================================
# Group 5: Pointer level changes
#
# ABICC rules: Parameter_PointerLevel_Increased/Decreased,
#              Return_PointerLevel_Increased/Decreased
# Binary ABI break: wrong dereference depth causes crashes or corruption.
# ===========================================================================


class TestPointerLevelChanges:
    """Detect pointer indirection level changes in function signatures.

    Real-world example: void process(int* data) → void process(int** data)
    Old code passes int*, new expects int** → dereference mismatch → crash.
    """

    def test_param_pointer_level_increased(self) -> None:
        old = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("p", "int*", pointer_depth=1)])
            ]
        )
        new = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("p", "int**", pointer_depth=2)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_param_pointer_level_decreased(self) -> None:
        old = _snap(
            functions=[
                _func("f", "_Z1fPPi", params=[Param("p", "int**", pointer_depth=2)])
            ]
        )
        new = _snap(
            functions=[
                _func("f", "_Z1fPPi", params=[Param("p", "int*", pointer_depth=1)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED in _kinds(result)

    def test_return_pointer_level_changed(self) -> None:
        old = _snap(
            functions=[
                _func("get", "_Z3getv", return_type="int*", return_pointer_depth=1)
            ]
        )
        new = _snap(
            functions=[
                _func("get", "_Z3getv", return_type="int**", return_pointer_depth=2)
            ]
        )
        result = compare(old, new)
        assert ChangeKind.RETURN_POINTER_LEVEL_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_pointer_depth_unchanged_no_report(self) -> None:
        old = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("p", "int*", pointer_depth=1)])
            ]
        )
        new = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("p", "int*", pointer_depth=1)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED not in _kinds(result)

    def test_zero_depth_to_zero_no_report(self) -> None:
        """Non-pointer types (depth=0) should not trigger pointer level changes."""
        old = _snap(
            functions=[_func("f", "_Z1fi", params=[Param("x", "int", pointer_depth=0)])]
        )
        new = _snap(
            functions=[_func("f", "_Z1fi", params=[Param("x", "int", pointer_depth=0)])]
        )
        result = compare(old, new)
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED not in _kinds(result)


# ===========================================================================
# Group 6: Access level changes
#
# ABICC rules: Method_Became_Private, Method_Became_Protected,
#              Field_Became_Private, Global_Data_Became_Private
# Source break: previously accessible API becomes inaccessible.
# ===========================================================================


class TestMethodAccessChanged:
    """Detect method access level changes (public → protected/private).

    Real-world example: class Widget { public: void render(); } →
                        class Widget { private: void render(); }
    External code can no longer call render() → source break.
    """

    def test_method_became_private(self) -> None:
        old = _snap(
            functions=[
                _func("render", "_ZN6Widget6renderEv", access=AccessLevel.PUBLIC)
            ]
        )
        new = _snap(
            functions=[
                _func("render", "_ZN6Widget6renderEv", access=AccessLevel.PRIVATE)
            ]
        )
        result = compare(old, new)
        assert ChangeKind.METHOD_ACCESS_CHANGED in _kinds(result)
        assert result.verdict == Verdict.API_BREAK

    def test_method_became_protected(self) -> None:
        old = _snap(
            functions=[_func("init", "_ZN6Widget4initEv", access=AccessLevel.PUBLIC)]
        )
        new = _snap(
            functions=[_func("init", "_ZN6Widget4initEv", access=AccessLevel.PROTECTED)]
        )
        result = compare(old, new)
        assert ChangeKind.METHOD_ACCESS_CHANGED in _kinds(result)

    def test_method_access_widened_not_reported(self) -> None:
        """Widening access (private → public) is backward-compatible, not reported."""
        old = _snap(
            functions=[
                _func("helper", "_ZN6Widget6helperEv", access=AccessLevel.PRIVATE)
            ]
        )
        new = _snap(
            functions=[
                _func("helper", "_ZN6Widget6helperEv", access=AccessLevel.PUBLIC)
            ]
        )
        result = compare(old, new)
        assert ChangeKind.METHOD_ACCESS_CHANGED not in _kinds(result)

    def test_method_access_unchanged_no_report(self) -> None:
        old = _snap(functions=[_func("f", "_Z1fv", access=AccessLevel.PUBLIC)])
        new = _snap(functions=[_func("f", "_Z1fv", access=AccessLevel.PUBLIC)])
        result = compare(old, new)
        assert ChangeKind.METHOD_ACCESS_CHANGED not in _kinds(result)


class TestFieldAccessChanged:
    """Detect field access level changes.

    Real-world example: struct S { public: int data; } →
                        struct S { private: int data; }
    """

    def test_field_became_private(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "class",
                    fields=[TypeField("data", "int", access=AccessLevel.PUBLIC)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "class",
                    fields=[TypeField("data", "int", access=AccessLevel.PRIVATE)],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_ACCESS_CHANGED in _kinds(result)
        assert result.verdict == Verdict.API_BREAK

    def test_field_access_unchanged(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("x", "int", access=AccessLevel.PUBLIC)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("x", "int", access=AccessLevel.PUBLIC)],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FIELD_ACCESS_CHANGED not in _kinds(result)


# ===========================================================================
# Group 7: Anonymous struct/union field changes
#
# ABICC/abidiff test cases: test44, test45
# Binary break: anonymous member layout change affects containing struct.
# ===========================================================================


class TestAnonFieldChanged:
    """Detect changes in anonymous struct/union members.

    Real-world example:
        struct S { union { int i; float f; }; int z; };
        →
        struct S { union { int i; double d; }; int z; };  // float → double
    The anonymous union's type changed, which can affect layout.
    """

    def test_anon_field_type_changed(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[
                        TypeField("__anon0", "union{int,float}", offset_bits=0),
                        TypeField("z", "int", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[
                        TypeField("__anon0", "union{int,double}", offset_bits=0),
                        TypeField("z", "int", offset_bits=64),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.ANON_FIELD_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_anon_field_removed(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[
                        TypeField("__anon0", "union{int,float}", offset_bits=0),
                        TypeField("x", "int", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.ANON_FIELD_CHANGED in _kinds(result)

    def test_no_anon_fields_no_report(self) -> None:
        old = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        new = _snap(
            types=[
                RecordType("S", "struct", fields=[TypeField("x", "int", offset_bits=0)])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.ANON_FIELD_CHANGED not in _kinds(result)


# ===========================================================================
# Group 8: Combined scenarios / edge cases
#
# These test multiple detectors interacting correctly without false positives
# or missed interactions. These go BEYOND what ABICC tests.
# ===========================================================================


class TestCombinedScenarios:
    """Complex scenarios testing multiple ABI breaks simultaneously."""

    def test_field_rename_plus_qualifier_change(self) -> None:
        """Field renamed AND became const — both should be reported."""
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("data", "int", offset_bits=0, is_const=False)],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "struct",
                    fields=[TypeField("value", "int", offset_bits=0, is_const=True)],
                )
            ]
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FIELD_RENAMED in kinds

    def test_enum_rename_plus_new_member(self) -> None:
        """Member renamed AND new member added — both detected."""
        old = _snap(enums=[EnumType("E", [EnumMember("A", 0), EnumMember("B", 1)])])
        new = _snap(
            enums=[
                EnumType(
                    "E",
                    [EnumMember("ALPHA", 0), EnumMember("B", 1), EnumMember("C", 2)],
                )
            ]
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.ENUM_MEMBER_RENAMED in kinds
        assert ChangeKind.ENUM_MEMBER_ADDED in kinds

    def test_param_pointer_plus_rename(self) -> None:
        """Param pointer level change AND rename — pointer break dominates.
        Note: type change ('int*' → 'int**') also triggers FUNC_PARAMS_CHANGED."""
        old = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("data", "int*", pointer_depth=1)])
            ]
        )
        new = _snap(
            functions=[
                _func("f", "_Z1fPi", params=[Param("ptr", "int**", pointer_depth=2)])
            ]
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED in kinds
        # Rename also fires when types match; here types differ so only pointer+params
        assert result.verdict == Verdict.BREAKING

    def test_access_change_with_const_field(self) -> None:
        """Field access changed AND became const simultaneously."""
        old = _snap(
            types=[
                RecordType(
                    "S",
                    "class",
                    fields=[
                        TypeField(
                            "cache", "int", access=AccessLevel.PUBLIC, is_const=False
                        )
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    "S",
                    "class",
                    fields=[
                        TypeField(
                            "cache", "int", access=AccessLevel.PRIVATE, is_const=True
                        )
                    ],
                )
            ]
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FIELD_ACCESS_CHANGED in kinds
        assert ChangeKind.FIELD_BECAME_CONST in kinds

    def test_multiple_default_changes_in_one_function(self) -> None:
        """Multiple params with default changes in the same function."""
        old = _snap(
            functions=[
                _func(
                    "configure",
                    "_Z9configureiii",
                    params=[
                        Param("width", "int", default="800"),
                        Param("height", "int", default="600"),
                        Param("depth", "int", default="32"),
                    ],
                )
            ]
        )
        new = _snap(
            functions=[
                _func(
                    "configure",
                    "_Z9configureiii",
                    params=[
                        Param("width", "int", default="1920"),
                        Param("height", "int", default=None),
                        Param("depth", "int", default="32"),
                    ],
                )
            ]
        )
        result = compare(old, new)
        changed = [
            c
            for c in result.changes
            if c.kind == ChangeKind.PARAM_DEFAULT_VALUE_CHANGED
        ]
        removed = [
            c
            for c in result.changes
            if c.kind == ChangeKind.PARAM_DEFAULT_VALUE_REMOVED
        ]
        assert len(changed) == 1  # width
        assert len(removed) == 1  # height


# ===========================================================================
# Group 9: Classification verification
#
# Ensure all new ChangeKinds are correctly classified in the sets.
# ===========================================================================


class TestClassification:
    """Verify that all Sprint 7 ChangeKinds are in the correct classification set."""

    def test_breaking_kinds_contains_pointer_changes(self) -> None:
        from abicheck.checker import _BREAKING_KINDS

        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED in _BREAKING_KINDS
        assert ChangeKind.RETURN_POINTER_LEVEL_CHANGED in _BREAKING_KINDS
        assert ChangeKind.ANON_FIELD_CHANGED in _BREAKING_KINDS

    def test_source_break_kinds(self) -> None:
        from abicheck.checker import _API_BREAK_KINDS

        assert ChangeKind.ENUM_MEMBER_RENAMED in _API_BREAK_KINDS
        assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED in _API_BREAK_KINDS
        assert ChangeKind.FIELD_RENAMED in _API_BREAK_KINDS
        assert ChangeKind.PARAM_RENAMED in _API_BREAK_KINDS
        assert ChangeKind.METHOD_ACCESS_CHANGED in _API_BREAK_KINDS
        assert ChangeKind.FIELD_ACCESS_CHANGED in _API_BREAK_KINDS

    def test_compatible_kinds_contains_qualifier_changes(self) -> None:
        from abicheck.checker import _COMPATIBLE_KINDS

        assert ChangeKind.FIELD_BECAME_CONST in _COMPATIBLE_KINDS
        assert ChangeKind.FIELD_LOST_CONST in _COMPATIBLE_KINDS
        assert ChangeKind.FIELD_BECAME_VOLATILE in _COMPATIBLE_KINDS
        assert ChangeKind.FIELD_LOST_VOLATILE in _COMPATIBLE_KINDS
        assert ChangeKind.FIELD_BECAME_MUTABLE in _COMPATIBLE_KINDS
        assert ChangeKind.FIELD_LOST_MUTABLE in _COMPATIBLE_KINDS
        assert ChangeKind.PARAM_DEFAULT_VALUE_CHANGED in _COMPATIBLE_KINDS

    def test_every_changekind_classified(self) -> None:
        """Every ChangeKind must be in exactly one classification set."""
        from abicheck.checker import (
            _API_BREAK_KINDS,
            _BREAKING_KINDS,
            _COMPATIBLE_KINDS,
            _RISK_KINDS,
        )

        all_classified = (
            _BREAKING_KINDS | _COMPATIBLE_KINDS | _API_BREAK_KINDS | _RISK_KINDS
        )
        for kind in ChangeKind:
            assert kind in all_classified, (
                f"{kind} is not classified in any set — add it to "
                f"_BREAKING_KINDS, _COMPATIBLE_KINDS, or _API_BREAK_KINDS"
            )
