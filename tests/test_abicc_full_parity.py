"""Tests for ABICC full parity — all remaining gaps closed.

Covers:
- VAR_VALUE_CHANGED              (P1: global data value changed)
- TYPE_KIND_CHANGED              (P2: struct↔union kind change)
- USED_RESERVED_FIELD            (P2: reserved field put into use)
- REMOVED_CONST_OVERLOAD         (P2: const method overload removed)
- PARAM_RESTRICT_CHANGED         (P2: restrict qualifier change)
- PARAM_BECAME_VA_LIST           (P2: fixed param → va_list)
- PARAM_LOST_VA_LIST             (P2: va_list → fixed param)
- CONSTANT_CHANGED               (P2: preprocessor constant value changed)
- CONSTANT_ADDED                 (P2: new preprocessor constant)
- CONSTANT_REMOVED               (P2: preprocessor constant removed)
- VAR_ACCESS_CHANGED             (P2: variable access narrowed)

All tests build AbiSnapshot objects directly (no castxml required).
"""
from __future__ import annotations

from abicheck.checker import (
    _API_BREAK_KINDS,
    _BREAKING_KINDS,
    _COMPATIBLE_KINDS,
    ChangeKind,
    Verdict,
    compare,
)
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
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


def _var(name: str, mangled: str, type_: str, **kwargs: object) -> Variable:
    defaults: dict[str, object] = dict(visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Variable(name=name, mangled=mangled, type=type_, **defaults)  # type: ignore[arg-type]


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ===========================================================================
# 1. VAR_VALUE_CHANGED — Global data value changed (P1)
#
# ABICC rule: Global_Data_Value_Changed
# Old binaries may use stale compile-time-inlined constant values.
# ===========================================================================


class TestVarValueChanged:
    """Detect global data initial value changes."""

    def test_value_changed_detected(self) -> None:
        """Simple value change: 42 → 100."""
        old = _snap(variables=[_var("buf_size", "_buf_size", "const int", value="42")])
        new = _snap(variables=[_var("buf_size", "_buf_size", "const int", value="100")])
        result = compare(old, new)
        assert ChangeKind.VAR_VALUE_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.VAR_VALUE_CHANGED)
        assert change.old_value == "42"
        assert change.new_value == "100"

    def test_value_unchanged_no_change(self) -> None:
        """Same value → no change."""
        old = _snap(variables=[_var("x", "_x", "int", value="10")])
        new = _snap(variables=[_var("x", "_x", "int", value="10")])
        result = compare(old, new)
        assert ChangeKind.VAR_VALUE_CHANGED not in _kinds(result)

    def test_value_unknown_no_change(self) -> None:
        """When value is not tracked (None), don't flag."""
        old = _snap(variables=[_var("x", "_x", "int", value=None)])
        new = _snap(variables=[_var("x", "_x", "int", value="5")])
        result = compare(old, new)
        assert ChangeKind.VAR_VALUE_CHANGED not in _kinds(result)

    def test_string_value_changed(self) -> None:
        """String constant value change."""
        old = _snap(variables=[_var("ver", "_ver", "const char*", value='"1.0"')])
        new = _snap(variables=[_var("ver", "_ver", "const char*", value='"2.0"')])
        result = compare(old, new)
        assert ChangeKind.VAR_VALUE_CHANGED in _kinds(result)

    def test_value_changed_is_compatible(self) -> None:
        """Value change is classified as COMPATIBLE (not binary ABI break, but risk)."""
        old = _snap(variables=[_var("x", "_x", "const int", value="1")])
        new = _snap(variables=[_var("x", "_x", "const int", value="2")])
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE

    def test_multiple_value_changes(self) -> None:
        """Multiple variables can change value independently."""
        old = _snap(variables=[
            _var("a", "_a", "int", value="1"),
            _var("b", "_b", "int", value="2"),
        ])
        new = _snap(variables=[
            _var("a", "_a", "int", value="10"),
            _var("b", "_b", "int", value="20"),
        ])
        result = compare(old, new)
        val_changes = [c for c in result.changes if c.kind == ChangeKind.VAR_VALUE_CHANGED]
        assert len(val_changes) == 2


# ===========================================================================
# 2. TYPE_KIND_CHANGED — struct↔union kind change
#
# ABICC rule: DataType_Type / StructToUnion RegTest
# Layout completely changes when kind changes.
# ===========================================================================


