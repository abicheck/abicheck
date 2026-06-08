"""Tests for the 9 new ABI change detectors added in the gap analysis.

Covers:
- tls_checks (TLS_VAR_SIZE_CHANGED)
- protected_visibility (PROTECTED_VISIBILITY_CHANGED)
- symbol_version_alias (SYMBOL_VERSION_ALIAS_CHANGED)
- glibcxx_dual_abi (GLIBCXX_DUAL_ABI_FLIP_DETECTED)
- inline_namespace (INLINE_NAMESPACE_MOVED)
- vtable_identity (VTABLE_SYMBOL_IDENTITY_CHANGED)
- abi_surface (ABI_SURFACE_EXPLOSION)
- func_ref_qual_changed (inline in _check_function_signature)
- func_language_linkage_changed (inline in _check_function_signature)
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, elf=None, from_headers=False):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf, from_headers=from_headers,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _elf_sym(name, sym_type=SymbolType.FUNC, size=0, visibility="default",
             binding=SymbolBinding.GLOBAL, version="", is_default=True):
    return ElfSymbol(name=name, sym_type=sym_type, size=size,
                     visibility=visibility, binding=binding,
                     version=version, is_default=is_default)


def _has_kind(result, kind):
    return any(c.kind == kind for c in result.changes)


def _changes_of_kind(result, kind):
    return [c for c in result.changes if c.kind == kind]


# ── TLS_VAR_SIZE_CHANGED ─────────────────────────────────────────────────────

class TestTlsChecks:
    def test_tls_size_change_detected(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=4),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=8),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.TLS_VAR_SIZE_CHANGED)

    def test_tls_same_size_no_change(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=4),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=4),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.TLS_VAR_SIZE_CHANGED)

    def test_tls_zero_size_ignored(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=0),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("tls_var", sym_type=SymbolType.TLS, size=8),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.TLS_VAR_SIZE_CHANGED)

    def test_non_tls_not_reported(self):
        """Non-TLS symbols should not trigger TLS_VAR_SIZE_CHANGED."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("data_var", sym_type=SymbolType.OBJECT, size=4),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("data_var", sym_type=SymbolType.OBJECT, size=8),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.TLS_VAR_SIZE_CHANGED)


# ── PROTECTED_VISIBILITY_CHANGED ──────────────────────────────────────────────

