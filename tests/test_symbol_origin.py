"""Tests for symbol origin detection and SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED.

Covers:
- _guess_symbol_origin() heuristic for each symbol category
- ElfSymbol.origin_lib field population
- SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED in RISK_KINDS (not BREAKING)
- Integration: leaked-dependency symbols produce COMPATIBLE_WITH_RISK verdict
"""
from __future__ import annotations

import pytest

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    Verdict,
    compute_verdict,
)
from abicheck.elf_metadata import (
    ElfSymbol,
    SymbolBinding,
    SymbolType,
    _guess_symbol_origin,
)

# ---------------------------------------------------------------------------
# Tests for _guess_symbol_origin
# ---------------------------------------------------------------------------

class TestGuessSymbolOrigin:
    """Test heuristic origin detection for symbol names."""

    def test_cxx_stdlib_prefix_ZNSt_returns_libstdcxx(self):
        """_ZNSt... → libstdc++.so.6 (std:: member function)."""
        result = _guess_symbol_origin("_ZNSt6thread8_M_startEv", [])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefix_ZNKSt_returns_libstdcxx(self):
        """_ZNKSt... → libstdc++ (std:: const member function)."""
        result = _guess_symbol_origin("_ZNKSt3mapIiSsE4findERKi", [])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefix_ZSt_returns_libstdcxx(self):
        """_ZSt... → libstdc++ (std namespace free function)."""
        result = _guess_symbol_origin("_ZSt9terminatev", [])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefix_ZTI_returns_libstdcxx(self):
        """_ZTI... → libstdc++ (typeinfo)."""
        result = _guess_symbol_origin("_ZTISt9exception", [])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefix_ZTS_returns_libstdcxx(self):
        """_ZTS... → libstdc++ (typeinfo string)."""
        result = _guess_symbol_origin("_ZTSSt11logic_error", [])
        assert result == "libstdc++.so.6"

    @pytest.mark.parametrize("symbol", ["_ZTISi", "_ZTSSi", "_ZTVSi"])
    def test_cxx_stdlib_substitution_rtti_returns_libstdcxx(self, symbol):
        """RTTI/vtable symbols using Itanium std substitutions are stdlib-owned."""
        result = _guess_symbol_origin(symbol, [])
        assert result == "libstdc++.so.6"

    @pytest.mark.parametrize(
        "symbol",
        [
            "_ZTIi",     # typeinfo for int
            "_ZTSi",     # typeinfo-name for int
            "_ZTIPi",    # typeinfo for int*
            "_ZTSPi",    # typeinfo-name for int*
            "_ZTIPKi",   # typeinfo for int const*
            "_ZTSPKi",   # typeinfo-name for int const*
            "_ZTIDu",    # typeinfo for char8_t
            "_ZTSDu",    # typeinfo-name for char8_t
            "_ZTIDh",    # typeinfo for half
            "_ZTIDf",    # typeinfo for decimal32
            "_ZTIDF32_",      # typeinfo for _Float32
            "_ZTIPDF16_",     # typeinfo for _Float16*
            "_ZTIPKDF128_",   # typeinfo for _Float128 const*
            "_ZTIDF16b",      # typeinfo for std::bfloat16_t
            "_ZTIPDF32x",     # typeinfo for _Float32x*
            "_ZTIPKDF64x",    # typeinfo for _Float64x const*
        ],
    )
    def test_cxx_fundamental_rtti_returns_libstdcxx(self, symbol):
        """Fundamental RTTI symbols are owned by the C++ runtime."""
        result = _guess_symbol_origin(symbol, [])
        assert result == "libstdc++.so.6"

    @pytest.mark.parametrize("symbol", ["_ZTIi", "_ZTSi"])
    def test_cxx_fundamental_rtti_prefers_needed_libcxx(self, symbol):
        """Fundamental RTTI attribution still honors the runtime in DT_NEEDED."""
        result = _guess_symbol_origin(symbol, ["libc++.so.1"])
        assert result == "libc++.so.1"

    def test_cxx_fundamental_rtti_does_not_select_libcxxabi(self):
        """libc++abi should not be reported as the C++ stdlib owner."""
        result = _guess_symbol_origin("_ZTIi", ["libc++abi.so.1"])
        assert result == "libstdc++.so.6"

    def test_native_project_typeinfo_returns_none(self):
        """Project-owned RTTI symbols must not be attributed to libstdc++."""
        result = _guess_symbol_origin("_ZTIN11flatbuffers13FileNameSaverE", ["libstdc++.so.6"])
        assert result is None

    def test_native_project_typeinfo_name_returns_none(self):
        """Project-owned typeinfo-name symbols must not be attributed to libstdc++."""
        result = _guess_symbol_origin("_ZTSN11flatbuffers13FileNameSaverE", ["libstdc++.so.6"])
        assert result is None

    @pytest.mark.parametrize(
        "symbol",
        [
            "_ZTIPN11flatbuffers13FileNameSaverE",
            "_ZTSPN11flatbuffers13FileNameSaverE",
        ],
    )
    def test_native_project_pointer_rtti_returns_none(self, symbol):
        """Pointer RTTI for project types must not be attributed to libstdc++."""
        result = _guess_symbol_origin(symbol, ["libstdc++.so.6"])
        assert result is None

    def test_cxx_abi_vtable_returns_libstdcxx(self):
        """_ZTVN10__cxxabiv... → libstdc++ (C++ ABI vtable)."""
        result = _guess_symbol_origin("_ZTVN10__cxxabiv117__class_type_infoE", [])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefers_needed_libstdcxx(self):
        """If libstdc++.so.6 appears in DT_NEEDED, return that exact name."""
        result = _guess_symbol_origin("_ZNSt3mapC1Ev", ["libm.so.6", "libstdc++.so.6"])
        assert result == "libstdc++.so.6"

    def test_cxx_stdlib_prefers_needed_libcxx(self):
        """If libc++.so.1 appears in DT_NEEDED, return that exact name."""
        result = _guess_symbol_origin("_ZNSt3vecC1Ev", ["libc++.so.1"])
        assert result == "libc++.so.1"

    def test_cxx_stdlib_fallback_when_not_in_needed(self):
        """Falls back to 'libstdc++.so.6' when no matching lib in DT_NEEDED."""
        result = _guess_symbol_origin("_ZSt9terminatev", ["libm.so.6"])
        assert result == "libstdc++.so.6"

    def test_gcc_runtime_ix86(self):
        """ix86_... → libgcc.a (static) — statically linked x87 math helper."""
        result = _guess_symbol_origin("ix86_math_helper", [])
        assert result == "libgcc.a (static)"

    def test_gcc_runtime_ZGV(self):
        """libmvec vector ABI symbol → libmvec.so.1 (not libgcc_s)."""
        result = _guess_symbol_origin("_ZGVnN4v_sin", [])
        assert result == "libmvec.so.1"

    def test_cxx_guard_variable_ZGVZ_is_native(self):
        """_ZGVZ... is an Itanium C++ guard variable, not a libmvec symbol."""
        result = _guess_symbol_origin("_ZGVZN4YAML3Exp9AmpersandEvE1e", [])
        assert result is None

    def test_gcc_runtime_cpu_model(self):
        """__cpu_model → libgcc_s.so.1."""
        result = _guess_symbol_origin("__cpu_model", [])
        assert result == "libgcc_s.so.1"

    def test_gcc_runtime_cpu_features(self):
        """__cpu_features_init → libgcc_s.so.1."""
        result = _guess_symbol_origin("__cpu_features_init", [])
        assert result == "libgcc_s.so.1"

    def test_gcc_runtime_svml(self):
        """__svml_sin4 → <intel-compiler-rt> (not libgcc_s — Intel static RT)."""
        result = _guess_symbol_origin("__svml_sin4", [])
        assert result == "<intel-compiler-rt>"

    def test_libc_prefix_libc(self):
        """__libc_start_main → libc.so.6."""
        result = _guess_symbol_origin("__libc_start_main", [])
        assert result == "libc.so.6"

    def test_libc_prefix_glibc(self):
        """__glibc_safe_len → libc.so.6."""
        result = _guess_symbol_origin("__glibc_safe_len", [])
        assert result == "libc.so.6"

    def test_native_symbol_returns_none(self):
        """Regular C symbol (not matching any known prefix) → None."""
        result = _guess_symbol_origin("my_lib_function", [])
        assert result is None

    def test_native_mangled_symbol_returns_none(self):
        """Mangled non-stdlib C++ symbol → None (native to this lib)."""
        result = _guess_symbol_origin("_ZN3Foo3barEv", [])
        assert result is None

    def test_empty_name_returns_none(self):
        """Empty symbol name → None (no prefix match)."""
        result = _guess_symbol_origin("", [])
        assert result is None