class TestTypeKindChanged:
    """Detect struct↔union aggregate kind changes."""

    def test_struct_to_union(self) -> None:
        old = _snap(types=[RecordType(name="Data", kind="struct", fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])])
        new = _snap(types=[RecordType(name="Data", kind="union", is_union=True, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.TYPE_KIND_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.TYPE_KIND_CHANGED)
        assert change.old_value == "struct"
        assert change.new_value == "union"

    def test_union_to_struct(self) -> None:
        old = _snap(types=[RecordType(name="U", kind="union", is_union=True)])
        new = _snap(types=[RecordType(name="U", kind="struct")])
        result = compare(old, new)
        assert ChangeKind.TYPE_KIND_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.TYPE_KIND_CHANGED)
        assert change.old_value == "union"
        assert change.new_value == "struct"

    def test_struct_to_class(self) -> None:
        """struct→class is a source-level kind change, not breaking."""
        old = _snap(types=[RecordType(name="Foo", kind="struct")])
        new = _snap(types=[RecordType(name="Foo", kind="class")])
        result = compare(old, new)
        assert ChangeKind.SOURCE_LEVEL_KIND_CHANGED in _kinds(result)
        assert ChangeKind.TYPE_KIND_CHANGED not in _kinds(result)
        assert result.verdict == Verdict.API_BREAK

    def test_same_kind_no_change(self) -> None:
        old = _snap(types=[RecordType(name="S", kind="struct")])
        new = _snap(types=[RecordType(name="S", kind="struct")])
        result = compare(old, new)
        assert ChangeKind.TYPE_KIND_CHANGED not in _kinds(result)
        assert ChangeKind.SOURCE_LEVEL_KIND_CHANGED not in _kinds(result)

    def test_kind_change_is_breaking(self) -> None:
        """struct→union is BREAKING (layout completely changes)."""
        old = _snap(types=[RecordType(name="D", kind="struct")])
        new = _snap(types=[RecordType(name="D", kind="union", is_union=True)])
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING


# ===========================================================================
# 3. USED_RESERVED_FIELD — reserved field put into use
#
# ABICC rule: Used_Reserved_Field
# Fields named __reserved, _pad, etc. replaced by meaningful names.
# ===========================================================================


class TestUsedReservedField:
    """Detect reserved fields being put into real use."""

    def test_reserved_to_real_field(self) -> None:
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="flags", type="int", offset_bits=0),
            TypeField(name="__reserved", type="int", offset_bits=32),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="flags", type="int", offset_bits=0),
            TypeField(name="priority", type="int", offset_bits=32),
        ])])
        result = compare(old, new)
        assert ChangeKind.USED_RESERVED_FIELD in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.USED_RESERVED_FIELD)
        assert change.old_value == "__reserved"
        assert change.new_value == "priority"

    def test_pad_field_to_real(self) -> None:
        """_pad fields should also be detected."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="_pad", type="char", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="mode", type="char", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.USED_RESERVED_FIELD in _kinds(result)

    def test_reserved_with_number_suffix(self) -> None:
        """reserved1, __pad2, etc."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="reserved1", type="int", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="new_field", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.USED_RESERVED_FIELD in _kinds(result)

    def test_no_reserved_no_detection(self) -> None:
        """Normal field rename should NOT trigger Used_Reserved."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="count", type="int", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="total", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.USED_RESERVED_FIELD not in _kinds(result)

    def test_unused_pattern_to_real(self) -> None:
        """'unused' pattern detected."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="__unused", type="int", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="feature_flags", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.USED_RESERVED_FIELD in _kinds(result)

    def test_reserved_is_compatible(self) -> None:
        """Using a reserved field is compatible (field was unused before)."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="__reserved", type="int", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="priority", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        # Should also trigger FIELD_RENAMED (source break), but USED_RESERVED itself is compatible
        assert ChangeKind.USED_RESERVED_FIELD in _kinds(result)


# ===========================================================================
# 4. REMOVED_CONST_OVERLOAD — const method overload removed
#
# ABICC rule: Removed_Const_Overload
# When both const and non-const versions existed but only non-const remains.
# ===========================================================================


class TestRemovedConstOverload:
    """Detect removal of const method overloads."""

    def test_const_overload_removed(self) -> None:
        """Widget::get() and Widget::get() const → only Widget::get()."""
        old = _snap(functions=[
            _func("Widget::get", "_ZN6Widget3getEv", is_const=False),
            _func("Widget::get", "_ZNK6Widget3getEv", is_const=True),
        ])
        new = _snap(functions=[
            _func("Widget::get", "_ZN6Widget3getEv", is_const=False),
        ])
        result = compare(old, new)
        assert ChangeKind.REMOVED_CONST_OVERLOAD in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.REMOVED_CONST_OVERLOAD)
        assert "const" in change.description.lower()

    def test_both_versions_present_no_change(self) -> None:
        """Both const and non-const still present → no change."""
        old = _snap(functions=[
            _func("Foo::bar", "_ZN3Foo3barEv", is_const=False),
            _func("Foo::bar", "_ZNK3Foo3barEv", is_const=True),
        ])
        new = _snap(functions=[
            _func("Foo::bar", "_ZN3Foo3barEv", is_const=False),
            _func("Foo::bar", "_ZNK3Foo3barEv", is_const=True),
        ])
        result = compare(old, new)
        assert ChangeKind.REMOVED_CONST_OVERLOAD not in _kinds(result)

    def test_nonconst_removed_not_const_overload(self) -> None:
        """Non-const removed with const kept is NOT a const overload removal."""
        old = _snap(functions=[
            _func("Foo::bar", "_ZN3Foo3barEv", is_const=False),
            _func("Foo::bar", "_ZNK3Foo3barEv", is_const=True),
        ])
        new = _snap(functions=[
            _func("Foo::bar", "_ZNK3Foo3barEv", is_const=True),
        ])
        result = compare(old, new)
        assert ChangeKind.REMOVED_CONST_OVERLOAD not in _kinds(result)

    def test_only_const_method_no_overload(self) -> None:
        """Only const method existed (no overload pair) → not a const overload removal."""
        old = _snap(functions=[
            _func("Foo::get", "_ZNK3Foo3getEv", is_const=True),
        ])
        new = _snap(functions=[])
        result = compare(old, new)
        assert ChangeKind.REMOVED_CONST_OVERLOAD not in _kinds(result)

    def test_const_overload_removed_is_source_break(self) -> None:
        """Removing const overload is a source-level break (callers with const ref break)."""
        old = _snap(functions=[
            _func("Foo::get", "_ZN3Foo3getEv", is_const=False),
            _func("Foo::get", "_ZNK3Foo3getEv", is_const=True),
        ])
        new = _snap(functions=[
            _func("Foo::get", "_ZN3Foo3getEv", is_const=False),
        ])
        result = compare(old, new)
        assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK)


# ===========================================================================
# 5. PARAM_RESTRICT_CHANGED — restrict qualifier change
#
# ABICC rule: Parameter_Became_Restrict / Parameter_Became_Non_Restrict
# restrict is an optimization hint; no ABI break but tracked for completeness.
# ===========================================================================


class TestParamRestrictChanged:
    """Detect restrict qualifier changes on parameters."""

    def test_restrict_added(self) -> None:
        old = _snap(functions=[_func("memcpy", "_memcpy", params=[
            Param(name="dst", type="void*", is_restrict=False),
            Param(name="src", type="const void*", is_restrict=False),
        ])])
        new = _snap(functions=[_func("memcpy", "_memcpy", params=[
            Param(name="dst", type="void*", is_restrict=True),
            Param(name="src", type="const void*", is_restrict=True),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(result)
        restrict_changes = [c for c in result.changes if c.kind == ChangeKind.PARAM_RESTRICT_CHANGED]
        assert len(restrict_changes) == 2

    def test_restrict_removed(self) -> None:
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=True),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=False),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.PARAM_RESTRICT_CHANGED)
        assert "removed" in change.description

    def test_restrict_unchanged(self) -> None:
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=True),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=True),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(result)

    def test_restrict_is_compatible(self) -> None:
        """restrict change is compatible (optimization hint only)."""
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=False),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int*", is_restrict=True),
        ])])
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE


# ===========================================================================
# 6. PARAM_BECAME_VA_LIST / PARAM_LOST_VA_LIST — va_list transition
#
# ABICC rule: Parameter_Became_VaList / Parameter_Became_Non_VaList
# ===========================================================================


class TestParamVaList:
    """Detect va_list parameter transitions."""

    def test_param_became_va_list(self) -> None:
        old = _snap(functions=[_func("vprintf", "_vprintf", params=[
            Param(name="fmt", type="const char*"),
            Param(name="args", type="int*", is_va_list=False),
        ])])
        new = _snap(functions=[_func("vprintf", "_vprintf", params=[
            Param(name="fmt", type="const char*"),
            Param(name="args", type="va_list", is_va_list=True),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(result)

    def test_param_lost_va_list(self) -> None:
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="args", type="va_list", is_va_list=True),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="args", type="int*", is_va_list=False),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_LOST_VA_LIST in _kinds(result)

    def test_va_list_unchanged(self) -> None:
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="args", type="va_list", is_va_list=True),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="args", type="va_list", is_va_list=True),
        ])])
        result = compare(old, new)
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(result)
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(result)

    def test_va_list_transition_is_compatible_kind(self) -> None:
        """va_list ChangeKind itself is classified as compatible."""
        from abicheck.checker import _COMPATIBLE_KINDS
        assert ChangeKind.PARAM_BECAME_VA_LIST in _COMPATIBLE_KINDS
        assert ChangeKind.PARAM_LOST_VA_LIST in _COMPATIBLE_KINDS


# ===========================================================================
# 7. CONSTANT_CHANGED / CONSTANT_ADDED / CONSTANT_REMOVED
#
# ABICC rules: Changed_Constant, Added_Constant, Removed_Constant
# Preprocessor #define value changes.
# ===========================================================================


class TestPreprocessorConstants:
    """Detect preprocessor constant (#define) changes."""

    def test_constant_changed(self) -> None:
        old = _snap(constants={"MAX_SIZE": "1024", "VERSION": "1"})
        new = _snap(constants={"MAX_SIZE": "2048", "VERSION": "1"})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.CONSTANT_CHANGED)
        assert change.old_value == "1024"
        assert change.new_value == "2048"

    def test_constant_added(self) -> None:
        old = _snap(constants={"A": "1"})
        new = _snap(constants={"A": "1", "B": "2"})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_ADDED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.CONSTANT_ADDED)
        assert change.new_value == "2"

    def test_constant_removed(self) -> None:
        old = _snap(constants={"A": "1", "B": "2"})
        new = _snap(constants={"A": "1"})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_REMOVED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.CONSTANT_REMOVED)
        assert change.old_value == "2"

    def test_no_constants_no_change(self) -> None:
        old = _snap(constants={})
        new = _snap(constants={})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_CHANGED not in _kinds(result)
        assert ChangeKind.CONSTANT_ADDED not in _kinds(result)
        assert ChangeKind.CONSTANT_REMOVED not in _kinds(result)

    def test_constants_unchanged(self) -> None:
        old = _snap(constants={"X": "42", "Y": "100"})
        new = _snap(constants={"X": "42", "Y": "100"})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_CHANGED not in _kinds(result)

    def test_constant_changed_is_source_break(self) -> None:
        """Changed constant is source-level semantic break."""
        old = _snap(constants={"MAX": "1024"})
        new = _snap(constants={"MAX": "2048"})
        result = compare(old, new)
        assert result.verdict == Verdict.API_BREAK

    def test_constant_removed_is_source_break(self) -> None:
        old = _snap(constants={"X": "1"})
        new = _snap(constants={})
        result = compare(old, new)
        assert result.verdict == Verdict.API_BREAK

    def test_constant_added_is_compatible(self) -> None:
        """Adding a new constant is always compatible."""
        old = _snap(constants={})
        new = _snap(constants={"NEW_CONST": "42"})
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE

    def test_multiple_constant_changes(self) -> None:
        old = _snap(constants={"A": "1", "B": "2", "C": "3"})
        new = _snap(constants={"A": "10", "C": "3", "D": "4"})
        result = compare(old, new)
        assert ChangeKind.CONSTANT_CHANGED in _kinds(result)
        assert ChangeKind.CONSTANT_REMOVED in _kinds(result)
        assert ChangeKind.CONSTANT_ADDED in _kinds(result)


