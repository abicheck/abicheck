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

"""The compiler command castxml emulates, and which flags it must carry.

``castxml --castxml-cc-<id> <cc>`` makes castxml *emulate* a real compiler.
castxml runs that command (never on the headers themselves) to learn its
predefined macros and system include directories, then configures its own
bundled Clang to match. That is why abicheck uses emulation at all: the
headers are parsed with the ``__GNUC__``/``_GLIBCXX_*``/target macros and
the libstdc++/libc search path the library's consumers actually compile
against, rather than castxml's bundled Clang's own defaults.

castxml passes the emulated compiler **only** the arguments inside
``--castxml-cc-<id> "(" <cc> <args>... ")"``. Anything after that group
reaches castxml's own parser but not the query. So a flag that changes what
the compiler reports must be written *inside* the group as well, or the
parse runs under one configuration and the macros and include paths of
another. Measured with castxml 0.7.0 and g++ 13::

    castxml --castxml-cc-gnu g++ -std=c++20 ...            -> __cplusplus 201703L,
                                                             __cpp_concepts unset
    castxml --castxml-cc-gnu "(" g++ -std=c++20 ")" ...     -> 202002L / 202002L

With the first form, libstdc++'s ``<concepts>`` sees C++17 and declares no
``std::integral``, so every C++20 header that uses it fails to parse (the
SVS core headers failed with 20 errors). The same holds for ``--sysroot``
and ``-nostdinc`` (which system directories are reported), ``-m*`` target
flags (``__AVX2__``, ``__x86_64__`` vs ``__i386__``), and ``-f`` flags that
set feature macros (``__cpp_sized_deallocation``, ``__EXCEPTIONS``,
``_OPENMP``).

:func:`emulated_compiler_command` is the one place that decides which of a
run's compiler arguments go inside the group. Every castxml command builder
calls it rather than choosing for itself.

**Why an allowlist and not "every argument".** The arguments a run gives
are written for castxml's bundled *Clang*. The emulated compiler is often
GCC, which rejects Clang-only spellings (``--target=``, ``-stdlib=``,
``-fno-delayed-template-parsing``). If the query fails, castxml fails. So
only arguments that change predefined macros or system search paths, and
that the emulated compiler family is known to accept, are copied.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "emulated_compiler_command",
    "emulation_arguments",
]

#: ``-f`` flags GCC and Clang both accept that change a predefined macro.
#: Each name is also accepted with a ``no-`` prefix.
_FEATURE_MACRO_F_FLAGS: frozenset[str] = frozenset(
    {
        "exceptions",  # __EXCEPTIONS, __cpp_exceptions
        "rtti",  # __GXX_RTTI, __cpp_rtti
        "openmp",  # _OPENMP
        "sized-deallocation",  # __cpp_sized_deallocation
        "aligned-new",  # __cpp_aligned_new
        "char8_t",  # __cpp_char8_t
        "coroutines",  # __cpp_impl_coroutine (GCC 10)
        "concepts",  # __cpp_concepts (GCC 9, -std=c++2a)
        "unsigned-char",  # __CHAR_UNSIGNED__
        "signed-char",
        "short-wchar",  # __WCHAR_MAX__
        "short-enums",
        "pic",  # __pic__
        "PIC",
        "pie",  # __pie__
        "PIE",
        "fast-math",  # __FAST_MATH__
        "finite-math-only",  # __FINITE_MATH_ONLY__
        "gnu89-inline",  # __GNUC_GNU_INLINE__
        "threadsafe-statics",
    }
)

#: Clang-driver spellings GCC rejects; copied only when the emulated
#: compiler is itself Clang-family.
_CLANG_ONLY_PREFIXES: tuple[str, ...] = ("--target=", "-stdlib=")

#: Flags that take their value as the *next* argument.
_SEPARATE_VALUE_FLAGS: frozenset[str] = frozenset({"-x", "--sysroot", "-isysroot"})

#: Clang-driver flags whose operand is the *next* argument and which only
#: castxml's parser should see: ``-mllvm <backend-option>`` (it would
#: otherwise match the ``-m*`` target-flag rule and reach GCC without its
#: operand) and ``-Xclang <cc1-option>`` (whose operand may itself be
#: ``-mllvm``). The pair is consumed together and never copied.
_PARSER_ONLY_VALUE_FLAGS: frozenset[str] = frozenset({"-mllvm", "-Xclang"})


def _is_clang_family(cc_bin: str) -> bool:
    name = cc_bin.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return "clang" in name


#: Exact GNU spellings that change predefined macros or search paths.
_GNU_EXACT_FLAGS: frozenset[str] = frozenset(
    {"-nostdinc", "-nostdinc++", "-ansi", "-pthread"}
)
#: GNU prefixes that do (``-O`` sets ``__OPTIMIZE__``/``__OPTIMIZE_SIZE__``).
_GNU_PREFIXES: tuple[str, ...] = ("-std=", "--sysroot=", "-O")


def _is_target_flag(token: str) -> bool:
    """``-m32``, ``-march=...``, ``-mavx2``, ``-mno-sse4.2``; never
    ``-mllvm`` (parser-only) nor ``-MD``/``-MF`` (upper case, excluded by
    construction)."""
    return token.startswith("-m") and len(token) > 2 and not token.startswith("-mllvm")


def _is_feature_macro_flag(token: str) -> bool:
    if not token.startswith("-f"):
        return False
    name = token[2:]
    return name.removeprefix("no-") in _FEATURE_MACRO_F_FLAGS


def _gnu_argument_kept(token: str, *, clang_family: bool) -> bool:
    return (
        token in _GNU_EXACT_FLAGS
        or token.startswith(_GNU_PREFIXES)
        or _is_target_flag(token)
        or _is_feature_macro_flag(token)
        or (clang_family and token.startswith(_CLANG_ONLY_PREFIXES))
    )


def _msvc_argument_kept(token: str) -> bool:
    # ``_MSVC_LANG`` follows ``/std:``; ``__cplusplus`` follows it only
    # under ``/Zc:__cplusplus``.
    lowered = token.lower()
    return lowered.startswith(("/std:", "-std:")) or lowered in (
        "/zc:__cplusplus",
        "-zc:__cplusplus",
    )


def emulation_arguments(
    arguments: Sequence[str], *, cc_bin: str, cc_id: str
) -> list[str]:
    """The subset of *arguments* the emulated compiler must also be given.

    *arguments* are the compiler arguments of the castxml run itself (what
    follows the emulation group), in order. Order is preserved, so a later
    ``-std=`` still wins inside the group exactly as it does outside it. A
    separate-value flag (``-x c``, ``--sysroot DIR``) is copied together with
    its value.
    """
    clang_family = _is_clang_family(cc_bin)
    kept: list[str] = []
    tokens = list(arguments)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _PARSER_ONLY_VALUE_FLAGS:
            index += 2
        elif token in _SEPARATE_VALUE_FLAGS and index + 1 < len(tokens):
            if _value_flag_kept(cc_id):
                kept += [token, tokens[index + 1]]
            index += 2
        else:
            if _single_argument_kept(token, cc_id=cc_id, clang_family=clang_family):
                kept.append(token)
            index += 1
    return kept


def _value_flag_kept(cc_id: str) -> bool:
    # GCC and Clang both accept all three (GCC's -isysroot moves the
    # system search path just as Clang's does); MSVC accepts none.
    return cc_id != "msvc"


def _single_argument_kept(token: str, *, cc_id: str, clang_family: bool) -> bool:
    if cc_id == "msvc":
        return _msvc_argument_kept(token)
    return _gnu_argument_kept(token, clang_family=clang_family)


def emulated_compiler_command(
    cc_bin: str, cc_id: str, arguments: Sequence[str]
) -> list[str]:
    """The argv tokens that follow ``--castxml-cc-<id>``.

    A bare ``[cc_bin]`` when none of *arguments* changes what the compiler
    reports, so the common invocation keeps its long-standing shape.
    Otherwise the parenthesised group castxml documents for "a compiler
    command with arguments": ``["(", cc_bin, *kept, ")"]``.
    """
    kept = emulation_arguments(arguments, cc_bin=cc_bin, cc_id=cc_id)
    if not kept:
        return [cc_bin]
    return ["(", cc_bin, *kept, ")"]