# ---------------------------------------------------------------------------
# Tests for ElfSymbol.origin_lib field
# ---------------------------------------------------------------------------

class TestElfSymbolOriginLib:
    """Test that ElfSymbol.origin_lib is populated correctly."""

    def test_origin_lib_default_is_none(self):
        """ElfSymbol without origin_lib defaults to None."""
        sym = ElfSymbol(name="my_func")
        assert sym.origin_lib is None

    def test_origin_lib_can_be_set(self):
        """ElfSymbol.origin_lib can be set to a library name."""
        sym = ElfSymbol(name="_ZNSt6thread8_M_startEv", origin_lib="libstdc++.so.6")
        assert sym.origin_lib == "libstdc++.so.6"

    def test_origin_lib_none_for_native(self):
        """ElfSymbol.origin_lib is None for native symbols."""
        sym = ElfSymbol(name="native_func", origin_lib=None)
        assert sym.origin_lib is None

    def test_elf_symbol_dataclass_fields(self):
        """ElfSymbol dataclass has all expected fields including origin_lib."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ElfSymbol)}
        assert "origin_lib" in field_names


# ---------------------------------------------------------------------------
# Tests for SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED in RISK_KINDS
# ---------------------------------------------------------------------------

class TestSymbolLeakedFromDependencyPolicy:
    """Test policy classification of SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED."""

    def test_in_risk_kinds(self):
        """SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED must be in RISK_KINDS."""
        assert ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED in RISK_KINDS

    def test_not_in_breaking_kinds(self):
        """Must not be in BREAKING_KINDS."""
        assert ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED not in BREAKING_KINDS

    def test_not_in_compatible_kinds(self):
        """Must not be in COMPATIBLE_KINDS."""
        assert ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED not in COMPATIBLE_KINDS

    def test_not_in_api_break_kinds(self):
        """Must not be in API_BREAK_KINDS."""
        assert ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED not in API_BREAK_KINDS

    def test_verdict_is_compatible_with_risk(self):
        """A change list with only SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED → COMPATIBLE_WITH_RISK."""
        from abicheck.checker import Change

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol="_ZNSt6thread8_M_startEv",
                description="test",
            )
        ]
        verdict = compute_verdict(changes, policy="strict_abi")
        assert verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_verdict_not_breaking_alone(self):
        """Alone it does not produce BREAKING verdict."""
        from abicheck.checker import Change

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol="_ZNSt6thread8_M_startEv",
                description="test",
            )
        ]
        verdict = compute_verdict(changes)
        assert verdict != Verdict.BREAKING

    def test_mixed_breaking_overrides(self):
        """When combined with a BREAKING change, verdict is BREAKING."""
        from abicheck.checker import Change

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol="_ZNSt6thread8_M_startEv",
                description="leaked dependency",
            ),
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="my_func",
                description="public function removed",
            ),
        ]
        verdict = compute_verdict(changes)
        assert verdict == Verdict.BREAKING


# ---------------------------------------------------------------------------
# Integration: _diff_leaked_dependency_symbols detector
# ---------------------------------------------------------------------------

class TestDiffLeakedDependencySymbols:
    """Test that the detector in checker.py emits the correct Changes."""

    def _make_elf_meta(self, symbols: list[ElfSymbol]) -> object:
        """Build a minimal ElfMetadata-like object with a symbol_map."""
        from abicheck.elf_metadata import ElfMetadata
        meta = ElfMetadata()
        meta.symbols = symbols
        return meta

    def test_removed_leaked_symbol_emits_change(self):
        """A symbol with origin_lib removed from new ELF → Change emitted."""
        from abicheck.checker import _diff_leaked_dependency_symbols

        old_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZNSt6thread8_M_startEv",
                origin_lib="libstdc++.so.6",
            )
        ])
        new_elf = self._make_elf_meta([])  # symbol removed

        changes = _diff_leaked_dependency_symbols(old_elf, new_elf)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED
        assert "_ZNSt6thread8_M_startEv" in changes[0].symbol
        assert "libstdc++.so.6" in changes[0].description

    def test_native_symbol_removed_no_change(self):
        """A removed symbol with origin_lib=None → no LEAKED change emitted."""
        from abicheck.checker import _diff_leaked_dependency_symbols

        old_elf = self._make_elf_meta([
            ElfSymbol(name="my_native_func", origin_lib=None)
        ])
        new_elf = self._make_elf_meta([])

        changes = _diff_leaked_dependency_symbols(old_elf, new_elf)
        assert changes == []

    def test_added_leaked_symbol_emits_change(self):
        """A newly added symbol with origin_lib → Change emitted."""
        from abicheck.checker import _diff_leaked_dependency_symbols

        old_elf = self._make_elf_meta([])
        new_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZGVnN4v_sin",
                origin_lib="libmvec.so.1",
            )
        ])

        changes = _diff_leaked_dependency_symbols(old_elf, new_elf)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED
        assert "libmvec.so.1" in changes[0].description

    def test_changed_leaked_symbol_no_double_annotation(self):
        """C2: a leaked symbol that changed type → NOT emitted by _diff_leaked_dependency_symbols.

        _diff_elf_symbol_metadata already handles changed symbols.
        Emitting from both detectors would create contradictory Change records.
        """
        from abicheck.checker import _diff_leaked_dependency_symbols

        old_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZNSt6locale5_Impl16_M_check_same_nameEPKc",
                sym_type=SymbolType.FUNC,
                origin_lib="libstdc++.so.6",
            )
        ])
        new_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZNSt6locale5_Impl16_M_check_same_nameEPKc",
                sym_type=SymbolType.OBJECT,  # type changed
                origin_lib="libstdc++.so.6",
            )
        ])

        # C2 fix: changed (not added/removed) symbols must NOT emit here
        changes = _diff_leaked_dependency_symbols(old_elf, new_elf)
        assert changes == [], (
            "Changed symbols must not be double-annotated by _diff_leaked_dependency_symbols"
        )

    def test_unchanged_leaked_symbol_no_change(self):
        """A leaked symbol that did NOT change → no LEAKED change emitted."""
        from abicheck.checker import _diff_leaked_dependency_symbols

        sym = ElfSymbol(
            name="_ZNSt6thread8_M_startEv",
            sym_type=SymbolType.FUNC,
            binding=SymbolBinding.GLOBAL,
            size=0,
            origin_lib="libstdc++.so.6",
        )
        old_elf = self._make_elf_meta([sym])
        new_elf = self._make_elf_meta([sym])

        changes = _diff_leaked_dependency_symbols(old_elf, new_elf)
        assert changes == []


# ---------------------------------------------------------------------------
# New tests: C1 post-parse fixup ordering
# ---------------------------------------------------------------------------

class TestPostParseFixup:
    """C1: post-parse origin fixup runs after .dynamic is parsed."""

    def test_fixup_corrects_libcxx_when_needed_populated(self):
        """_ZNSt3__1... must resolve to libc++.so.1 when that's in needed, not libstdc++.so.6."""
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol

        # Simulate a library that DT_NEEDS libc++.so.1 (not libstdc++)
        meta = ElfMetadata()
        meta.needed = ["libc++.so.1"]
        # Create a symbol that was initially guessed as libstdc++ (empty needed at parse time)
        sym = ElfSymbol(
            name="_ZNSt3__112basic_stringIcEC1Ev",
            origin_lib="libstdc++.so.6",  # initial wrong guess
        )
        meta.symbols = [sym]

        # Re-run the fixup (simulates what _parse() now does after sections are read)
        from abicheck.elf_metadata import _guess_symbol_origin
        _GENERIC_FALLBACKS = frozenset({"libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"})
        for s in meta.symbols:
            if s.origin_lib is None or s.origin_lib in _GENERIC_FALLBACKS:
                new_origin = _guess_symbol_origin(s.name, meta.needed)
                if new_origin is not None:
                    s.origin_lib = new_origin

        assert meta.symbols[0].origin_lib == "libc++.so.1", (
            "Post-parse fixup must override generic fallback when libc++.so.1 is in needed"
        )

    def test_fixup_leaves_native_symbols_alone(self):
        """Native symbols (origin_lib=None) must not be affected by fixup."""
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol, _guess_symbol_origin

        meta = ElfMetadata()
        meta.needed = ["libstdc++.so.6"]
        sym = ElfSymbol(name="my_native_func", origin_lib=None)
        meta.symbols = [sym]

        _GENERIC_FALLBACKS = frozenset({"libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"})
        for s in meta.symbols:
            if s.origin_lib is None or s.origin_lib in _GENERIC_FALLBACKS:
                new_origin = _guess_symbol_origin(s.name, meta.needed)
                if new_origin is not None:
                    s.origin_lib = new_origin

        assert meta.symbols[0].origin_lib is None