class TestProtectedVisibility:
    def test_data_default_to_protected(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_data_protected_to_default(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="protected"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="default"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_func_not_reported(self):
        """Function DEFAULT↔PROTECTED is handled by FUNC_VISIBILITY_PROTECTED_CHANGED."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("func_sym", sym_type=SymbolType.FUNC, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("func_sym", sym_type=SymbolType.FUNC, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_tls_not_reported(self):
        """TLS symbols don't use copy relocations — DEFAULT↔PROTECTED is benign."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("tls_sym", sym_type=SymbolType.TLS, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("tls_sym", sym_type=SymbolType.TLS, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_ifunc_not_reported(self):
        """IFUNC symbols should not trigger PROTECTED_VISIBILITY_CHANGED."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("ifunc_sym", sym_type=SymbolType.IFUNC, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("ifunc_sym", sym_type=SymbolType.IFUNC, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_common_default_to_protected(self):
        """COMMON data symbols should also trigger PROTECTED_VISIBILITY_CHANGED."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("common_sym", sym_type=SymbolType.COMMON, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("common_sym", sym_type=SymbolType.COMMON, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_common_protected_to_default(self):
        """COMMON data symbols: protected→default should also trigger."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("common_sym", sym_type=SymbolType.COMMON, visibility="protected"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("common_sym", sym_type=SymbolType.COMMON, visibility="default"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)

    def test_same_visibility_no_change(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("global_data", sym_type=SymbolType.OBJECT, visibility="default"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.PROTECTED_VISIBILITY_CHANGED)


# ── SYMBOL_VERSION_ALIAS_CHANGED ──────────────────────────────────────────────

class TestSymbolVersionAlias:
    def test_default_version_changed(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_1.0", is_default=True),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_2.0", is_default=True),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED)

    def test_same_version_no_change(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_1.0", is_default=True),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_1.0", is_default=True),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED)

    def test_no_versioned_symbols_no_change(self):
        old_elf = ElfMetadata(symbols=[_elf_sym("foo")])
        new_elf = ElfMetadata(symbols=[_elf_sym("foo")])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED)

    def test_is_default_flip_same_version_no_change(self):
        """Same version string but is_default flips → not a version alias change.

        The detector tracks default *version string* changes, not default flag flips.
        When is_default goes True→False the symbol loses its default designation
        but the version string hasn't changed to a different value.
        """
        old_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_1.0", is_default=True),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="VER_1.0", is_default=False),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED)

    def test_unversioned_is_default_flip_no_change(self):
        """Non-versioned symbols flipping is_default → no change."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="", is_default=False),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("foo", version="", is_default=True),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED)


# ── GLIBCXX_DUAL_ABI_FLIP_DETECTED ───────────────────────────────────────────

class TestGlibcxxDualAbi:
    def _make_cxx11_funcs(self, prefix, count, vis=Visibility.PUBLIC):
        """Generate functions with __cxx11 in mangled name."""
        funcs = []
        for i in range(count):
            funcs.append(Function(
                name=f"{prefix}::std::__cxx11::basic_string::func{i}",
                mangled=f"_ZN{prefix}std__cxx11_func{i}Ev",
                return_type="void",
                visibility=vis,
            ))
        return funcs

    def _make_legacy_funcs(self, prefix, count, vis=Visibility.PUBLIC):
        """Generate functions without __cxx11 marker (legacy ABI)."""
        funcs = []
        for i in range(count):
            funcs.append(Function(
                name=f"{prefix}::std::basic_string::func{i}",
                mangled=f"_ZN{prefix}std_func{i}Ev",
                return_type="void",
                visibility=vis,
            ))
        return funcs

    def test_cxx11_to_legacy_detected(self):
        old_funcs = self._make_cxx11_funcs("lib", 10)
        new_funcs = self._make_legacy_funcs("lib", 10)
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)

    def test_legacy_to_cxx11_detected(self):
        old_funcs = self._make_legacy_funcs("lib", 10)
        new_funcs = self._make_cxx11_funcs("lib", 10)
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)

    def test_small_churn_not_detected(self):
        """Below threshold (< 5 removed + < 5 added) → no detection."""
        old_funcs = self._make_cxx11_funcs("lib", 3)
        new_funcs = self._make_legacy_funcs("lib", 3)
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert not _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)

    def test_no_markers_not_detected(self):
        """Churn without CXX11 markers → no detection."""
        old_funcs = [_pub_func(f"func{i}", f"_Zfunc{i}v") for i in range(10)]
        new_funcs = [_pub_func(f"other{i}", f"_Zother{i}v") for i in range(10)]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert not _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)

    def test_exact_threshold_cxx11_to_legacy(self):
        """Exactly 5 removed + 5 added (threshold boundary) → detected."""
        old_funcs = self._make_cxx11_funcs("lib", 5)
        new_funcs = self._make_legacy_funcs("lib", 5)
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)

    def test_exact_threshold_legacy_to_cxx11(self):
        """Exactly 5 removed + 5 added (threshold boundary), reverse direction."""
        old_funcs = self._make_legacy_funcs("lib", 5)
        new_funcs = self._make_cxx11_funcs("lib", 5)
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)


# ── INLINE_NAMESPACE_MOVED ────────────────────────────────────────────────────

