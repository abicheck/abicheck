"""Tests for abicheck.build_mode — normalized build-mode capture.

Pure unit tests against synthetic input strings.  The CI-engineer
swarm-review dropdead requirement: normalization must be stable across
real-world ``DW_AT_producer`` / ELF ``.comment`` strings from every
shipped compiler version, so this file pins ALL of them with fixtures.
"""
from __future__ import annotations

from abicheck.build_mode import (
    BuildMode,
    BuildModeProvenance,
    CompilerFamily,
    CxxStandard,
    GlibcxxDualAbi,
    StdlibFamily,
    build_mode_from_signals,
    detect_compiler_family,
    detect_cxx_standard,
    detect_stdlib_and_abi,
)

# ── Compiler-family normalization ──────────────────────────────────────


class TestCompilerFamily:
    def test_gcc_producer_strings(self) -> None:
        fixtures = [
            "GNU C++17 11.4.0 -mtune=generic -march=x86-64",
            "GNU C 9.3.0",
            "GCC: (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0",
            "GCC: (GNU) 13.2.1 20231205 (Red Hat 13.2.1-6)",
            "GNU C++23 14.2.0",
        ]
        for f in fixtures:
            fam, ver = detect_compiler_family(producer=f, comment=None)
            assert fam == CompilerFamily.GCC, f"failed on {f!r}"
            assert ver is not None and ver.split(".")[0].isdigit()

    def test_clang_producer_strings(self) -> None:
        fixtures = [
            "clang version 14.0.6 (Fedora 14.0.6-1.fc36)",
            "clang version 17.0.6 (https://github.com/llvm/llvm-project ...)",
            "clang version 18.1.8",
            "Ubuntu clang version 16.0.0",
        ]
        for f in fixtures:
            fam, ver = detect_compiler_family(producer=f, comment=None)
            assert fam == CompilerFamily.CLANG, f"failed on {f!r}"
            assert ver is not None

    def test_msvc_producer_strings(self) -> None:
        fixtures = [
            "Microsoft (R) Optimizing Compiler",
            "Microsoft (R) C/C++ Optimizing Compiler Version 19.35.32217",
            "MSVC 19.36.32532",
        ]
        for f in fixtures:
            fam, ver = detect_compiler_family(producer=f, comment=None)
            assert fam == CompilerFamily.MSVC, f"failed on {f!r}"

    def test_icx_producer_strings(self) -> None:
        fixtures = [
            "Intel(R) oneAPI DPC++/C++ Compiler 2024.1.0 (2024.x.y.20240202)",
            "Intel(R) oneAPI DPC++/C++ Compiler 2025.0.0",
        ]
        for f in fixtures:
            fam, _ver = detect_compiler_family(producer=f, comment=None)
            assert fam == CompilerFamily.ICX, f"failed on {f!r}"

    def test_icc_producer_strings(self) -> None:
        """Classic Intel C++ Compiler (pre-oneAPI). Producer strings vary,
        and may not always include the literal 'Intel' prefix."""
        fixtures = [
            "Intel(R) C++ Compiler 19.1.3.304",
            "icc 19.1.3.304",
            "icc (ICC) 2021.7.1 20221019",
        ]
        for f in fixtures:
            fam, _ver = detect_compiler_family(producer=f, comment=None)
            assert fam == CompilerFamily.ICC, f"failed on {f!r}"

    def test_unknown_producer_strings(self) -> None:
        fam, _ver = detect_compiler_family(producer=None, comment=None)
        assert fam == CompilerFamily.UNKNOWN
        fam, _ver = detect_compiler_family(producer="random garbage", comment="")
        assert fam == CompilerFamily.UNKNOWN

    def test_falls_back_to_comment_when_producer_empty(self) -> None:
        fam, _ver = detect_compiler_family(
            producer=None,
            comment="GCC: (Ubuntu 13.2.0-23ubuntu4) 13.2.0",
        )
        assert fam == CompilerFamily.GCC

    def test_oneapi_substring_does_not_trigger_icx_alone(self) -> None:
        """Regression for the Codex P2 finding: the un-parenthesized
        condition ``m and "DPC++" in text or "oneAPI" in text`` would
        classify any string mentioning oneAPI as ICX even when the ICX
        producer regex did not match. With the fix, an unrelated string
        like a third-party tool that happens to print 'oneAPI' in its
        version banner must NOT be misclassified.
        """
        # No 'Intel(R) oneAPI DPC++/C++' producer prefix, just a stray
        # mention of "oneAPI" — must NOT classify as ICX.
        fam, _ver = detect_compiler_family(
            producer="some_tool 1.2.3 (built against oneAPI)",
            comment=None,
        )
        assert fam == CompilerFamily.UNKNOWN
        # Same for a GCC producer string with 'oneAPI' in it: should
        # still resolve to GCC (later branch), not ICX.
        fam, _ver = detect_compiler_family(
            producer="GCC: (Ubuntu 11.4.0) 11.4.0 (oneAPI compat layer)",
            comment=None,
        )
        assert fam == CompilerFamily.GCC


