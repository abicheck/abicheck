"""tests/test_changekind_completeness.py — Ensure every ChangeKind has unit test coverage.

This file:
1. A meta-test that asserts every ChangeKind member is asserted in at least one test.
2. Unit tests for ChangeKinds that previously lacked assertion coverage:
   - SYMBOL_BINDING_STRENGTHENED  (WEAK→GLOBAL: compatible)
   - VAR_ACCESS_WIDENED           (private→public: compatible)
   - TYPE_VTABLE_CHANGED          (vtable layout change: breaking)
3. TypedefToFunction gap test      (function-pointer typedef signature change)
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, DiffResult, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    RecordType,
    Variable,
    Visibility,
)

# ── Meta-test: every ChangeKind member must appear in at least one test ──────

# Manually maintained set of ChangeKind members that are asserted somewhere in
# the test suite.  When a new member is added to the enum, this set (and a
# corresponding scenario) must be updated – the meta-test below will fail
# until that happens.
ASSERTED_CHANGE_KINDS: set[ChangeKind] = {
    ChangeKind.ANON_FIELD_CHANGED,
    ChangeKind.BASE_CLASS_POSITION_CHANGED,
    ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,
    ChangeKind.CALLING_CONVENTION_CHANGED,
    ChangeKind.COMMON_SYMBOL_RISK,
    ChangeKind.CONSTANT_ADDED,
    ChangeKind.CONSTANT_CHANGED,
    ChangeKind.CONSTANT_REMOVED,
    ChangeKind.DWARF_INFO_MISSING,
    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
    ChangeKind.ENUM_MEMBER_ADDED,
    ChangeKind.ENUM_MEMBER_REMOVED,
    ChangeKind.ENUM_MEMBER_RENAMED,
    ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
    ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
    ChangeKind.FIELD_ACCESS_CHANGED,
    ChangeKind.FIELD_BECAME_CONST,
    ChangeKind.FIELD_BECAME_MUTABLE,
    ChangeKind.FIELD_BECAME_VOLATILE,
    ChangeKind.FIELD_BITFIELD_CHANGED,
    ChangeKind.FIELD_LOST_CONST,
    ChangeKind.FIELD_LOST_MUTABLE,
    ChangeKind.FIELD_LOST_VOLATILE,
    ChangeKind.FIELD_RENAMED,
    ChangeKind.FUNC_ADDED,
    ChangeKind.FUNC_CV_CHANGED,
    ChangeKind.FUNC_DELETED,
    ChangeKind.FUNC_NOEXCEPT_ADDED,
    ChangeKind.FUNC_NOEXCEPT_REMOVED,
    ChangeKind.FUNC_PARAMS_CHANGED,
    ChangeKind.FUNC_PURE_VIRTUAL_ADDED,
    ChangeKind.FUNC_REMOVED,
    ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ChangeKind.FUNC_RETURN_CHANGED,
    ChangeKind.FUNC_STATIC_CHANGED,
    ChangeKind.FUNC_VIRTUAL_ADDED,
    ChangeKind.FUNC_VIRTUAL_BECAME_PURE,
    ChangeKind.FUNC_VIRTUAL_REMOVED,
    ChangeKind.FUNC_VISIBILITY_CHANGED,
    ChangeKind.IFUNC_INTRODUCED,
    ChangeKind.IFUNC_REMOVED,
    ChangeKind.METHOD_ACCESS_CHANGED,
    ChangeKind.NEEDED_ADDED,
    ChangeKind.NEEDED_REMOVED,
    ChangeKind.PARAM_BECAME_VA_LIST,
    ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
    ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
    ChangeKind.PARAM_LOST_VA_LIST,
    ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
    ChangeKind.PARAM_RENAMED,
    ChangeKind.PARAM_RESTRICT_CHANGED,
    ChangeKind.REMOVED_CONST_OVERLOAD,
    ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
    ChangeKind.RPATH_CHANGED,
    ChangeKind.RUNPATH_CHANGED,
    ChangeKind.SONAME_CHANGED,
    ChangeKind.SONAME_MISSING,
    ChangeKind.COMPAT_VERSION_CHANGED,
    ChangeKind.VISIBILITY_LEAK,
    ChangeKind.SOURCE_LEVEL_KIND_CHANGED,
    ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    ChangeKind.STRUCT_FIELD_REMOVED,
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    ChangeKind.STRUCT_PACKING_CHANGED,
    ChangeKind.STRUCT_SIZE_CHANGED,
    ChangeKind.SYMBOL_BINDING_CHANGED,
    ChangeKind.SYMBOL_BINDING_STRENGTHENED,
    ChangeKind.SYMBOL_SIZE_CHANGED,
    ChangeKind.SYMBOL_TYPE_CHANGED,
    ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
    ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
    ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
    ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT,
    ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
    ChangeKind.TOOLCHAIN_FLAG_DRIFT,
    ChangeKind.FRAME_REGISTER_CHANGED,
    ChangeKind.VECTOR_ABI_CHANGED,
    ChangeKind.TYPEDEF_BASE_CHANGED,
    ChangeKind.TYPEDEF_REMOVED,
    ChangeKind.TYPE_ADDED,
    ChangeKind.TYPE_ALIGNMENT_CHANGED,
    ChangeKind.TYPE_BASE_CHANGED,
    ChangeKind.TYPE_BECAME_OPAQUE,
    ChangeKind.TYPE_FIELD_ADDED,
    ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
    ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
    ChangeKind.TYPE_FIELD_REMOVED,
    ChangeKind.TYPE_FIELD_TYPE_CHANGED,
    ChangeKind.TYPE_KIND_CHANGED,
    ChangeKind.TYPE_REMOVED,
    ChangeKind.TYPE_SIZE_CHANGED,
    ChangeKind.TYPE_VISIBILITY_CHANGED,
    ChangeKind.TYPE_VTABLE_CHANGED,
    ChangeKind.UNION_FIELD_ADDED,
    ChangeKind.UNION_FIELD_REMOVED,
    ChangeKind.UNION_FIELD_TYPE_CHANGED,
    ChangeKind.USED_RESERVED_FIELD,
    ChangeKind.VAR_ACCESS_CHANGED,
    ChangeKind.VAR_ACCESS_WIDENED,
    ChangeKind.VAR_ADDED,
    ChangeKind.VAR_BECAME_CONST,
    ChangeKind.VAR_LOST_CONST,
    ChangeKind.VAR_REMOVED,
    ChangeKind.VAR_TYPE_CHANGED,
    ChangeKind.VAR_VALUE_CHANGED,
    ChangeKind.VALUE_ABI_TRAIT_CHANGED,
    # Inline attribute changes (ABICC issue #125)
    ChangeKind.FUNC_BECAME_INLINE,
    ChangeKind.FUNC_LOST_INLINE,
    # PR #89: ELF fallback for = delete and template inner-type analysis
    ChangeKind.FUNC_DELETED_ELF_FALLBACK,
    ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
    ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
    # Symbol origin detection
    ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
    # Version-stamped typedef sentinels (libpng-style compile-time version checks)
    ChangeKind.TYPEDEF_VERSION_SENTINEL,
    # case51: ELF visibility default↔protected
    ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED,
    # WS-4a: ELF st_other visibility transitions (DEFAULT↔PROTECTED)
    ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
    # ELF visibility, executable stack, symbol rename batch
    ChangeKind.EXECUTABLE_STACK,
    ChangeKind.SYMBOL_RENAMED_BATCH,
    ChangeKind.FUNC_LIKELY_RENAMED,
    # Gap analysis: new checks
    ChangeKind.FUNC_REF_QUAL_CHANGED,
    ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED,
    ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
    ChangeKind.TLS_VAR_SIZE_CHANGED,
    ChangeKind.PROTECTED_VISIBILITY_CHANGED,
    ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED,
    ChangeKind.INLINE_NAMESPACE_MOVED,
    ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED,
    ChangeKind.ABI_SURFACE_EXPLOSION,
    # SYCL Plugin Interface (ADR-020)
    ChangeKind.SYCL_IMPLEMENTATION_CHANGED,
    ChangeKind.SYCL_PI_VERSION_CHANGED,
    ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED,
    ChangeKind.SYCL_PI_ENTRYPOINT_ADDED,
    ChangeKind.SYCL_PLUGIN_REMOVED,
    ChangeKind.SYCL_PLUGIN_ADDED,
    ChangeKind.SYCL_PLUGIN_SEARCH_PATH_CHANGED,
    ChangeKind.SYCL_RUNTIME_VERSION_CHANGED,
    ChangeKind.SYCL_BACKEND_DRIVER_REQ_CHANGED,
    # ELF symbol-version policy checks
    ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
    ChangeKind.SYMBOL_MOVED_VERSION_NODE,
    ChangeKind.SONAME_BUMP_RECOMMENDED,
    ChangeKind.SONAME_BUMP_UNNECESSARY,
    ChangeKind.VERSION_SCRIPT_MISSING,
    # Flexible array member detection (libabigail parity)
    ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
    # DWARF-based = delete detection (P3 gap)
    ChangeKind.FUNC_DELETED_DWARF,
    # Internal-namespace leak via public API (oneDAL-style detail:: pattern)
    # Exercised in tests/test_internal_leak.py.
    ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
    # oneDAL-shaped detectors (case77–case89) — exercised in tests/test_onedal_detectors.py
    ChangeKind.INSTANTIATION_MISSING_FROM_BINARY,
    ChangeKind.SERIALIZATION_TAG_CHANGED,
    ChangeKind.SYCL_OVERLOAD_SET_REMOVED,
    ChangeKind.CPU_DISPATCH_ISA_DROPPED,
    ChangeKind.BUNDLE_SONAME_SKEW,
    ChangeKind.TAG_TYPE_RENAMED,
    ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED,
    ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER,
    # Bundle / multi-library findings (ADR-023).
    # Exercised in tests/test_bundle.py.
    ChangeKind.BUNDLE_INTRA_DEP_REMOVED,
    ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED,
    ChangeKind.BUNDLE_INTRA_TYPE_CHANGED,
    ChangeKind.BUNDLE_PROVIDER_CHANGED,
    ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED,
    ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_ADDED,
    ChangeKind.BUNDLE_LIBRARY_REMOVED,
    ChangeKind.BUNDLE_LIBRARY_ADDED,
    ChangeKind.BUNDLE_INTRA_DEP_VERSION_DRIFT,
    # Explicit specifier transitions — exercised in tests/test_explicit_ctor.py
    ChangeKind.CTOR_EXPLICIT_ADDED,
    ChangeKind.CTOR_EXPLICIT_REMOVED,
    # Namespace-shape patterns — exercised in tests/test_diff_namespaces.py
    ChangeKind.EXPERIMENTAL_GRADUATED,
    ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT,
    ChangeKind.STD_REEXPORT_REMOVED,
    ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED,
    # Template / overload-set patterns — exercised in tests/test_diff_templates.py
    ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API,
    ChangeKind.CPO_KIND_CHANGED,
    ChangeKind.OVERLOAD_SET_REROUTED,
    ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED,
    ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
    # Build-config / probe-harness patterns — exercised in tests/test_diff_build_config.py
    ChangeKind.API_DEPENDS_ON_CONSUMER_ENV,
    ChangeKind.CXX_STANDARD_FLOOR_RAISED,
    ChangeKind.BEHAVIOURAL_DEFAULT_CHANGED,
    # Hidden friend transitions — exercised in tests/test_hidden_friends.py
    ChangeKind.HIDDEN_FRIEND_ADDED,
    ChangeKind.HIDDEN_FRIEND_REMOVED,
    # Modern-C++/oneAPI ABI hazards — exercised in tests/test_new_detectors.py
    ChangeKind.INTEGER_MODEL_CHANGED,
    ChangeKind.ABI_TAG_CHANGED,
    ChangeKind.CHAR8T_MIGRATION,
    ChangeKind.BIT_INT_WIDTH_CHANGED,
    ChangeKind.ATOMIC_QUALIFIER_CHANGED,
}


def test_all_change_kinds_covered() -> None:
    """Every ChangeKind enum member must appear in ASSERTED_CHANGE_KINDS.

    If this test fails, a new ChangeKind was added without a corresponding
    test scenario.  Add the member to ASSERTED_CHANGE_KINDS *and* write a
    test that exercises it.
    """
    all_kinds = set(ChangeKind)
    missing = all_kinds - ASSERTED_CHANGE_KINDS
    assert not missing, (
        f"ChangeKind members lack test coverage — add tests and update "
        f"ASSERTED_CHANGE_KINDS: {sorted(m.name for m in missing)}"
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


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


def _elf(**kwargs: object) -> ElfMetadata:
    defaults: dict[str, object] = dict(soname="libfoo.so.1")
    defaults.update(kwargs)
    return ElfMetadata(**defaults)  # type: ignore[arg-type]


def _elf_snap(elf: ElfMetadata, **kwargs: object) -> AbiSnapshot:
    s = _snap(**kwargs)
    s.elf = elf
    return s


def _kinds(result: DiffResult) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ===========================================================================
# 1. SYMBOL_BINDING_STRENGTHENED — WEAK→GLOBAL transition (compatible)
#
# ABICC does not have this detector. This is abicheck-only.
# WEAK→GLOBAL means the symbol is more strongly bound; existing consumers
# that linked against the WEAK version will still resolve fine.
# ===========================================================================


class TestSymbolBindingStrengthened:
    def test_weak_to_global_detected(self) -> None:
        """WEAK→GLOBAL binding change should emit SYMBOL_BINDING_STRENGTHENED."""
        old_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="foo", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
                ),
            ]
        )
        new_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                ),
            ]
        )
        result = compare(_elf_snap(old_elf), _elf_snap(new_elf))
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED in _kinds(result)

    def test_weak_to_global_is_compatible(self) -> None:
        """Strengthening should NOT be BREAKING — it's compatible."""
        old_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="bar", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
                ),
            ]
        )
        new_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="bar", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                ),
            ]
        )
        result = compare(_elf_snap(old_elf), _elf_snap(new_elf))
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED in _kinds(result)
        assert result.verdict == Verdict.COMPATIBLE

    def test_global_to_weak_is_not_strengthened(self) -> None:
        """GLOBAL→WEAK should emit SYMBOL_BINDING_CHANGED, not STRENGTHENED."""
        old_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="baz", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                ),
            ]
        )
        new_elf = _elf(
            symbols=[
                ElfSymbol(
                    name="baz", binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
                ),
            ]
        )
        result = compare(_elf_snap(old_elf), _elf_snap(new_elf))
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED not in _kinds(result)
        assert ChangeKind.SYMBOL_BINDING_CHANGED in _kinds(result)


