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
* MSVC (PE): the decorated name is read type by type, and the type must be
  the leaf of a class-type code ``U``/``V``/``T``/``W4`` met at a type
  boundary -- in a template-argument list, the return or parameter types --
  never a class code letter that is only the first character of a longer
  name (``VUktag_a@@`` is ``class Uktag_a``).

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


def _skip_abi_tags(s: str, i: int) -> int:
    """Past every ``B<source-name>`` ABI tag at *i*."""
    while i < len(s) and s[i] == "B":
        tag, k = read_length_prefixed_name(s, i + 1)
        if tag is None:
            break
        i = k
    return i


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
                # ABI tags (`B<source-name>`) belong to this component, never
                # a component of their own (`_ZN2ns6ktag_aB2v1Ev`).
                i = _skip_abi_tags(s, i2)
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
        i = _skip_abi_tags(s, i)  # ABI tags on the entity's own name
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


class _MsvcParseError(Exception):
    pass


#: Two-character fundamental codes (``_N`` bool, ``_J`` int64, ...).
_MSVC_EXTENDED = "_"
#: Pointer/reference codes, each followed by optional ``E``/``I``/``F``
#: modifiers and one cv letter, then the pointee type.
_MSVC_INDIRECTION = "PQRSAB"


def _msvc_name(s: str, i: int, refs: set[str]) -> tuple[str, int]:
    """One ``@``-terminated name component at *i* (a template name's
    arguments are read as types into *refs*)."""
    if s.startswith("?$", i):
        at = s.find("@", i + 2)
        if at < 0:
            raise _MsvcParseError
        name, i = s[i + 2 : at], at + 1
        while i < len(s) and s[i] != "@":
            i = _msvc_type(s, i, refs)
        return name, i + 1
    if i < len(s) and s[i].isdigit():  # a name back-reference
        return "", i + 1
    at = s.find("@", i)
    if at <= i:
        raise _MsvcParseError
    return s[i:at], at + 1


def _msvc_qualified(s: str, i: int, refs: set[str]) -> tuple[list[str], int]:
    """A qualified name ``leaf@scope@...@`` -> components (leaf first)."""
    names: list[str] = []
    while True:
        if i >= len(s):
            raise _MsvcParseError
        if s[i] == "@":
            return names, i + 1
        name, i = _msvc_name(s, i, refs)
        names.append(name)


def _msvc_type(s: str, i: int, refs: set[str]) -> int:
    """Past the type encoded at *i*, adding every class-type leaf to *refs*."""
    if i >= len(s):
        raise _MsvcParseError
    c = s[i]
    if c in "UVT" or s.startswith("W4", i):
        names, end = _msvc_qualified(s, i + (2 if c == "W" else 1), refs)
        if names and names[0]:
            refs.add(names[0])
        return end
    if c in _MSVC_INDIRECTION:
        i += 1
        while i < len(s) and s[i] in "EIF":
            i += 1
        return _msvc_type(s, i + 1, refs)  # one cv letter, then the pointee
    if c == "?":  # a cv-qualified class value: `?A`/`?B` then the type
        return _msvc_type(s, i + 2, refs)
    if s.startswith("$$", i):  # `$$Q`/`$$R` rvalue reference, `$$C` cv
        kind, i = s[i + 2 : i + 3], i + 3
        if kind in ("Q", "R"):
            while i < len(s) and s[i] in "EIF":
                i += 1
        elif kind != "C":
            raise _MsvcParseError
        return _msvc_type(s, i + 1, refs)  # one cv letter, then the type
    if c == "$":
        raise _MsvcParseError
    if c == _MSVC_EXTENDED:
        return i + 2
    return i + 1  # a one-letter fundamental type or a type back-reference


def _msvc_referenced_types(symbol: str) -> set[str] | None:
    """Class-type leaves a decorated MSVC spelling references, or None when
    it cannot be read. The entity's own name is no reference; its
    enclosing scopes and a template's arguments are."""
    refs: set[str] = set()
    try:
        if symbol.startswith("??$"):
            scopes, i = _template_entity(symbol, refs)
        else:
            names, i = _msvc_qualified(symbol, 1, refs)
            scopes = names[1:]
        # An enclosing scope is a reference, as on the Itanium side.
        refs.update(n for n in scopes if n)
        if i >= len(symbol):
            return refs
        code = symbol[i]
        if code.isdigit():  # a variable: storage class, its type, cv
            _msvc_type(symbol, i + 1, refs)
            return refs
        # A function: access/kind letter; a member adds `E` and a cv letter.
        i += 1
        if code not in "YZ":
            while i < len(symbol) and symbol[i] in "EIF":
                i += 1
            i += 1
        i += 1  # calling convention
        if symbol.startswith("?A", i):  # return-value storage
            i += 2
        i = _msvc_type(symbol, i, refs)  # return type
        while i < len(symbol) and symbol[i] not in "@Z":
            i = _msvc_type(symbol, i, refs)
    except (_MsvcParseError, IndexError):
        return None
    return refs


def _template_entity(symbol: str, refs: set[str]) -> tuple[list[str], int]:
    """``??$name@args@scope@@...``: the template's own name, its arguments
    read as types, then its scopes."""
    _, i = _msvc_name(symbol, 1, refs)
    names: list[str] = []
    while i < len(symbol) and symbol[i] != "@":
        name, i = _msvc_name(symbol, i, refs)
        names.append(name)
    return names, i + 1


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
        refs = _msvc_referenced_types(symbol)
        return refs is not None and type_leaf in refs
    return False


def symbols_referencing_type(symbols: Iterable[str], type_leaf: str) -> list[str]:
    """The spellings in *symbols* whose mangling references *type_leaf*,
    sorted."""
    return sorted(s for s in symbols if mangling_references_type(s, type_leaf))
