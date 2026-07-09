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

"""G23 Phase D3 — unnamed-type (lambda / anonymous struct) ABI-leak detector.

An exported C++ symbol whose mangled name embeds an *unnamed* type is an ABI
hazard: the Itanium mangling of a lambda closure (``Ul…E_``) or an unnamed
struct/enum (``Ut…_``) is per-translation-unit and depends on the order the
compiler encounters unnamed types, so recompiling — or merely reordering
unrelated declarations — can renumber ``{lambda#1}`` → ``{lambda#2}`` and break
symbol resolution for an already-built consumer.

This is a single-snapshot hygiene anti-pattern (like ADR-027's
``polymorphic_type_non_virtual_dtor``): at diff time it is reported only for
symbols *newly introduced* on the new side, so an unchanged pre-existing leak
does not spam every comparison.
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .demangle import demangle
from .detector_registry import registry
from .diff_helpers import make_change
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot

# Itanium unnamed-type productions, both <unqualified-name> alternatives:
#   closure-type  ::= Ul <lambda-sig> E [<number>] _
#   unnamed-type  ::= Ut [<number>] _
# A plain substring search is unsound: an ordinary source name can contain the
# letters (a function `aUt_()` mangles as `_Z4aUt_v`), producing a false leak.
# We instead walk the mangled string, skipping every length-prefixed
# `<source-name>` (`<decimal><identifier>`) so those tokens are only recognized
# at real *structural* positions. At a structural position `U` is either a
# vendor qualifier (`U <source-name> …`, i.e. `U` + digit) or one of these two
# productions (`Ut`/`Ul`), which is unambiguous because `t`/`l` cannot start a
# length-prefixed source name. This is demangler-independent, so a lambda is
# caught identically across libstdc++/libc++abi.
_UNNAMED_STRUCT_TOKEN = re.compile(r"Ut\d*_")


def _unnamed_kind(mangled: str) -> str | None:
    """Return a human label if *mangled* embeds an unnamed type at a real
    mangling-token boundary, else None."""
    i = 0
    n = len(mangled)
    while i < n:
        ch = mangled[i]
        if ch.isdigit():
            # <source-name> ::= <decimal length> <identifier>. Skip the whole
            # identifier so tokens inside a user name are never matched.
            j = i
            while j < n and mangled[j].isdigit():
                j += 1
            length = int(mangled[i:j])
            i = j + length
            continue
        # Structural position: `Ut[<n>]_` / `Ul…E[<n>]_` are the productions.
        if mangled.startswith("Ul", i):
            return "lambda closure"
        if mangled.startswith("Ut", i) and _UNNAMED_STRUCT_TOKEN.match(mangled, i):
            return "unnamed struct/enum"
        i += 1
    return None


def _exported_symbol_names(snap: AbiSnapshot) -> set[str]:
    elf = snap.elf
    if elf is None:
        return set()
    return {
        s.name
        for s in elf.symbols
        if s.name.startswith("_Z") and is_abi_relevant_elf_symbol(s.name)
    }


@registry.detector(
    "unnamed_types",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None,
        "missing ELF metadata on one side",
    ),
)
def _diff_unnamed_types(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Flag newly-introduced exported symbols that leak an unnamed type (D3).

    ``requires_support`` already demands a captured ELF symbol table on *both*
    sides, so an absent (header-only / parse-failed) baseline disables the
    detector rather than reaching here — a genuinely-empty captured baseline is
    a real "exported nothing before" surface, against which a new unnamed-type
    export is correctly newly introduced.
    """
    old_syms = _exported_symbol_names(old)
    changes: list[Change] = []
    for name in sorted(_exported_symbol_names(new) - old_syms):
        label = _unnamed_kind(name)
        if label is None:
            continue
        pretty = demangle(name) or name
        changes.append(
            make_change(
                ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI,
                symbol=name,
                name=pretty,
                detail=label,
            )
        )
    return changes
