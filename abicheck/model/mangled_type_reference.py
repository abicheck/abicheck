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

"""Whether a linker spelling's mangling encodes a reference to a named type.

A substring test (``"ktag_a" in symbol``) accepts an unmangled C export such
as ``ktag_a_count``, or a function merely *named* after the type. This module
answers structurally instead:

* Itanium (ELF, and Mach-O with or without its leading ``_``): the type's
  ``<source-name>`` (``<len><name>``) must occur in a template-argument list,
  the parameter types, or as an enclosing scope of the entity -- never as the
  entity's own unqualified name.
* MSVC (PE): the type must occur as a class-type code ``U``/``V``/``T``/``W4``
  followed by ``<name>@`` after the decorated name's own leading identifier.

An undecorated (C) spelling references no type.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from abicheck.model.mangled_name_template_args import (
    collect_type_candidate_identifiers,
    read_length_prefixed_name,
    skip_template_args,
)
from abicheck.model.mangling_scheme import is_msvc_decorated, itanium_encoding

_ABI_TAG = re.compile(r"\[abi:[^\]]*\]")


def _leaf(identifier: str) -> str:
    return _ABI_TAG.sub("", identifier).rsplit("::", 1)[-1]


def _itanium_referenced_types(s: str) -> set[str] | None:
    """Leaf identifiers a ``_Z`` spelling references as types, or None when
    its encoding cannot be parsed."""
    i, n = 2, len(s)
    refs: set[str] = set()
    if s.startswith("L", i):  # internal linkage
        i += 1
    if i < n and s[i] == "N":
        i += 1
        while i < n and s[i] in "rVKRO":
            i += 1
        components: list[str] = []
        while i < n and s[i] != "E":
            if s[i] == "I":
                end = skip_template_args(s, i)
                if end is None:
                    return None
                refs |= collect_type_candidate_identifiers(s, i + 1, end - 1)
                i = end
                continue
            if s[i].isdigit():
                name, i2 = read_length_prefixed_name(s, i)
                if name is None:
                    return None
                components.append(name)
                i = i2
                continue
            # ctor/dtor (`C1`, `CI1`, `D0`): opaque, and its digit is no
            # source-name length.
            if s[i] in "CD":
                i += 1
                if i < n and s[i] == "I":
                    i += 1
                while i < n and s[i].isdigit():
                    i += 1
                components.append("")
                continue
            # substitution: its seq-id digits are no source-name length.
            if s[i] in "ST":
                j = i + 1
                while j < n and (s[j].isdigit() or s[j].isupper()) and s[j] != "E":
                    j += 1
                i = j + 1 if j < n and s[j] == "_" else i + 2
                components.append("")
                continue
            # operator code (two lowercase letters): opaque.
            components.append("")
            i += 2
        if i >= n:
            return None
        i += 1
        # Every component but the entity's own name is an enclosing scope.
        refs |= {c for c in components[:-1] if c}
    elif i < n and s[i].isdigit():
        name, i = read_length_prefixed_name(s, i)
        if name is None:
            return None
        while i < n and s[i] == "B":  # ABI tag on the entity's own name
            tag, k = read_length_prefixed_name(s, i + 1)
            if tag is None:
                break
            i = k
        if i < n and s[i] == "I":
            end = skip_template_args(s, i)
            if end is None:
                return None
            refs |= collect_type_candidate_identifiers(s, i + 1, end - 1)
            i = end
    # Remainder: parameter (and return) types; for a special name such as
    # ``_ZTV6ktag_a`` the whole tail is the referenced type.
    refs |= collect_type_candidate_identifiers(s, i, n)
    return {_leaf(r) for r in refs}


def _msvc_references(symbol: str, leaf: str) -> bool:
    # Skip the decorated name's own leading identifier (`?name@` or a
    # template's `??$name@`): a function named after the type is no reference.
    body = symbol[3:] if symbol.startswith("??$") else symbol[1:]
    at = body.find("@")
    if at < 0:
        return False
    rest = body[at + 1 :]
    return re.search(r"(?:U|V|T|W4)" + re.escape(leaf) + "@", rest) is not None


def mangling_references_type(symbol: str, type_leaf: str) -> bool:
    """True when *symbol*'s mangling encodes a reference to the type whose
    unqualified name is *type_leaf* (see the module docstring)."""
    if not type_leaf:
        return False
    itanium = itanium_encoding(symbol)
    if itanium is not None:
        refs = _itanium_referenced_types(itanium)
        return refs is not None and type_leaf in refs
    if is_msvc_decorated(symbol):
        return _msvc_references(symbol, type_leaf)
    return False


def symbols_referencing_type(symbols: Iterable[str], type_leaf: str) -> list[str]:
    """The spellings in *symbols* whose mangling references *type_leaf*,
    sorted."""
    return sorted(s for s in symbols if mangling_references_type(s, type_leaf))
