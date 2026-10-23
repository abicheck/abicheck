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

"""Recover the C++ dialect and target ISA flags a binary was built with, so a
header parse of that binary uses the same language mode as its build.

Without build data (``compile_commands.json``/``--build-info``) the header
frontend parses in its default dialect, which is C++17 on modern GCC. A
library whose public headers use C++20 syntax (``concept``, ``requires``)
that the header-level heuristic does not see then fails to parse, and so
does a header that uses intrinsics guarded by ``__AVX2__`` when the
library was built with ``-mavx2``.

The compiler records what it was given in the image's own DWARF:

* ``DW_AT_producer`` begins with the language, e.g. ``GNU C++20 13.3.0``
  for ``-std=c++20`` and ``GNU C++17`` for the default. With
  ``-grecord-gcc-switches`` it also carries the full flag list, including
  ``-std=`` and the ``-m`` ISA flags; GCC emits the target ``-m`` flags
  (``-mavx2``, ``-march=``, ``-mtune=``) by default.
* ``DW_AT_language`` is the coarse fallback. GCC maps every C++ standard
  from C++14 on to ``DW_LANG_C_plus_plus_14``, so only ``C++20``/``C++23``
  codes are unambiguous there; clang stamps the same ambiguous code.

Only the producer string of the image's best-ranked CU is read; a stripped
image with no DWARF yields no dialect, and the header parse keeps its
existing defaults.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..compile_context import CompileContext
    from ..model import AbiSnapshot

__all__ = [
    "BinaryDialect",
    "binary_dialect_compile",
    "binary_dialect_option_tokens",
    "header_parse_option_tokens",
    "parse_producer_dialect",
    "read_elf_binary_dialect",
    "record_implied_options",
]

#: ``GNU C++20`` / ``GNU C17`` language token at the start of a GCC producer.
_GNU_LANG_TOKEN = re.compile(r"\bGNU (C\+\+|C)(\w+)")
#: ``-std=c++20`` / ``-std=gnu11`` as recorded by ``-grecord-gcc-switches``.
_STD_FLAG = re.compile(r"^-std=(?:gnu|c)(\+\+)?(\w+)$", re.IGNORECASE)
#: Standard-revision spelling -> year. ``2a``/``2b``/``2c`` are the pre-C++23
#: draft spellings GCC and clang still accept (``-std=c++2b``).
_STD_YEAR: dict[str, int] = {
    "0x": 11,
    "11": 11,
    "1y": 14,
    "14": 14,
    "1z": 17,
    "17": 17,
    "2a": 20,
    "20": 20,
    "2b": 23,
    "23": 23,
}
#: ``-m`` flags that pin the target ABI or multilib rather than the ISA.
#: Forwarding them would make castxml require 32-bit or x32 headers the host
#: may not have, so they are never inferred.
_TARGET_ABI_FLAGS = frozenset({"-m32", "-m64", "-mx32"})
#: Dialect a binary of C++20 or later is parsed with. castxml has no C++23
#: mode; ``gnu++20`` parses C++23 code that stays within C++20 and fails
#: loudly on the rest, as it does today.
_INFERRED_STD_FLAG = "-std=gnu++20"
_INFERRED_CXX_YEAR = 20


@dataclass(frozen=True)
class BinaryDialect:
    """What a binary's compile recorded about its language and target.

    ``is_cxx`` is ``None`` when no signal names a language; ``cxx_year`` is
    ``None`` when the signal names C++ without a usable standard (clang's
    ambiguous ``DW_LANG_C_plus_plus_14``, or a C++98/11/14 build that needs
    no inferred flag). ``source`` names the signal that decided it.
    """

    is_cxx: bool | None = None
    cxx_year: int | None = None
    source: str | None = None
    machine_flags: tuple[str, ...] = ()


def _year_for(token: str) -> int | None:
    return _STD_YEAR.get(token.lower())


def _dwarf_language_dialect(dwarf_language: int | None) -> BinaryDialect:
    from ..build_mode import detect_cxx_standard
    from ..model.build_mode_facts import CxxStandard

    std = detect_cxx_standard(dwarf_language)
    if std is CxxStandard.UNKNOWN:
        return BinaryDialect()
    if std is CxxStandard.C:
        return BinaryDialect(is_cxx=False, source="dwarf")
    years = {
        CxxStandard.CXX20: 20,
        CxxStandard.CXX23: 23,
        CxxStandard.CXX17: 17,
        CxxStandard.CXX14: 14,
    }
    # CXX14_OR_LATER (clang's stamp for every C++14+ standard) and the
    # pre-C++14 buckets carry no year this module can rely on.
    return BinaryDialect(is_cxx=True, cxx_year=years.get(std), source="dwarf")


def parse_producer_dialect(
    producer: str | None, dwarf_language: int | None = None
) -> BinaryDialect:
    """The dialect a compile recorded, from its ``DW_AT_producer`` string and
    ``DW_AT_language`` code.

    Precedence: an explicit ``-std=`` the producer recorded, then the
    language token GCC always writes, then the DWARF language code. A
    producer that names no language falls through to the DWARF code.
    """
    if not producer:
        return _dwarf_language_dialect(dwarf_language)
    tokens = producer.split()
    machine_flags = tuple(
        dict.fromkeys(
            token
            for token in tokens
            if token.startswith("-m")
            and len(token) > 2
            and token not in _TARGET_ABI_FLAGS
        )
    )
    std_flags = [m for t in tokens if (m := _STD_FLAG.match(t))]
    if std_flags:
        match = std_flags[-1]
        is_cxx = match.group(1) is not None
        return BinaryDialect(
            is_cxx=is_cxx,
            cxx_year=_year_for(match.group(2)) if is_cxx else None,
            source="producer",
            machine_flags=machine_flags,
        )
    lang = _GNU_LANG_TOKEN.search(producer)
    if lang is not None:
        is_cxx = lang.group(1) == "C++"
        return BinaryDialect(
            is_cxx=is_cxx,
            cxx_year=_year_for(lang.group(2)) if is_cxx else None,
            source="producer",
            machine_flags=machine_flags,
        )
    fallback = _dwarf_language_dialect(dwarf_language)
    return BinaryDialect(
        is_cxx=fallback.is_cxx,
        cxx_year=fallback.cxx_year,
        source=fallback.source,
        machine_flags=machine_flags,
    )


def _option_key(token: str) -> str:
    """``-march=native`` -> ``-march``: the option a token sets."""
    return token.split("=", 1)[0]


def binary_dialect_option_tokens(
    dialect: BinaryDialect,
    *,
    explicit_tokens: Iterable[str],
    explicit_std: bool,
    cxx_mode: bool,
) -> tuple[str, ...]:
    """Option tokens a header parse of a binary built as *dialect* appends.

    ``cxx_mode`` is whether the header parse already runs as C++ (the
    caller's ``_resolve_force_cpp`` decision). A C++20+ binary adds
    ``-x c++ -std=gnu++20`` only then: the binary's dialect refines the
    standard of a C++ parse and never turns a C header into a C++ one, which
    is the guard ``_exported_symbols_indicate_cpp`` exists to keep.

    The producer's ``-m`` flags are appended whatever the language, since the
    intrinsics a header guards with ``__AVX2__`` etc. depend only on the
    target. A flag the caller already gave (same option name) is left alone.
    """
    user_keys = {_option_key(token) for token in explicit_tokens}
    tokens: list[str] = []
    if (
        cxx_mode
        and not explicit_std
        and dialect.is_cxx
        and dialect.cxx_year is not None
        and dialect.cxx_year >= _INFERRED_CXX_YEAR
    ):
        tokens += ["-x", "c++", _INFERRED_STD_FLAG]
    tokens += [
        flag for flag in dialect.machine_flags if _option_key(flag) not in user_keys
    ]
    return tuple(tokens)


def read_elf_binary_dialect(
    path: Path, debug_path: Path | None = None
) -> BinaryDialect:
    """The dialect of an ELF image, read from its own DWARF producer.

    *debug_path* is the detached debug file, read only when the image itself
    carries no producer (a stripped release build with a build-id debug
    file). An image with no DWARF at all yields an empty :class:`BinaryDialect`.
    """
    from .build_mode_capture import read_elf_build_signals

    signals = read_elf_build_signals(path, read_dwarf=True, debug_path=debug_path)
    if signals is None:
        return BinaryDialect()
    return parse_producer_dialect(signals.producer, signals.dwarf_language)


def header_parse_option_tokens(
    path: Path,
    *,
    debug_path: Path | None,
    lang: str | None,
    headers: Sequence[Path],
    gcc_options: str | None,
    gcc_option_tokens: Sequence[str],
) -> tuple[str, ...]:
    """Option tokens to append to a header parse of the ELF image *path*.

    *lang* is the language the caller forced (``None`` when auto-detected),
    as ``dumper.dump`` receives it. The C++ mode decision is the same
    :func:`~abicheck.extract.headers.toolchain._resolve_force_cpp` the
    frontends make, fed the image's own export table, so a binary's dialect
    is applied only to a parse that is already C++.
    """
    from .._compiler_options import has_explicit_std, split_gcc_options
    from .elf_symbol_classify import _pyelftools_exported_symbols
    from .headers.toolchain import _resolve_force_cpp

    dialect = read_elf_binary_dialect(path, debug_path)
    explicit_tokens = [*split_gcc_options(gcc_options or ""), *gcc_option_tokens]
    explicit_std = has_explicit_std(gcc_options, tuple(gcc_option_tokens))
    needs_std = (
        not explicit_std
        and dialect.is_cxx is True
        and dialect.cxx_year is not None
        and dialect.cxx_year >= _INFERRED_CXX_YEAR
    )
    cxx_mode = False
    if needs_std:
        exported_dynamic, exported_static = _pyelftools_exported_symbols(path)
        cxx_mode = _resolve_force_cpp(
            lang,
            list(headers),
            gcc_options,
            tuple(gcc_option_tokens),
            frozenset(exported_dynamic | exported_static),
        )
    return binary_dialect_option_tokens(
        dialect,
        explicit_tokens=explicit_tokens,
        explicit_std=explicit_std,
        cxx_mode=cxx_mode,
    )


def binary_dialect_compile(
    compile: CompileContext | None,
    path: Path,
    *,
    lang: str,
    lang_explicit: bool,
    headers: list[Path] | None,
) -> tuple[CompileContext | None, tuple[str, ...]]:
    """*compile* with the option tokens an ELF image's own build implies
    appended, and those implied tokens.

    Returns *compile* itself when nothing is implied, so the common path
    keeps its identity. An image with no DWARF producer (stripped, no debug
    file) implies nothing, and the parse keeps its defaults.
    """
    from ..compile_context import CompileContext

    cc = compile if compile is not None else CompileContext()
    implied = header_parse_option_tokens(
        path,
        debug_path=None,
        lang=lang if (lang_explicit or lang == "c") else None,
        headers=list(headers or []),
        gcc_options=cc.gcc_options,
        gcc_option_tokens=cc.gcc_option_tokens,
    )
    if not implied:
        return compile, ()
    return replace(cc, gcc_option_tokens=(*cc.gcc_option_tokens, *implied)), implied


def record_implied_options(snap: AbiSnapshot, implied: tuple[str, ...]) -> AbiSnapshot:
    """Stamp the flags a binary's own build implied onto the snapshot's
    ``ast_toolchain`` provenance, so the parse is visible in the report.

    The implied ``-std=`` was not given by the caller, so
    ``language_standard_explicit`` (stamped from the full token list) is reset
    to ``"0"``: it records a pin the caller asserted, not one inferred here.
    """
    if implied:
        if any(token.startswith("-std=") for token in implied):
            snap.ast_toolchain["language_standard_explicit"] = "0"
        snap.ast_toolchain["implied_options"] = " ".join(implied)
    return snap
