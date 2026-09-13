"""Sprint 2 gap detectors: unit tests.

Covers:
- FUNC_DELETED           (= delete added to previously callable function)
- VAR_BECAME_CONST       (non-const global → const)
- VAR_LOST_CONST         (const global → non-const)
- TYPE_BECAME_OPAQUE     (complete struct → forward-declaration only)
- BASE_CLASS_POSITION_CHANGED  (base class order reordered)
- BASE_CLASS_VIRTUAL_CHANGED   (base became virtual or non-virtual)
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    Variable,
    Visibility,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(
    version: str = "1.0",
    *,
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
    )


def _func(name: str, mangled: str | None = None, **kwargs) -> Function:
    return Function(
        name=name,
        mangled=mangled or f"_{name}",
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


def _var(
    name: str, mangled: str | None = None, type: str = "int", **kwargs
) -> Variable:
    return Variable(
        name=name,
        mangled=mangled or f"_{name}",
        type=type,
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


def _type(name: str, **kwargs) -> RecordType:
    return RecordType(name=name, kind="struct", size_bits=64, **kwargs)


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── FUNC_DELETED ──────────────────────────────────────────────────────────────


class TestFuncDeleted:
    def test_func_becomes_deleted_is_breaking(self):
        old = _snap(functions=[_func("foo", "_Z3foov", is_deleted=False)])
        new = _snap(functions=[_func("foo", "_Z3foov", is_deleted=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_func_deleted_in_both_no_change(self):
        """Function was already deleted in old — no new break."""
        old = _snap(functions=[_func("foo", "_Z3foov", is_deleted=True)])
        new = _snap(functions=[_func("foo", "_Z3foov", is_deleted=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED not in _kinds(result)

    def test_func_not_deleted_no_change(self):
        old = _snap(functions=[_func("foo", "_Z3foov")])
        new = _snap(functions=[_func("foo", "_Z3foov")])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED not in _kinds(result)

    def test_func_undeleted_is_func_added(self):
        """= delete removed: function becomes callable again — treated as FUNC_ADDED."""
        old = _snap(functions=[_func("foo", "_Z3foov", is_deleted=True)])
        new = _snap(functions=[_func("foo", "_Z3foov", is_deleted=False)])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED not in _kinds(result)


# ── VAR_BECAME_CONST ─────────────────────────────────────────────────────────


class TestVarBecameConst:
    def test_var_became_const_is_breaking(self):
        old = _snap(variables=[_var("g_buf", "_g_buf", is_const=False)])
        new = _snap(variables=[_var("g_buf", "_g_buf", is_const=True)])
        result = compare(old, new)
        assert ChangeKind.VAR_BECAME_CONST in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_var_already_const_no_change(self):
        old = _snap(variables=[_var("g_buf", "_g_buf", is_const=True)])
        new = _snap(variables=[_var("g_buf", "_g_buf", is_const=True)])
        result = compare(old, new)
        assert ChangeKind.VAR_BECAME_CONST not in _kinds(result)

    def test_var_not_const_no_change(self):
        old = _snap(variables=[_var("g_buf", "_g_buf")])
        new = _snap(variables=[_var("g_buf", "_g_buf")])
        result = compare(old, new)
        assert ChangeKind.VAR_BECAME_CONST not in _kinds(result)


# ── VAR_LOST_CONST ───────────────────────────────────────────────────────────


class TestVarLostConst:
    def test_var_lost_const_is_breaking(self):
        old = _snap(variables=[_var("g_limit", "_g_limit", is_const=True)])
        new = _snap(variables=[_var("g_limit", "_g_limit", is_const=False)])
        result = compare(old, new)
        assert ChangeKind.VAR_LOST_CONST in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_var_already_non_const_no_change(self):
        old = _snap(variables=[_var("g_limit", "_g_limit", is_const=False)])
        new = _snap(variables=[_var("g_limit", "_g_limit", is_const=False)])
        result = compare(old, new)
        assert ChangeKind.VAR_LOST_CONST not in _kinds(result)

    def test_var_became_const_does_not_trigger_lost_const(self):
        old = _snap(variables=[_var("x", "_x", is_const=False)])
        new = _snap(variables=[_var("x", "_x", is_const=True)])
        result = compare(old, new)
        assert ChangeKind.VAR_LOST_CONST not in _kinds(result)
        assert ChangeKind.VAR_BECAME_CONST in _kinds(result)


# ── TYPE_BECAME_OPAQUE ───────────────────────────────────────────────────────


class TestTypeBecameOpaque:
    def test_complete_to_opaque_is_breaking(self):
        old = _snap(types=[_type("Ctx", is_opaque=False)])
        new = _snap(types=[_type("Ctx", is_opaque=True)])
        result = compare(old, new)
        assert ChangeKind.TYPE_BECAME_OPAQUE in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_always_opaque_no_change(self):
        old = _snap(types=[_type("Ctx", is_opaque=True)])
        new = _snap(types=[_type("Ctx", is_opaque=True)])
        result = compare(old, new)
        assert ChangeKind.TYPE_BECAME_OPAQUE not in _kinds(result)

    def test_opaque_to_complete_no_opaque_change(self):
        """Opaque → complete: type gains definition, not a break (no OPAQUE change)."""
        old = _snap(types=[_type("Ctx", is_opaque=True)])
        new = _snap(types=[_type("Ctx", is_opaque=False)])
        result = compare(old, new)
        assert ChangeKind.TYPE_BECAME_OPAQUE not in _kinds(result)

    def test_complete_to_opaque_no_further_field_checks(self):
        """When type goes opaque, no spurious TYPE_FIELD_REMOVED should fire."""
        fields_type = _type("S", fields=[], is_opaque=False)
        old = _snap(types=[fields_type])
        new = _snap(types=[_type("S", is_opaque=True)])
        result = compare(old, new)
        assert ChangeKind.TYPE_BECAME_OPAQUE in _kinds(result)
        assert ChangeKind.TYPE_FIELD_REMOVED not in _kinds(result)


# ── BASE_CLASS_POSITION_CHANGED ───────────────────────────────────────────────


class TestBaseClassPositionChanged:
    def test_base_reorder_is_breaking(self):
        old = _snap(types=[_type("D", bases=["A", "B", "C"])])
        new = _snap(types=[_type("D", bases=["C", "A", "B"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_base_reorder_two_bases(self):
        old = _snap(types=[_type("D", bases=["A", "B"])])
        new = _snap(types=[_type("D", bases=["B", "A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in _kinds(result)

    def test_base_same_order_no_change(self):
        old = _snap(types=[_type("D", bases=["A", "B"])])
        new = _snap(types=[_type("D", bases=["A", "B"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED not in _kinds(result)

    def test_base_added_not_reorder(self):
        """Adding a new base → TYPE_BASE_CHANGED, not POSITION_CHANGED."""
        old = _snap(types=[_type("D", bases=["A"])])
        new = _snap(types=[_type("D", bases=["A", "B"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED not in _kinds(result)
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(result)

    def test_single_base_unchanged(self):
        old = _snap(types=[_type("D", bases=["A"])])
        new = _snap(types=[_type("D", bases=["A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED not in _kinds(result)


# ── BASE_CLASS_VIRTUAL_CHANGED ────────────────────────────────────────────────


class TestBaseClassVirtualChanged:
    def test_base_became_virtual_is_breaking(self):
        old = _snap(types=[_type("D", bases=["A"], virtual_bases=[])])
        new = _snap(types=[_type("D", bases=[], virtual_bases=["A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_virtual_base_lost_virtual_is_breaking(self):
        old = _snap(types=[_type("D", bases=[], virtual_bases=["A"])])
        new = _snap(types=[_type("D", bases=["A"], virtual_bases=[])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_virtual_base_unchanged_no_change(self):
        old = _snap(types=[_type("D", bases=["B"], virtual_bases=["A"])])
        new = _snap(types=[_type("D", bases=["B"], virtual_bases=["A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED not in _kinds(result)

    def test_virtual_base_added_not_virtual_changed(self):
        """Adding a brand-new virtual base → TYPE_BASE_CHANGED, not VIRTUAL_CHANGED."""
        old = _snap(types=[_type("D", virtual_bases=[])])
        new = _snap(types=[_type("D", virtual_bases=["A"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED not in _kinds(result)
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(result)

    def test_non_virtual_base_no_virtual_change(self):
        old = _snap(types=[_type("D", bases=["A", "B"])])
        new = _snap(types=[_type("D", bases=["A", "B"])])
        result = compare(old, new)
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED not in _kinds(result)

    def test_combined_reorder_and_virtual_change(self):
        """Reorder + virtualize simultaneously: both changes reported."""
        old = _snap(types=[_type("D", bases=["A", "B"], virtual_bases=[])])
        new = _snap(types=[_type("D", bases=["B"], virtual_bases=["A"])])
        result = compare(old, new)
        # A moved from non-virtual to virtual → BASE_CLASS_VIRTUAL_CHANGED
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED in _kinds(result)


class TestFuncDeletedEdgeCases:
    def test_func_deleted_no_func_removed_emitted(self):
        """FUNC_REMOVED must NOT be emitted alongside FUNC_DELETED for same symbol."""
        old = _snap(functions=[_func("bar", "_Z3barv", is_deleted=False)])
        new = _snap(functions=[_func("bar", "_Z3barv", is_deleted=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED in _kinds(result)
        assert ChangeKind.FUNC_REMOVED not in _kinds(result)


class TestOpaqueSizeBitsNone:
    def test_opaque_type_no_size_bits(self):
        """Real castxml forward-decls have size_bits=None — must not crash."""
        old = _snap(types=[RecordType(name="Ctx", kind="struct", size_bits=64)])
        new = _snap(
            types=[
                RecordType(name="Ctx", kind="struct", size_bits=None, is_opaque=True)
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_BECAME_OPAQUE in _kinds(result)
        assert result.verdict == Verdict.BREAKING


class TestVarConstFalsePositive:
    def test_type_name_with_const_substring_no_false_positive(self):
        """Type names like 'constructor_t' must not trigger VAR_BECAME_CONST."""
        old = _snap(variables=[_var("x", "_x", type="constructor_t", is_const=False)])
        new = _snap(variables=[_var("x", "_x", type="constructor_t", is_const=False)])
        result = compare(old, new)
        assert ChangeKind.VAR_BECAME_CONST not in _kinds(result)

    def test_type_name_const_iterator_no_false_positive(self):
        old = _snap(
            variables=[_var("it", "_it", type="const_iterator", is_const=False)]
        )
        new = _snap(
            variables=[_var("it", "_it", type="const_iterator", is_const=False)]
        )
        result = compare(old, new)
        assert ChangeKind.VAR_BECAME_CONST not in _kinds(result)