# ===========================================================================
# 2. VAR_ACCESS_WIDENED — private/protected→public (compatible)
#
# ABICC rule: Global_Data_Became_Public (Method_Became_Public analog)
# Widening access is always compatible — more code can use it.
# ===========================================================================


class TestVarAccessWidened:
    def test_private_to_public_detected(self) -> None:
        """private→public variable access change should emit VAR_ACCESS_WIDENED."""
        old = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PRIVATE)]
        )
        new = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PUBLIC)]
        )
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_WIDENED in _kinds(result)

    def test_protected_to_public_detected(self) -> None:
        """protected→public should also emit VAR_ACCESS_WIDENED."""
        old = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PROTECTED)]
        )
        new = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PUBLIC)]
        )
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_WIDENED in _kinds(result)

    def test_widened_is_compatible(self) -> None:
        """Widening access should be COMPATIBLE, not BREAKING."""
        old = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PRIVATE)]
        )
        new = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PUBLIC)]
        )
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_WIDENED in _kinds(result)
        assert result.verdict == Verdict.COMPATIBLE

    def test_narrowing_is_not_widened(self) -> None:
        """public→private should emit VAR_ACCESS_CHANGED, not VAR_ACCESS_WIDENED."""
        old = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PUBLIC)]
        )
        new = _snap(
            variables=[_var("g_val", "_g_val", "int", access=AccessLevel.PRIVATE)]
        )
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_WIDENED not in _kinds(result)
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(result)


