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

"""The identity that tells one overload from its siblings
(``diff_templates.detect_overload_set_rerouted``).

An overload is its parameter types plus its implicit-object cv/ref
qualifiers. A header-modeled ``Function`` carries both. An export-table stub
(``extract.export_symbol_identity.itanium_export_function``) carries neither
-- no parameters, no qualifiers -- so every overload of ``kput`` would share
one empty key and a retyped overload would be invisible at binary-only
evidence. The mangling still encodes the signature, so such a stem is keyed
on the demangled parameter list of every member's linker spelling instead.
Both sides of a stem that holds any stub are keyed that way, so one side's
header spelling never meets the other's demangler spelling.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from abicheck.demangle import demangle
from abicheck.model.special_member_identity import split_top_level
from abicheck.model.surface_facts import is_export_table_only_record

if TYPE_CHECKING:
    from abicheck.model.declarations import Function

OverloadKey = tuple[tuple[str, ...], bool, bool, str]


def _declared_key(f: Function) -> OverloadKey:
    return (
        tuple(p.type for p in f.params),
        f.is_const,
        f.is_volatile,
        f.ref_qualifier or "",
    )


def _mangled_key(f: Function) -> OverloadKey | None:
    """*f*'s key read from its linker spelling's demangling, or None when it
    does not demangle to a ``name(params) [cv] [ref]`` shape."""
    text = demangle(f.mangled) if f.mangled else None
    if not text or text == f.mangled:
        return None
    # The implicit-object qualifiers are the words after the parameter
    # list's closing parenthesis (`get(int) const &`).
    close = text.rfind(")")
    if close < 0:
        return None
    trailer = text[close + 1 :].split()
    if not set(trailer) <= {"const", "volatile", "&", "&&"}:
        return None
    text = text[: close + 1]
    is_const, is_volatile = "const" in trailer, "volatile" in trailer
    ref = next((t for t in trailer if t in ("&", "&&")), "")
    params = _qualified_params(text)
    if params is None:
        return None
    return (params, is_const, is_volatile, ref)


def _qualified_params(text: str) -> tuple[str, ...] | None:
    """The parameter list of a demangled ``name(params)``, each parameter's
    namespace kept: ``put(a::Token)`` and ``put(b::Token)`` are different
    overloads. (``demangled_params`` drops scope on purpose -- constructor
    matching compares against castxml's unqualified spellings -- so it
    cannot key an overload set.) ``None`` when *text* has no trailing
    parameter list."""
    if not text.endswith(")"):
        return None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch in ")>]":
            depth += 1
        elif ch in "(<[":
            depth -= 1
            if depth == 0:
                if ch != "(":
                    return None
                params = [
                    " ".join(p.split()) for p in split_top_level(text[i + 1 : -1])
                ]
                return () if params == ["void"] else tuple(params)
    return None


def overload_keys(
    old_group: Iterable[Function], new_group: Iterable[Function]
) -> tuple[set[OverloadKey], set[OverloadKey]]:
    """Both sides' overload keys for one stem (see the module docstring)."""
    old_list, new_list = list(old_group), list(new_group)
    # An unchanged overload is often the very same object on both sides:
    # key each object once for this call.
    members = list({id(f): f for f in (*old_list, *new_list)}.values())
    if not any(is_export_table_only_record(f) for f in members):
        declared = {id(f): _declared_key(f) for f in members}
        return {declared[id(f)] for f in old_list}, {declared[id(f)] for f in new_list}

    def key(f: Function) -> OverloadKey:
        mangled = _mangled_key(f)
        if mangled is not None:
            return mangled
        # Undemanglable: the linker spelling itself is the overload's
        # identity -- distinct per overload, never an empty shared key.
        return ((f"<symbol {f.mangled or f.name}>",), False, False, "")

    keys = {id(f): key(f) for f in members}
    return {keys[id(f)] for f in old_list}, {keys[id(f)] for f in new_list}


def format_overload_key(key: OverloadKey) -> str:
    params, is_const, is_volatile, ref_qual = key
    sig = "(" + ", ".join(params) + ")"
    if is_const:
        sig += " const"
    if is_volatile:
        sig += " volatile"
    if ref_qual:
        sig += f" {ref_qual}"
    return sig
