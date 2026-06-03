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

"""ELF ABI-relevance filtering shared by symbol and DWARF paths."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

# ELF symbol types (STT_*) that represent a callable function surface and a
# data/variable surface respectively. ``STT_NOTYPE`` is deliberately accepted by
# *both*: assembly stubs, IFUNC resolvers and stripped dynamic entries are
# frequently emitted as NOTYPE, so excluding them would drop real ABI. The minor
# over-inclusion (a NOTYPE name counting toward both sets) is harmless for the
# membership / retention checks these sets feed. ``SymbolType`` is a ``str``
# enum, so these string values compare equal to its members.
FUNCTION_SYMBOL_TYPES: frozenset[str] = frozenset({"func", "ifunc", "notype"})
VARIABLE_SYMBOL_TYPES: frozenset[str] = frozenset({"object", "tls", "common", "notype"})

# Prefixes that identify GCC/compiler-internal symbols which may leak into
# .dynsym through statically-linked runtime (e.g. libgcc_s, SVML).
_GCC_INTERNAL_PREFIXES = (
    "ix86_",
    "x86_64_",
    "__cpu_model",
    "__cpu_features",
    "_ZGV",          # GCC SIMD vector variants (e.g. _ZGVbN2v_sin)
    "__svml_",       # Intel Short Vector Math Library
    "__libm_sse2_",
    "__libm_avx_",
)

# Prefixes that identify transitive C++ standard-library symbols which may
# appear in .dynsym via weak linkage (libstdc++ / libc++).
_STDLIB_PREFIXES = (
    "std::",
    "__gnu_cxx::",
    "__gnu_debug::",
    "__cxxabiv1::",
    "__cxx11::",
    "_ZNSt",              # std:: namespace members (libstdc++)
    "_ZNKSt",             # const std:: methods
    "_ZNVSt",             # volatile std:: methods
    "_ZNRSt",             # ref-qualified std:: methods
    "_ZNKRSt",            # const/ref-qualified std:: methods
    "_ZNVRSt",            # volatile/ref-qualified std:: methods
    "_ZNSt3__1",          # libc++ inline-namespace __1
    "_ZdlPv",             # operator delete(void*)
    "_ZnwSt",             # operator new(std::size_t)
    "_ZnaSt",             # operator new[](std::size_t)
    "_ZdaPv",             # operator delete[](void*)
    "_ZTVN10__cxxabiv",   # vtables for RTTI (typeinfo infrastructure)
    "_ZSt",               # std:: global symbols (e.g. _ZSt4cout)
)

_STDLIB_RTTI_PREFIXES = (
    "_ZTISt",             # typeinfo for std::* / std::exception, etc.
    "_ZTSSt",             # typeinfo name for std::* / std::exception, etc.
    "_ZTINSt",            # typeinfo for nested std::* names
    "_ZTSNSt",            # typeinfo name for nested std::* names
    "_ZTIN9__gnu_cxx",
    "_ZTSN9__gnu_cxx",
    "_ZTIN10__cxxabiv",
    "_ZTSN10__cxxabiv",
    "_ZTIN7__cxx11",
    "_ZTSN7__cxx11",
)


def is_abi_relevant_elf_symbol(name: str) -> bool:
    """Return False for ELF symbols that are not the library's own ABI.

    This filter must be shared by both the ELF-only and DWARF-backed snapshot
    paths. Otherwise a weak transitive libstdc++ export can be excluded from
    symbols-only reports yet re-enter as a ``PUBLIC`` DWARF function, producing
    false ``FUNC_REMOVED`` and type-reachability findings.
    """
    if not name:
        return False

    for prefix in _GCC_INTERNAL_PREFIXES:
        if name.startswith(prefix):
            return False

    for prefix in _STDLIB_PREFIXES:
        if name.startswith(prefix):
            return False

    for prefix in _STDLIB_RTTI_PREFIXES:
        if name.startswith(prefix):
            return False

    # Private C symbols with __ as a namespace separator
    # (e.g. H5C__flush_marked_entries, MPI__send). C++ mangled names start
    # with _Z and are handled separately above.
    if not name.startswith("_Z") and "__" in name[2:]:
        return False

    return True


def exported_symbol_names(
    elf: Any,
    symbol_types: Collection[str],
    *,
    abi_relevant_only: bool = False,
) -> set[str]:
    """Return dynamic-export names of the requested ``symbol_types`` from *elf*.

    *elf* is an :class:`~abicheck.elf_metadata.ElfMetadata` (or ``None``).
    Returns an empty set when no ELF symbol table is present, so callers can use
    ``if not exported`` to fall back to a DWARF-derived view.

    ``getattr(sym.sym_type, "value", sym.sym_type)`` accepts both a ``SymbolType``
    enum member and a raw string (e.g. from a serialized snapshot). When
    *abi_relevant_only* is set, transitive runtime / compiler-internal exports
    are dropped via :func:`is_abi_relevant_elf_symbol`.
    """
    if elf is None or not getattr(elf, "symbols", None):
        return set()
    names: set[str] = set()
    for sym in elf.symbols:
        if not sym.name:
            continue
        sym_type = getattr(sym.sym_type, "value", sym.sym_type)
        if sym_type not in symbol_types:
            continue
        if abi_relevant_only and not is_abi_relevant_elf_symbol(sym.name):
            continue
        names.add(sym.name)
    return names
