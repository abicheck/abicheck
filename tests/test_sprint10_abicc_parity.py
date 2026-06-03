"""Sprint 10: ABICC parity — func_visibility_changed + type_field_added
breaking variant coverage.

Tests for two detector improvements:
1. FUNC_VISIBILITY_CHANGED: function moved from default to hidden visibility
   (binary ABI break — symbol disappears from dynamic export table).
   Detection works with castxml headers: _CastxmlParser._visibility() assigns
   Visibility.HIDDEN to functions present in XML but absent from .dynsym.
2. TYPE_FIELD_ADDED breaking path: field added to polymorphic class or class
   with virtual bases is BREAKING (not compatible).

All fixtures are original C++ ABI scenarios authored for this project.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
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
    elf_only_mode: bool = False,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        elf_only_mode=elf_only_mode,
    )


def _func(
    name: str,
    mangled: str,
    ret: str = "void",
    visibility: Visibility = Visibility.PUBLIC,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        visibility=visibility,
    )


# ── FUNC_VISIBILITY_CHANGED ───────────────────────────────────────────────────

class TestFuncVisibilityChanged:
    """Public function becomes hidden: binary ABI break.

    Callers that linked against the public symbol will get an undefined
    reference at load time — strictly more severe than FUNC_REMOVED from
    the dynamic linker's perspective.
    """

    def test_public_to_hidden_is_breaking(self) -> None:
        old_f = _func("api", "_Z3apiv", visibility=Visibility.PUBLIC)
        new_f = _func("api", "_Z3apiv", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED for c in r.changes), (
            "Expected FUNC_VISIBILITY_CHANGED"
        )

    def test_visibility_change_not_reported_as_func_removed(self) -> None:
        """FUNC_VISIBILITY_CHANGED must NOT also emit FUNC_REMOVED."""
        old_f = _func("process", "_Z7processv", visibility=Visibility.PUBLIC)
        new_f = _func("process", "_Z7processv", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        assert not any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes), (
            "FUNC_REMOVED must not be emitted when FUNC_VISIBILITY_CHANGED covers it"
        )

    def test_visibility_change_old_new_values_and_description(self) -> None:
        """old_value/new_value reflect actual visibility; description names the function."""
        old_f = _func("render", "_Z6renderv", visibility=Visibility.PUBLIC)
        new_f = _func("render", "_Z6renderv", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        change = next(c for c in r.changes if c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED)
        assert change.old_value == Visibility.PUBLIC.value
        assert change.new_value == Visibility.HIDDEN.value
        assert "_Z6renderv" in change.symbol
        assert "render" in change.description

    def test_func_truly_removed_still_reported(self) -> None:
        """Function absent from new snapshot entirely → FUNC_REMOVED (not visibility change)."""
        old_f = _func("gone", "_Z4gonev", visibility=Visibility.PUBLIC)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[]))
        assert any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes)
        assert not any(c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED for c in r.changes)

    def test_hidden_to_public_is_compatible(self) -> None:
        """Hidden function becoming public: COMPATIBLE (appears as FUNC_ADDED).

        Hidden functions are not tracked in old_map (built from PUBLIC|ELF_ONLY
        only), so the function is invisible to the old-side diff. In new_map it
        appears as a new PUBLIC export → FUNC_ADDED. This is intentional: there
        was no prior public ABI contract to break.
        """
        old_f = _func("impl", "_Z4implv", visibility=Visibility.HIDDEN)
        new_f = _func("impl", "_Z4implv", visibility=Visibility.PUBLIC)
        r = compare(_snap(functions=[old_f]), _snap("1.1", functions=[new_f]))
        assert r.verdict == Verdict.COMPATIBLE
        assert not any(c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED for c in r.changes)

    def test_multiple_functions_only_changed_one_reported(self) -> None:
        """Only the visibility-changed function emits FUNC_VISIBILITY_CHANGED."""
        stable = _func("stable", "_Z6stablev", visibility=Visibility.PUBLIC)
        old_api = _func("api", "_Z3apiv", visibility=Visibility.PUBLIC)
        new_api = _func("api", "_Z3apiv", visibility=Visibility.HIDDEN)
        r = compare(
            _snap(functions=[stable, old_api]),
            _snap("2.0", functions=[stable, new_api]),
        )
        assert r.verdict == Verdict.BREAKING
        vis_changes = [c for c in r.changes if c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED]
        assert len(vis_changes) == 1
        assert "_Z3apiv" in vis_changes[0].symbol

    def test_elf_only_symbol_absent_is_func_removed_elf_only(self) -> None:
        """ELF_ONLY symbol absent from new snapshot → FUNC_REMOVED_ELF_ONLY.

        ELF-only symbols (no header/DWARF data) that disappear may be internal
        symbols getting properly hidden via -fvisibility=hidden, but in strict
        binary-only mode abicheck must still treat removed dynamic exports as
        breaks because old binaries or dlsym() consumers may depend on them.
        Requires elf_only_mode=True on the old snapshot (set by dumper when
        no headers are provided).
        """
        old_f = _func("sym", "_Z3symv", visibility=Visibility.ELF_ONLY)
        r = compare(_snap(functions=[old_f], elf_only_mode=True), _snap("2.0", functions=[]))
        assert any(c.kind == ChangeKind.FUNC_REMOVED_ELF_ONLY for c in r.changes)
        assert not any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes)
        assert not any(c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED for c in r.changes)
        assert r.verdict == Verdict.BREAKING

    def test_elf_only_to_hidden_is_visibility_change(self) -> None:
        """ELF_ONLY → HIDDEN: callers that resolved the symbol dynamically break.

        old_value reflects actual prior visibility (elf_only), not "public".
        ELF_ONLY functions are tracked in old_map alongside PUBLIC functions.
        """
        old_f = _func("sym", "_Z3symv", visibility=Visibility.ELF_ONLY)
        new_f = _func("sym", "_Z3symv", visibility=Visibility.HIDDEN)
        r = compare(_snap(functions=[old_f]), _snap("2.0", functions=[new_f]))
        assert r.verdict == Verdict.BREAKING
        change = next(c for c in r.changes if c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED)
        assert change.old_value == Visibility.ELF_ONLY.value
        assert change.new_value == Visibility.HIDDEN.value


# ── TYPE_FIELD_ADDED breaking variant ────────────────────────────────────────

class TestTypeFieldAddedBreaking:
    """Field addition is BREAKING for polymorphic types.

    Standard-layout non-polymorphic structs get TYPE_FIELD_ADDED_COMPATIBLE.
    Classes with vtable or virtual_bases get TYPE_FIELD_ADDED (BREAKING).
    """

    def test_field_added_to_vtable_class_is_breaking(self) -> None:
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
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes)
        assert not any(c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE for c in r.changes)

    def test_field_added_to_virtual_base_class_is_breaking(self) -> None:
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
        """Standard-layout non-polymorphic struct: field addition is compatible."""
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
        assert r.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE for c in r.changes)
        assert not any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes)

    def test_field_added_to_pure_virtual_class_is_breaking(self) -> None:
        """Abstract class (pure virtual in vtable) — field addition is BREAKING."""
        old_t = RecordType(
            name="IRenderer",
            kind="class",
            vtable=["_ZN9IRenderer6renderEv", "__cxa_pure_virtual"],
            fields=[],
        )
        new_t = RecordType(
            name="IRenderer",
            kind="class",
            vtable=["_ZN9IRenderer6renderEv", "__cxa_pure_virtual"],
            fields=[TypeField("_reserved", "int", offset_bits=0)],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        assert r.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_ADDED for c in r.changes)

    def test_field_added_breaking_symbol_matches(self) -> None:
        old_t = RecordType(
            name="EventHandler",
            kind="class",
            vtable=["_ZN12EventHandler6handleEv"],
            fields=[TypeField("id", "int", offset_bits=0)],
        )
        new_t = RecordType(
            name="EventHandler",
            kind="class",
            vtable=["_ZN12EventHandler6handleEv"],
            fields=[
                TypeField("id", "int", offset_bits=0),
                TypeField("data", "int", offset_bits=32),
            ],
        )
        r = compare(_snap(types=[old_t]), _snap("2.0", types=[new_t]))
        change = next(c for c in r.changes if c.kind == ChangeKind.TYPE_FIELD_ADDED)
        assert change.symbol == "EventHandler"