class TestInlineNamespace:
    def test_v1_to_v2_move_detected(self):
        old_funcs = [
            _pub_func(f"ns::v1::func{i}", f"_ZN2ns2v1func{i}Ev")
            for i in range(5)
        ]
        new_funcs = [
            _pub_func(f"ns::v2::func{i}", f"_ZN2ns2v2func{i}Ev")
            for i in range(5)
        ]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)

    def test_single_symbol_not_detected(self):
        """Need >= 2 matched symbols for detection."""
        old_funcs = [_pub_func("ns::v1::func0", "_ZN2ns2v1func0Ev")]
        new_funcs = [_pub_func("ns::v2::func0", "_ZN2ns2v2func0Ev")]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert not _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)

    def test_no_namespace_version_no_detection(self):
        """Functions without versioned namespaces should not trigger."""
        old_funcs = [_pub_func(f"ns::func{i}", f"_ZN2nsfunc{i}Ev") for i in range(5)]
        new_funcs = [_pub_func(f"other::func{i}", f"_ZN5otherfunc{i}Ev") for i in range(5)]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert not _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)

    def test_regex_does_not_match_v_in_identifier(self):
        """v1 inside an identifier (not a namespace) should NOT match."""
        old_funcs = [
            _pub_func(f"convert_v1_data{i}", f"_Zconvert_v1_data{i}v")
            for i in range(5)
        ]
        new_funcs = [
            _pub_func(f"convert_v2_data{i}", f"_Zconvert_v2_data{i}v")
            for i in range(5)
        ]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert not _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)

    def test_libcxx_1_to_2_move_detected(self):
        """libc++ inline namespace ::__1:: → ::__2:: should be detected."""
        old_funcs = [
            _pub_func(f"std::__1::func{i}", f"_ZNSt3__1func{i}Ev")
            for i in range(5)
        ]
        new_funcs = [
            _pub_func(f"std::__2::func{i}", f"_ZNSt3__2func{i}Ev")
            for i in range(5)
        ]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)

    def test_unversioned_to_versioned_move_detected(self):
        """Unversioned → versioned namespace move should be detected."""
        old_funcs = [
            _pub_func(f"ns::func{i}", f"_ZN2nsfunc{i}Ev_old")
            for i in range(5)
        ]
        new_funcs = [
            _pub_func(f"ns::v2::func{i}", f"_ZN2ns2v2func{i}Ev_new")
            for i in range(5)
        ]
        r = compare(_snap(functions=old_funcs), _snap(functions=new_funcs))
        assert _has_kind(r, ChangeKind.INLINE_NAMESPACE_MOVED)


# ── VTABLE_SYMBOL_IDENTITY_CHANGED ────────────────────────────────────────────

class TestVtableIdentity:
    def test_cross_prefix_not_identity_change(self):
        """_ZTV→_ZTS for same type is NOT an identity change (different RTTI artefacts)."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, size=24),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTS5MyObj", sym_type=SymbolType.OBJECT, size=16),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        # Different prefixes → not same RTTI artefact
        assert not _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)

    def test_same_prefix_identity_change(self):
        """Same RTTI prefix removed and re-added with different properties → identity change.

        Simulates version-script change: _ZTV5MyObj present in both old and new
        but with different versions (handled via common_rtti path).
        """
        old_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, version="VER_1", is_default=True),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, version="VER_2", is_default=True),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)

    def test_rtti_visibility_change(self):
        """RTTI symbol visibility change for existing symbols."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, visibility="default"),
            _elf_sym("_ZTI5MyObj", sym_type=SymbolType.OBJECT, visibility="default"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, visibility="protected"),
            _elf_sym("_ZTI5MyObj", sym_type=SymbolType.OBJECT, visibility="protected"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)

    def test_no_rtti_no_change(self):
        old_elf = ElfMetadata(symbols=[
            _elf_sym("regular_func", sym_type=SymbolType.FUNC),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("regular_func", sym_type=SymbolType.FUNC),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)

    def test_same_rtti_no_change(self):
        """Identical RTTI symbols should not trigger."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, visibility="default",
                     version="VER_1", is_default=True),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTV5MyObj", sym_type=SymbolType.OBJECT, visibility="default",
                     version="VER_1", is_default=True),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)

    def test_transitive_stdlib_rtti_is_filtered(self):
        """Weak stdlib RTTI churn from dependencies is not this DSO's ABI."""
        old_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTVSt9exception", sym_type=SymbolType.OBJECT, version="VER_1"),
        ])
        new_elf = ElfMetadata(symbols=[
            _elf_sym("_ZTVSt9exception", sym_type=SymbolType.OBJECT, version="VER_2"),
        ])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert not _has_kind(r, ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED)


