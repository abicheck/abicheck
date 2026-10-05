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

"""Linkage facts the export-obligation predicate reads from a symbol's spelling.

``buildsource.cross_source_checks._has_export_obligation`` decides whether a
public-header declaration promises a dynamic symbol. Three of its questions
are answered here, from the mangled name and the snapshot rather than from a
per-declaration flag no backend records:

* **Is a ``static`` function a class member?** ``Function.is_static`` is set
  for both a namespace-scope ``static`` function (internal linkage, no
  export) and a ``static`` *member* function (external linkage, exported
  like any other member). The predicate used to drop both, so a static
  member a header declares and the binary lacks was never reported -- e.g.
  oneCCL's ``communicator`` factory members. :func:`is_static_member_symbol`
  separates them: GCC and clang (and therefore castxml) mangle every
  internal-linkage name with Itanium's ``L`` prefix (``_ZL3foov``,
  ``_ZN2nsL3fooEv``), and castxml leaves an internal-linkage free function
  unmangled altogether; MSVC encodes a static member's access as
  ``C``/``D``/``K``/``L``/``S``/``T`` right after the name's ``@@``.
* **Does the declaration's owning scope sit in an internal namespace**
  (``detail``/``impl``/``internal``/anonymous)? Reuses the exact predicate
  the reverse check (``exported_not_public``) already classifies an
  undocumented export with, so both directions share one convention.
* **Is any redeclaration of the symbol inline?** See
  :func:`inline_declared_symbols`.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model import Function
from ..model.export_entity_name import _NESTED_QUALIFIERS_RE, _read_decimal_length
from ..model.mangled_name_template_args import skip_substitution, skip_template_args
from .export_accounting import _entity_owner_is_internal

__all__ = [
    "has_internal_linkage",
    "inline_declared_symbols",
    "is_static_member_symbol",
    "owner_in_internal_namespace",
]

#: MSVC access codes that mark a *static* member function
#: (private C/D, protected K/L, public S/T; ``Y``/``Z`` are free functions).
_MSVC_STATIC_MEMBER_CODES = frozenset("CDKLST")


def _itanium_encoding(symbol: str) -> str | None:
    if symbol.startswith("__Z"):
        return symbol[3:]
    if symbol.startswith("_Z"):
        return symbol[2:]
    return None


def _itanium_nested_has_internal_linkage(rest: str) -> bool:
    """Whether a nested name's depth-0 components carry the ``L`` marker.

    Template-argument lists and substitutions are skipped with the shared
    structural walker (``skip_template_args``/``skip_substitution``), so a
    literal or nested name inside template arguments is never mistaken for a
    depth-0 component.
    """
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == "E":
            return False
        if c == "I":
            end = skip_template_args(rest, i)
            if end is None:
                return False
            i = end
        elif c == "S":
            i = skip_substitution(rest, i)
        elif c == "L" and i + 1 < len(rest) and rest[i + 1].isdigit():
            return True
        elif c.isdigit():
            parsed = _read_decimal_length(rest, i)
            if parsed is None:
                return False
            length, j = parsed
            i = j + length
        else:
            i += 1
    return False


def has_internal_linkage(mangled: str) -> bool:
    """Whether an Itanium *mangled* name carries the internal-linkage marker.

    ``_ZL3kVal`` (file scope) or ``_ZN2nsL3kValE`` (namespace scope): an
    object or function every including TU gets its own copy of, so no
    export is owed. ``False`` for an unmangled name -- there is no marker to
    read -- and for an MSVC one, whose mangling does not encode it this way.
    """
    rest = _itanium_encoding(mangled)
    if rest is None:
        return False
    if rest[:1] == "L":
        return True
    if rest[:1] != "N":
        return False
    return _itanium_nested_has_internal_linkage(
        _NESTED_QUALIFIERS_RE.sub("", rest[1:], count=1)
    )


def is_static_member_symbol(mangled: str) -> bool:
    """Whether *mangled* names a ``static`` **member** function.

    Only meaningful for a declaration already known to be ``static``: it
    answers "member (external linkage)" vs. "namespace-scope (internal
    linkage)". An unmangled spelling is never a member -- C has none, and
    castxml emits no mangling for an internal-linkage free function.
    """
    if mangled.startswith("?"):
        _, sep, tail = mangled.partition("@@")
        return bool(sep) and tail[:1] in _MSVC_STATIC_MEMBER_CODES
    rest = _itanium_encoding(mangled)
    if rest is None or not rest.startswith("N"):
        # Unmangled, or an un-nested ``_Z3foo``/``_ZL3foo``: no enclosing
        # class, so not a member.
        return False
    rest = _NESTED_QUALIFIERS_RE.sub("", rest[1:], count=1)
    return not _itanium_nested_has_internal_linkage(rest)


def owner_in_internal_namespace(mangled: str) -> bool:
    """Whether the declaration's enclosing scope is an internal namespace."""
    return _entity_owner_is_internal(mangled)


def inline_declared_symbols(functions: Iterable[Function]) -> frozenset[str]:
    """Mangled names for which *some* recorded declaration is inline.

    [dcl.inline]/6 makes a function inline if any of its declarations is. A
    snapshot can hold one record per declaration (an in-class declaration
    plus an out-of-line ``inline`` definition), and the obligation must be
    judged on the function, not on whichever record happens to be visited.
    """
    return frozenset(f.mangled for f in functions if f.is_inline and f.mangled)