# ── C++ standard mapping ───────────────────────────────────────────────


class TestCxxStandard:
    def test_dwarf_tags(self) -> None:
        # Spot-check the main mappings.
        assert detect_cxx_standard(0x02) == CxxStandard.CXX98
        assert detect_cxx_standard(0x1a) == CxxStandard.CXX11
        assert detect_cxx_standard(0x21) == CxxStandard.CXX14_OR_LATER
        assert detect_cxx_standard(0x2a) == CxxStandard.CXX17
        assert detect_cxx_standard(0x2b) == CxxStandard.CXX20
        assert detect_cxx_standard(0x2e) == CxxStandard.CXX23

    def test_cpp03_maps_to_pre_cxx11_bucket(self) -> None:
        """Regression for the Codex P2 finding: DW_LANG_C_plus_plus_03
        (0x19) must NOT be upgraded to CXX11. The enum has no CXX03
        bucket, so the closest pre-C++11 value (CXX98) is used. This
        avoids misattributing ABI differences between C++03 and C++11
        binaries to the wrong build mode."""
        assert detect_cxx_standard(0x19) == CxxStandard.CXX98

    def test_unknown_tag(self) -> None:
        assert detect_cxx_standard(None) == CxxStandard.UNKNOWN
        assert detect_cxx_standard(0xffff) == CxxStandard.UNKNOWN


# ── stdlib + dual-ABI inference ────────────────────────────────────────


class TestStdlibDetection:
    def test_libstdcxx_dual_abi_cxx11(self) -> None:
        # B5cxx11 ABI tag in any symbol signals new dual-ABI.
        syms = [
            "_ZNSt7__cxx115ctypeIcE13_M_widen_initEv",
            "_ZNKSs5emptyEv",
            "_ZN3foo3barB5cxx11Ev",   # the marker
        ]
        stdlib, abi, libcpp_v = detect_stdlib_and_abi(syms)
        assert stdlib == StdlibFamily.LIBSTDCXX
        assert abi == GlibcxxDualAbi.CXX11
        assert libcpp_v is None

    def test_libstdcxx_old_abi(self) -> None:
        # libstdc++ symbols without the B5cxx11 tag → old ABI.
        syms = ["_ZNSt5listIiSaIiEEC1Ev", "_ZNSsC1Ev"]
        stdlib, abi, libcpp_v = detect_stdlib_and_abi(syms)
        assert stdlib == StdlibFamily.LIBSTDCXX
        assert abi == GlibcxxDualAbi.OLD
        assert libcpp_v is None

    def test_libcxx_v1(self) -> None:
        syms = ["_ZNSt3__16vectorIiNS_9allocatorIiEEEC1Ev"]
        stdlib, abi, libcpp_v = detect_stdlib_and_abi(syms)
        assert stdlib == StdlibFamily.LIBCXX
        assert abi == GlibcxxDualAbi.NOT_APPLICABLE
        assert libcpp_v == 1

    def test_libcxx_v2(self) -> None:
        syms = ["_ZNSt3__26stringE"]
        stdlib, abi, libcpp_v = detect_stdlib_and_abi(syms)
        assert stdlib == StdlibFamily.LIBCXX
        assert libcpp_v == 2

    def test_empty_or_no_stdlib(self) -> None:
        stdlib, abi, libcpp_v = detect_stdlib_and_abi([])
        assert stdlib == StdlibFamily.UNKNOWN
        assert abi == GlibcxxDualAbi.NOT_APPLICABLE
        stdlib, _abi, _v = detect_stdlib_and_abi(["dispatch", "init_helper"])
        assert stdlib == StdlibFamily.UNKNOWN