# ── ABI_SURFACE_EXPLOSION ────────────────────────────────────────────────────

class TestAbiSurface:
    def _make_elf(self, count):
        return ElfMetadata(symbols=[
            _elf_sym(f"sym_{i}", sym_type=SymbolType.FUNC) for i in range(count)
        ])

    def test_surface_doubled(self):
        """2x+ growth with 50+ delta → detected."""
        r = compare(_snap(elf=self._make_elf(100)), _snap(elf=self._make_elf(250)))
        assert _has_kind(r, ChangeKind.ABI_SURFACE_EXPLOSION)

    def test_surface_halved(self):
        """<0.5x shrinkage with 50+ delta → detected."""
        r = compare(_snap(elf=self._make_elf(200)), _snap(elf=self._make_elf(50)))
        assert _has_kind(r, ChangeKind.ABI_SURFACE_EXPLOSION)

    def test_small_growth_not_detected(self):
        """Growth below 2x → not detected."""
        r = compare(_snap(elf=self._make_elf(100)), _snap(elf=self._make_elf(180)))
        assert not _has_kind(r, ChangeKind.ABI_SURFACE_EXPLOSION)

    def test_small_base_not_detected(self):
        """Base < 10 symbols → not detected."""
        r = compare(_snap(elf=self._make_elf(5)), _snap(elf=self._make_elf(100)))
        assert not _has_kind(r, ChangeKind.ABI_SURFACE_EXPLOSION)

    def test_boundary_delta_below_50(self):
        """Even with >2x ratio, delta < 50 → not detected."""
        r = compare(_snap(elf=self._make_elf(20)), _snap(elf=self._make_elf(60)))
        assert not _has_kind(r, ChangeKind.ABI_SURFACE_EXPLOSION)


# ── FUNC_REF_QUAL_CHANGED ────────────────────────────────────────────────────

class TestFuncRefQualChanged:
    def test_ref_qualifier_added(self):
        f_old = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="")
        f_new = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_REF_QUAL_CHANGED)

    def test_ref_qualifier_changed(self):
        f_old = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&")
        f_new = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&&")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_REF_QUAL_CHANGED)

    def test_ref_qualifier_removed(self):
        f_old = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&&")
        f_new = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_REF_QUAL_CHANGED)

    def test_same_ref_qualifier_no_change(self):
        f_old = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&")
        f_new = _pub_func("Foo::bar", "_ZN3Foo3barEv", ref_qualifier="&")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert not _has_kind(r, ChangeKind.FUNC_REF_QUAL_CHANGED)

    def test_ref_qualifier_different_mangled(self):
        """Ref-qualifier change with different mangled names (real-world case).

        In Itanium ABI, &/&& ref-qualifiers change the mangled name, so the
        functions won't match by mangled name.  The method_qualifiers detector
        should still pair them by (name, params) and report the change.
        """
        f_old = _pub_func("Foo::bar", "_ZNR3Foo3barEv", ref_qualifier="&")
        f_new = _pub_func("Foo::bar", "_ZNO3Foo3barEv", ref_qualifier="&&")
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_REF_QUAL_CHANGED)


# ── TYPE_BECAME_FINAL / TYPE_LOST_FINAL ──────────────────────────────────────

