# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Build-mode facts as data.

The compiler family, C++ standard, standard-library implementation and
glibcxx dual-ABI selection a build was made with, plus the provenance of how
each was determined. ``abicheck.build_mode`` detects them and re-exports these
shapes (ADR-061 Phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompilerFamily(str, Enum):
    """Normalized compiler identity.  Patch / minor version intentionally
    NOT captured here — those live in :attr:`BuildMode.provenance` so
    snapshots stay equal across CI runners with different point releases."""

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"
    ICX = "icx"  # Intel oneAPI DPC++/C++ (clang-based)
    ICC = "icc"  # Classic Intel C++ compiler (pre-oneAPI)
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
    CXX14_OR_LATER = "c++14_or_later"  # clang ≤ 16 ambiguity bucket
    CXX17 = "c++17"
    CXX20 = "c++20"
    CXX23 = "c++23"
    UNKNOWN = "unknown"


class GlibcxxDualAbi(str, Enum):
    """libstdc++ dual-ABI flavor (only meaningful when stdlib == LIBSTDCXX)."""

    CXX11 = "cxx11"  # _GLIBCXX_USE_CXX11_ABI=1 (default since gcc 5)
    OLD = "old"  # _GLIBCXX_USE_CXX11_ABI=0 (legacy)
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

    raw_producer: str | None = None  # DW_AT_producer of the first CU
    raw_comment: str | None = None  # ELF .comment section contents
    compiler_version: str | None = None  # extracted version, e.g. "11.4.0"


@dataclass
class BuildMode:
    """Normalized build-mode descriptor.  Stable across CI runners; the
    fields are exactly the dimensions that materially change ABI."""

    compiler_family: CompilerFamily = CompilerFamily.UNKNOWN
    language_std: CxxStandard = CxxStandard.UNKNOWN
    stdlib: StdlibFamily = StdlibFamily.UNKNOWN
    glibcxx_dual_abi: GlibcxxDualAbi = GlibcxxDualAbi.NOT_APPLICABLE
    libcpp_abi_version: int | None = None  # 1, 2 (libc++ inline-NS); None = N/A
    # Non-compared provenance.  Use a default factory so equality on
    # BuildMode is "frozen on the normalized fields only" even though
    # provenance is mutable / per-runner.
    provenance: BuildModeProvenance = field(
        default_factory=BuildModeProvenance,
        compare=False,
    )