# ---------------------------------------------------------------------------
# New tests: updated attributions M1/M2/M3/M4
# ---------------------------------------------------------------------------

class TestUpdatedAttributions:
    """Test corrected symbol attributions: M1/M2/M3/M4."""

    # --- M1: Intel SVML / ix86 / _ZGV ---

    def test_svml_to_intel_compiler_rt(self):
        """M1: __svml_* → <intel-compiler-rt>, not libgcc_s."""
        result = _guess_symbol_origin("__svml_sinf4_mask", [])
        assert result == "<intel-compiler-rt>"

    def test_svml_prefix_variants(self):
        """M1: Various __svml_ variants."""
        for name in ("__svml_sin4", "__svml_exp4_mask", "__svml_log4"):
            result = _guess_symbol_origin(name, [])
            assert result == "<intel-compiler-rt>", f"Expected <intel-compiler-rt> for {name}"

    def test_ix86_to_libgcc_static(self):
        """M1: ix86_* → libgcc.a (static), not libgcc_s.so.1."""
        result = _guess_symbol_origin("ix86_math_helper", [])
        assert result == "libgcc.a (static)"

    def test_ZGV_to_libmvec(self):
        """M1: libmvec vector ABI symbol → libmvec.so.1, not libgcc_s."""
        result = _guess_symbol_origin("_ZGVnN4v_sin", [])
        assert result == "libmvec.so.1"

    def test_ZGV_variants(self):
        """M1: Various _ZGV SIMD math variants."""
        for name in ("_ZGVbN4v_sin", "_ZGVcN4v_sin", "_ZGVdN2v_cos", "_ZGVeN8v_exp"):
            result = _guess_symbol_origin(name, [])
            assert result == "libmvec.so.1", f"Expected libmvec.so.1 for {name}"

    def test_ZGVs_sve_variant_to_libmvec(self):
        """M1: AArch64 SVE libmvec symbols use the _ZGVs... ABI form."""
        result = _guess_symbol_origin("_ZGVsMxv_sinf", [])
        assert result == "libmvec.so.1"

    def test_ZGVN_guard_variable_not_libmvec(self):
        """M1: Namespace-scope Itanium C++ guard variables are project-owned."""
        result = _guess_symbol_origin("_ZGVN4test7MyClassE", [])
        assert result is None

    # --- M2: operator new/delete ---

    def test_operator_new_size_t(self):
        """M2: _Znwm (operator new(size_t)) → libstdc++.so.6."""
        result = _guess_symbol_origin("_Znwm", [])
        assert result == "libstdc++.so.6"

    def test_operator_new_array(self):
        """M2: _Znam (operator new[]) → libstdc++.so.6."""
        result = _guess_symbol_origin("_Znam", [])
        assert result == "libstdc++.so.6"

    def test_operator_delete(self):
        """M2: _ZdlPv (operator delete(void*)) → libstdc++.so.6."""
        result = _guess_symbol_origin("_ZdlPv", [])
        assert result == "libstdc++.so.6"

    def test_operator_delete_array(self):
        """M2: _ZdaPv (operator delete[]) → libstdc++.so.6."""
        result = _guess_symbol_origin("_ZdaPv", [])
        assert result == "libstdc++.so.6"

    def test_operator_new_nothrow(self):
        """M2: _ZnwmSt (operator new nothrow) → libstdc++.so.6."""
        result = _guess_symbol_origin("_ZnwmSt", [])
        assert result == "libstdc++.so.6"

    def test_operator_new_prefers_needed_libcxx(self):
        """M2: operator new resolves to libc++ when that's in DT_NEEDED."""
        result = _guess_symbol_origin("_Znwm", ["libc++.so.1"])
        assert result == "libc++.so.1"

    # --- M3: libm SSE2/AVX helpers ---

    def test_libm_sse2_prefix(self):
        """M3: __libm_sse2_* → libm.so.6."""
        result = _guess_symbol_origin("__libm_sse2_sin", [])
        assert result == "libm.so.6"

    def test_libm_avx_prefix(self):
        """M3: __libm_avx_* → libm.so.6."""
        result = _guess_symbol_origin("__libm_avx_log", [])
        assert result == "libm.so.6"

    # --- M4: libc++ inline namespace __1 ---

    def test_ZNSt3__1_to_libcxx(self):
        """M4: _ZNSt3__1* → libc++.so.1 (not libstdc++.so.6)."""
        result = _guess_symbol_origin("_ZNSt3__112basic_stringIcEC1Ev", [])
        assert result == "libc++.so.1"

    def test_ZNKSt3__1_to_libcxx(self):
        """M4: _ZNKSt3__1* (const member) → libc++.so.1."""
        result = _guess_symbol_origin("_ZNKSt3__112basic_stringIcE4sizeEv", [])
        assert result == "libc++.so.1"

    def test_ZNSt3__1_prefers_libcxx_in_needed(self):
        """M4: libc++ inline namespace picks up libc++.so.1 from DT_NEEDED."""
        result = _guess_symbol_origin("_ZNSt3__112basic_stringIcEC1Ev", ["libc++.so.1"])
        assert result == "libc++.so.1"

    def test_ZNSt3__1_does_not_pick_libstdcxx(self):
        """M4: _ZNSt3__1* must NOT resolve to libstdc++.so.6 even if it's in needed."""
        result = _guess_symbol_origin("_ZNSt3__112basic_stringIcEC1Ev", ["libstdc++.so.6"])
        # libstdc++ doesn't match "c++" without "stdc++" exclusion, but the
        # fallback is still libc++.so.1 (not libstdc++) for __1 namespace
        assert result == "libc++.so.1"

    def test_ZNSt_without__1_still_libstdcxx(self):
        """M4: regular _ZNSt* (no __1) still maps to libstdc++.so.6."""
        result = _guess_symbol_origin("_ZNSt6thread8_M_startEv", [])
        assert result == "libstdc++.so.6"