def _rec(name, *, is_final, size_bits=64):
    # class with a field so it is a concrete record (not opaque).
    return RecordType(name=name, kind="class", size_bits=size_bits,
                      is_final=is_final)


class TestTypeFinalityChanged:
    # scope_to_public_surface=False isolates the detector from reachability
    # scoping (a class exposed only via its own methods is scoped out — a
    # separate, pre-existing behaviour; the example fixtures exercise the
    # reachable-from-a-public-function path end to end).
    def test_class_became_final(self):
        r = compare(_snap(types=[_rec("Widget", is_final=False)]),
                    _snap(types=[_rec("Widget", is_final=True)]),
                    scope_to_public_surface=False)
        assert _has_kind(r, ChangeKind.TYPE_BECAME_FINAL)
        assert not _has_kind(r, ChangeKind.TYPE_LOST_FINAL)

    def test_class_lost_final(self):
        r = compare(_snap(types=[_rec("Widget", is_final=True)]),
                    _snap(types=[_rec("Widget", is_final=False)]),
                    scope_to_public_surface=False)
        assert _has_kind(r, ChangeKind.TYPE_LOST_FINAL)
        assert not _has_kind(r, ChangeKind.TYPE_BECAME_FINAL)

    def test_same_finality_no_change(self):
        r = compare(_snap(types=[_rec("Widget", is_final=True)]),
                    _snap(types=[_rec("Widget", is_final=True)]),
                    scope_to_public_surface=False)
        assert not _has_kind(r, ChangeKind.TYPE_BECAME_FINAL)
        assert not _has_kind(r, ChangeKind.TYPE_LOST_FINAL)

    def test_unknown_finality_skipped_tristate(self):
        # None on either side (DWARF/symbols-only mode, or older snapshot) must
        # never produce a finding — avoids false positives on a tier downgrade.
        for old_f, new_f in ((None, True), (False, None), (None, None)):
            r = compare(_snap(types=[_rec("Widget", is_final=old_f)]),
                        _snap(types=[_rec("Widget", is_final=new_f)]),
                        scope_to_public_surface=False)
            assert not _has_kind(r, ChangeKind.TYPE_BECAME_FINAL)
            assert not _has_kind(r, ChangeKind.TYPE_LOST_FINAL)


# ── PARAM_DEFAULT_VALUE_* (default-argument values) ──────────────────────────

def _func_with_default(default):
    return _pub_func("f", "_Z1fii", params=[
        Param(name="x", type="int"),
        Param(name="y", type="int", default=default),
    ])


def _hsnap(**kw):
    # Header-aware snapshot: default-arg values and constants are header-tier
    # signals, so the detectors only run when both sides are from_headers.
    return _snap(from_headers=True, **kw)


class TestParamDefaultValue:
    def test_default_removed_is_api_break(self):
        r = compare(_hsnap(functions=[_func_with_default("1")]),
                    _hsnap(functions=[_func_with_default(None)]))
        assert _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)

    def test_default_value_changed(self):
        r = compare(_hsnap(functions=[_func_with_default("1")]),
                    _hsnap(functions=[_func_with_default("2")]))
        assert _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_CHANGED)

    def test_same_default_no_change(self):
        r = compare(_hsnap(functions=[_func_with_default("1")]),
                    _hsnap(functions=[_func_with_default("1")]))
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_CHANGED)
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)

    def test_adding_default_is_not_a_break(self):
        # Adding a default is source-compatible; no removal/changed finding.
        r = compare(_hsnap(functions=[_func_with_default(None)]),
                    _hsnap(functions=[_func_with_default("1")]))
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_CHANGED)

    def test_skipped_when_one_side_lacks_headers(self):
        # Mixed-tier: baseline has headers, candidate is DWARF/symbols-only.
        # The bare None default on the headerless side must NOT read as removed.
        r = compare(_hsnap(functions=[_func_with_default("1")]),
                    _snap(functions=[_func_with_default(None)], from_headers=False))
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)

    def test_skipped_when_header_provenance_only_inferred(self):
        # A legacy snapshot rehydrated with from_headers=True but
        # from_headers_inferred=True is only a guess — its missing defaults must
        # not read as removed against a confirmed-header baseline.
        old = _hsnap(functions=[_func_with_default("1")])
        new = _hsnap(functions=[_func_with_default(None)])
        new.from_headers_inferred = True
        r = compare(old, new)
        assert not _has_kind(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)


