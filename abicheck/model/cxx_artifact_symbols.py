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

"""Recognising a compiler-emitted C++ class artifact by its symbol name.

Its own leaf module rather than part of ``mangled_name.py``, which is
already at the new-file line ceiling: this is a small, self-contained
predicate over a symbol *spelling*, with no dependency on that module's
Itanium component parser.

Two consumers must agree on this answer or they contradict each other --
see :func:`is_cxx_class_artifact_symbol`.
"""

from __future__ import annotations

#: Compiler-generated C++ ABI artifacts that belong to a *class* rather than
#: to a free function or variable, by special-name prefix, with the noun a
#: report uses for each: Itanium vtables, typeinfo, typeinfo names, VTTs,
#: construction vtables and thunks, plus MSVC's ``??_`` vftable/vbtable/
#: RTTI/deleting-destructor names.
_SPECIAL_NAME_NOUNS: tuple[tuple[str, str], ...] = (
    ("_ZTV", "vtable"),
    ("_ZTI", "typeinfo"),
    ("_ZTS", "typeinfo name"),
    ("_ZTT", "VTT"),
    ("_ZTC", "construction vtable"),
    ("_ZTh", "thunk"),
    ("_ZTv", "thunk"),
    ("_ZTc", "thunk"),
    ("??_", "vtable/RTTI artifact"),
)
CXX_ARTIFACT_PREFIXES: tuple[str, ...] = tuple(p for p, _ in _SPECIAL_NAME_NOUNS)

#: What :func:`cxx_class_artifact_kind` returns for a structor.
KIND_CONSTRUCTOR = "constructor"
KIND_DESTRUCTOR = "destructor"

#: ``S<x>`` well-known substitutions (``St``, ``Sa``, ``Ss``, ...).
_SUBST_LOWER = frozenset("tabsiod")


def _skip_source_name(s: str, i: int) -> int:
    """Index after the ``<length><chars>`` source name starting at digit *i*."""
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    if j - i > 9:  # malformed: no real identifier is that long -- run off the end
        return len(s)
    return j + int(s[i:j])


def _skip_substitution(s: str, i: int) -> int:
    """Index after the ``S...`` substitution starting at *i*."""
    j = i + 1
    if j < len(s) and s[j] in _SUBST_LOWER:
        return j + 1
    close = s.find("_", j)
    return len(s) if close < 0 else close + 1


def _skip_group(s: str, i: int) -> int:
    """Index after the ``E`` closing a group whose opener ends before *i*.

    Source names are skipped whole by their length prefix, so an ``E`` (or a
    ``C1``) spelled inside an identifier is never read as structure.
    """
    depth, n = 1, len(s)
    while i < n:
        c = s[i]
        if c.isdigit():
            i = _skip_source_name(s, i)
        elif c == "E":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        elif c == "L":
            if s.startswith("_Z", i + 1):  # external name literal: L_Z<encoding>E
                depth += 1
                i += 3
            else:  # L<type><value>E -- the value may be digits
                close = s.find("E", i)
                i = n if close < 0 else close + 1
        elif c == "S":
            i = _skip_substitution(s, i)
        elif c == "T":  # template parameter T_ / T<n>_
            close = s.find("_", i)
            i = n if close < 0 else close + 1
        else:
            if c in "NIXJ":
                depth += 1
            i += 1
    return n


def _skip_class_type(s: str, i: int) -> int:
    """Index after the class type (``CI1``'s inherited base) starting at *i*."""
    if i >= len(s):
        return i
    if s[i] == "N":
        return _skip_group(s, i + 1)
    i = _skip_source_name(s, i) if s[i].isdigit() else _skip_substitution(s, i)
    if i < len(s) and s[i] == "I":
        i = _skip_group(s, i + 1)
    return i


def _structor_kind(s: str) -> str | None:
    """``constructor``/``destructor`` when *s* is an Itanium structor, else ``None``.

    Walks the top-level ``<nested-name>`` (``_ZN [r][V][K] [R|O] <prefix>
    <unqualified-name> E``). Every component at that level is a
    length-prefixed source name, a substitution, a template-argument list,
    an ABI tag, or a structor name, so a bare ``C1``/``C2``/``C3`` (and GCC's
    ``C4``/``C5``), ``CI1``/``CI2`` or ``D0``/``D1``/``D2`` (``D4``/``D5``) as
    the *last* component is unambiguously a structor -- unlike a regex,
    which also matched an identifier merely containing ``C1E``.
    """
    if not s.startswith("_ZN"):
        return None
    n, i = len(s), 3
    while i < n and s[i] in "rVKRO":
        i += 1
    kind: str | None = None
    while i < n:
        c = s[i]
        if c == "E":
            return kind
        if c == "B":  # ABI tag ``B<source-name>`` qualifies the previous name
            if i + 1 >= n or not s[i + 1].isdigit():
                return None
            i = _skip_source_name(s, i + 1)
            continue
        kind = None
        if c.isdigit():
            i = _skip_source_name(s, i)
        elif c == "I":
            i = _skip_group(s, i + 1)
        elif c == "S":
            i = _skip_substitution(s, i)
        elif c == "T":
            close = s.find("_", i)
            i = n if close < 0 else close + 1
        elif c == "C" and i + 1 < n and s[i + 1] in "12345":
            kind, i = KIND_CONSTRUCTOR, i + 2
        elif c == "C" and s.startswith(("I1", "I2"), i + 1):
            kind, i = KIND_CONSTRUCTOR, _skip_class_type(s, i + 3)
        elif c == "D" and i + 1 < n and s[i + 1] in "01245":
            kind, i = KIND_DESTRUCTOR, i + 2
        else:  # operator name, decltype, unnamed/local entity: not a structor
            return None
    return None


def cxx_class_artifact_kind(symbol: str) -> str | None:
    """What compiler-emitted class artifact *symbol* is, or ``None``.

    :data:`KIND_CONSTRUCTOR`, :data:`KIND_DESTRUCTOR`, or the noun of its
    special-name family (``"vtable"``, ``"typeinfo"``, ``"VTT"``,
    ``"thunk"``, ...). A report words the finding from this, so a
    constructor is never described as a vtable/typeinfo/VTT/thunk.
    """
    for prefix, noun in _SPECIAL_NAME_NOUNS:
        if symbol.startswith(prefix):
            return noun
    if symbol.startswith("??0"):
        return KIND_CONSTRUCTOR
    if symbol.startswith("??1"):
        return KIND_DESTRUCTOR
    return _structor_kind(symbol)


def is_cxx_class_artifact_symbol(symbol: str) -> bool:
    """Whether *symbol* is a compiler-emitted artifact of a C++ class.

    Constructors/destructors (which a header backend routinely leaves
    unmangled, so they never match a class's own parsed declarations) and
    the vtable/typeinfo/VTT/thunk family, which a header backend records as
    a ``RecordType`` and never as a ``Function``/``Variable``.

    The one shared answer, because two consumers must agree on it or they
    contradict each other. ``exported_not_public`` exempts these symbols --
    a class artifact is not an *undocumented* export, its documentation is
    its class's declaration. Public-surface scoping
    (``policy.public_surface_closure._seed_undeclared_exports``) must make
    the same exemption for the same reason: treating ``_ZTVN3foo3BarE`` as
    provably-undocumented demoted a real vtable loss on a publicly declared
    class, so the identical binary pair reported BREAKING without ``-H`` and
    clean with it (caught by
    ``tests/test_export_reconciliation_and_obligations.py``, which asserts
    exactly that adding headers never subtracts a break).
    """
    return cxx_class_artifact_kind(symbol) is not None
