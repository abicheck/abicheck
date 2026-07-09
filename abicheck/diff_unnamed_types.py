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

# Itanium unnamed-type productions in a mangled name:
#   closure-type  ::= Ul <lambda-sig> E [<number>] _
#   unnamed-type  ::= Ut [<number>] _
# Both are matched directly on the *mangled* form. The system demangler's
# spelling of a lambda closure varies by platform (e.g. libc++abi vs libstdc++),
# so relying on the demangled ``{lambda`` text made detection platform-dependent;
# the ``Ul…E[<n>]_`` token is the authoritative, portable signal. A negative
# lookbehind rejects a source name that merely begins with those letters — a
# class named ``Ul`` is length-prefixed (``2Ul``), so its ``Ul`` is preceded by a
# digit, whereas the closure marker never is.
_UNNAMED_STRUCT_RE = re.compile(r"Ut\d*_")
_LAMBDA_CLOSURE_RE = re.compile(r"(?<![0-9])Ul.*?E\d*_")


def _exported_symbol_names(snap: AbiSnapshot) -> set[str]:
    elf = snap.elf
    if elf is None:
        return set()
    return {
        s.name
        for s in elf.symbols
        if s.name.startswith("_Z") and is_abi_relevant_elf_symbol(s.name)
    }


def _unnamed_kind(mangled: str) -> str | None:
    """Return a human label if *mangled* embeds an unnamed type, else None."""
    if _UNNAMED_STRUCT_RE.search(mangled):
        return "unnamed struct/enum"
    if _LAMBDA_CLOSURE_RE.search(mangled):
        return "lambda closure"
    return None


@registry.detector(
    "unnamed_types",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None,
        "missing ELF metadata on one side",
    ),
)
def _diff_unnamed_types(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Flag newly-introduced exported symbols that leak an unnamed type (D3)."""
    old_syms = _exported_symbol_names(old)
    if not old_syms:
        # Empty baseline surface = the old side never captured an ELF symbol
        # table, so "newly introduced" cannot be proven — every pre-existing
        # unnamed-type export would look new. Stay quiet rather than false-flag.
        return []
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
