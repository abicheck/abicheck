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

"""The entity name an Itanium export spells, parsed structurally.

One walker (:func:`entity_name_components`) answers every "what entity does
this export name" question the export-accounting and export-only-addition
detectors ask -- its enclosing scopes, which of them carry template arguments,
and what those arguments are -- so the answers cannot disagree. Lives in
``model`` (moved out of ``buildsource/export_accounting.py``) because both the
``extract``-classified accounting and the ``compare``-classified
``undeclared_exports`` detector need it, and ``compare`` may only import
``model``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .mangled_name_template_args import (
    skip_substitution as _skip_substitution,
    skip_template_args,
)

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

#: Itanium prefixes whose *operand* is a type whose owning namespace decides
#: external-vs-native: vtable/typeinfo/typeinfo-name/VTT/construction-vtable
#: (``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT``/``_ZTC``) and a guard variable (``_ZGV``,
#: ``_ZGVZ`` for a local static). Peeling them lets a leaked *dependency*
#: vtable/typeinfo/construction-vtable (``_ZTVNSt…``, ``_ZTIN3fmt…``,
#: ``_ZTCN3fmt3FooE0_NS_3BarE``) be attributed to the dependency instead of exempted
#: as this library's own class artifact (Codex review). Prefix order is immaterial —
#: the fourth char (``C``/``V``/``I``/``S``/``T``) disambiguates them — and the
#: construction-vtable operand (``N3fmt3FooE0_…``) still begins with the nested-name
#: ``N`` that :func:`_mangled_owner_namespace` reads. Thunks carry a numeric
#: call-offset before the operand and are peeled by :data:`_THUNK_PREFIX_RE` instead.
_ARTIFACT_OPERAND_PREFIXES = (
    "_ZTV",
    "_ZTI",
    "_ZTS",
    "_ZTT",
    "_ZTC",
    "_ZGVZ",
    "_ZGV",
)

#: A non-virtual/virtual/covariant thunk prefix (``_ZTh``/``_ZTv``/``_ZTc``)
#: followed by its call-offset run. Each offset chunk is ``n?<num>_`` and, for a
#: covariant (``_ZTc``) thunk, may carry an ``h``/``v`` tag (``h<n>_`` /
#: ``v<n>_<n>_``), so the run token is ``[hv]?n?\d+_``. Peeling the whole run —
#: not just the ``_ZTh`` letters — leaves the nested-name operand, so a leaked
#: dependency thunk (``_ZThn16_N3fmt…``, ``_ZTv0_n24_N5boost…``, covariant
#: ``_ZTchn16_h16_N3fmt…``) attributes to its dependency instead of falling through
#: to the artifact exemption (Codex review).
_THUNK_PREFIX_RE = re.compile(r"^_ZT[hvc](?:[hv]?n?\d+_)+")

#: Itanium nested-name CV-qualifiers (``r`` restrict / ``V`` volatile / ``K`` const)
#: and ref-qualifiers (``R`` ``&`` / ``O`` ``&&``) that sit between the ``N`` intro
#: and the first name component. Skipped before reading the owner so a const/ref
#: member export (``_ZNK3fmt…``) still resolves its namespace (Codex review).
_NESTED_QUALIFIERS_RE = re.compile(r"^[rVK]*[RO]?")


def _read_decimal_length(text: str, start: int) -> tuple[int, int] | None:
    """Read an Itanium source-name length without unbounded ``int()`` conversion.

    Exported symbol names can come from untrusted binaries/snapshots. Feeding an
    arbitrary digit run to :func:`int` can raise on modern Python when it exceeds
    the interpreter's integer-string limit (and is needless work on older
    runtimes). Accumulate only while the length can still address ``text``; a
    longer run is still safe and simply skips past the end of the malformed name.
    """
    if start >= len(text) or not ("0" <= text[start] <= "9"):
        return None
    i = start
    length = 0
    limit = len(text)
    while i < len(text) and "0" <= text[i] <= "9":
        digit = ord(text[i]) - ord("0")
        length = length * 10 + digit
        i += 1
        if length > limit:
            while i < len(text) and "0" <= text[i] <= "9":
                i += 1
            return length, i
    return length, i


def _encoding_after_prefixes(symbol: str) -> str:
    """Strip ``_Z`` and any vtable/typeinfo/guard-variable/thunk prefix.

    Returns the Itanium *encoding* that follows — the operand type for an artifact,
    or the name+signature for a plain function/data — with the nested-name ``N``
    intro left intact so callers can tell a nested name (``N3fmt…E``) from an
    un-nested top-level name (``3fmt`` = a global ``fmt``, no namespace).
    """
    thunk = _THUNK_PREFIX_RE.match(symbol)
    if thunk:
        return symbol[thunk.end() :]
    for pfx in _ARTIFACT_OPERAND_PREFIXES:
        if symbol.startswith(pfx):
            return symbol[len(pfx) :]
    return symbol[2:] if symbol.startswith("_Z") else symbol


@dataclass(frozen=True)
class EntityName:
    """The structurally-parsed entity name of an Itanium export.

    ``components`` are the *bare* scope components (namespace/class/entity),
    template arguments stripped; ``template_positions`` are the indices of the
    components that carried a template-argument list; ``template_arg_spans``
    are the raw encodings of those lists (their contents name the types a
    template was instantiated over); ``nested`` is whether the name used the
    ``N…E`` nested form. A ctor/dtor component reads ``{ctor}``/``{dtor}``, an
    operator ``{op:<code>}``, and a substitution that stands for an earlier
    scope ``{subst}``.
    """

    components: tuple[str, ...]
    template_positions: frozenset[int]
    template_arg_spans: tuple[str, ...]
    nested: bool


def _read_source_name(rest: str, i: int, comps: list[str]) -> int | None:
    """A length-prefixed source name plus any trailing ``B`` ABI tags."""
    n = len(rest)
    parsed = _read_decimal_length(rest, i)
    if parsed is None:
        return None
    length, j = parsed
    if length > n - j or length == 0:
        return None
    comps.append(rest[j : j + length])
    i = j + length
    while i < n and rest[i] == "B":  # GNU ABI tags
        tag = _read_decimal_length(rest, i + 1)
        if tag is None or tag[0] > n - tag[1]:
            return None
        i = tag[1] + tag[0]
    return i


def _read_substitution(rest: str, i: int, comps: list[str]) -> int | None:
    end = _skip_substitution(rest, i)
    if end == i + 1:
        return None
    comps.append("{subst}")
    return end


def _read_component(rest: str, i: int, comps: list[str]) -> tuple[int, bool] | None:
    """Read one name component at *i* into *comps*.

    Returns ``(next index, stop)`` -- *stop* when the component ends the name
    (an inheriting constructor, a conversion operator) -- or ``None`` for a
    form :func:`entity_name_components` does not model. ``St`` is handled by
    the caller, since ``std`` is followed directly by the next component.
    """
    n = len(rest)
    c = rest[i]
    if "0" <= c <= "9":
        end = _read_source_name(rest, i, comps)
        return None if end is None else (end, False)
    if c == "S":
        end = _read_substitution(rest, i, comps)
        return None if end is None else (end, False)
    if c in "CD" and i + 1 < n and rest[i + 1] in "0123456I":
        if not comps:
            return None
        comps.append("{ctor}" if c == "C" else "{dtor}")
        # Inheriting constructor (``CI1<base>``): the base type that follows
        # is not a name component; the ctor is the leaf.
        return i + 2, rest[i + 1] == "I"
    if (
        "a" <= c <= "z"
        and i + 1 < n
        and rest[i + 1].isascii()
        and rest[i + 1].isalpha()
    ):
        code = rest[i : i + 2]
        comps.append(f"{{op:{code}}}")
        # A conversion operator's target type follows; the name ends there.
        return i + 2, code == "cv"
    return None


def _name_encoding(symbol: str) -> tuple[str, bool]:
    """The entity-name encoding of an ``_Z`` *symbol* and whether it is nested.

    Peels artifact/thunk prefixes, the ``N`` intro and its CV/ref qualifiers,
    or an un-nested name's internal-linkage ``L`` marker.
    """
    rest = _encoding_after_prefixes(symbol)
    if rest.startswith("N"):
        return _NESTED_QUALIFIERS_RE.sub("", rest[1:], count=1), True
    if rest.startswith("L"):
        return rest[1:], False
    return rest, False


def _skip_component_template_args(
    rest: str, i: int, index: int, positions: set[int], spans: list[str]
) -> int | None:
    """Consume a template-argument list at *i*, if any, for component *index*.

    Returns the index past it (or *i* unchanged when there is none), or
    ``None`` when the list does not parse.
    """
    if i >= len(rest) or rest[i] != "I":
        return i
    args_end = skip_template_args(rest, i)
    if args_end is None:
        return None
    positions.add(index)
    spans.append(rest[i:args_end])
    return args_end


def entity_name_components(symbol: str) -> EntityName | None:
    """Parse an Itanium *symbol*'s entity name, or ``None`` when it cannot.

    One walker behind every entity-name question this module asks (internal
    scope, template instantiation, *n*-th component), so they cannot disagree.
    Vtable/typeinfo/guard/thunk prefixes are peeled first (their operand is the
    owning type). Each template-argument list is skipped as one balanced
    production with :func:`~abicheck.model.mangled_name_template_args.skip_template_args`
    -- the shared primitive that tracks nested names, packs, expressions,
    namespaced-enumerator literals and substitutions -- rather than a local
    ``I``/``E`` depth counter, which a ``N…E`` or ``L…E`` inside the arguments
    unbalanced: the walk then resumed *inside* the argument list and read a
    ``detail``/``impl`` namespace of a template argument or return type as the
    entity's own scope. ``None`` (never a guess) for a form it does not model;
    every caller treats that as "no claim".
    """
    if not symbol.startswith("_Z"):
        return None
    rest, nested = _name_encoding(symbol)
    comps: list[str] = []
    positions: set[int] = set()
    spans: list[str] = []
    i, n = 0, len(rest)
    while i < n and not (nested and rest[i] == "E"):
        if rest.startswith("St", i):
            comps.append("std")
            i += 2
            continue  # the next component follows directly
        step = _read_component(rest, i, comps)
        if step is None:
            return None
        i, stop = step
        if stop:
            break
        args_end = _skip_component_template_args(
            rest, i, len(comps) - 1, positions, spans
        )
        if args_end is None:
            return None
        i = args_end
        if not nested:
            break
    if not comps:
        return None
    return EntityName(tuple(comps), frozenset(positions), tuple(spans), nested)


def strip_template_args(name: str) -> str:
    """``ns::Box<int, ns::T<2>>::get`` -> ``ns::Box::get`` (bracket-aware)."""
    out: list[str] = []
    depth = 0
    for ch in name:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def split_qualified(name: str) -> tuple[str, ...]:
    """Bare scope components of a demangled/declared name (args stripped)."""
    bare = strip_template_args(name.split("(", 1)[0])
    return tuple(p.strip() for p in bare.split("::") if p.strip())


@dataclass(frozen=True)
class PublicTemplateScopes:
    """The public-header scopes an exported instantiation can belong to.

    ``scopes`` are bare qualified names of public class types and of every
    template a public declaration instantiates or is (``ns::Box`` for a public
    ``ns::Box<int>``); ``heads`` are the same keyed ``(top-level namespace,
    last component)`` so a versioned inline namespace one side spells
    (``ccl::v1::X``) and the other omits (``ccl::X``) still meet.
    """

    scopes: frozenset[tuple[str, ...]] = frozenset()
    heads: frozenset[tuple[str, str]] = frozenset()

    def union(self, other: PublicTemplateScopes) -> PublicTemplateScopes:
        return PublicTemplateScopes(
            self.scopes | other.scopes, self.heads | other.heads
        )


def public_template_scopes(snapshot: AbiSnapshot) -> PublicTemplateScopes:
    """Collect :class:`PublicTemplateScopes` from a snapshot's public decls."""
    from .vocabulary import ScopeOrigin

    scopes: set[tuple[str, ...]] = set()
    for decls, is_type in (
        (snapshot.declarations.functions, False),
        (snapshot.declarations.variables, False),
        (snapshot.declarations.types, True),
    ):
        for d in decls:
            if getattr(d, "origin", None) != ScopeOrigin.PUBLIC_HEADER:
                continue
            name = str(getattr(d, "name", "") or "")
            parts = split_qualified(name)
            if parts and (
                is_type or "<" in name or getattr(d, "is_template_pattern", False)
            ):
                scopes.add(parts)
            mangled = getattr(d, "mangled", None)
            parsed = entity_name_components(mangled) if mangled else None
            if parsed is not None:
                for pos in parsed.template_positions:
                    scopes.add(parsed.components[: pos + 1])
    heads = frozenset((s[0], s[-1]) for s in scopes if len(s) >= 2)
    return PublicTemplateScopes(frozenset(scopes), heads)


def public_template_for_symbol(symbol: str, public: PublicTemplateScopes) -> str | None:
    """The public template/class *symbol* instantiates or is a member of.

    Only an export whose entity name carries template arguments qualifies. It
    matches when a template-bearing prefix of its bare scope is a public
    template or public class (a member of a class template specialization),
    or when the entity is a member template of a public class. ``None`` when
    the snapshot's public declarations do not speak for it.
    """
    parsed = entity_name_components(symbol)
    if parsed is None or not parsed.template_positions:
        return None
    comps = parsed.components
    candidates = [comps[: p + 1] for p in sorted(parsed.template_positions)]
    if max(parsed.template_positions) == len(comps) - 1 and len(comps) >= 2:
        candidates.append(comps[:-1])  # member template of a (public) class
    for raw in candidates:
        # A templated ctor/operator names its class.
        cand = raw[:-1] if raw and raw[-1].startswith("{") else raw
        if cand and (
            cand in public.scopes
            or (len(cand) >= 2 and (comps[0], cand[-1]) in public.heads)
        ):
            return "::".join(cand)
    return None