# ===========================================================================
# 8. VAR_ACCESS_CHANGED — variable access level narrowed
#
# ABICC rule: Global_Data_Became_Private / Protected / Public
# ===========================================================================


class TestVarAccessChanged:
    """Detect variable access level narrowing."""

    def test_var_became_private(self) -> None:
        old = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        new = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PRIVATE)])
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(result)
        change = next(c for c in result.changes if c.kind == ChangeKind.VAR_ACCESS_CHANGED)
        assert change.old_value == "public"
        assert change.new_value == "private"

    def test_var_became_protected(self) -> None:
        old = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        new = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PROTECTED)])
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(result)

    def test_var_widened_no_change(self) -> None:
        """private→public is widening, should NOT be flagged."""
        old = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PRIVATE)])
        new = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(result)

    def test_var_access_unchanged(self) -> None:
        old = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        new = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(result)

    def test_var_access_narrowed_is_source_break(self) -> None:
        """Narrowing access is a source-level break."""
        old = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PUBLIC)])
        new = _snap(variables=[_var("data", "_data", "int", access=AccessLevel.PRIVATE)])
        result = compare(old, new)
        assert result.verdict == Verdict.API_BREAK


# ===========================================================================
# Integration: cross-detector scenarios
# ===========================================================================


class TestCrossDetectorIntegration:
    """Test scenarios that exercise multiple new detectors together."""

    def test_struct_to_union_with_field_changes(self) -> None:
        """struct→union also triggers field-level changes."""
        old = _snap(types=[RecordType(name="Mix", kind="struct", size_bits=64, fields=[
            TypeField(name="a", type="int", offset_bits=0),
            TypeField(name="b", type="int", offset_bits=32),
        ])])
        new = _snap(types=[RecordType(name="Mix", kind="union", is_union=True, size_bits=32, fields=[
            TypeField(name="a", type="int", offset_bits=0),
            TypeField(name="b", type="int", offset_bits=0),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_KIND_CHANGED in kinds
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds

    def test_reserved_field_with_type_change(self) -> None:
        """Reserved field put into use with a type change → NOT matched as
        USED_RESERVED_FIELD because the type differs (M5 fix: require offset
        AND type match to avoid silent data truncation with COPY relocations).
        Falls through to TYPE_FIELD_REMOVED + TYPE_FIELD_ADDED."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="__reserved", type="int", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="flags", type="unsigned int", offset_bits=0),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.USED_RESERVED_FIELD not in kinds
        assert ChangeKind.TYPE_FIELD_REMOVED in kinds

    def test_all_new_kinds_in_classification_sets(self) -> None:
        """Verify all new ChangeKinds are properly classified."""
        new_kinds = {
            ChangeKind.VAR_VALUE_CHANGED,
            ChangeKind.TYPE_KIND_CHANGED,
            ChangeKind.SOURCE_LEVEL_KIND_CHANGED,
            ChangeKind.USED_RESERVED_FIELD,
            ChangeKind.REMOVED_CONST_OVERLOAD,
            ChangeKind.PARAM_RESTRICT_CHANGED,
            ChangeKind.PARAM_BECAME_VA_LIST,
            ChangeKind.PARAM_LOST_VA_LIST,
            ChangeKind.CONSTANT_CHANGED,
            ChangeKind.CONSTANT_ADDED,
            ChangeKind.CONSTANT_REMOVED,
            ChangeKind.VAR_ACCESS_CHANGED,
            ChangeKind.VAR_ACCESS_WIDENED,
        }
        all_classified = _BREAKING_KINDS | _COMPATIBLE_KINDS | _API_BREAK_KINDS
        for kind in new_kinds:
            assert kind in all_classified, f"{kind} not in any classification set"


# ===========================================================================
# 9. SYMBOL_RENAMED_BATCH — batch symbol rename (namespace refactoring)
#
# Heuristic: 2+ removed symbols each have a matching added symbol where
# the added name ends with the removed name (common prefix pattern).
# ===========================================================================


class TestSymbolRenamedBatch:
    """Detect batch symbol renames (namespace refactoring)."""

    def test_batch_rename_detected(self) -> None:
        """3 symbols renamed with common prefix → SYMBOL_RENAMED_BATCH."""
        old = _snap(functions=[
            _func("init", "init"),
            _func("process", "process"),
            _func("cleanup", "cleanup"),
        ])
        new = _snap(functions=[
            _func("mylib_init", "mylib_init"),
            _func("mylib_process", "mylib_process"),
            _func("mylib_cleanup", "mylib_cleanup"),
        ])
        result = compare(old, new)
        assert ChangeKind.SYMBOL_RENAMED_BATCH in _kinds(result)

    def test_single_rename_not_batch(self) -> None:
        """Renaming only 1 function must NOT trigger batch detection."""
        old = _snap(functions=[
            _func("init", "init"),
            _func("process", "process"),
        ])
        new = _snap(functions=[
            _func("mylib_init", "mylib_init"),
            _func("process", "process"),
        ])
        result = compare(old, new)
        assert ChangeKind.SYMBOL_RENAMED_BATCH not in _kinds(result)

    def test_batch_rename_is_breaking(self) -> None:
        """Batch rename breaks all existing consumers (undefined symbols)."""
        old = _snap(functions=[
            _func("open", "open"),
            _func("close", "close"),
        ])
        new = _snap(functions=[
            _func("mylib_open", "mylib_open"),
            _func("mylib_close", "mylib_close"),
        ])
        result = compare(old, new)
        assert ChangeKind.SYMBOL_RENAMED_BATCH in _kinds(result)
        assert result.verdict == Verdict.BREAKING
