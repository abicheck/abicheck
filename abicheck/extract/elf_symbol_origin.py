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

"""Which dependency runtime a symbol name most likely comes from.

Moved out of ``elf_metadata.py`` (it classifies binary symbol names, an
``extract`` responsibility, and that module is at its no-growth ratchet).
:func:`guess_symbol_origin` is a heuristic: it names the runtime a symbol
likely comes from, or ``None`` when it is native to the library being read.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from .mangled_foreign_template_args import has_foreign_template_argument
from .stdlib_local_origin import (
    find_cxx_stdlib as _find_cxx_stdlib,
    find_libcxx as _find_libcxx,
    stdlib_local_static_origin,
)

__all__ = ["guess_symbol_origin"]


def _find_fundamental_cxx_rtti_runtime(needed_libs: list[str]) -> str | None:
    """Find the C++ runtime that owns fundamental RTTI, excluding libc++abi."""
    for lib in needed_libs:
        if "stdc++" in os.path.basename(lib):
            return lib
    for lib in needed_libs:
        if os.path.basename(lib).startswith("libc++."):
            return lib
    return None


def _is_libmvec_vector_symbol(name: str) -> bool:
    """Return true for glibc libmvec vector ABI symbols, not C++ guard vars."""
    if not name.startswith("_ZGV"):
        return False
    if name.startswith("_ZGVZ"):
        return False
    return len(name) > 4 and name[4] in {"b", "c", "d", "e", "n", "s"}


# Lookup table: (prefix_tuple, finder_fn_or_None, default_if_no_finder_match)
# When finder_fn is None, default is returned unconditionally.
_FinderFn = Callable[[list[str]], str | None]
_ORIGIN_PREFIX_TABLE: list[tuple[tuple[str, ...], _FinderFn | None, str]] = [
    # libc++ inline namespace __1 — must be checked BEFORE generic _ZNSt
    (("_ZNSt3__1", "_ZNKSt3__1"), _find_libcxx, "libc++.so.1"),
    # C++ stdlib symbols (libstdc++ / libc++)
    (
        (
            "_ZNSt",
            "_ZNKSt",
            "_ZSt",
            "_ZTISt",
            "_ZTSSt",
            "_ZTVSt",
            "_ZTIS",
            "_ZTSS",
            "_ZTVS",
            "_ZTINSt",
            "_ZTSNSt",
            "_ZTVNSt",
            "_ZTIN9__gnu_cxx",
            "_ZTSN9__gnu_cxx",
            "_ZTVN9__gnu_cxx",
            "_ZTIN10__cxxabiv",
            "_ZTSN10__cxxabiv",
            "_ZTVN10__cxxabiv",
            # Guard variables of std static members (``_ZGVNSt5ctype...2idE``).
            "_ZGVNSt",
            "_ZGVN9__gnu_cxx",
            "_ZGVN10__cxxabiv",
        ),
        _find_cxx_stdlib,
        "libstdc++.so.6",
    ),
    # C++ operator new / delete (Itanium ABI)
    (
        ("_Znwm", "_Znwj", "_Znam", "_Znaj", "_ZdlPv", "_ZdaPv", "_ZnwmSt", "_ZnamSt"),
        _find_cxx_stdlib,
        "libstdc++.so.6",
    ),
    # Intel SVML
    (("__svml_",), None, "<intel-compiler-rt>"),
    # x87 math helpers (libgcc.a static)
    (("ix86_",), None, "libgcc.a (static)"),
    # libm SIMD helpers
    (("__libm_sse2_", "__libm_avx_"), None, "libm.so.6"),
    # GCC runtime support
    (("__cpu_model", "__cpu_features"), None, "libgcc_s.so.1"),
    # GNU libc internal
    (("__libc_", "__glibc_"), None, "libc.so.6"),
]


_FUNDAMENTAL_CXX_RTTI_SINGLE_CHAR_TYPE_CODES: frozenset[str] = frozenset(
    {
        "v",  # void
        "w",  # wchar_t
        "b",  # bool
        "c",  # char
        "a",  # signed char
        "h",  # unsigned char
        "s",  # short
        "t",  # unsigned short
        "i",  # int
        "j",  # unsigned int
        "l",  # long
        "m",  # unsigned long
        "x",  # long long
        "y",  # unsigned long long
        "n",  # __int128
        "o",  # unsigned __int128
        "f",  # float
        "d",  # double
        "e",  # long double
        "g",  # __float128
        "z",  # ellipsis
    }
)

_FUNDAMENTAL_CXX_RTTI_MULTI_CHAR_TYPE_CODES: frozenset[str] = frozenset(
    {
        "Dn",  # std::nullptr_t
        "Du",  # char8_t
        "Di",  # char32_t
        "Ds",  # char16_t
        "Dh",  # half-precision floating point
        "Df",  # decimal32
        "Dd",  # decimal64
        "De",  # decimal128
    }
)

_CXX_SIZED_FLOAT_TYPE_CODE_RE = re.compile(r"DF[0-9]+(?:_|[A-Za-z]+)")

_FUNDAMENTAL_CXX_RTTI_TYPE_MODIFIERS: frozenset[str] = frozenset(
    {
        "P",  # pointer
        "R",  # lvalue reference
        "O",  # rvalue reference
        "K",  # const qualifier
        "V",  # volatile qualifier
        "r",  # restrict qualifier
    }
)


def _is_fundamental_cxx_type_encoding(encoding: str) -> bool:
    """Return True for builtin Itanium C++ type encodings and simple wrappers."""
    while encoding:
        if encoding in _FUNDAMENTAL_CXX_RTTI_SINGLE_CHAR_TYPE_CODES:
            return True
        if encoding in _FUNDAMENTAL_CXX_RTTI_MULTI_CHAR_TYPE_CODES:
            return True
        if _CXX_SIZED_FLOAT_TYPE_CODE_RE.fullmatch(encoding):
            return True
        if encoding[0] not in _FUNDAMENTAL_CXX_RTTI_TYPE_MODIFIERS:
            return False
        encoding = encoding[1:]
    return False


def _is_fundamental_cxx_rtti_symbol(name: str) -> bool:
    """Return True for libstdc++ RTTI/typeinfo-name symbols for builtin types."""
    if not (name.startswith("_ZTI") or name.startswith("_ZTS")):
        return False
    return _is_fundamental_cxx_type_encoding(name[4:])


def guess_symbol_origin(name: str, needed_libs: list[str]) -> str | None:
    """Guess which dependency library a symbol likely originates from.

    Analyses the symbol's mangled name prefix to determine whether it is likely
    exported by a well-known runtime dependency (libstdc++, libgcc, libc) rather
    than natively defined by the library being inspected.

    Returns a library name hint (e.g. ``'libstdc++.so.6'``) or ``None`` if the
    symbol appears to be native to this library.

    This is a heuristic — false positives are possible for symbols that happen to
    share a prefix with standard-library symbols but are defined by the library
    itself.  The result is used to annotate the ``origin_lib`` field of
    :class:`ElfSymbol`; it is informational and never suppresses real changes.
    """
    if _is_fundamental_cxx_rtti_symbol(name):
        return _find_fundamental_cxx_rtti_runtime(needed_libs) or "libstdc++.so.6"

    if _is_libmvec_vector_symbol(name):
        return "libmvec.so.1"

    # A std template instantiated over one of this library's own types
    # (``std::_Sp_counted_deleter<dnnl_stream*, ...>``) is emitted by this
    # library, not exported by the C++ runtime -- which never saw the type.
    if has_foreign_template_argument(name):
        return None

    # Function-local std template statics (``_ZZNSt...``): see stdlib_local_origin.
    local_static_origin = stdlib_local_static_origin(name, needed_libs)
    if local_static_origin is not None:
        return local_static_origin

    for prefixes, finder_fn, default in _ORIGIN_PREFIX_TABLE:
        if name.startswith(prefixes):
            if finder_fn is not None:
                found = finder_fn(needed_libs)
                if found is not None:
                    return found
            return default

    return None  # likely native to this library
