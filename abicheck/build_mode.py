# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build-mode capture for ABI snapshots.

Captures normalized toolchain / build-configuration metadata into the
snapshot so the comparator can attribute layout/mangling differences to
build mode rather than to a real ABI change.

The dropdead requirement from the PR-ε.1 design review (CI / infra
engineer):

    Only normalized fields go into the comparable :class:`BuildMode`
    record. Raw producer strings live under ``provenance.raw_producer``
    which is *not* part of equality. This guarantees stable snapshots
    across CI runners with different point-version toolchains.

Detection heuristics (from PR-ε.1's DWARF expert review):

* **Compiler family** — ``DW_AT_producer`` (per-CU) and ELF
  ``.comment``.  Producer strings differ in format but are reliably
  prefixed: ``GCC: (...) 11.4.0``, ``clang version 17.0.6``,
  ``Microsoft (R) Optimizing Compiler``, ``Intel(R) oneAPI DPC++/C++``.
* **C++ standard** — ``DW_AT_language`` is *unreliable* (clang stamps
  ``DW_LANG_C_plus_plus_14`` for everything ≤ c++17 in older versions).
  We fall back to ``unknown`` rather than guessing.
* **libstdc++ dual-ABI** — presence of the ``B5cxx11`` ABI tag in any
  mangled symbol indicates ``_GLIBCXX_USE_CXX11_ABI=1``. The
  ``__cxx11::`` namespace component is a less-reliable secondary signal.
* **libc++ ABI version** — ``_ZNSt3__1`` prefix = ABI v1 (stable);
  ``_ZNSt3__2`` = ABI v2 (``_LIBCPP_ABI_VERSION=2``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class CompilerFamily(str, Enum):
    """Normalized compiler identity.  Patch / minor version intentionally
    NOT captured here — those live in :attr:`BuildMode.provenance` so
    snapshots stay equal across CI runners with different point releases."""

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"
    ICX = "icx"          # Intel oneAPI DPC++/C++ (clang-based)
    ICC = "icc"          # Classic Intel C++ compiler (pre-oneAPI)
    UNKNOWN = "unknown"


class StdlibFamily(str, Enum):
    LIBSTDCXX = "libstdc++"
    LIBCXX = "libc++"
    MSVC_STL = "msvc_stl"
    UNKNOWN = "unknown"


class CxxStandard(str, Enum):
    """Coarse-grained C++ standard bucket.

    Maps DWARF ``DW_AT_language`` values to a stable enum.  Note that
    clang ≤ 16 emits ``DW_LANG_C_plus_plus_14`` for any ``-std=c++14/17``
    target, so the bucket for those cases is :attr:`CXX14_OR_LATER` —
    callers must not assume the literal ``c++14`` constraint.
    """

    C = "c"
    CXX98 = "c++98"
    CXX11 = "c++11"
    CXX14 = "c++14"
    CXX14_OR_LATER = "c++14_or_later"   # clang ≤ 16 ambiguity bucket
    CXX17 = "c++17"
    CXX20 = "c++20"
    CXX23 = "c++23"
    UNKNOWN = "unknown"


class GlibcxxDualAbi(str, Enum):
    """libstdc++ dual-ABI flavor (only meaningful when stdlib == LIBSTDCXX)."""

    CXX11 = "cxx11"      # _GLIBCXX_USE_CXX11_ABI=1 (default since gcc 5)
    OLD = "old"          # _GLIBCXX_USE_CXX11_ABI=0 (legacy)
    NOT_APPLICABLE = "n/a"


@dataclass(frozen=True)
class BuildModeProvenance:
    """Raw, non-normalized capture for debugging / human inspection.

    These fields are **excluded from equality comparison** so two
    snapshots produced on different CI runners with the same effective
    build configuration compare equal.  Mark this clearly: anything that
    encodes a point-release version, a build timestamp, or a runner
    identifier belongs here, not in :class:`BuildMode`.
    """

    raw_producer: str | None = None       # DW_AT_producer of the first CU
    raw_comment: str | None = None        # ELF .comment section contents
    compiler_version: str | None = None   # extracted version, e.g. "11.4.0"


@dataclass
class BuildMode:
    """Normalized build-mode descriptor.  Stable across CI runners; the
    fields are exactly the dimensions that materially change ABI."""

    compiler_family: CompilerFamily = CompilerFamily.UNKNOWN
    language_std: CxxStandard = CxxStandard.UNKNOWN
    stdlib: StdlibFamily = StdlibFamily.UNKNOWN
    glibcxx_dual_abi: GlibcxxDualAbi = GlibcxxDualAbi.NOT_APPLICABLE
    libcpp_abi_version: int | None = None   # 1, 2 (libc++ inline-NS); None = N/A
    # Non-compared provenance.  Use a default factory so equality on
    # BuildMode is "frozen on the normalized fields only" even though
    # provenance is mutable / per-runner.
    provenance: BuildModeProvenance = field(
        default_factory=BuildModeProvenance, compare=False,
    )


# ---------------------------------------------------------------------------
# Detection helpers (pure functions, easy to unit-test against raw strings)
# ---------------------------------------------------------------------------


# The patterns below capture both shapes GCC emits:
#   1. ELF .comment: ``GCC: (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0``
#   2. DW_AT_producer: ``GNU C++17 11.4.0 -mtune=generic -march=x86-64``
_GCC_PRODUCER = re.compile(
    r"(?:GCC:?|GNU\s+(?:C|C\+\+|C99|Fortran)[\d+]*)\s*\(?[^)]*\)?\s*"
    r"(?P<ver>\d+(?:\.\d+){0,2})",
    re.IGNORECASE,
)
_CLANG_PRODUCER = re.compile(
    r"clang\s+version\s+(?P<ver>\d+(?:\.\d+){0,2})", re.IGNORECASE,
)
# Word boundaries on ``icx``/``icc`` so we don't false-match arbitrary
# substrings (e.g. an unrelated ``pacicx-tool`` or ``pickle``).
_ICX_PRODUCER = re.compile(
    r"(?:Intel\(R\)\s+oneAPI\s+DPC\+\+/C\+\+|\bicx\b)"
    r"\s*[A-Za-z]*\s*(?P<ver>\d+(?:\.\d+){0,2})?",
    re.IGNORECASE,
)
_ICC_PRODUCER = re.compile(
    r"(?:Intel\(R\)\s+C\+\+\s+Compiler|\bicc\b)"
    r"\s*[A-Za-z]*\s*(?P<ver>\d+(?:\.\d+){0,2})?",
    re.IGNORECASE,
)
# MSVC ships several producer-string variants:
#   ``Microsoft (R) Optimizing Compiler``
#   ``Microsoft (R) C/C++ Optimizing Compiler Version 19.35.32217``
# Match any "Microsoft (R) ... Compiler" prefix.
_MSVC_PRODUCER = re.compile(
    r"(?:Microsoft\s*\(R\)\s+[\w/+ ]*?Compiler|^MSVC)"
    r"(?:\s+Version)?\s*(?P<ver>\d+(?:\.\d+){0,2})?",
    re.IGNORECASE,
)


def detect_compiler_family(
    producer: str | None,
    comment: str | None,
) -> tuple[CompilerFamily, str | None]:
    """Return (family, version_string) from raw producer / comment strings.

    Priority order matches real-world ambiguity: ICX strings can contain
    the substring "clang", so we check Intel markers first; MSVC strings
    are unique; classic ICC is checked before generic clang.
    """
    for text in (producer or "", comment or ""):
        if not text:
            continue
        m = _ICX_PRODUCER.search(text)
        # Parentheses required: ``m and (... or ...)``; the un-parenthesized
        # form ``m and X or Y`` parses as ``(m and X) or Y`` and incorrectly
        # classifies any string containing "oneAPI" as ICX even when the
        # ICX regex did not match (e.g. mixed .comment blobs from linked
        # objects, or unrelated tooling that mentions oneAPI in passing).
        if m and ("DPC++" in text or "oneAPI" in text):
            return CompilerFamily.ICX, m.group("ver")
        m = _ICC_PRODUCER.search(text)
        # Classic icc producer strings sometimes omit the literal
        # "Intel" prefix (e.g. ``icc 19.1.3.304``); the regex already
        # requires either the full "Intel(R) C++ Compiler" form or a
        # word-boundary-bounded ``icc`` token, so we only need to guard
        # against an oneAPI / DPC++ string accidentally hitting the
        # generic icc match.
        if m and "DPC++" not in text and "oneAPI" not in text:
            return CompilerFamily.ICC, m.group("ver")
        m = _MSVC_PRODUCER.search(text)
        if m and ("Microsoft" in text or "MSVC" in text):
            return CompilerFamily.MSVC, m.group("ver")
        m = _CLANG_PRODUCER.search(text)
        if m:
            return CompilerFamily.CLANG, m.group("ver")
        m = _GCC_PRODUCER.search(text)
        if m:
            return CompilerFamily.GCC, m.group("ver")
    return CompilerFamily.UNKNOWN, None


# DWARF language tags (from <dwarf.h>) → CxxStandard.  Many real
# compilers stamp coarse tags even for stricter -std= flags; the bucket
# below reflects what is actually observable.  Caller MUST treat
# CXX14_OR_LATER as a lower-bound, not a literal claim of C++14.
_DWARF_LANG_TO_STD: dict[int, CxxStandard] = {
    0x01: CxxStandard.C,                # DW_LANG_C89
    0x02: CxxStandard.CXX98,            # DW_LANG_C_plus_plus (pre-C++03)
    0x0c: CxxStandard.C,                # DW_LANG_C99
    # 0x19 (DW_LANG_C_plus_plus_03) is mapped to CXX98 rather than CXX11
    # because the enum has no CXX03 bucket and CXX98 is the closest
    # pre-C++11 standard; upgrading C++03 binaries to CXX11 would
    # misattribute ABI differences to the wrong build mode.
    0x19: CxxStandard.CXX98,            # DW_LANG_C_plus_plus_03
    0x1a: CxxStandard.CXX11,            # DW_LANG_C_plus_plus_11
    0x21: CxxStandard.CXX14_OR_LATER,   # DW_LANG_C_plus_plus_14
    0x2a: CxxStandard.CXX17,            # DW_LANG_C_plus_plus_17
    0x2b: CxxStandard.CXX20,            # DW_LANG_C_plus_plus_20
    0x2e: CxxStandard.CXX23,            # DW_LANG_C_plus_plus_23
}


def detect_cxx_standard(dwarf_language: int | None) -> CxxStandard:
    """Map a DWARF ``DW_AT_language`` constant to a :class:`CxxStandard`."""
    if dwarf_language is None:
        return CxxStandard.UNKNOWN
    return _DWARF_LANG_TO_STD.get(int(dwarf_language), CxxStandard.UNKNOWN)


# Substrings on mangled names that uniquely identify a stdlib.  The
# B5cxx11 ABI tag is the *primary* libstdc++ dual-ABI signal; namespace
# components like __cxx11 are secondary.
_LIBCXX_NAMESPACE_RE = re.compile(r"_ZN(?:K?)St(\d+)__([12])")
_GLIBCXX_CXX11_TAG = "B5cxx11"
# The libstdc++ ``__cxx11::`` inline-namespace mangles as ``7__cxx11`` in
# Itanium symbols.  Presence is a secondary signal of the C++11 dual-ABI
# when the more specific ``B5cxx11`` ABI tag isn't present on the sample.
_GLIBCXX_CXX11_NAMESPACE = "7__cxx11"


def detect_stdlib_and_abi(
    mangled_symbols: list[str],
) -> tuple[StdlibFamily, GlibcxxDualAbi, int | None]:
    """Infer stdlib family + dual-ABI flag + libc++ ABI version from a
    sample of mangled names. Returns ``(stdlib, glibcxx_dual_abi,
    libcpp_abi_version)``."""
    saw_libstdcxx = False
    saw_libcxx = False
    saw_glibcxx_tag = False
    libcpp_abi: int | None = None

    for sym in mangled_symbols:
        if not sym or not sym.startswith("_Z"):
            continue
        if _GLIBCXX_CXX11_TAG in sym or _GLIBCXX_CXX11_NAMESPACE in sym:
            saw_glibcxx_tag = True
            saw_libstdcxx = True
        m = _LIBCXX_NAMESPACE_RE.match(sym)
        if m:
            saw_libcxx = True
            if libcpp_abi is None:
                # The second capture group is the inline-NS digit.
                libcpp_abi = int(m.group(2))
        # libstdc++ symbols are prefixed _ZNSt without the __[12] suffix.
        elif sym.startswith(("_ZNSt", "_ZNKSt")):
            saw_libstdcxx = True

    if saw_libcxx and not saw_libstdcxx:
        return StdlibFamily.LIBCXX, GlibcxxDualAbi.NOT_APPLICABLE, libcpp_abi
    if saw_libstdcxx:
        abi = (
            GlibcxxDualAbi.CXX11 if saw_glibcxx_tag
            else GlibcxxDualAbi.OLD
        )
        return StdlibFamily.LIBSTDCXX, abi, None
    return StdlibFamily.UNKNOWN, GlibcxxDualAbi.NOT_APPLICABLE, libcpp_abi


def build_mode_from_signals(
    *,
    raw_producer: str | None = None,
    raw_comment: str | None = None,
    dwarf_language: int | None = None,
    mangled_symbols: list[str] | None = None,
) -> BuildMode:
    """Convenience constructor that runs every detector in turn.

    Test inputs go in as raw strings; production callers thread the
    same values from :class:`AbiSnapshot.dwarf` and :class:`ElfMetadata`.
    """
    family, version = detect_compiler_family(raw_producer, raw_comment)
    std = detect_cxx_standard(dwarf_language)
    stdlib, dual_abi, libcpp_abi = detect_stdlib_and_abi(mangled_symbols or [])
    return BuildMode(
        compiler_family=family,
        language_std=std,
        stdlib=stdlib,
        glibcxx_dual_abi=dual_abi,
        libcpp_abi_version=libcpp_abi,
        provenance=BuildModeProvenance(
            raw_producer=raw_producer,
            raw_comment=raw_comment,
            compiler_version=version,
        ),
    )
