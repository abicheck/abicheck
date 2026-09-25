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

"""Resolve a constructor/destructor's linker identity from the export table
(evidence-entity-model plan, Phase 1 gap: castxml ctor/dtor placeholders).

**Why castxml has no linker name here.** castxml 0.7.0 emits a ``mangled``
attribute on every ``Function``/``Method``/``OperatorMethod`` but never on a
``Constructor`` or ``Destructor`` element -- in ``--castxml-output=1`` and in
gccxml mode alike -- because one source declaration corresponds to several
Itanium symbols (``C1`` complete-object, ``C2`` base-object, ``C3``
allocating; ``D0`` deleting, ``D1`` complete-object, ``D2`` base-object). The
castxml parser therefore keys such a declaration on a synthetic placeholder
(``__abicheck_ctor__ns::W(int)``, ``~ns::W``). clang's AST reports the
complete-object variant (``C1``/``D1``) as ``mangledName``; an ELF/Mach-O
export table commonly carries ``C1`` *and* ``C2`` (plus ``D0`` for a virtual
destructor) as separate entries.

**The rule.** An exported Itanium ctor/dtor symbol is grouped into its
*variant family* by substituting its structurally-located variant code
(:func:`~abicheck.model.mangled_name.itanium_ctor_dtor_marker_span`, never a
substring search) with the complete-object code. A placeholder resolves to a
family when the evidence pairs them one-to-one:

* the family's owner (the mangled scope components before the marker) is
  spelled exactly the castxml placeholder's qualified scope -- a templated
  owner never is (the mangling keeps the raw ``I...E`` encoding), so a
  template's special members stay unresolved;
* a destructor needs nothing more: a class has one destructor;
* a constructor's parameter list must equal the family's demangled one after
  both are canonicalized (:func:`canonicalize_type_name`) and reduced to
  unqualified leaf spellings -- castxml spells a parameter type relative to
  its scope (``const Argument&``) while the demangler fully qualifies it;
* and the pairing is a bijection per owner: a placeholder matching two
  families, or a family matched by two placeholders (two overloads that
  differ only in a namespace the leaf reduction erased), resolves neither.

The resolved node id is the complete-object spelling (``C1``/``D1``, the one
clang itself reports, so a castxml and a clang dump of one declaration share a
node); the *observed* sibling variants are its aliases. A constructor with no
exported family (``inline``, implicitly declared and never ODR-used, a
template) keeps its ``unresolved`` identity -- no evidence, no merge.

Parameter matching needs a demangler (``abicheck.demangle``: ``cxxfilt`` or
``c++filt``); without one no constructor resolves and each stays
``unresolved``, never guessed. Destructors are resolved structurally.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..demangle import demangle_batch
from ..name_classification import canonicalize_type_name
from .mangled_name import itanium_ctor_dtor_marker_span, itanium_scope_components
from .synthetic_key import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)

if TYPE_CHECKING:
    from .declarations import Function

__all__ = [
    "SpecialMemberLinkerNames",
    "resolve_special_member_linker_names",
    "special_member_variant_aliases",
]

_CTOR_VARIANTS = ("C1", "C2", "C3")
_DTOR_VARIANTS = ("D1", "D0", "D2")
_COMPLETE = {"C": "C1", "D": "D1"}
_CTOR = "{ctor}"
_DTOR = "{dtor}"
_QUALIFIER = re.compile(r"(?:[A-Za-z_]\w*::)+")


@dataclass(frozen=True)
class SpecialMemberLinkerNames:
    """The linker spellings a placeholder resolved to."""

    #: The complete-object (``C1``/``D1``) spelling -- the canonical key.
    canonical: str
    #: The other variants of the family actually present in an export table.
    variants: tuple[str, ...]


@dataclass(frozen=True)
class _Family:
    marker: str
    owner: str
    canonical: str
    members: frozenset[str]


def _family_key(symbol: str) -> tuple[str, str] | None:
    """``(marker, complete-object spelling)`` of an exported ctor/dtor
    variant, or ``None`` when *symbol* is not one this rule models (an
    inherited ``CI1`` constructor, a GCC-internal ``C4``/``D4`` unified
    variant, anything that is not a ctor/dtor)."""
    span = itanium_ctor_dtor_marker_span(symbol)
    if span is None:
        return None
    start, end = span
    code = symbol[start:end]
    if code not in _CTOR_VARIANTS and code not in _DTOR_VARIANTS:
        return None
    marker = _CTOR if code[0] == "C" else _DTOR
    return marker, symbol[:start] + _COMPLETE[code[0]] + symbol[end:]


def _families(export_names: Collection[str]) -> list[_Family]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for sym in export_names:
        key = _family_key(sym)
        if key is not None:
            grouped.setdefault(key, set()).add(sym)
    out: list[_Family] = []
    for (marker, canonical), members in grouped.items():
        comps = itanium_scope_components(canonical)
        if not comps or comps[-1] != marker or len(comps) < 2:
            continue
        out.append(
            _Family(marker, "::".join(comps[:-1]), canonical, frozenset(members))
        )
    return out


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """*text* split on *sep* at bracket depth 0 (a ``pair<int, int>``
    parameter stays one parameter)."""
    if not text.strip():
        return []
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


_INTEGER_WORDS = frozenset({"signed", "unsigned", "short", "long", "int", "char"})
_INTEGER_RUN = re.compile(r"\b(?:(?:signed|unsigned|short|long|int|char)\b\s*)+")


def _integer_spelling(words: Sequence[str]) -> str:
    """One spelling per builtin integer type, whatever the specifier order
    (``long unsigned int`` -> ``unsigned long``, the demangler's form)."""
    unsigned = "unsigned" in words
    if "char" in words:
        if unsigned:
            return "unsigned char"
        return "signed char" if "signed" in words else "char"
    longs = words.count("long")
    base = "short" if "short" in words else ("long " * longs).strip() or "int"
    return f"unsigned {base}" if unsigned else base


def _normalize_integers(spelling: str) -> str:
    return _INTEGER_RUN.sub(
        lambda m: _integer_spelling(m.group(0).split()) + " ", spelling
    )


def _param_signature(params: Sequence[str]) -> tuple[str, ...]:
    """The comparison form of a parameter list: canonical, unqualified."""
    return tuple(
        canonicalize_type_name(_normalize_integers(_QUALIFIER.sub("", p.strip())))
        for p in params
    )


_NAME = re.compile(r"(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*")
#: Expansion rounds for a typedef whose target is itself a typedef.
_TYPEDEF_DEPTH = 4


def _expand_typedefs(param: str, scope: str, typedefs: Mapping[str, str]) -> str:
    """*param* with every name that ordinary lookup from *scope* finds in
    *typedefs* (qualified alias -> target) replaced by its target: the
    mangling names the underlying type, castxml the alias. Lookup is the
    enclosing-scope chain, innermost first, and stops at the first hit --
    a name it cannot find (a using-declaration, a base-class member) is left
    as spelled, so the parameter simply fails to match."""
    if not typedefs:
        return param
    chain = scope.split("::")

    def _lookup(m: re.Match[str]) -> str:
        name = m.group(0)
        for depth in range(len(chain), -1, -1):
            qualified = "::".join((*chain[:depth], name))
            target = typedefs.get(qualified)
            if target:
                return f"{target} "
        return name

    for _ in range(_TYPEDEF_DEPTH):
        expanded = _NAME.sub(_lookup, param)
        if expanded == param:
            break
        param = expanded
    return param


def _demangled_params(demangled: str) -> tuple[str, ...] | None:
    """The parameter list of a demangled ctor, or ``None`` when its shape is
    not ``...(<params>)``."""
    text = demangled.strip()
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
                inner = text[i + 1 : -1]
                params = split_top_level(inner)
                if params == ["void"]:
                    params = []
                return _param_signature(params)
    return None


def _placeholder(
    key: str, typedefs: Mapping[str, str]
) -> tuple[str, str, tuple[str, ...]] | None:
    """``(marker, qualified scope, parameter signature)`` of a castxml
    placeholder key, or ``None`` for any other spelling."""
    if is_synthetic_ctor_key(key):
        body = key[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
        paren = body.find("(")
        if paren <= 0 or not body.endswith(")"):
            return None
        scope = body[:paren]
        params = [
            _expand_typedefs(p, scope, typedefs)
            for p in split_top_level(body[paren + 1 : -1])
        ]
        return _CTOR, scope, _param_signature(params)
    if is_synthetic_dtor_key(key) and len(key) > 1:
        return _DTOR, key[1:], ()
    return None


def resolve_special_member_linker_names(
    functions: Sequence[Function],
    export_names: Collection[str],
    typedefs: Mapping[str, str] | None = None,
) -> dict[int, SpecialMemberLinkerNames]:
    """Index into *functions* -> the linker names its placeholder resolved
    to, for every placeholder the export evidence pairs one-to-one (see the
    module docstring). Pure function of its inputs and independent of their
    order."""
    declared: dict[tuple[str, str], list[tuple[int, tuple[str, ...]]]] = {}
    for idx, fn in enumerate(functions):
        parsed = _placeholder(fn.mangled, typedefs or {})
        if parsed is None:
            continue
        marker, scope, decl_sig = parsed
        if "<" in scope:
            continue  # a template: the mangling spells its owner differently
        declared.setdefault((marker, scope), []).append((idx, decl_sig))
    if not declared:
        return {}
    families = [f for f in _families(export_names) if (f.marker, f.owner) in declared]
    ctor_names = [f.canonical for f in families if f.marker == _CTOR]
    demangled = demangle_batch(sorted(ctor_names)) if ctor_names else {}

    by_owner: dict[tuple[str, str], list[tuple[_Family, tuple[str, ...] | None]]] = {}
    for fam in families:
        sig: tuple[str, ...] | None = ()
        if fam.marker == _CTOR:
            text = demangled.get(fam.canonical)
            sig = _demangled_params(text) if text else None
        by_owner.setdefault((fam.marker, fam.owner), []).append((fam, sig))

    out: dict[int, SpecialMemberLinkerNames] = {}
    for owner_key, decls in declared.items():
        fams = by_owner.get(owner_key, [])
        edges = [
            (idx, fam)
            for idx, dsig in decls
            for fam, fsig in fams
            if fsig is not None and fsig == dsig
        ]
        decl_degree: dict[int, int] = {}
        fam_degree: dict[str, int] = {}
        for idx, fam in edges:
            decl_degree[idx] = decl_degree.get(idx, 0) + 1
            fam_degree[fam.canonical] = fam_degree.get(fam.canonical, 0) + 1
        for idx, fam in edges:
            if decl_degree[idx] == 1 and fam_degree[fam.canonical] == 1:
                out[idx] = SpecialMemberLinkerNames(
                    fam.canonical,
                    tuple(sorted(fam.members - {fam.canonical})),
                )
    return out


def special_member_variant_aliases(
    linker_name: str, export_names: Collection[str]
) -> tuple[str, ...]:
    """The observed sibling variants of a *real* ctor/dtor linker name
    (clang's ``C1``/``D1``): every other member of its variant family that
    an export table carries. ``()`` for anything else."""
    key = _family_key(linker_name)
    if key is None:
        return ()
    marker, _canonical = key
    span = itanium_ctor_dtor_marker_span(linker_name)
    assert span is not None
    start, end = span
    codes = _CTOR_VARIANTS if marker == _CTOR else _DTOR_VARIANTS
    siblings = (linker_name[:start] + c + linker_name[end:] for c in codes)
    return tuple(sorted(s for s in siblings if s != linker_name and s in export_names))