# ===========================================================================
# 3. TYPE_VTABLE_CHANGED — vtable layout change (breaking)
#
# ABICC rules: Virtual_Table_Changed_Unknown, Virtual_Method_Position,
#              Overridden_Virtual_Method, etc.
# Any change to vtable entry order/contents breaks existing compiled code.
# ===========================================================================


class TestTypeVtableChanged:
    def test_vtable_reorder_detected(self) -> None:
        """Reordering vtable entries should emit TYPE_VTABLE_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv", "_ZN4Base3barEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3barEv", "_ZN4Base3fooEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)

    def test_vtable_entry_added(self) -> None:
        """Adding a vtable entry should emit TYPE_VTABLE_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv", "_ZN4Base3barEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)

    def test_vtable_entry_removed(self) -> None:
        """Removing a vtable entry should emit TYPE_VTABLE_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv", "_ZN4Base3barEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)

    def test_vtable_change_is_breaking(self) -> None:
        """Vtable layout changes should be BREAKING."""
        old = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN6Widget4drawEv", "_ZN6Widget6resizeEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN6Widget6resizeEv", "_ZN6Widget4drawEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_no_vtable_change_when_identical(self) -> None:
        """Identical vtable should NOT emit TYPE_VTABLE_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    size_bits=64,
                    vtable=["_ZN4Base3fooEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED not in _kinds(result)


# ===========================================================================
# 4. TypedefToFunction — function-pointer typedef signature change
#
# ABICC RegTest: TypedefToFunction (C test)
# A typedef to a function pointer changes its parameter list.
# e.g., typedef int(*handler_t)(int) → typedef int(*handler_t)(int, int)
# The TYPEDEF_BASE_CHANGED detector should catch this.
# ===========================================================================


class TestTypedefToFunction:
    """Cover the last remaining ABICC RegTest gap: TypedefToFunction."""

    def test_funcptr_typedef_param_added(self) -> None:
        """Function-pointer typedef gains a parameter → TYPEDEF_BASE_CHANGED."""
        old = _snap(typedefs={"handler_t": "int(*)(int)"})
        new = _snap(typedefs={"handler_t": "int(*)(int, int)"})
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(result)
        change = next(
            c for c in result.changes if c.kind == ChangeKind.TYPEDEF_BASE_CHANGED
        )
        assert change.old_value == "int(*)(int)"
        assert change.new_value == "int(*)(int, int)"

    def test_funcptr_typedef_return_changed(self) -> None:
        """Function-pointer typedef return type changes → TYPEDEF_BASE_CHANGED."""
        old = _snap(typedefs={"callback_t": "int(*)(void)"})
        new = _snap(typedefs={"callback_t": "void(*)(void)"})
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(result)

    def test_funcptr_typedef_unchanged(self) -> None:
        """Identical function-pointer typedef → no change."""
        old = _snap(typedefs={"handler_t": "int(*)(int)"})
        new = _snap(typedefs={"handler_t": "int(*)(int)"})
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED not in _kinds(result)

    def test_funcptr_typedef_removed(self) -> None:
        """Function-pointer typedef removed → TYPEDEF_REMOVED."""
        old = _snap(typedefs={"handler_t": "int(*)(int)"})
        new = _snap(typedefs={})
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_REMOVED in _kinds(result)

    def test_funcptr_typedef_is_breaking(self) -> None:
        """Changing a function-pointer typedef signature should be BREAKING."""
        old = _snap(typedefs={"fn_t": "void(*)(int)"})
        new = _snap(typedefs={"fn_t": "void(*)(int, float)"})
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in _kinds(result)
        assert result.verdict == Verdict.BREAKING


# ===========================================================================
# FUNC_BECAME_INLINE / FUNC_LOST_INLINE (ABICC issue #125)
# ===========================================================================


class TestInlineTransitions:
    def test_func_became_inline_detected(self) -> None:
        """non-inline → inline transition should emit FUNC_BECAME_INLINE."""
        old = _snap(functions=[_func("compute", "_Z7computei", is_inline=False)])
        new = _snap(functions=[_func("compute", "_Z7computei", is_inline=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_BECAME_INLINE in _kinds(result)

    def test_func_became_inline_is_api_break(self) -> None:
        """FUNC_BECAME_INLINE must be classified as API_BREAK (not BREAKING)."""
        old = _snap(functions=[_func("foo", "_Z3foov", is_inline=False)])
        new = _snap(functions=[_func("foo", "_Z3foov", is_inline=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_BECAME_INLINE in _kinds(result)
        assert result.verdict == Verdict.API_BREAK

    def test_func_lost_inline_detected(self) -> None:
        """inline → non-inline transition should emit FUNC_LOST_INLINE."""
        old = _snap(functions=[_func("fast", "_Z4fasti", is_inline=True)])
        new = _snap(functions=[_func("fast", "_Z4fasti", is_inline=False)])
        result = compare(old, new)
        assert ChangeKind.FUNC_LOST_INLINE in _kinds(result)

    def test_func_lost_inline_is_compatible(self) -> None:
        """FUNC_LOST_INLINE must be classified as COMPATIBLE."""
        old = _snap(functions=[_func("bar", "_Z3barv", is_inline=True)])
        new = _snap(functions=[_func("bar", "_Z3barv", is_inline=False)])
        result = compare(old, new)
        assert ChangeKind.FUNC_LOST_INLINE in _kinds(result)
        assert result.verdict == Verdict.COMPATIBLE

    def test_inline_unchanged_no_report(self) -> None:
        """No change in inline attribute → no inline-transition event."""
        old = _snap(functions=[_func("baz", "_Z3bazv", is_inline=True)])
        new = _snap(functions=[_func("baz", "_Z3bazv", is_inline=True)])
        result = compare(old, new)
        assert ChangeKind.FUNC_BECAME_INLINE not in _kinds(result)
        assert ChangeKind.FUNC_LOST_INLINE not in _kinds(result)

    def test_func_became_inline_only_change(self) -> None:
        """FUNC_BECAME_INLINE transition must be the ONLY change emitted (isolation)."""
        old = _snap(functions=[_func("compute", "_Z7computei", is_inline=False)])
        new = _snap(functions=[_func("compute", "_Z7computei", is_inline=True)])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_BECAME_INLINE in kinds
        # No other unexpected changes — only FUNC_BECAME_INLINE
        unexpected = kinds - {ChangeKind.FUNC_BECAME_INLINE}
        assert not unexpected, f"Unexpected extra changes: {unexpected}"

    def test_func_lost_inline_only_change(self) -> None:
        """FUNC_LOST_INLINE transition must be the ONLY change emitted (isolation)."""
        old = _snap(functions=[_func("fast_path", "_Z9fast_pathv", is_inline=True)])
        new = _snap(functions=[_func("fast_path", "_Z9fast_pathv", is_inline=False)])
        result = compare(old, new)
        kinds = _kinds(result)
        assert ChangeKind.FUNC_LOST_INLINE in kinds
        # No other unexpected changes — only FUNC_LOST_INLINE
        unexpected = kinds - {ChangeKind.FUNC_LOST_INLINE}
        assert not unexpected, f"Unexpected extra changes: {unexpected}"


# ===========================================================================
# COMPAT_VERSION_CHANGED — Mach-O LC_ID_DYLIB compatibility version change
# ===========================================================================


class TestCompatVersionChanged:
    def test_compat_version_changed_detected(self) -> None:
        """compat_version change in Mach-O metadata emits COMPAT_VERSION_CHANGED."""
        old = _snap()
        old.macho = MachoMetadata(compat_version="1.0.0")
        new = _snap()
        new.macho = MachoMetadata(compat_version="2.0.0")
        result = compare(old, new)
        assert ChangeKind.COMPAT_VERSION_CHANGED in _kinds(result)

    def test_compat_version_unchanged_no_report(self) -> None:
        """Identical compat_version → no COMPAT_VERSION_CHANGED."""
        old = _snap()
        old.macho = MachoMetadata(compat_version="1.0.0")
        new = _snap()
        new.macho = MachoMetadata(compat_version="1.0.0")
        result = compare(old, new)
        assert ChangeKind.COMPAT_VERSION_CHANGED not in _kinds(result)

    def test_compat_version_gained_reported(self) -> None:
        """old=None, new=set → reported (library gains a compat contract)."""
        old = _snap()
        old.macho = MachoMetadata(compat_version="")
        new = _snap()
        new.macho = MachoMetadata(compat_version="1.0.0")
        result = compare(old, new)
        assert ChangeKind.COMPAT_VERSION_CHANGED in _kinds(result)
