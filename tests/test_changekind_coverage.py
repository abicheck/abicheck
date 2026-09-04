# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""tests/test_changekind_coverage.py — Explicit ChangeKind assertion coverage.

Covers the 10 ChangeKinds that had no explicit ``assert c.kind ==`` check:
  func_virtual_removed, type_added, type_alignment_changed, type_field_added,
  type_field_type_changed, type_removed, type_visibility_changed,
  typedef_removed, var_added, var_type_changed

All fixtures are original C++ ABI scenarios authored for this project.
"""
from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
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
    typedefs: dict[str, str] | None = None,
    dwarf_advanced: AdvancedDwarfMetadata | None = None,
) -> AbiSnapshot:
    s = AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        typedefs=typedefs or {},
    )
    if dwarf_advanced is not None:
        s.dwarf_advanced = dwarf_advanced  # type: ignore[attr-defined]
    return s


def _pub_func(
    name: str,
    mangled: str,
    ret: str = "void",
    *,
    virtual: bool = False,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        visibility=Visibility.PUBLIC,
        is_virtual=virtual,
    )


def _pub_var(name: str, mangled: str, type_: str) -> Variable:
    return Variable(name=name, mangled=mangled, type=type_, visibility=Visibility.PUBLIC)


def _minimal_adv() -> AdvancedDwarfMetadata:
    """Minimal AdvancedDwarfMetadata for monkeypatched tests."""
    return AdvancedDwarfMetadata(
        has_dwarf=True,
        toolchain=ToolchainInfo(
            producer_string="gcc",
            compiler="GCC",
            version="13.2",
            abi_flags=set(),
        ),
        calling_conventions={},
        packed_structs=set(),
        all_struct_names=set(),
    )


# ── FUNC_VIRTUAL_REMOVED ─────────────────────────────────────────────────────

class TestFuncVirtualRemoved:
    def test_virtual_removed_is_breaking(self) -> None:
        old_f = _pub_func("update", "_Z6updatev", virtual=True)
        new_f = _pub_func("update", "_Z6updatev", virtual=False)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_VIRTUAL_REMOVED for c in r.changes), (
            "Expected FUNC_VIRTUAL_REMOVED in changes"
        )

    def test_virtual_removed_symbol_matches(self) -> None:
        old_f = _pub_func("render", "_Z6renderv", virtual=True)
        new_f = _pub_func("render", "_Z6renderv", virtual=False)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        change = next(c for c in r.changes if c.kind == ChangeKind.FUNC_VIRTUAL_REMOVED)
        assert "_Z6renderv" in change.symbol

    def test_virtual_removed_not_emitted_when_unchanged(self) -> None:
        """No FUNC_VIRTUAL_REMOVED if virtual flag stays the same."""
        f = _pub_func("paint", "_Z5paintv", virtual=True)
        r = compare(_snap(functions=[f]), _snap("1.1", functions=[f]))
        assert not any(c.kind == ChangeKind.FUNC_VIRTUAL_REMOVED for c in r.changes)


# ── VAR_TYPE_CHANGED ─────────────────────────────────────────────────────────

class TestVarTypeChanged:
    def test_var_type_changed_is_breaking(self) -> None:
        old_v = _pub_var("g_limit", "_ZN3lib7g_limitE", "int")
        new_v = _pub_var("g_limit", "_ZN3lib7g_limitE", "unsigned int")
        r = compare(_snap(variables=[old_v]), _snap("2.0", variables=[new_v]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.VAR_TYPE_CHANGED for c in r.changes), (
            "Expected VAR_TYPE_CHANGED in changes"
        )

    def test_var_type_changed_records_old_and_new(self) -> None:
        old_v = _pub_var("g_flags", "_ZN3lib7g_flagsE", "int")
        new_v = _pub_var("g_flags", "_ZN3lib7g_flagsE", "long")
        r = compare(_snap(variables=[old_v]), _snap("2.0", variables=[new_v]))
        change = next(c for c in r.changes if c.kind == ChangeKind.VAR_TYPE_CHANGED)
        assert change.old_value == "int"
        assert change.new_value == "long"
        assert "_ZN3lib7g_flagsE" in change.symbol

    def test_var_type_unchanged_not_reported(self) -> None:
        v = _pub_var("g_limit", "_ZN3lib7g_limitE", "int")
        r = compare(_snap(variables=[v]), _snap("1.1", variables=[v]))
        assert not any(c.kind == ChangeKind.VAR_TYPE_CHANGED for c in r.changes)


# ── VAR_ADDED ────────────────────────────────────────────────────────────────

class TestVarAdded:
    def test_var_added_is_compatible(self) -> None:
        old_v = _pub_var("g_count", "_ZN3lib7g_countE", "int")
        new_v1 = _pub_var("g_count", "_ZN3lib7g_countE", "int")
        new_v2 = _pub_var("g_max", "_ZN3lib5g_maxE", "int")
        r = compare(
            _snap(variables=[old_v]),
            _snap("1.1", variables=[new_v1, new_v2]),
        )
        assert r.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.VAR_ADDED for c in r.changes), (
            "Expected VAR_ADDED in changes"
        )
        added = next(c for c in r.changes if c.kind == ChangeKind.VAR_ADDED)
        assert "_ZN3lib5g_maxE" in added.symbol

    def test_hidden_var_added_not_reported(self) -> None:
        """Hidden (non-public) variable addition must not appear in changes."""
        hidden_v = Variable(
            name="g_internal", mangled="_ZN3lib10g_internalE",
            type="int", visibility=Visibility.HIDDEN,
        )
        r = compare(_snap(), _snap("1.1", variables=[hidden_v]))
        assert not any(c.kind == ChangeKind.VAR_ADDED for c in r.changes)

    def test_var_added_no_change_when_nothing_new(self) -> None:
        v = _pub_var("g_count", "_ZN3lib7g_countE", "int")
        r = compare(_snap(variables=[v]), _snap("1.1", variables=[v]))
        assert not any(c.kind == ChangeKind.VAR_ADDED for c in r.changes)


# ── TYPE_REMOVED ─────────────────────────────────────────────────────────────

class TestTypeRemoved:
    def test_type_removed_is_breaking(self) -> None:
        t = RecordType(name="Handle", kind="struct")
        r = compare(_snap(types=[t]), _snap("2.0", types=[]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_REMOVED for c in r.changes), (
            "Expected TYPE_REMOVED in changes"
        )

    def test_type_removed_symbol_matches(self) -> None:
        t = RecordType(name="Context", kind="class", vtable=["_ZN7Context4initEv"])
        r = compare(_snap(types=[t]), _snap("2.0", types=[]))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_REMOVED)
        assert change.symbol == "Context"


# ── TYPE_ADDED ───────────────────────────────────────────────────────────────

class TestTypeAdded:
    def test_type_added_is_compatible(self) -> None:
        t = RecordType(name="NewConfig", kind="struct")
        r = compare(_snap(), _snap("1.1", types=[t]))
        assert r.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.TYPE_ADDED for c in r.changes), (
            "Expected TYPE_ADDED in changes"
        )

    def test_type_added_symbol_matches(self) -> None:
        t = RecordType(name="NewConfig", kind="struct")
        r = compare(_snap(), _snap("1.1", types=[t]))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_ADDED)
        assert change.symbol == "NewConfig"

    def test_type_added_alongside_breaking(self) -> None:
        """Adding a type while removing another: TYPE_ADDED present, verdict BREAKING."""
        old_t = RecordType(name="OldHandle", kind="struct")
        new_t = RecordType(name="NewHandle", kind="struct")
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.TYPE_REMOVED in kinds
        assert ChangeKind.TYPE_ADDED in kinds


# ── TYPE_ALIGNMENT_CHANGED ───────────────────────────────────────────────────

class TestTypeAlignmentChanged:
    def test_alignment_increased_is_breaking(self) -> None:
        """Increasing alignment is BREAKING: callers that stack-allocate the
        type may not satisfy the new stricter requirement."""
        old_t = RecordType(name="Buffer", kind="struct", size_bits=512, alignment_bits=64)
        new_t = RecordType(name="Buffer", kind="struct", size_bits=512, alignment_bits=512)
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_ALIGNMENT_CHANGED for c in r.changes)

    def test_alignment_decreased_is_breaking(self) -> None:
        """Decreasing alignment is also BREAKING: dependent code may rely on
        over-aligned guarantees (SIMD loads, atomic ops, etc.)."""
        old_t = RecordType(name="SIMDVec", kind="struct", size_bits=256, alignment_bits=256)
        new_t = RecordType(name="SIMDVec", kind="struct", size_bits=256, alignment_bits=64)
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_ALIGNMENT_CHANGED for c in r.changes)

    def test_alignment_old_and_new_values_recorded(self) -> None:
        old_t = RecordType(name="Vec", kind="struct", alignment_bits=32)
        new_t = RecordType(name="Vec", kind="struct", alignment_bits=128)
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_ALIGNMENT_CHANGED)
        assert change.old_value == "32"
        assert change.new_value == "128"

    def test_alignment_none_on_either_side_not_reported(self) -> None:
        """If alignment_bits is None on either side, no change reported
        (insufficient DWARF data — not a real ABI drift)."""
        old_t = RecordType(name="Pod", kind="struct", alignment_bits=None)
        new_t = RecordType(name="Pod", kind="struct", alignment_bits=64)
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert not any(c.kind == ChangeKind.TYPE_ALIGNMENT_CHANGED for c in r.changes)


# ── TYPE_FIELD_TYPE_CHANGED ──────────────────────────────────────────────────

class TestTypeFieldTypeChanged:
    def test_field_type_changed_is_breaking(self) -> None:
        old_t = RecordType(
            name="Packet",
            kind="struct",
            fields=[TypeField("length", "short", offset_bits=0)],
        )
        new_t = RecordType(
            name="Packet",
            kind="struct",
            fields=[TypeField("length", "int", offset_bits=0)],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED for c in r.changes), (
            "Expected TYPE_FIELD_TYPE_CHANGED in changes"
        )

    def test_field_type_changed_records_values(self) -> None:
        old_t = RecordType(
            name="Header",
            kind="struct",
            fields=[TypeField("flags", "uint8_t", offset_bits=0)],
        )
        new_t = RecordType(
            name="Header",
            kind="struct",
            fields=[TypeField("flags", "uint32_t", offset_bits=0)],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED)
        assert change.old_value == "uint8_t"
        assert change.new_value == "uint32_t"
        assert change.symbol == "Header"

    def test_field_type_unchanged_not_reported(self) -> None:
        t = RecordType(name="Rect", kind="struct",
                       fields=[TypeField("x", "int", offset_bits=0)])
        r = compare(_snap(types=[t]), _snap("1.1", types=[t]))
        assert not any(c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED for c in r.changes)


# ── TYPE_FIELD_ADDED ─────────────────────────────────────────────────────────

class TestTypeFieldAdded:
    def test_field_added_to_polymorphic_is_breaking(self) -> None:
        """Adding a field to a class with a vtable → TYPE_FIELD_ADDED (BREAKING),
        not TYPE_FIELD_ADDED_COMPATIBLE."""
        old_t = RecordType(
            name="Widget",
            kind="class",
            size_bits=64,
            vtable=["_ZN6Widget6renderEv"],
            fields=[TypeField("id", "int", offset_bits=0)],
        )
        new_t = RecordType(
            name="Widget",
            kind="class",
            size_bits=96,
            vtable=["_ZN6Widget6renderEv"],
            fields=[
                TypeField("id", "int", offset_bits=0),
                TypeField("flags", "int", offset_bits=32),
            ],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes), (
            "Expected BREAKING TYPE_FIELD_ADDED for polymorphic class"
        )
        # Must NOT emit the compatible variant
        assert not any(c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE for c in r.changes)

    def test_field_added_to_virtual_base_class_is_breaking(self) -> None:
        """Adding a field to a class with virtual bases → TYPE_FIELD_ADDED (BREAKING)."""
        old_t = RecordType(
            name="Derived",
            kind="class",
            virtual_bases=["Base"],
            fields=[TypeField("x", "int", offset_bits=0)],
        )
        new_t = RecordType(
            name="Derived",
            kind="class",
            virtual_bases=["Base"],
            fields=[
                TypeField("x", "int", offset_bits=0),
                TypeField("y", "int", offset_bits=32),
            ],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes)
        assert not any(c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE for c in r.changes)

    def test_field_added_to_plain_struct_is_compatible(self) -> None:
        """Appending a field to a standard-layout non-polymorphic struct
        → TYPE_FIELD_ADDED_COMPATIBLE (COMPATIBLE), NOT TYPE_FIELD_ADDED (BREAKING)."""
        old_t = RecordType(
            name="Point",
            kind="struct",
            fields=[TypeField("x", "float", offset_bits=0)],
        )
        new_t = RecordType(
            name="Point",
            kind="struct",
            fields=[
                TypeField("x", "float", offset_bits=0),
                TypeField("y", "float", offset_bits=32),
            ],
        )
        r = compare(_snap(types=[old_t]), _snap("1.1", types=[new_t]))
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE for c in r.changes)
        # Must NOT emit the breaking variant
        assert not any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes)


# ── TYPEDEF_REMOVED ──────────────────────────────────────────────────────────

class TestTypedefRemoved:
    def test_typedef_removed_is_breaking(self) -> None:
        old = _snap(typedefs={"size_type": "unsigned long"})
        new = _snap("2.0", typedefs={})
        r = compare(old, new)
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPEDEF_REMOVED for c in r.changes), (
            "Expected TYPEDEF_REMOVED in changes"
        )

    def test_typedef_removed_symbol_and_value(self) -> None:
        old = _snap(typedefs={"handle_t": "void*"})
        new = _snap("2.0", typedefs={})
        r = compare(old, new)
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED)
        assert change.symbol == "handle_t"
        assert change.old_value == "void*"

    def test_typedef_removed_only_missing_one(self) -> None:
        """Only the removed alias emits TYPEDEF_REMOVED; surviving aliases are silent."""
        old = _snap(typedefs={"size_t": "unsigned long", "ptr_t": "void*"})
        new = _snap("2.0", typedefs={"size_t": "unsigned long"})
        r = compare(old, new)
        removed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "ptr_t"

    def test_typedef_added_not_tracked(self) -> None:
        """Adding a new typedef emits no change (checker only tracks removals/renames).
        New aliases are backwards-compatible — callers that don't use the alias
        are unaffected, so NO_CHANGE is the correct verdict."""
        old = _snap(typedefs={})
        new = _snap("1.1", typedefs={"offset_t": "long"})
        r = compare(old, new)
        assert r.verdict == Verdict.NO_CHANGE
        assert not any(c.kind == ChangeKind.TYPEDEF_REMOVED for c in r.changes)


# ── TYPE_VISIBILITY_CHANGED ──────────────────────────────────────────────────

class TestTypeVisibilityChanged:
    """TYPE_VISIBILITY_CHANGED is emitted via diff_advanced_dwarf.

    checker.py imports diff_advanced_dwarf at module level:
        from .compare.dwarf_advanced_diff import diff_advanced_dwarf
    So the correct monkeypatch target is abicheck.checker.diff_advanced_dwarf —
    that is where the already-bound reference lives at call time.
    Tests inject pre-built tuples without needing real .so files.
    """

    def test_type_visibility_changed_is_breaking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.checker as checker_mod

        def fake_diff(
            old: AdvancedDwarfMetadata,
            new: AdvancedDwarfMetadata,
        ) -> list[tuple[str, str, str, str | None, str | None]]:
            return [(
                "type_visibility_changed",
                "MyClass",
                "Type visibility changed: MyClass (default → hidden)",
                "default",
                "hidden",
            )]

        monkeypatch.setattr(checker_mod, "diff_advanced_dwarf", fake_diff)

        adv = _minimal_adv()
        r = compare(_snap(dwarf_advanced=adv), _snap("2.0", dwarf_advanced=adv))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_VISIBILITY_CHANGED for c in r.changes), (
            "Expected TYPE_VISIBILITY_CHANGED in changes"
        )

    def test_type_visibility_changed_symbol_and_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.checker as checker_mod

        def fake_diff(
            old: AdvancedDwarfMetadata,
            new: AdvancedDwarfMetadata,
        ) -> list[tuple[str, str, str, str | None, str | None]]:
            return [(
                "type_visibility_changed",
                "AbstractBase",
                "Type visibility changed: AbstractBase",
                "default",
                "hidden",
            )]

        monkeypatch.setattr(checker_mod, "diff_advanced_dwarf", fake_diff)

        adv = _minimal_adv()
        r = compare(_snap(dwarf_advanced=adv), _snap("2.0", dwarf_advanced=adv))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_VISIBILITY_CHANGED)
        assert change.symbol == "AbstractBase"
        assert change.old_value == "default"
        assert change.new_value == "hidden"

    def test_no_type_visibility_without_dwarf(self) -> None:
        """Without dwarf_advanced metadata, TYPE_VISIBILITY_CHANGED is never emitted."""
        r = compare(_snap(), _snap("2.0"))
        assert not any(c.kind == ChangeKind.TYPE_VISIBILITY_CHANGED for c in r.changes)