# ── End-to-end BuildMode synthesis ─────────────────────────────────────


class TestBuildModeFromSignals:
    def test_typical_linux_gcc_cxx17(self) -> None:
        bm = build_mode_from_signals(
            raw_producer="GNU C++17 11.4.0 -mtune=generic",
            raw_comment="GCC: (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0",
            dwarf_language=0x2a,   # DW_LANG_C_plus_plus_17
            mangled_symbols=[
                "_ZNSt7__cxx115ctypeIcE13_M_widen_initEv",
                "_Z3fooi",
            ],
        )
        assert bm.compiler_family == CompilerFamily.GCC
        assert bm.language_std == CxxStandard.CXX17
        assert bm.stdlib == StdlibFamily.LIBSTDCXX
        assert bm.glibcxx_dual_abi == GlibcxxDualAbi.CXX11
        # Provenance retained but does not affect equality.
        assert bm.provenance.compiler_version is not None
        assert bm.provenance.raw_producer is not None

    def test_typical_macos_clang_libcxx(self) -> None:
        bm = build_mode_from_signals(
            raw_producer="clang version 17.0.6 (https://github.com/llvm/llvm-project ...)",
            dwarf_language=0x21,
            mangled_symbols=["_ZNSt3__16vectorIiNS_9allocatorIiEEEC1Ev"],
        )
        assert bm.compiler_family == CompilerFamily.CLANG
        assert bm.stdlib == StdlibFamily.LIBCXX
        assert bm.libcpp_abi_version == 1
        assert bm.glibcxx_dual_abi == GlibcxxDualAbi.NOT_APPLICABLE

    def test_provenance_excluded_from_equality(self) -> None:
        """Two BuildModes from different point versions of the same
        compiler must compare equal (CI engineer's dropdead requirement).
        """
        a = build_mode_from_signals(
            raw_producer="GCC: (Ubuntu 11.4.0) 11.4.0",
            dwarf_language=0x2a,
            mangled_symbols=["_ZNSt7__cxx115ctypeIcE_xyz"],
        )
        b = build_mode_from_signals(
            raw_producer="GCC: (Ubuntu 13.2.0-23ubuntu4) 13.2.0",
            dwarf_language=0x2a,
            mangled_symbols=["_ZNSt7__cxx115ctypeIcE_xyz"],
        )
        # Same normalized fields → equal even with different provenance.
        assert a == b
        # …yet the raw producer string differs.
        assert a.provenance.raw_producer != b.provenance.raw_producer

    def test_default_constructed_buildmode(self) -> None:
        """A bare ``BuildMode()`` has all-unknown fields and an empty
        provenance.  Used when capture is unavailable (no DWARF, no
        ELF .comment)."""
        bm = BuildMode()
        assert bm.compiler_family == CompilerFamily.UNKNOWN
        assert bm.language_std == CxxStandard.UNKNOWN
        assert bm.stdlib == StdlibFamily.UNKNOWN
        assert bm.glibcxx_dual_abi == GlibcxxDualAbi.NOT_APPLICABLE
        assert bm.libcpp_abi_version is None
        assert isinstance(bm.provenance, BuildModeProvenance)