# ---------------------------------------------------------------------------
# C2: no double annotation integration test
# ---------------------------------------------------------------------------

class TestNoDoubleAnnotation:
    """C2: a changed leaked symbol must produce only ONE Change record total."""

    def _make_elf_meta(self, symbols):
        from abicheck.elf_metadata import ElfMetadata
        meta = ElfMetadata()
        meta.symbols = symbols
        return meta

    def test_changed_leaked_symbol_single_change(self):
        """One changed leaked symbol → exactly one Change from the two ELF detectors combined."""
        from abicheck.checker import (
            _diff_elf_symbol_metadata,
            _diff_leaked_dependency_symbols,
        )

        old_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZNSt6locale5_Impl16_M_check_same_nameEPKc",
                sym_type=SymbolType.FUNC,
                binding=SymbolBinding.GLOBAL,
                size=0,
                origin_lib="libstdc++.so.6",
            )
        ])
        new_elf = self._make_elf_meta([
            ElfSymbol(
                name="_ZNSt6locale5_Impl16_M_check_same_nameEPKc",
                sym_type=SymbolType.OBJECT,  # type changed
                binding=SymbolBinding.GLOBAL,
                size=0,
                origin_lib="libstdc++.so.6",
            )
        ])

        changes_meta = _diff_elf_symbol_metadata(old_elf, new_elf)
        changes_leaked = _diff_leaked_dependency_symbols(old_elf, new_elf)

        # _diff_elf_symbol_metadata should catch the type change
        assert len(changes_meta) >= 1
        # _diff_leaked_dependency_symbols must NOT double-emit for the same symbol
        assert changes_leaked == [], (
            "_diff_leaked_dependency_symbols must not emit for changed (non-removed/added) symbols"
        )
        # Combined: exactly the records from meta, no duplicates
        total = changes_meta + changes_leaked
        symbols_in_changes = [c.symbol for c in total]
        assert symbols_in_changes.count("_ZNSt6locale5_Impl16_M_check_same_nameEPKc") == 1, (
            "Symbol must appear in exactly one Change record, not duplicated"
        )


class TestEdgeCoverage:
    """Cover remaining edge cases in elf_metadata for 100% coverage."""

    def test_guess_symbol_origin_libcxx_fallback_no_needed(self):
        """_ZNSt3__1 with empty needed_libs → fallback to libc++.so.1"""
        from abicheck.elf_metadata import _guess_symbol_origin
        result = _guess_symbol_origin("_ZNSt3__1string8nposE", [])
        assert result == "libc++.so.1"

    def test_guess_symbol_origin_libcxx_in_needed(self):
        """_ZNSt3__1 with libc++ in needed_libs → returns that lib"""
        from abicheck.elf_metadata import _guess_symbol_origin
        result = _guess_symbol_origin("_ZNKSt3__16vectorIiNS_9allocatorIiEEE4sizeEv", ["libc++.so.1"])
        assert result == "libc++.so.1"

    def test_parse_elf_metadata_non_regular_file(self, tmp_path):
        """parse_elf_metadata returns empty ElfMetadata for non-regular files (e.g. dir)."""
        from abicheck.elf_metadata import parse_elf_metadata
        # Use a directory — not a regular file
        result = parse_elf_metadata(tmp_path)
        assert result is not None
        # Should return empty metadata gracefully
        from abicheck.elf_metadata import ElfMetadata
        assert isinstance(result, ElfMetadata)