# ── CONSTANT_* (const / constexpr header constant values) ─────────────────────

class TestHeaderConstants:
    def test_constant_value_changed(self):
        # constants survive public-surface scoping (default on) because they are
        # extracted only from provided public headers — see surface._NEVER_FILTER.
        r = compare(_snap_with_constants({"kLimit": "100"}),
                    _snap_with_constants({"kLimit": "200"}))
        assert _has_kind(r, ChangeKind.CONSTANT_CHANGED)

    def test_constant_removed(self):
        r = compare(_snap_with_constants({"kLimit": "100"}),
                    _snap_with_constants({}))
        assert _has_kind(r, ChangeKind.CONSTANT_REMOVED)

    def test_constant_added_is_compatible(self):
        r = compare(_snap_with_constants({}),
                    _snap_with_constants({"kNew": "1"}))
        assert _has_kind(r, ChangeKind.CONSTANT_ADDED)

    def test_same_constant_no_change(self):
        r = compare(_snap_with_constants({"kLimit": "100"}),
                    _snap_with_constants({"kLimit": "100"}))
        assert not _has_kind(r, ChangeKind.CONSTANT_CHANGED)
        assert not _has_kind(r, ChangeKind.CONSTANT_REMOVED)

    def test_skipped_when_one_side_lacks_headers(self):
        # Mixed-tier: a header baseline vs a DWARF/symbols-only candidate (empty
        # constants because unavailable, not removed) must not report removals.
        r = compare(_snap_with_constants({"kLimit": "100"}),
                    _snap_with_constants({}, from_headers=False))
        assert not _has_kind(r, ChangeKind.CONSTANT_REMOVED)


def _snap_with_constants(constants, from_headers=True):
    s = _snap(from_headers=from_headers)
    s.constants = dict(constants)
    return s


# ── FUNC_LANGUAGE_LINKAGE_CHANGED ─────────────────────────────────────────────

class TestFuncLanguageLinkageChanged:
    def test_linkage_changed_same_mangled(self):
        """Same mangled name, extern C flag flipped."""
        f_old = _pub_func("c_func", "c_func", is_extern_c=True)
        f_new = _pub_func("c_func", "c_func", is_extern_c=False)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED)

    def test_extern_c_to_cpp_different_mangled(self):
        """extern "C" → C++ flip: mangled name changes (c_func → _Z6c_funcv).

        The fallback matcher should still pair them by plain name.
        """
        f_old = _pub_func("c_func", "c_func", is_extern_c=True)
        f_new = _pub_func("c_func", "_Z6c_funcv", is_extern_c=False)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED)

    def test_cpp_to_extern_c_different_mangled(self):
        """C++ → extern "C" flip: mangled name changes (_Z6c_funcv → c_func).

        The reverse direction should also be detected.
        """
        f_old = _pub_func("c_func", "_Z6c_funcv", is_extern_c=False)
        f_new = _pub_func("c_func", "c_func", is_extern_c=True)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert _has_kind(r, ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED)

    def test_same_linkage_no_change(self):
        f_old = _pub_func("c_func", "c_func", is_extern_c=True)
        f_new = _pub_func("c_func", "c_func", is_extern_c=True)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert not _has_kind(r, ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED)


# ── INTEGER_MODEL_CHANGED (LP64 ↔ ILP64) ─────────────────────────────────────

