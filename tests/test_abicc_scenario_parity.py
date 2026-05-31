"""Parity tests for abicc test scenarios not yet covered.

Covers all 10 P1 gaps identified in tool-comparison-gap-analysis.md:
1. Function pointer field/parameter changes
2. Return type void transitions
3. Hidden parameter / large struct return (value ABI trait)
4. Leaf-class virtual method additions
5. Diamond / multiple inheritance
6. Private field layout impact
7. Array size changes in fields
8. Member function pointer changes
9. Template specialization removal
10. Register/stack parameter allocation (calling convention)

All tests build AbiSnapshot objects directly (no castxml required).
"""
from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
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
# 1. Function pointer field/parameter changes
#
# abicc rule: Field_Type_Format (function pointer)
# typedef void (*cb)(int) → typedef void (*cb)(long) in struct field
# ===========================================================================


class TestFunctionPointerChanges:
    """Detect function pointer type changes in fields and parameters."""

    def test_funcptr_field_type_changed(self) -> None:
        """Field type from void(*)(int) to void(*)(long) → TYPE_FIELD_TYPE_CHANGED."""
        old = _snap(types=[RecordType(name="Callbacks", kind="struct", fields=[
            TypeField(name="on_data", type="void (*)(int)", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="Callbacks", kind="struct", fields=[
            TypeField(name="on_data", type="void (*)(long)", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(result)

    def test_funcptr_param_type_changed(self) -> None:
        """Parameter callback type changed → FUNC_PARAMS_CHANGED."""
        old = _snap(functions=[_func("register_cb", "_register_cb", params=[
            Param(name="cb", type="void (*)(int)"),
        ])])
        new = _snap(functions=[_func("register_cb", "_register_cb", params=[
            Param(name="cb", type="void (*)(long)"),
        ])])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)

    def test_funcptr_return_type_changed(self) -> None:
        """Return type changed from funcptr returning int to funcptr returning long."""
        old = _snap(functions=[_func("get_handler", "_get_handler",
                                     return_type="int (*)(void)")])
        new = _snap(functions=[_func("get_handler", "_get_handler",
                                     return_type="long (*)(void)")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)

    def test_funcptr_field_unchanged(self) -> None:
        """Same function pointer type → no change."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="cb", type="void (*)(int, int)", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="cb", type="void (*)(int, int)", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in _kinds(result)

    def test_funcptr_field_is_breaking(self) -> None:
        """Function pointer field type change is BREAKING."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="fn", type="int (*)(void)", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="fn", type="void (*)(int)", offset_bits=0),
        ])])
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING


# ===========================================================================
# 2. Return type void transitions
#
# abicc rules: Return_Type_Became_Void, Return_Type_From_Void,
#   Return_Type_Became_Void_And_Stack_Layout
# ===========================================================================


class TestReturnTypeVoidTransitions:
    """Detect return type transitions to/from void."""

    def test_return_became_void(self) -> None:
        """int → void return type → FUNC_RETURN_CHANGED."""
        old = _snap(functions=[_func("get_val", "_get_val", return_type="int")])
        new = _snap(functions=[_func("get_val", "_get_val", return_type="void")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)

    def test_return_from_void(self) -> None:
        """void → int return type → FUNC_RETURN_CHANGED."""
        old = _snap(functions=[_func("process", "_process", return_type="void")])
        new = _snap(functions=[_func("process", "_process", return_type="int")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)

    def test_return_became_void_from_struct(self) -> None:
        """Struct return → void (stack layout change) → BREAKING."""
        old = _snap(functions=[_func("make_point", "_make_point",
                                     return_type="Point")])
        new = _snap(functions=[_func("make_point", "_make_point",
                                     return_type="void")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_return_from_void_to_struct(self) -> None:
        """void → struct return (introduces hidden parameter on SysV ABI)."""
        old = _snap(functions=[_func("init", "_init", return_type="void")])
        new = _snap(functions=[_func("init", "_init", return_type="LargeStruct")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_return_void_to_pointer(self) -> None:
        """void → void* return type change."""
        old = _snap(functions=[_func("get", "_get", return_type="void")])
        new = _snap(functions=[_func("get", "_get", return_type="void *")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(result)

    def test_return_void_unchanged(self) -> None:
        """void → void: no change."""
        old = _snap(functions=[_func("noop", "_noop", return_type="void")])
        new = _snap(functions=[_func("noop", "_noop", return_type="void")])
        result = compare(old, new)
        assert ChangeKind.FUNC_RETURN_CHANGED not in _kinds(result)


# ===========================================================================
# 3. Hidden parameter / large struct return (Value ABI trait)
#
# abicc rule: Return_Type_And_Register_Became_Hidden_Parameter
# abicheck: VALUE_ABI_TRAIT_CHANGED (DWARF-based)
# ===========================================================================


class TestHiddenParameterLargeStructReturn:
    """Detect value-ABI trait changes (trivial→non-trivial struct return).

    When a struct gains a non-trivial destructor/copy-ctor, the System V AMD64
    ABI requires it to be returned via hidden pointer instead of registers.
    abicheck detects this via VALUE_ABI_TRAIT_CHANGED from DWARF.

    These tests verify the scenario at the snapshot level. The DWARF-level
    detection is tested in test_sprint4_dwarf_advanced.py.
    """

    def test_struct_size_change_affects_return(self) -> None:
        """Struct returned by value with size change → BREAKING."""
        old = _snap(
            functions=[_func("make", "_make", return_type="Widget")],
            types=[RecordType(name="Widget", kind="struct", size_bits=32, fields=[
                TypeField(name="x", type="int", offset_bits=0),
            ])],
        )
        new = _snap(
            functions=[_func("make", "_make", return_type="Widget")],
            types=[RecordType(name="Widget", kind="struct", size_bits=128, fields=[
                TypeField(name="x", type="int", offset_bits=0),
                TypeField(name="y", type="int", offset_bits=32),
                TypeField(name="z", type="int", offset_bits=64),
                TypeField(name="w", type="int", offset_bits=96),
            ])],
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_SIZE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING


# ===========================================================================
# 4. Leaf-class virtual method additions
#
# abicc rule: Added_Virtual_Method_At_End_Of_Leaf_Copying_Class (Medium)
#   and Added_Virtual_Method_At_End_Of_Leaf_Allocable_Class (Safe)
# abicheck: FUNC_VIRTUAL_ADDED (always BREAKING — intentionally conservative)
# ===========================================================================


class TestLeafClassVirtualMethodAdditions:
    """Detect virtual method additions to leaf classes.

    abicc treats leaf-class vtable additions at lower severity.
    abicheck is intentionally more conservative — always BREAKING.
    """

    def test_virtual_added_to_leaf_class(self) -> None:
        """Adding first virtual method to a leaf class → BREAKING.

        Even though the class may not have derived classes today,
        a consumer could have subclassed it.
        """
        old = _snap(
            functions=[_func("Leaf::get", "_ZN4Leaf3getEv")],
            types=[RecordType(name="Leaf", kind="class", size_bits=32, fields=[
                TypeField(name="x", type="int", offset_bits=0),
            ])],
        )
        new = _snap(
            functions=[
                _func("Leaf::get", "_ZN4Leaf3getEv"),
                _func("Leaf::process", "_ZN4Leaf7processEv", is_virtual=True),
            ],
            types=[RecordType(name="Leaf", kind="class", size_bits=96, fields=[
                TypeField(name="__vptr", type="void *", offset_bits=0),
                TypeField(name="x", type="int", offset_bits=64),
            ], vtable=["_ZN4Leaf7processEv"])],
        )
        # Verdict-parity check: the Leaf class is reachable only through its own
        # methods (not a free function), so disable surface scoping (default-on
        # since ADR-024) to exercise raw vtable-change detection.
        result = compare(old, new, scope_to_public_surface=False)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_VIRTUAL_ADDED in kinds or ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_virtual_added_at_end_of_existing_vtable(self) -> None:
        """Adding virtual method at end of existing vtable → BREAKING (vtable size grows)."""
        old = _snap(
            functions=[_func("Base::foo", "_ZN4Base3fooEv", is_virtual=True)],
            types=[RecordType(name="Base", kind="class",
                              vtable=["_ZN4Base3fooEv"])],
        )
        new = _snap(
            functions=[
                _func("Base::foo", "_ZN4Base3fooEv", is_virtual=True),
                _func("Base::bar", "_ZN4Base3barEv", is_virtual=True),
            ],
            types=[RecordType(name="Base", kind="class",
                              vtable=["_ZN4Base3fooEv", "_ZN4Base3barEv"])],
        )
        result = compare(old, new, scope_to_public_surface=False)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds or ChangeKind.FUNC_VIRTUAL_ADDED in kinds


# ===========================================================================
# 5. Diamond / multiple inheritance
#
# abicc rules: Virtual_Method_Position (multiple bases),
#   Added_Base_Class_And_Shift_And_VTable
# ===========================================================================


class TestDiamondMultipleInheritance:
    """Detect ABI changes in diamond / multiple inheritance scenarios."""

    def test_base_class_added_with_vtable_shift(self) -> None:
        """Adding a base class that shifts vtable → BREAKING."""
        old = _snap(types=[RecordType(name="D", kind="class", size_bits=64,
                                      bases=["A"],
                                      vtable=["_ZN1A3fooEv"])])
        new = _snap(types=[RecordType(name="D", kind="class", size_bits=128,
                                      bases=["A", "B"],
                                      vtable=["_ZN1A3fooEv", "_ZN1B3barEv"])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_BASE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_base_class_reordering(self) -> None:
        """Reordering base classes → BASE_CLASS_POSITION_CHANGED."""
        old = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["A", "B"])])
        new = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["B", "A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_virtual_base_added(self) -> None:
        """Non-virtual base → virtual base → BASE_CLASS_VIRTUAL_CHANGED."""
        old = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["A"], virtual_bases=[])])
        new = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["A"], virtual_bases=["A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_diamond_vtable_reorder(self) -> None:
        """Vtable reordering in diamond hierarchy → TYPE_VTABLE_CHANGED."""
        old = _snap(types=[RecordType(name="Diamond", kind="class",
                                      bases=["Left", "Right"],
                                      vtable=["_ZN4Left3fooEv", "_ZN5Right3barEv"])])
        new = _snap(types=[RecordType(name="Diamond", kind="class",
                                      bases=["Left", "Right"],
                                      vtable=["_ZN5Right3barEv", "_ZN4Left3fooEv"])])
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_multiple_inheritance_unchanged(self) -> None:
        """Same multiple inheritance hierarchy → no change."""
        old = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["A", "B"], size_bits=128)])
        new = _snap(types=[RecordType(name="D", kind="class",
                                      bases=["A", "B"], size_bits=128)])
        result = compare(old, new)
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(result)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED not in _kinds(result)


# ===========================================================================
# 6. Private field layout impact
#
# abicc rules: Private_Field_Size_And_Layout*, Private_Field_Type_And_Size*
# abicheck: TYPE_FIELD_TYPE_CHANGED / TYPE_SIZE_CHANGED (no severity downgrade)
# ===========================================================================


class TestPrivateFieldLayoutImpact:
    """Detect private field changes that impact layout.

    abicc has ~20 private-field-specific rules at lower severity.
    abicheck detects the change but does not distinguish private fields
    for severity (intentional — sizeof is still affected).
    """

    def test_private_field_type_and_size_changed(self) -> None:
        """Private field type+size change → detected."""
        old = _snap(types=[RecordType(name="Impl", kind="class", size_bits=64, fields=[
            TypeField(name="pub", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
            TypeField(name="priv", type="int", offset_bits=32, access=AccessLevel.PRIVATE),
        ])])
        new = _snap(types=[RecordType(name="Impl", kind="class", size_bits=96, fields=[
            TypeField(name="pub", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
            TypeField(name="priv", type="long", offset_bits=32, access=AccessLevel.PRIVATE),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        # Should detect either field type change or size change (or both)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds or ChangeKind.TYPE_SIZE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_private_field_added_changes_size(self) -> None:
        """Private field added that changes class size → detected."""
        old = _snap(types=[RecordType(name="C", kind="class", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
        ])])
        new = _snap(types=[RecordType(name="C", kind="class", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
            TypeField(name="_cache", type="int", offset_bits=32, access=AccessLevel.PRIVATE),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds or ChangeKind.TYPE_FIELD_ADDED in kinds

    def test_private_field_removed_shifts_layout(self) -> None:
        """Removing a private field shifts subsequent field offsets."""
        old = _snap(types=[RecordType(name="C", kind="class", size_bits=96, fields=[
            TypeField(name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
            TypeField(name="_pad", type="int", offset_bits=32, access=AccessLevel.PRIVATE),
            TypeField(name="y", type="int", offset_bits=64, access=AccessLevel.PUBLIC),
        ])])
        new = _snap(types=[RecordType(name="C", kind="class", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC),
            TypeField(name="y", type="int", offset_bits=32, access=AccessLevel.PUBLIC),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert (ChangeKind.TYPE_FIELD_REMOVED in kinds
                or ChangeKind.TYPE_FIELD_OFFSET_CHANGED in kinds
                or ChangeKind.TYPE_SIZE_CHANGED in kinds)
        assert result.verdict == Verdict.BREAKING


# ===========================================================================
# 7. Array size changes in fields
#
# abicc rule: Field_Type_And_Size (Array)
# ===========================================================================


class TestArrayFieldSizeChanges:
    """Detect array field size changes."""

    def test_array_field_size_increased(self) -> None:
        """char buf[128] → char buf[256] changes field type → detected."""
        old = _snap(types=[RecordType(name="Buffer", kind="struct", size_bits=1024, fields=[
            TypeField(name="buf", type="char [128]", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="Buffer", kind="struct", size_bits=2048, fields=[
            TypeField(name="buf", type="char [256]", offset_bits=0),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds or ChangeKind.TYPE_SIZE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_array_field_dimension_changed(self) -> None:
        """int arr[4][4] → int arr[4][8] — multi-dimensional array change."""
        old = _snap(types=[RecordType(name="Matrix", kind="struct", size_bits=512, fields=[
            TypeField(name="data", type="int [4][4]", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="Matrix", kind="struct", size_bits=1024, fields=[
            TypeField(name="data", type="int [4][8]", offset_bits=0),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds or ChangeKind.TYPE_SIZE_CHANGED in kinds

    def test_array_field_unchanged(self) -> None:
        """Same array field → no change."""
        old = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="arr", type="int [10]", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="S", kind="struct", fields=[
            TypeField(name="arr", type="int [10]", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in _kinds(result)


# ===========================================================================
# 8. Member function pointer changes
#
# abicc rule: MethodPtr (member function pointer change)
# ===========================================================================


class TestMemberFunctionPointerChanges:
    """Detect member function pointer type changes."""

    def test_method_ptr_field_type_changed(self) -> None:
        """Field with member function pointer type changed."""
        old = _snap(types=[RecordType(name="Dispatch", kind="struct", fields=[
            TypeField(name="handler", type="void (Foo::*)(int)", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="Dispatch", kind="struct", fields=[
            TypeField(name="handler", type="void (Foo::*)(long)", offset_bits=0),
        ])])
        result = compare(old, new)
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(result)

    def test_method_ptr_param_changed(self) -> None:
        """Parameter with member function pointer type changed."""
        old = _snap(functions=[_func("set_handler", "_set_handler", params=[
            Param(name="h", type="void (Base::*)(int)"),
        ])])
        new = _snap(functions=[_func("set_handler", "_set_handler", params=[
            Param(name="h", type="void (Base::*)(double)"),
        ])])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)


# ===========================================================================
# 9. Template specialization removal
#
# abicc rule: Removed_Symbol (Template Specializations)
# ===========================================================================


class TestTemplateSpecializationRemoval:
    """Detect removal of explicit template specialization symbols."""

    def test_template_instantiation_removed(self) -> None:
        """Explicit template instantiation symbol removed → BREAKING."""
        old = _snap(functions=[
            _func("vector<int>::push_back", "_ZNSt6vectorIiE9push_backEi"),
            _func("vector<int>::size", "_ZNSt6vectorIiE4sizeEv"),
        ])
        new = _snap(functions=[
            _func("vector<int>::push_back", "_ZNSt6vectorIiE9push_backEi"),
        ])
        result = compare(old, new)
        assert ChangeKind.FUNC_REMOVED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_template_specialization_param_changed(self) -> None:
        """Template specialization parameter type changed.

        e.g. Foo<int>::process(int) → Foo<int>::process(long)
        """
        old = _snap(functions=[_func(
            "Foo<int>::process", "_ZN3FooIiE7processEi",
            params=[Param(name="x", type="int")],
        )])
        new = _snap(functions=[_func(
            "Foo<int>::process", "_ZN3FooIiE7processEi",
            params=[Param(name="x", type="long")],
        )])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)

    def test_multiple_template_specializations_removed(self) -> None:
        """Multiple template specializations removed → BREAKING."""
        old = _snap(functions=[
            _func("Foo<int>::bar", "_ZN3FooIiE3barEv"),
            _func("Foo<double>::bar", "_ZN3FooIdE3barEv"),
            _func("Foo<float>::bar", "_ZN3FooIfE3barEv"),
        ])
        new = _snap(functions=[
            _func("Foo<int>::bar", "_ZN3FooIiE3barEv"),
        ])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_REMOVED in kinds
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 2


# ===========================================================================
# 10. Register/stack parameter allocation (calling convention)
#
# abicc rules: Parameter_To_Register, Parameter_From_Register,
#   Parameter_Changed_Register, Parameter_Changed_Offset
# abicheck: CALLING_CONVENTION_CHANGED (function-level via DWARF)
# ===========================================================================


class TestCallingConventionParameterAllocation:
    """Detect calling convention changes affecting parameter passing.

    abicc tracks per-parameter register/stack allocation.
    abicheck detects this at the function level via CALLING_CONVENTION_CHANGED
    from DWARF DW_AT_calling_convention, plus FUNC_PARAMS_CHANGED for
    type changes that would force a different register/stack allocation.
    """

    def test_param_type_forces_register_change(self) -> None:
        """Changing param type from int to struct forces stack passing → BREAKING."""
        old = _snap(functions=[_func("process", "_process", params=[
            Param(name="val", type="int"),
        ])])
        new = _snap(functions=[_func("process", "_process", params=[
            Param(name="val", type="LargeStruct"),
        ])])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_param_count_changes_stack_layout(self) -> None:
        """Adding extra params pushes arguments to different registers/stack."""
        old = _snap(functions=[_func("calc", "_calc", params=[
            Param(name="a", type="int"),
            Param(name="b", type="int"),
        ])])
        new = _snap(functions=[_func("calc", "_calc", params=[
            Param(name="a", type="int"),
            Param(name="b", type="int"),
            Param(name="c", type="int"),
        ])])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_param_size_widening(self) -> None:
        """int → long may change register allocation on some ABIs."""
        old = _snap(functions=[_func("set", "_set", params=[
            Param(name="x", type="int"),
        ])])
        new = _snap(functions=[_func("set", "_set", params=[
            Param(name="x", type="long long"),
        ])])
        result = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)

    def test_param_pointer_to_value_transition(self) -> None:
        """int* → int: changes from register (pointer) to register (value).
        Different type → FUNC_PARAMS_CHANGED."""
        old = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int *", kind=ParamKind.POINTER, pointer_depth=1),
        ])])
        new = _snap(functions=[_func("f", "_f", params=[
            Param(name="p", type="int", kind=ParamKind.VALUE),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_PARAMS_CHANGED in kinds or ChangeKind.PARAM_POINTER_LEVEL_CHANGED in kinds


# ===========================================================================
# Cross-scenario integration tests
# ===========================================================================


class TestCrossScenarioIntegration:
    """Multi-detector scenarios combining P1 gap cases."""

    def test_funcptr_field_change_with_struct_size(self) -> None:
        """Function pointer field type change + struct size change."""
        old = _snap(types=[RecordType(name="API", kind="struct", size_bits=64, fields=[
            TypeField(name="init", type="void (*)(void)", offset_bits=0),
        ])])
        new = _snap(types=[RecordType(name="API", kind="struct", size_bits=128, fields=[
            TypeField(name="init", type="void (*)(void)", offset_bits=0),
            TypeField(name="cleanup", type="void (*)(void)", offset_bits=64),
        ])])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds or ChangeKind.TYPE_FIELD_ADDED in kinds

    def test_diamond_inheritance_with_virtual_removal(self) -> None:
        """Diamond inheritance + virtual method removed → multiple changes."""
        old = _snap(
            functions=[_func("D::foo", "_ZN1D3fooEv", is_virtual=True)],
            types=[RecordType(name="D", kind="class",
                              bases=["Left", "Right"],
                              vtable=["_ZN1D3fooEv", "_ZN1D3barEv"])],
        )
        new = _snap(
            functions=[],
            types=[RecordType(name="D", kind="class",
                              bases=["Left", "Right"],
                              vtable=["_ZN1D3barEv"])],
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_REMOVED in kinds or ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING
