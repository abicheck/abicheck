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

"""C++ member functions in a DWARF-only snapshot.

A member function's DWARF is split in two. The class holds its *declaration*
(``DW_AT_declaration``, with the name, linkage name, virtuality, vtable slot
and an artificial ``this`` parameter); the definition sits outside the class
and names it through ``DW_AT_specification`` (a concrete constructor or
destructor instance goes through ``DW_AT_abstract_origin`` first), usually
with no ``DW_AT_name`` of its own. A namespace-scope function declared in a
header has the same split.

This module gives the walker what it needs to record them:

* :class:`MemberScopes` remembers each declaration's scope as the walk
  passes it, so a definition met later resolves its class scope without a
  parent walk; a definition met *before* its declaration is deferred and
  retried at the end of the compile unit.
* :func:`member_declaration` follows a definition to its declaration.
* :func:`member_qualifiers` reads the cv- and ref-qualifiers of the implicit
  object parameter, which the mangled name encodes (``_ZNK``) but the
  declaration carries only as the ``this`` pointer's pointee type.
* :func:`is_pure_virtual_declaration`: a pure virtual has no definition, so
  its declaration is the only DIE there is; it is recognized by
  ``DW_VIRTUALITY_pure_virtual``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..dwarf_utils import attr_bool, attr_int, attr_str, resolve_die_ref
from ..model.fact import Fact
from ..model.identity import Record
from ..model.mangled_name import itanium_scope_components

__all__ = [
    "MemberQualifiers",
    "MemberScopes",
    "chained_linkage_name",
    "explicit_specifier",
    "is_pure_virtual_member",
    "qualifiers_in_scope",
    "is_member_scope",
    "is_pure_virtual_declaration",
    "linkage_name",
    "member_declaration",
    "member_qualifiers",
    "parameter_dies",
    "vtable_slot",
]

#: ``DW_AT_virtuality`` value of a pure virtual (DWARF ``DW_VIRTUALITY_pure_virtual``).
_PURE_VIRTUAL = 2
_CV_TAGS = {"DW_TAG_const_type": "const", "DW_TAG_volatile_type": "volatile"}
#: Origin/specification links a definition may chain through to its declaration.
_LINKS = ("DW_AT_abstract_origin", "DW_AT_specification")
_MAX_HOPS = 4


def linkage_name(die: Any) -> str:
    """The DIE's own linkage (mangled) name, ``""`` when it carries none."""
    return attr_str(die, "DW_AT_linkage_name") or attr_str(
        die, "DW_AT_MIPS_linkage_name"
    )


def member_declaration(die: Any, CU: Any) -> Any | None:
    """The in-class declaration a nameless member definition refers to, or
    ``None`` when *die* names no declaration (or the chain is broken).

    An abstract instance (``DW_AT_inline``, the out-of-line template a
    concrete constructor instance points at) is not itself a definition,
    whatever the attribute's value: GCC writes ``DW_INL_not_inlined`` on the
    abstract root of a constructor or destructor (``_ZN1SD4Ev``), which DWARF 5
    Table 3.4 would read as no attribute at all.
    """
    if "DW_AT_inline" in die.attributes:
        return None
    current = die
    for _ in range(_MAX_HOPS):
        link = next((a for a in _LINKS if a in current.attributes), None)
        if link is None:
            break
        try:
            current = resolve_die_ref(current, link, CU)
        except Exception:  # noqa: BLE001 -- a broken ref is no declaration
            return None
    if current is die or not attr_bool(current, "DW_AT_declaration"):
        return None
    return current


def chained_linkage_name(die: Any, CU: Any) -> str:
    """*die*'s linkage name, else the nearest one along its origin and
    specification chain (a concrete instance names itself; an out-of-line
    definition usually leaves it on the declaration)."""
    current = die
    for _ in range(_MAX_HOPS + 1):
        name = linkage_name(current)
        if name:
            return name
        link = next((a for a in _LINKS if a in current.attributes), None)
        if link is None:
            return ""
        try:
            current = resolve_die_ref(current, link, CU)
        except Exception:  # noqa: BLE001
            return ""
    return ""


def parameters_and_variadic(die: Any, CU: Any) -> tuple[list[Any], bool]:
    """``parameter_dies`` and whether *die* takes ``...``, in one pass over its
    children (a ``DW_TAG_unspecified_parameters`` child; every producer emits it
    for a prototyped variadic function, on the definition DIE too, so its
    absence is a real "fixed arity").
    """
    variadic = False

    def note(child: Any) -> bool:
        nonlocal variadic
        if child.tag == "DW_TAG_unspecified_parameters":
            variadic = True
        return True

    params = list(parameter_dies(die, CU, observe=note))
    return params, variadic


def parameter_dies(
    die: Any, CU: Any, observe: Callable[[Any], object] | None = None
) -> Iterator[Any]:
    """*die*'s declared parameters, artificial ones (``this``) left out.

    A concrete instance's parameter usually carries only
    ``DW_AT_abstract_origin``: its name, type and artificial flag are on the
    abstract instance's parameter, which is yielded in its place. The
    artificial flag is read from either DIE, since a concrete parameter may
    repeat its type without repeating the flag.
    """
    for child in die.iter_children():
        if observe is not None:
            observe(child)
        if child.tag != "DW_TAG_formal_parameter":
            continue
        origin = None
        if "DW_AT_abstract_origin" in child.attributes:
            try:
                origin = resolve_die_ref(child, "DW_AT_abstract_origin", CU)
            except Exception:  # noqa: BLE001 -- keep the bare DIE ("?" type)
                origin = None
        if attr_bool(child, "DW_AT_artificial") or (
            origin is not None and attr_bool(origin, "DW_AT_artificial")
        ):
            continue
        local = "DW_AT_type" in child.attributes or origin is None
        yield child if local else origin


#: ``DW_OP_constu``: the one operation GCC and clang write in
#: ``DW_AT_vtable_elem_location`` (``[0x10, <uleb128 slot>]``).
_DW_OP_CONSTU = 0x10
#: Forms that make a location attribute a location-list offset: ``sec_offset``
#: and ``loclistx`` from DWARF 4/5, and ``data4``/``data8`` in DWARF 2/3.
_LOCATION_LIST_FORMS = frozenset(
    {"DW_FORM_sec_offset", "DW_FORM_loclistx", "DW_FORM_data4", "DW_FORM_data8"}
)


def vtable_slot(die: Any) -> int | None:
    """The vtable slot *die*'s ``DW_AT_vtable_elem_location`` names, ``None``
    when absent or written as any expression other than one ``DW_OP_constu``
    (an unknown slot, never slot 0)."""
    attr = die.attributes.get("DW_AT_vtable_elem_location")
    if attr is None:
        return None
    value = attr.value
    if getattr(attr, "form", None) in _LOCATION_LIST_FORMS:  # a list, not a slot
        return None
    if isinstance(value, int):  # a constant form
        return value
    ops = list(value) if isinstance(value, (list, tuple, bytes)) else []
    if len(ops) < 2 or ops[0] != _DW_OP_CONSTU:
        return None
    slot, shift = 0, 0
    for i, byte in enumerate(ops[1:], start=1):
        slot |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return slot if i == len(ops) - 1 else None
    return None


def is_pure_virtual_declaration(die: Any) -> bool:
    """Whether *die* is an in-class declaration the producer marked pure
    virtual (its only DIE: a pure virtual has no definition)."""
    return attr_bool(die, "DW_AT_declaration") and (
        attr_int(die, "DW_AT_virtuality") == _PURE_VIRTUAL
    )


def is_member_scope(scope_path: tuple[Any, ...]) -> bool:
    """Whether a declaration in *scope_path* is a class member."""
    return bool(scope_path) and isinstance(scope_path[-1], Record)


def is_pure_virtual_member(die: Any, scope_path: tuple[Any, ...]) -> bool:
    """Whether *die* is a class member's pure virtual declaration."""
    return is_member_scope(scope_path) and is_pure_virtual_declaration(die)


def explicit_specifier(mangled: str, decl: Any) -> Fact[bool | None]:
    """The ``explicit`` fact of the function declared by *decl*.

    g++/clang++ emit ``DW_AT_explicit`` on a constructor or conversion
    operator whose declaration carries ``explicit`` (C++20 ``explicit(bool)``
    collapses to its resolved bool). An absent attribute reads ``False``, and
    the compiler only ever emits it on a constructor/conversion-operator DIE,
    so absence is evidence of "implicit" there and of nothing anywhere else:
    eligibility comes from the mangled name's own Itanium encoding
    (``{ctor}``/``{op:cv:...}``), since DWARF carries no AST node kind
    (Codex review, PR #982). Ineligible reads ``not_applicable``.
    """
    components = itanium_scope_components(mangled) or []
    last = components[-1] if components else ""
    if not (last == "{ctor}" or last.startswith("{op:cv:")):
        return Fact.not_applicable()
    return Fact.present(attr_bool(decl, "DW_AT_explicit"))


@dataclass(frozen=True)
class MemberQualifiers:
    """What a declaration says about its implicit object parameter, and
    whether it is static (a member without ``this``; a non-member without
    external linkage)."""

    has_this: bool
    is_const: bool = False
    is_volatile: bool = False
    ref_qualifier: str = ""
    is_static: bool = False


def qualifiers_in_scope(
    decl: Any, CU: Any, scope_path: tuple[Any, ...]
) -> MemberQualifiers:
    """:func:`member_qualifiers` for a class member; for any other function
    only its static-ness (no external linkage)."""
    if is_member_scope(scope_path):
        quals = member_qualifiers(decl, CU)
        return MemberQualifiers(
            has_this=quals.has_this,
            is_const=quals.is_const,
            is_volatile=quals.is_volatile,
            ref_qualifier=quals.ref_qualifier,
            is_static=not quals.has_this,
        )
    return MemberQualifiers(
        has_this=False, is_static=not attr_bool(decl, "DW_AT_external")
    )


def _object_parameter(decl: Any, CU: Any) -> Any | None:
    if "DW_AT_object_pointer" in decl.attributes:
        try:
            return resolve_die_ref(decl, "DW_AT_object_pointer", CU)
        except Exception:  # noqa: BLE001
            pass
    for child in decl.iter_children():
        if child.tag == "DW_TAG_formal_parameter":
            return child if attr_bool(child, "DW_AT_artificial") else None
    return None


def _strip_cv(die: Any | None, CU: Any) -> tuple[Any | None, set[str]]:
    seen: set[str] = set()
    for _ in range(8):
        if die is None or die.tag not in _CV_TAGS:
            break
        seen.add(_CV_TAGS[die.tag])
        if "DW_AT_type" not in die.attributes:
            return None, seen
        die = resolve_die_ref(die, "DW_AT_type", CU)
    return die, seen


