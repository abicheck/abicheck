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

"""Source castxml must see ahead of the headers so it can parse glibc's.

castxml emulates GCC, so glibc selects its GCC branches -- and two of them
name types GCC provides as builtins that castxml's Clang lacks on some
targets. Each block below supplies one, and only where it is missing.

**``_Float128`` on targets whose ``long double`` is binary128.** castxml
emulates GCC, so glibc sees ``__GNUC__ >= 13`` and, in C++, assumes the
compiler provides ``_Float128`` as a builtin type
(``bits/floatn.h`` stops typedef'ing it for g++ >= 13). castxml makes that
true on x86-64 by predefining ``_Float128`` to its own builtin
``__castxml_Float128``, backed by Clang's ``__float128``. AArch64 Clang has no
``__float128``, so on AArch64 (and on every other target where ``long
double`` is already IEEE binary128: s390x, RISC-V, LoongArch) castxml defines
nothing. ``<cwchar>`` and ``<cstdlib>`` then fail with ``unknown type name
'_Float128'``, which takes down every C++ header that reaches libstdc++'s
``<string>``/``<memory>``. Measured with castxml 0.6.20260105 (bundled Clang
21.1.8) and with 0.7.0, both emulating ``aarch64-linux-gnu-g++`` 13.

What g++ 13 provides there is a type *distinct* from ``long double``, of the
same format, converting to and from it implicitly. glibc depends on all three
properties: ``math.h`` specializes ``__iseqsig_type`` for both ``long double``
and ``_Float128`` (so an alias is a redefinition error) and implements the
latter by calling ``__iseqsigl`` (so an opaque type has no viable call). A
class with converting constructor and conversion operator has exactly those
properties, which is what the preamble declares.

The guard makes the preamble inert everywhere else: it applies only in C++,
only under an emulated GCC >= 13, only when castxml has not already defined
``_Float128`` (x86-64), and only when ``long double`` is binary128
(``__LDBL_MANT_DIG__ == 113``), the condition under which the conversion
loses nothing. C mode never needs it, because glibc keeps the typedef there.

**AArch64 Advanced SIMD vector builtins.** Same mechanism, second type:
glibc's ``bits/math-vector.h`` (reached from ``<cmath>``/``<math.h>``)
typedefs ``__f32x4_t``/``__f64x2_t`` from GCC's builtin ``__Float32x4_t``/
``__Float64x2_t`` when ``__GNUC_PREREQ (9, 0)``, and from Clang's
``__neon_vector_type__`` spelling otherwise. Clang provides only the latter, so
under GCC emulation every C or C++ header reaching ``<math.h>`` failed on
AArch64. The preamble declares the two names exactly as glibc's own Clang
branch spells the types.

Both blocks are additionally gated on ``__castxml__``, so the file is inert if
anything other than castxml ever reads it.

The preamble is written to its own file, :data:`PREAMBLE_FILENAME`, which
the aggregate header includes first. castxml records each declaration's
*physical* file (it ignores ``#line``), so that reserved basename is what
lets the one synthetic-declaration filter (``extract.headers.castxml.
location.is_builtin_element``) drop the stand-in *and* the members castxml
synthesizes for it (copy/move constructors, destructor: ``artificial="1"``)
from every declaration list, exactly as it drops castxml's own ``<builtin>``
declarations. Where a declaration *uses* the stand-in, castxml reports a
``Struct``; the type resolver
(``extract.headers.castxml.type_resolution``) reports it as the fundamental
``_Float128`` it stands for, with no record identity, exactly as castxml's own
builtin reads on x86-64.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "CASTXML_HEADER_PREAMBLE",
    "FLOAT128_SPELLING",
    "FLOAT128_STANDIN",
    "PREAMBLE_FILENAME",
    "write_castxml_aggregate",
]

#: Basename of the file the preamble is written to. Reserved: a declaration
#: in a file of this name is synthetic, never part of any library's surface.
PREAMBLE_FILENAME = "__abicheck_castxml_preamble.hpp"

#: The record the preamble declares, and the spelling every reader reports
#: in its place -- the one castxml's own builtin already reports on x86-64,
#: so a library's ``_Float128`` API reads identically on every target.
FLOAT128_STANDIN = "__abicheck_Float128"
FLOAT128_SPELLING = "_Float128"

#: Included first by every aggregate header castxml parses, C and C++ alike;
#: each block guards its own language and target.
CASTXML_HEADER_PREAMBLE = """\
#if defined(__castxml__) && defined(__cplusplus) && defined(__GNUC__) && __GNUC__ >= 13 \\
    && !defined(_Float128) && defined(__LDBL_MANT_DIG__) && __LDBL_MANT_DIG__ == 113
struct __abicheck_Float128 {
  long double __abicheck_value;
  constexpr __abicheck_Float128() noexcept : __abicheck_value() {}
  constexpr __abicheck_Float128(long double __x) noexcept : __abicheck_value(__x) {}
  constexpr operator long double() const noexcept { return __abicheck_value; }
};
#define _Float128 __abicheck_Float128
#endif
#if defined(__castxml__) && defined(__aarch64__) && defined(__GNUC__) && __GNUC__ >= 9
typedef __attribute__((__neon_vector_type__(4))) float __Float32x4_t;
typedef __attribute__((__neon_vector_type__(2))) double __Float64x2_t;
#endif
"""


def write_castxml_aggregate(headers: Iterable[Path], suffix: str) -> Path:
    """Write the header castxml parses: the preamble, then every *headers* entry.

    Both files live in one fresh private directory -- the preamble under its
    reserved basename, the aggregate as ``aggregate<suffix>`` -- so the caller
    removes the whole thing with ``shutil.rmtree(path.parent)``.
    """
    agg_dir = Path(tempfile.mkdtemp(prefix="abicheck_castxml_"))
    preamble = agg_dir / PREAMBLE_FILENAME
    preamble.write_text(CASTXML_HEADER_PREAMBLE)
    # ``#line 1`` restores the aggregate-TU layout every diagnostic consumer
    # relies on -- header ``i`` on line ``i+1`` -- so the preamble include
    # costs no line (``unparseable_header_fallback._attribute`` maps a failing
    # aggregate frame back to its header by that line number alone).
    lines = [f'#include "{preamble}"\n', "#line 1\n"]
    lines += [f'#include "{h.resolve()}"\n' for h in headers]
    agg_path = agg_dir / f"aggregate{suffix}"
    agg_path.write_text("".join(lines))
    return agg_path
