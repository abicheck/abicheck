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

"""Direct-clang vtable reconstruction (G31 Phase C).

Split out of ``dumper_clang.py`` to stay under its line-count cap, mirroring
``dumper_clang_expr.py``'s split. A leaf module (must not import from
``dumper_clang`` to avoid an import cycle); ``dumper_clang.py`` imports back
``build_vtable`` and calls it from ``_build_record`` with a plain
``qualname -> node`` index it already builds from its own ``_records``.

Before this, the direct-clang backend hardcoded ``RecordType.vtable = []``
unconditionally and never set ``vptr_offset_bits`` at all — not an
imprecise heuristic like castxml's, a total gap. Two real detectors read
``rec.vtable`` structurally (``diff_layout._check_vptr_introduced``,
``diff_types``'s ``TYPE_VTABLE_CHANGED``), so both were silently inert for
every direct-clang-only comparison, not merely missing one fact.

Mirrors castxml's own ``_collect_virtual_methods``/``_build_vtable``
(``dumper_castxml.py``) in shape — same recursive base-then-own walk, same
"a derived override replaces its base's slot in place, a genuinely new
virtual appends" dict-ordering trick — but NOT in mechanism, because
clang's ``-ast-dump=json`` output is missing the one signal castxml's real
semantic analysis gives for free: **which of the derived class's own
methods are semantically virtual at all.**

Confirmed empirically (real ``clang++ -Xclang -ast-dump=json``, this
session): castxml/GCC-XML tags every effectively-virtual method
``virtual="1"`` in its own XML, including a re-declaration that overrides a
base's virtual method without repeating the ``virtual`` keyword and without
an ``override`` keyword either (implicit virtuality via pure signature
match — extremely common real-world style). Clang's JSON AST dumper does
NOT: a ``CXXMethodDecl`` gets ``"virtual": true`` in the JSON only when the
`virtual` keyword is literally written in source, and an ``OverrideAttr``
child only when the ``override`` keyword is written — an override that
writes neither (compiles fine, just emits clang's own
``-Winconsistent-missing-override`` warning) carries **no signal
whatsoever** in the JSON tree distinguishing it from an unrelated ordinary
method of the same name. (The equivalent *textual* ``-ast-dump`` output
DOES print an ``Overrides: [...]`` annotation for this exact case — this is
specifically a gap in the JSON serializer, not a fact clang's semantic
analysis lacks.) So this module reconstructs virtuality itself via
signature matching: a method is virtual if explicitly marked (`virtual`
keyword or ``OverrideAttr``), OR if its (name, parameter types,
const-qualifier) identity matches an already-known virtual slot inherited
from a base — the same test C++ override resolution itself applies.

A second, structurally different gap: a class's own destructor is
implicitly virtual whenever ANY base has a virtual destructor, regardless
of whether the class's own destructor is user-declared or
compiler-implicit, and regardless of any keyword — confirmed empirically
that neither case carries ``"virtual": true`` in the JSON (only the base's
*own* explicitly-virtual destructor does). Name-based signature matching
can't apply here either, since ``~Base``/``~Derived`` are never the same
string — handled via a fixed sentinel key (``_DTOR_SLOT_KEY``) so a base's
destructor slot and a derived class's destructor slot unify regardless of
each class's own name.

Ordering: castxml prefers castxml's own ``vtable_index`` attribute when
present, falling back to declaration order only when it's absent
(``_vt_sort_key`` in ``dumper_castxml.py``). Clang's JSON AST exposes no
equivalent numeric slot index at all (confirmed: no such attribute on any
``CXXMethodDecl``/``CXXDestructorDecl`` observed), so this module always
uses declaration order — base slots first (recursively, in each base's own
declaration order), each own member visited in ``inner`` array order,
inherited slot keys replaced in place (preserving the base's insertion
position) rather than appended. This exactly matches what castxml's own
``_vt_sort_key`` degrades to when every entry's index is ``None`` (a
stable sort over all-equal keys is a no-op), so the two backends produce
identically-shaped output whenever castxml also lacks index data.

Known limitation, accepted rather than solved here: a covariant return
type is deliberately excluded from the signature key (return type is never
part of override *identity* — C++ allows a covariant return, and the whole
point of matching is to recognize the SAME slot despite a differing
return spelling), but a template-dependent base whose own record isn't in
this TU's ``records_by_qualname`` (e.g. a base defined in an unparsed
header, or one this snapshot's dependency-scoping already excluded)
degrades the same way castxml degrades on an unresolvable ``Base`` XML
element: that base's own virtual methods are simply invisible, so an
override of one of ITS virtual methods is only caught if the override
itself carries an explicit ``virtual``/``override`` marker. This is a
false negative (an inherited slot silently not recognized as inherited,
so it might get double-counted as new), never a false positive — the same
conservative-degradation posture this codebase's other clang-side fixes
already use throughout (see ``type_reachability.py``'s own docstring).
"""

from __future__ import annotations

from typing import Any

#: Sentinel signature key for a destructor slot -- unifies a base's own
#: `~Base` and a derived class's own `~Derived` under one dict key, since
#: their literal names never match but they occupy the SAME vtable slot
#: whenever either is virtual. No real method can be named this (a C++
#: identifier can't start with `~` followed by this exact spelling), so
#: there is no risk of an ordinary method colliding with it.
_DTOR_SLOT_KEY: tuple[str, tuple[str, ...], bool] = ("~dtor~", (), False)