def member_qualifiers(decl: Any, CU: Any) -> MemberQualifiers:
    """cv- and ref-qualifiers of the member declared by *decl*.

    The ``this`` parameter is ``T cv *`` (a definition's copy may add a
    top-level ``const``, ignored here); no ``this`` means a static member.
    DWARF 5 marks a ref-qualified member with ``DW_AT_reference`` or
    ``DW_AT_rvalue_reference``.
    """
    ref = (
        "&&"
        if attr_bool(decl, "DW_AT_rvalue_reference")
        else "&"
        if attr_bool(decl, "DW_AT_reference")
        else ""
    )
    this = _object_parameter(decl, CU)
    if this is None:
        return MemberQualifiers(has_this=False, ref_qualifier=ref)
    if "DW_AT_type" not in this.attributes:
        # An object parameter whose type is unrecorded: still a ``this``.
        return MemberQualifiers(has_this=True, ref_qualifier=ref)
    try:
        pointer, _ = _strip_cv(resolve_die_ref(this, "DW_AT_type", CU), CU)
        if pointer is None or pointer.tag != "DW_TAG_pointer_type":
            return MemberQualifiers(has_this=True, ref_qualifier=ref)
        pointee = (
            resolve_die_ref(pointer, "DW_AT_type", CU)
            if "DW_AT_type" in pointer.attributes
            else None
        )
        _, quals = _strip_cv(pointee, CU)
    except Exception:  # noqa: BLE001 -- unreadable qualifiers stay unqualified
        return MemberQualifiers(has_this=True, ref_qualifier=ref)
    return MemberQualifiers(
        has_this=True,
        is_const="const" in quals,
        is_volatile="volatile" in quals,
        ref_qualifier=ref,
    )


@dataclass(frozen=True)
class _Scope:
    scope: str
    scope_path: tuple[Any, ...]
    default_access: Any


class MemberScopes:
    """Per-compile-unit memory of member declarations' scopes, and the
    definitions whose declaration had not been walked yet."""

    def __init__(self) -> None:
        self._by_offset: dict[int, _Scope] = {}
        self._pending: list[tuple[Any, ...]] = []

    def remember(
        self, decl: Any, scope: str, scope_path: tuple[Any, ...], default_access: Any
    ) -> None:
        self._by_offset[decl.offset] = _Scope(scope, scope_path, default_access)

    def scope_of(self, decl: Any) -> _Scope | None:
        return self._by_offset.get(decl.offset)

    def resolve(
        self,
        die: Any,
        CU: Any,
        scope: str,
        scope_path: tuple[Any, ...],
        default_access: Any,
        *,
        retry: bool,
    ) -> tuple[str, Any, str, tuple[Any, ...], Any] | None:
        """``(name, declaration, scope, scope_path, default_access)`` of the
        subprogram *die*, or ``None`` when it cannot be recorded (yet).

        An in-class declaration answers for itself and is remembered on the
        way. A definition linked to a declaration takes that declaration's
        scope (and its name, when it has none of its own); one whose
        declaration the walk has not passed yet is deferred (unless this is
        the *retry*), and a member one with no linkage name is not a
        concrete function. A named DIE with no declaration link answers for
        itself (its declaration DIE is ``None``).
        """
        name = attr_str(die, "DW_AT_name")
        if attr_bool(die, "DW_AT_declaration"):
            self.remember(die, scope, scope_path, default_access)
            return (name, None, scope, scope_path, default_access) if name else None
        decl = member_declaration(die, CU)
        if decl is None:
            return (name, None, scope, scope_path, default_access) if name else None
        found = self.scope_of(decl)
        if found is None:
            if not retry:
                self.defer((die, CU, scope, scope_path, default_access))
                return None
            # The declaration never came by: a named definition still
            # answers for itself, from where it sits.
            return (name, None, scope, scope_path, default_access) if name else None
        name = name or attr_str(decl, "DW_AT_name")
        # No linkage name anywhere on the chain: an ``extern "C"`` function
        # (its symbol is its name), never a member, which is always mangled.
        if not name or (
            is_member_scope(found.scope_path) and not chained_linkage_name(die, CU)
        ):
            return None
        return name, decl, found.scope, found.scope_path, found.default_access

    def defer(self, args: tuple[Any, ...]) -> None:
        self._pending.append(args)

    def drain(self) -> Iterator[tuple[Any, ...]]:
        """The deferred definitions, once each; the memory is then cleared
        (declarations do not cross compile units)."""
        pending, self._pending = self._pending, []
        yield from pending
        self._by_offset.clear()