class TestIntegerModelChanged:
    def _int_func(self, name, ret, ptypes):
        return Function(
            name=name, mangled="_Z" + name, return_type=ret,
            params=[Param(name=f"a{i}", type=t) for i, t in enumerate(ptypes)],
            visibility=Visibility.PUBLIC,
        )

    def test_lp64_to_ilp64_mass_flip(self):
        old = [self._int_func(f"fn{i}", "int", ["int", "int"]) for i in range(5)]
        new = [self._int_func(f"fn{i}", "long", ["long", "long"]) for i in range(5)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)
        c = _changes_of_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)[0]
        assert "LP64 → ILP64" in c.description

    def test_int32_to_int64_flip(self):
        old = [self._int_func(f"fn{i}", "int32_t", ["int32_t"]) for i in range(6)]
        new = [self._int_func(f"fn{i}", "int64_t", ["int64_t"]) for i in range(6)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_ilp64_to_lp64_direction(self):
        old = [self._int_func(f"fn{i}", "long", ["long", "long"]) for i in range(5)]
        new = [self._int_func(f"fn{i}", "int", ["int", "int"]) for i in range(5)]
        r = compare(_snap(functions=old), _snap(functions=new))
        c = _changes_of_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)
        assert c and "ILP64 → LP64" in c[0].description

    def test_few_flips_not_detected(self):
        old = [self._int_func("fn0", "int", ["int"])]
        new = [self._int_func("fn0", "long", ["long"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_non_integer_changes_not_detected(self):
        old = [self._int_func(f"fn{i}", "void", ["double"]) for i in range(6)]
        new = [self._int_func(f"fn{i}", "void", ["float"]) for i in range(6)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_signedness_only_not_detected(self):
        old = [self._int_func(f"fn{i}", "int", ["int"]) for i in range(6)]
        new = [self._int_func(f"fn{i}", "unsigned int", ["unsigned int"]) for i in range(6)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_integer_typedef_resize_detected(self):
        old = _snap(typedefs={"MKL_INT": "int"})
        new = _snap(typedefs={"MKL_INT": "long"})
        r = compare(old, new)
        assert _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_unrelated_typedef_resize_not_detected(self):
        old = _snap(typedefs={"my_handle": "int"})
        new = _snap(typedefs={"my_handle": "long"})
        r = compare(old, new)
        assert not _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)

    def test_llp64_int_to_long_typedef_not_flagged(self):
        # Windows/LLP64: `long` is 32-bit, so int->long is NOT a model flip.
        old = _snap(typedefs={"MKL_INT": "int"})
        new = _snap(typedefs={"MKL_INT": "long"})
        old.platform = "pe"
        new.platform = "pe"
        r = compare(old, new)
        assert not _has_kind(r, ChangeKind.INTEGER_MODEL_CHANGED)


# ── char8t / _BitInt / _Atomic / abi_tag spelling detectors ──────────────────

def _spell_func(name, ret="void", ptypes=None):
    return Function(
        name=name, mangled="_Z" + name, return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(ptypes or [])],
        visibility=Visibility.PUBLIC,
    )


class TestChar8tMigration:
    def test_param_char_to_char8t(self):
        old = [_spell_func("g", "void", ["char *"])]
        new = [_spell_func("g", "void", ["char8_t *"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.CHAR8T_MIGRATION)

    def test_return_char8t_to_char(self):
        old = [_spell_func("g", "char8_t", [])]
        new = [_spell_func("g", "unsigned char", [])]
        r = compare(_snap(functions=old), _snap(functions=new))
        c = _changes_of_kind(r, ChangeKind.CHAR8T_MIGRATION)
        assert c and "char8_t → char-family" in c[0].description

    def test_non_char8t_change_not_detected(self):
        old = [_spell_func("g", "void", ["int"])]
        new = [_spell_func("g", "void", ["long"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.CHAR8T_MIGRATION)

    def test_char8t_migration_with_mangling_change(self):
        # Realistic: char->char8_t changes the mangled name (PKc->PKDu) so the
        # symbols don't share a key. The demangled-name fallback must still pair
        # them and surface the migration (Codex review P2).
        old = [Function(name="f", mangled="_Z1fPKc", return_type="void",
                        params=[Param(name="s", type="char *")],
                        visibility=Visibility.PUBLIC)]
        new = [Function(name="f", mangled="_Z1fPKDu", return_type="void",
                        params=[Param(name="s", type="char8_t *")],
                        visibility=Visibility.PUBLIC)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.CHAR8T_MIGRATION)


class TestBitIntWidthChanged:
    def test_param_width_change(self):
        old = [_spell_func("g", "void", ["_BitInt(32)"])]
        new = [_spell_func("g", "void", ["_BitInt(64)"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.BIT_INT_WIDTH_CHANGED)

    def test_migration_to_bit_int(self):
        old = [_spell_func("g", "void", ["int"])]
        new = [_spell_func("g", "void", ["_BitInt(24)"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        c = _changes_of_kind(r, ChangeKind.BIT_INT_WIDTH_CHANGED)
        assert c and "became _BitInt(24)" in c[0].description

    def test_no_bit_int_not_detected(self):
        old = [_spell_func("g", "void", ["int"])]
        new = [_spell_func("g", "void", ["long"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.BIT_INT_WIDTH_CHANGED)


class TestAtomicQualifierChanged:
    def test_atomic_added(self):
        old = [_spell_func("g", "void", ["int"])]
        new = [_spell_func("g", "void", ["_Atomic(int)"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        c = _changes_of_kind(r, ChangeKind.ATOMIC_QUALIFIER_CHANGED)
        assert c and "qualifier added" in c[0].description

    def test_atomic_removed(self):
        old = [_spell_func("g", "void", ["_Atomic(long)"])]
        new = [_spell_func("g", "void", ["long"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.ATOMIC_QUALIFIER_CHANGED)

    def test_non_atomic_change_not_detected(self):
        old = [_spell_func("g", "void", ["int"])]
        new = [_spell_func("g", "void", ["long"])]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.ATOMIC_QUALIFIER_CHANGED)


class TestAbiTagChanged:
    def test_tag_gained(self):
        # Untagged _Z3foov gains the cxx11 ABI tag -> _Z3fooB5cxx11v.
        old = [_pub_func("foo", "_Z3foov")]
        new = [_pub_func("foo", "_Z3fooB5cxx11v")]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.ABI_TAG_CHANGED)

    def test_tag_lost(self):
        old = [_pub_func("foo", "_Z3fooB5cxx11v")]
        new = [_pub_func("foo", "_Z3foov")]
        r = compare(_snap(functions=old), _snap(functions=new))
        c = _changes_of_kind(r, ChangeKind.ABI_TAG_CHANGED)
        assert c and "lost" in c[0].description

    def test_real_rename_not_tag_change(self):
        old = [_pub_func("foo", "_Z3foov")]
        new = [_pub_func("bar", "_Z3barv")]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.ABI_TAG_CHANGED)

    def test_identical_not_reported(self):
        old = [_pub_func("foo", "_Z3fooB5cxx11v")]
        new = [_pub_func("foo", "_Z3fooB5cxx11v")]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert not _has_kind(r, ChangeKind.ABI_TAG_CHANGED)

    def test_suppressed_under_mass_dual_abi_flip(self):
        old = [
            _pub_func(f"f{i}", f"_ZN3foo{i}B5cxx11Ev") for i in range(8)
        ]
        new = [_pub_func(f"f{i}", f"_ZN3foo{i}Ev") for i in range(8)]
        r = compare(_snap(functions=old), _snap(functions=new))
        assert _has_kind(r, ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED)
        assert not _has_kind(r, ChangeKind.ABI_TAG_CHANGED)