#: Node kinds that can occupy a vtable slot. Constructors, fields, and
#: everything else are structurally excluded (a constructor is never
#: virtual in C++; this mirrors castxml's own ``tag in ("Method",
#: "Destructor")`` gate in ``dumper_castxml.py``).
_METHOD_KIND = "CXXMethodDecl"
_DESTRUCTOR_KIND = "CXXDestructorDecl"


def _method_signature_key(
    node: dict[str, Any],
) -> tuple[str, tuple[str, ...], bool] | None:
    """``(name, param_qualtypes, is_const)`` identity for a ``CXXMethodDecl``.

    Deliberately excludes the return type (covariant returns are a
    different spelling for the SAME slot, never a different slot) and any
    ref-qualifier/``noexcept`` suffix (neither participates in override
    identity). ``None`` for an unnamed node (shouldn't occur for a real
    method, but keeps this total rather than raising).
    """
    name = node.get("name")
    if not name:
        return None
    params = tuple(
        str(child["type"]["qualType"])
        for child in node.get("inner", []) or []
        if isinstance(child, dict)
        and child.get("kind") == "ParmVarDecl"
        and isinstance(child.get("type"), dict)
        and "qualType" in child["type"]
    )
    type_obj = node.get("type")
    qual_type = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
    # The outer function type's own closing paren is always the LAST ")" in
    # the qualType string -- any parenthesized sub-expression inside a
    # parameter type (e.g. a function-pointer parameter) closes strictly
    # before it. Everything after it is the cv/ref/noexcept qualifier
    # suffix; splitting off a trailing `noexcept(...)` defensively before
    # checking for "const" avoids a false match from noexcept's own operand
    # (not observed in this clang version's output, but cheap to guard).
    tail = qual_type[qual_type.rfind(")") + 1 :] if ")" in qual_type else ""
    is_const = "const" in tail.split("noexcept", 1)[0]
    return (str(name), params, is_const)


def _has_override_attr(node: dict[str, Any]) -> bool:
    return any(
        isinstance(child, dict) and child.get("kind") == "OverrideAttr"
        for child in node.get("inner", []) or []
    )


def _base_qualnames(node: dict[str, Any]) -> list[str]:
    """Direct + virtual base qualified names, in ``bases`` array order.

    Mirrors ``dumper_clang._parse_bases``'s own extraction of
    ``type.qualType`` from each ``bases`` entry, but doesn't distinguish
    virtual from non-virtual -- castxml's own inherited-slot walk
    (``_inherited_vtable_slots``) doesn't either, since every base
    contributes to the derived class's *set of virtual methods it must
    provide slots for*, regardless of how that base is placed at runtime.
    """
    out: list[str] = []
    for b in node.get("bases", []) or []:
        if not isinstance(b, dict):
            continue
        type_obj = b.get("type")
        bname = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
        if bname:
            out.append(bname)
    return out


def _collect_virtual_slots(
    qualname: str,
    records_by_qualname: dict[str, dict[str, Any]],
    seen: set[str],
) -> dict[tuple[str, tuple[str, ...], bool], str]:
    """Ordered ``signature-key -> mangled name`` for *qualname*'s vtable.

    Recurses into bases first (their own slots seed the result), then walks
    this record's own children in declaration order, replacing an inherited
    key in place on an override (preserving the base's insertion position,
    same as castxml) or appending a genuinely new virtual at the end.
    """
    if qualname in seen:
        return {}
    seen.add(qualname)
    node = records_by_qualname.get(qualname)
    if node is None:
        return {}

    slots: dict[tuple[str, tuple[str, ...], bool], str] = {}
    for base_qualname in _base_qualnames(node):
        slots.update(_collect_virtual_slots(base_qualname, records_by_qualname, seen))

    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        mangled = str(child.get("mangledName", "")) or str(child.get("name", ""))
        if not mangled:
            continue
        if kind == _DESTRUCTOR_KIND:
            # Implicitly virtual the moment ANY base contributed the
            # destructor slot, or explicitly marked -- name comparison
            # never applies to destructors (see module docstring).
            if child.get("virtual") or _DTOR_SLOT_KEY in slots:
                slots[_DTOR_SLOT_KEY] = mangled
        elif kind == _METHOD_KIND:
            key = _method_signature_key(child)
            if key is None:
                continue
            is_virtual = (
                bool(child.get("virtual")) or _has_override_attr(child) or key in slots
            )
            if is_virtual:
                slots[key] = mangled

    return slots


def build_vtable(
    qualname: str, records_by_qualname: dict[str, dict[str, Any]]
) -> list[str]:
    """Ordered mangled-name vtable for the record named *qualname*.

    ``records_by_qualname`` is the caller's own ``"::".join(scope + [name])
    -> node`` index over every parsed ``CXXRecordDecl``/``RecordDecl`` in
    this translation unit (``dumper_clang.py``'s ``_record_index()``).
    """
    slots = _collect_virtual_slots(qualname, records_by_qualname, set())
    return list(slots.values())
