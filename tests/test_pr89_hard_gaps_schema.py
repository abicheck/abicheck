"""PR #89 tests — ELF fallback for = delete, template inner-type analysis, schema baseline.

Covers:
1. Issue #100 follow-up: FUNC_DELETED_ELF_FALLBACK — ELF fallback path when castxml
   metadata lacks deleted="1" but symbol disappears from .dynsym.
2. Issues #38 / #73: TEMPLATE_PARAM_TYPE_CHANGED / TEMPLATE_RETURN_TYPE_CHANGED —
   detect ABI-relevant inner type changes for templated params/returns.
3. Schema baseline: schema_version field in snapshot serialization output;
   backward-compatible loading of snapshots without schema_version.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Param, ParamKind, Visibility
from abicheck.serialization import (
    SCHEMA_VERSION,
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _elf_with_syms(*names: str) -> ElfMetadata:
    """Build a minimal ElfMetadata with the given symbol names exported."""
    syms = [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC, size=0)
        for n in names
    ]
    return ElfMetadata(symbols=syms)


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


# =============================================================================
# Part 1 — Issue #100: FUNC_DELETED_ELF_FALLBACK
# =============================================================================


class TestFuncDeletedElfFallbackPolicy:
    """Policy checks: enum value, BREAKING_KINDS membership."""

    def test_enum_value(self) -> None:
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK.value == "func_deleted_elf_fallback"

    def test_in_breaking_kinds(self) -> None:
        """FUNC_DELETED_ELF_FALLBACK must be classified as BREAKING (symbol absent from dynsym is binary-incompatible)."""
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK in BREAKING_KINDS


class TestFuncDeletedElfFallbackDetection:
    """Positive + negative tests for the ELF fallback detector."""

    def test_symbol_disappears_from_dynsym_is_breaking(self) -> None:
        """Symbol in old ELF + old header, absent from new ELF, still in new header → BREAKING."""
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[_func("process", mangled)],  # still in header model
            elf=_elf_with_syms(),                   # but NOT in new ELF
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_symbol_present_in_both_elf_no_change(self) -> None:
        """Symbol exported in both old and new ELF → no fallback change."""
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in _kinds(result)

    def test_symbol_only_in_new_elf_no_change(self) -> None:
        """Symbol added to new ELF (new function) → no fallback change."""
        mangled = "_Z7processv"
        old = _snap(functions=[], elf=_elf_with_syms())
        new = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in _kinds(result)

    def test_no_elf_data_no_fallback(self) -> None:
        """Without ELF metadata, fallback detector must not fire."""
        mangled = "_Z7processv"
        old = _snap(functions=[_func("process", mangled)])  # no elf=
        new = _snap(functions=[_func("process", mangled)])  # no elf=
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in _kinds(result)

    def test_explicit_deleted_uses_func_deleted_not_fallback(self) -> None:
        """Explicit is_deleted=True → FUNC_DELETED, NOT FUNC_DELETED_ELF_FALLBACK."""
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[_func("process", mangled, is_deleted=True)],
            elf=_elf_with_syms(),  # also absent from ELF
        )
        result = compare(old, new)
        kinds = _kinds(result)
        # FUNC_DELETED must be present (castxml path takes priority)
        assert ChangeKind.FUNC_DELETED in kinds
        # ELF fallback must NOT double-report
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds

    def test_became_inline_symbol_vanished_is_breaking(self) -> None:
        """is_inline transition where symbol vanishes from .dynsym is a binary break.

        FUNC_BECAME_INLINE (API_BREAK) fires for the inline transition, AND
        FUNC_DELETED_ELF_FALLBACK (BREAKING) fires because the symbol actually
        disappeared from .dynsym — old binaries linked against it will fail.
        """
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled, is_inline=False)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[_func("process", mangled, is_inline=True)],
            elf=_elf_with_syms(),  # inline function may not appear in .dynsym
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_BECAME_INLINE in kinds
        # Symbol vanished from .dynsym → binary break for pre-compiled consumers
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK in kinds

    def test_func_removed_not_in_new_header_no_fallback(self) -> None:
        """Symbol removed from both header AND ELF → FUNC_REMOVED (not fallback)."""
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[],      # completely gone from header model too
            elf=_elf_with_syms(),
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds

    def test_multiple_functions_only_disappeared_one_flagged(self) -> None:
        """Only the function that disappeared from ELF should get the fallback change."""
        m1 = "_Z4foo1v"
        m2 = "_Z4foo2v"
        old = _snap(
            functions=[_func("foo1", m1), _func("foo2", m2)],
            elf=_elf_with_syms(m1, m2),
        )
        new = _snap(
            functions=[_func("foo1", m1), _func("foo2", m2)],
            elf=_elf_with_syms(m1),  # foo2 disappeared
        )
        result = compare(old, new)
        fb_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_DELETED_ELF_FALLBACK]
        assert len(fb_changes) == 1
        assert fb_changes[0].symbol == m2

    def test_elf_only_mode_not_flagged_as_fallback(self) -> None:
        """ELF-only symbols that disappear are handled by FUNC_REMOVED_ELF_ONLY, not fallback."""
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled, visibility=Visibility.ELF_ONLY)],
            elf=_elf_with_syms(mangled),
            elf_only_mode=True,
        )
        # ELF-only removal: symbol removed from both header and ELF
        new = _snap(
            functions=[],
            elf=_elf_with_syms(),
            elf_only_mode=True,
        )
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds


# =============================================================================
# Part 2 — Issues #38 / #73: Template inner-type analysis
# =============================================================================


class TestExtractTemplateArgs:
    """Unit tests for the _extract_template_args helper."""

    def test_simple_vector_int(self) -> None:
        from abicheck.checker import _extract_template_args
        assert _extract_template_args("std::vector<int>") == ["int"]

    def test_simple_vector_double(self) -> None:
        from abicheck.checker import _extract_template_args
        assert _extract_template_args("std::vector<double>") == ["double"]

    def test_map_two_args(self) -> None:
        from abicheck.checker import _extract_template_args
        result = _extract_template_args("std::map<int, double>")
        assert result == ["int", "double"]

    def test_nested_template(self) -> None:
        from abicheck.checker import _extract_template_args
        result = _extract_template_args("Foo<Bar<int>, double>")
        assert result == ["Bar<int>", "double"]

    def test_non_template_returns_none(self) -> None:
        from abicheck.checker import _extract_template_args
        assert _extract_template_args("int") is None
        assert _extract_template_args("std::string") is None

    def test_empty_template_args(self) -> None:
        from abicheck.checker import _extract_template_args
        result = _extract_template_args("Empty<>")
        assert result == []

    def test_pointer_template(self) -> None:
        from abicheck.checker import _extract_template_args
        result = _extract_template_args("std::unique_ptr<Foo>")
        assert result == ["Foo"]



    def test_function_type_with_comma_in_parens(self) -> None:
        """std::function<void(int, double)> must NOT be split on the comma inside ()."""
        from abicheck.checker import _extract_template_args
        result = _extract_template_args("std::function<void(int, double)>")
        assert result == ["void(int, double)"], (
            f"Expected single arg 'void(int, double)', got: {result}"
        )

class TestTemplateOuterName:
    """Unit tests for the _template_outer helper."""

    def test_vector(self) -> None:
        from abicheck.checker import _template_outer
        assert _template_outer("std::vector<int>") == "std::vector"

    def test_map(self) -> None:
        from abicheck.checker import _template_outer
        assert _template_outer("std::map<int, double>") == "std::map"

    def test_non_template(self) -> None:
        from abicheck.checker import _template_outer
        assert _template_outer("int") == "int"


class TestTemplatePolicyKinds:
    """Policy checks: in BREAKING_KINDS."""

    def test_template_param_type_changed_breaking(self) -> None:
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED in BREAKING_KINDS

    def test_template_return_type_changed_breaking(self) -> None:
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED in BREAKING_KINDS

    def test_enum_values(self) -> None:
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED.value == "template_param_type_changed"
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED.value == "template_return_type_changed"


class TestTemplateParamTypeChanged:
    """Tests for TEMPLATE_PARAM_TYPE_CHANGED detection.

    NOTE: Under the Itanium C++ ABI, parameter types are encoded in the mangled
    symbol name. A real std::vector<int> → std::vector<double> param change produces
    different mangled names (FUNC_REMOVED + FUNC_ADDED). These tests simulate
    header-only / simplified snapshot mode by reusing the same mangled name for
    old and new, which is the documented use case #2 for this detector.
    For ELF-based snapshots, FUNC_PARAMS_CHANGED is the primary signal.
    """

    def _make_func_with_vec_param(
        self,
        name: str,
        mangled: str,
        inner_type: str,
    ) -> Function:
        return Function(
            name=name,
            mangled=mangled,
            return_type="void",
            params=[Param(name="v", type=f"std::vector<{inner_type}>", kind=ParamKind.VALUE)],
            visibility=Visibility.PUBLIC,
        )

    def test_vector_int_to_double_param_change(self) -> None:
        """std::vector<int> param → std::vector<double>: TEMPLATE_PARAM_TYPE_CHANGED."""
        mangled = "_Z7processNSt6vectorIiEE"
        old = _snap(functions=[self._make_func_with_vec_param("process", mangled, "int")])
        new = _snap(functions=[self._make_func_with_vec_param("process", mangled, "double")])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_same_template_inner_type_no_change(self) -> None:
        """Same inner type → no TEMPLATE_PARAM_TYPE_CHANGED."""
        mangled = "_Z7processNSt6vectorIiEE"
        old = _snap(functions=[self._make_func_with_vec_param("process", mangled, "int")])
        new = _snap(functions=[self._make_func_with_vec_param("process", mangled, "int")])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED not in _kinds(result)

    def test_different_outer_template_no_template_change(self) -> None:
        """vector<int> → list<int>: outer name changed → should NOT fire TEMPLATE_PARAM_TYPE_CHANGED
        (FUNC_PARAMS_CHANGED covers it instead)."""
        mangled = "_Z7processv"
        old = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="v", type="std::vector<int>")],
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="v", type="std::list<int>")],
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        # The full type changed → FUNC_PARAMS_CHANGED, not TEMPLATE_PARAM_TYPE_CHANGED
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED not in _kinds(result)

    def test_map_key_type_changes(self) -> None:
        """std::map<int, string> → std::map<double, string>: first arg changed."""
        mangled = "_Z7processv"
        old = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="m", type="std::map<int, std::string>")],
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="m", type="std::map<double, std::string>")],
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_non_template_param_no_template_change(self) -> None:
        """Plain int → double: no TEMPLATE_PARAM_TYPE_CHANGED."""
        mangled = "_Z7processv"
        old = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="x", type="int")],
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="x", type="double")],
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED not in _kinds(result)


class TestTemplateReturnTypeChanged:
    """Positive tests: template return type inner change is detected."""

    def test_vector_int_to_double_return(self) -> None:
        """Return type std::vector<int> → std::vector<double>: TEMPLATE_RETURN_TYPE_CHANGED."""
        mangled = "_Z7getVecv"
        old = _snap(functions=[Function(
            name="getVec", mangled=mangled,
            return_type="std::vector<int>",
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="getVec", mangled=mangled,
            return_type="std::vector<double>",
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_same_return_template_no_change(self) -> None:
        """Same return template inner type → no TEMPLATE_RETURN_TYPE_CHANGED."""
        mangled = "_Z7getVecv"
        old = _snap(functions=[Function(
            name="getVec", mangled=mangled,
            return_type="std::vector<int>",
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="getVec", mangled=mangled,
            return_type="std::vector<int>",
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED not in _kinds(result)

    def test_non_template_return_no_template_change(self) -> None:
        """Plain int return type → no template change."""
        mangled = "_Z7getValv"
        old = _snap(functions=[Function(
            name="getVal", mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
        )])
        new = _snap(functions=[Function(
            name="getVal", mangled=mangled, return_type="double", visibility=Visibility.PUBLIC
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED not in _kinds(result)

    def test_unique_ptr_inner_type_change(self) -> None:
        """Return std::unique_ptr<Foo> → std::unique_ptr<Bar>: TEMPLATE_RETURN_TYPE_CHANGED."""
        mangled = "_Z5makeFv"
        old = _snap(functions=[Function(
            name="makeF", mangled=mangled,
            return_type="std::unique_ptr<Foo>",
            visibility=Visibility.PUBLIC,
        )])
        new = _snap(functions=[Function(
            name="makeF", mangled=mangled,
            return_type="std::unique_ptr<Bar>",
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING


class TestTemplateAnalysisNegative:
    """Negative tests: template analysis must not produce false positives."""

    def test_function_not_in_both_snapshots_no_crash(self) -> None:
        """New function with template param → FUNC_ADDED, not template change."""
        mangled = "_Z7processNSt6vectorIiEE"
        old = _snap(functions=[])
        new = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="v", type="std::vector<int>")],
            visibility=Visibility.PUBLIC,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_hidden_function_not_checked(self) -> None:
        """Hidden functions are not part of public ABI — no template change."""
        mangled = "_Z7processNSt6vectorIiEE"
        old = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="v", type="std::vector<int>")],
            visibility=Visibility.HIDDEN,
        )])
        new = _snap(functions=[Function(
            name="process", mangled=mangled, return_type="void",
            params=[Param(name="v", type="std::vector<double>")],
            visibility=Visibility.HIDDEN,
        )])
        result = compare(old, new)
        assert ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED not in _kinds(result)


# =============================================================================
# Part 3 — Schema baseline: schema_version in snapshot serialization
# =============================================================================


class TestSchemaVersionBaseline:
    """schema_version field in snapshot output."""

    def test_schema_version_constant_exists(self) -> None:
        """SCHEMA_VERSION must be an int >= 2 (baseline added in PR #89)."""
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION >= 2

    def test_snapshot_to_dict_includes_schema_version(self) -> None:
        """snapshot_to_dict must include schema_version at the top level."""
        snap = _snap()
        d = snapshot_to_dict(snap)
        assert "schema_version" in d
        assert d["schema_version"] == SCHEMA_VERSION

    def test_schema_version_is_int(self) -> None:
        """schema_version in the serialized dict must be an int."""
        snap = _snap()
        d = snapshot_to_dict(snap)
        assert isinstance(d["schema_version"], int)

    def test_schema_version_survives_json_roundtrip(self) -> None:
        """schema_version must survive JSON serialization and deserialization."""
        snap = _snap(
            functions=[Function(
                name="init",
                mangled="_Z4initv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )]
        )
        d = snapshot_to_dict(snap)
        raw_json = json.dumps(d)
        d2 = json.loads(raw_json)
        assert d2["schema_version"] == SCHEMA_VERSION

    def test_snapshot_from_dict_without_schema_version_treated_as_v1(self) -> None:
        """Loading a snapshot dict without schema_version must work (treats as v1)."""
        snap = _snap()
        d = snapshot_to_dict(snap)
        # Simulate old snapshot without schema_version
        del d["schema_version"]
        # Must not raise; backward-compat load
        snap2 = snapshot_from_dict(d)
        assert snap2.library == snap.library
        assert snap2.version == snap.version

    def test_snapshot_from_dict_with_schema_version_1(self) -> None:
        """Explicitly loading schema_version=1 snapshot must work."""
        snap = _snap()
        d = snapshot_to_dict(snap)
        d["schema_version"] = 1
        snap2 = snapshot_from_dict(d)
        assert snap2.library == snap.library

    def test_snapshot_from_dict_with_current_schema_version(self) -> None:
        """Loading a snapshot with the current schema_version must work."""
        snap = _snap()
        d = snapshot_to_dict(snap)
        assert d["schema_version"] == SCHEMA_VERSION
        snap2 = snapshot_from_dict(d)
        assert snap2.library == snap.library

    def test_save_and_load_preserves_schema_version(self) -> None:
        """save_snapshot + load_snapshot: schema_version appears in JSON file."""
        snap = _snap(
            functions=[Function(
                name="compute",
                mangled="_Z7computev",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )]
        )
        with tempfile.NamedTemporaryFile(suffix=".abi.json", delete=False) as f:
            tmp = Path(f.name)
        try:
            save_snapshot(snap, tmp)
            raw = json.loads(tmp.read_text())
            assert raw["schema_version"] == SCHEMA_VERSION
            snap2 = load_snapshot(tmp)
            assert snap2.functions[0].name == "compute"
        finally:
            tmp.unlink(missing_ok=True)

    def test_schema_version_not_present_in_abi_snapshot_object(self) -> None:
        """schema_version is a serialization concern only; AbiSnapshot has no such field."""
        snap = _snap()
        assert not hasattr(snap, "schema_version")

    def test_schema_version_survives_roundtrip_with_elf(self) -> None:
        """Schema version survives snapshot_to_dict → snapshot_from_dict with ELF data."""
        elf = _elf_with_syms("_Z4initv")
        snap = _snap(
            functions=[Function(
                name="init", mangled="_Z4initv",
                return_type="void", visibility=Visibility.PUBLIC,
            )],
            elf=elf,
        )
        d = snapshot_to_dict(snap)
        assert d["schema_version"] == SCHEMA_VERSION
        snap2 = snapshot_from_dict(d)
        assert snap2.elf is not None
        assert len(snap2.elf.symbols) == 1


    def test_future_schema_version_hard_rejects(self) -> None:
        """Loading a snapshot with schema_version >=
        _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION must raise
        IncompatibleSnapshotSchemaError (ADR-050 D1), not merely warn — a
        verdict-blocking field (AbiSnapshot.contract) was introduced at that
        threshold, and warn-and-continue would let this reader silently
        compare two possibly-incomparable snapshots."""
        from abicheck.errors import IncompatibleSnapshotSchemaError

        snap = _snap()
        d = snapshot_to_dict(snap)
        d["schema_version"] = 999
        try:
            snapshot_from_dict(d)
            raise AssertionError("expected IncompatibleSnapshotSchemaError")
        except IncompatibleSnapshotSchemaError as exc:
            assert "999" in str(exc)


    def test_not_in_api_break_kinds(self) -> None:
        """FUNC_DELETED_ELF_FALLBACK must NOT be in API_BREAK_KINDS (it is a binary break)."""
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in API_BREAK_KINDS, (
            "FUNC_DELETED_ELF_FALLBACK must produce Verdict.BREAKING — symbol absent "
            "from dynsym is a binary ABI break"
        )

    def test_elf_fallback_hidden_symbol_not_double_reported(self) -> None:
        """FUNC_DELETED_ELF_FALLBACK must not fire for symbols that moved to HIDDEN visibility."""
        from abicheck.checker import ChangeKind, compare

        old_fn = _func("foo", "_Z3foov", visibility=Visibility.PUBLIC)
        new_fn = _func("foo", "_Z3foov", visibility=Visibility.HIDDEN)
        old_snap = _snap(functions=[old_fn], elf=_elf_with_syms("_Z3foov"))
        new_snap = _snap(version="2.0", functions=[new_fn], elf=_elf_with_syms())

        result = compare(old_snap, new_snap)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds, (
            "FUNC_DELETED_ELF_FALLBACK should not fire when visibility moved to HIDDEN"
        )
