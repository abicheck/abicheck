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
cv/ref-qualifier) identity matches an already-known virtual slot inherited
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

**Signature identity is NOT slot identity** (Codex review, fresh evidence,
real gap found and fixed after the first version of this module landed): a
signature match is only a *candidate for* an override, never the slot
itself, because two UNRELATED bases can independently declare an
identically-signed virtual method — ``struct D : B1, B2`` where both `B1`
and `B2` declare `virtual void q();` with no inheritance relationship
between them. Confirmed with a minimal repro that the naive "one dict keyed
by signature" design from the first version collapsed these onto ONE slot,
silently discarding one of the two real, independent vtable-group entries.
Per [class.virtual], a *further* override in `D` — `void q() override;` —
is actually valid and becomes the final overrider for BOTH slots at once
(the same "non-virtual multiple inheritance" case castxml's own
``_resolved_override_keys`` docstring documents handling via a multi-id
``overrides`` attribute). So this module tracks two structures together: an
ordered ``slots: dict[key, mangled]`` (the actual per-physical-slot
occupant, one key per real vtable-group entry — keyed on Python object
identity of the introducing node, ``id(child)``, guaranteed unique and
free within one parse) and a ``sig_index: dict[signature, list[key]]``
(which currently-live physical keys a given signature answers to, unioned
across bases without collapsing). A signature match resolves through
``sig_index`` to every matching physical key and replaces all of them —
one new key when the signature is unseen, N replacements when N unrelated
bases already answer to it.

Two more real gaps found in the same review round, both in what counts as
matching *identity*, not in the slot-vs-signature distinction above:

1. **Base name resolution must prefer clang's own desugared spelling.**
   `namespace ns { struct A {...}; struct C : A {...}; }` — the ordinary,
   idiomatic unqualified spelling for a base declared in the SAME namespace
   as the derived class — reports `type.qualType == "A"` (bare), not
   `"ns::A"`, confirmed with a real clang build; a type-alias base
   (`using AliasA = ns::A; struct D : AliasA {...};`) reports
   `qualType == "AliasA"` similarly. Both cases carry a SEPARATE
   `desugaredQualType` field with the fully-resolved `"ns::A"` spelling
   whenever it differs from `qualType` (confirmed absent when a base is
   already spelled fully-qualified, e.g. `struct C : ns::A {...}` written
   explicitly — no redundant field when there's nothing to desugar). Since
   ``records_by_qualname`` is keyed on the fully-qualified form
   (``dumper_clang._record_index``), reading only `qualType` silently
   failed to resolve either of these common shapes. Fixed by preferring
   `desugaredQualType` when present, falling back to `qualType`.
2. **CV/ref-qualifiers beyond `const` participate in override identity
   too.** `virtual void f() &;` / `virtual void g() volatile;` are real,
   confirmed-compiling declarations (ref-qualified and volatile-qualified
   member functions), and a derived re-declaration that drops the
   qualifier is a DIFFERENT signature, not an override — matching on a
   bare `is_const: bool` (the first version of this module) would have
   incorrectly treated `void f() &&` or unqualified `void f()` as
   overriding `virtual void f() &`. Fixed by keeping the qualifier tail as
   a full normalized string (whitespace-collapsed) in the signature key
   instead of reducing it to a single boolean.

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

A second known limitation, confirmed real but deliberately NOT attempted
here: a **non-virtual diamond** -- ``struct X { virtual void q(); };
struct L : X {}; struct R : X {}; struct Z : L, R {};`` (compiles fine;
only an unqualified, ambiguous call to `z.q()` would error, and nothing
here constructs one) -- genuinely gives `Z` TWO physical `X::q` vtable-group
slots, one per `X` subobject. This module's `seen` set (and physical-slot
identity, ``id(child)``) is global across the whole recursion, keyed on
the single AST node that declares `X::q` -- there is only ONE such node in
the whole translation unit regardless of how many derived paths reach it,
so both the `Z->L->X` and `Z->R->X` paths resolve to the identical
physical key and collapse onto one slot instead of two. Confirmed this is
NOT a clang-specific regression: castxml's own ``_collect_virtual_methods``
(``dumper_castxml.py``) shares the identical shape of limitation -- its
`seen` set is likewise threaded globally through `_inherited_vtable_slots`,
and its own per-method identity (`_virtual_methods_by_class`) is populated
once per XML `Method` element in a single pass over the whole document, so
`X::q`'s single XML element is equally reachable-once regardless of path.
Fixing this for real needs path-local (not global) slot identity threaded
through the recursion, for BOTH backends together -- fixing only this one
would trade one asymmetry (a total gap, closed) for a new one (clang more
precise than castxml on this specific shape), which is its own scoped
follow-up, not a drive-by extension of this pass.
"""

from __future__ import annotations

import re
from typing import Any

from .dumper_clang_expr import _SCOPE_NODE_KINDS


def _is_record_definition(node: dict[str, Any]) -> bool:
    """Whether a record node is a definition (has a body) vs. a forward decl.

    Shared by ``dumper_clang.py``'s own ``_record_index()`` (an ordinary
    ``CXXRecordDecl``/``RecordDecl``) and :func:`build_specialization_index`
    below (a ``ClassTemplateSpecializationDecl``) -- both need the identical
    "prefer a complete definition over a forward-decl stub sharing the same
    qualname" tie-break, and both node kinds carry the same
    ``completeDefinition``/member-child shape.
    """
    if node.get("completeDefinition"):
        return True
    return any(
        isinstance(c, dict)
        and c.get("kind") in ("FieldDecl", "AccessSpecDecl", "CXXMethodDecl")
        for c in node.get("inner", []) or []
    )


#: Sentinel signature key for a destructor slot -- unifies a base's own
#: `~Base` and a derived class's own `~Derived` under one signature-index
#: entry, since their literal names never match but they occupy the SAME
#: vtable slot whenever either is virtual. No real method can be named
#: this (a C++ identifier can't start with `~` followed by this exact
#: spelling), so there is no risk of an ordinary method colliding with it.
_DTOR_SLOT_KEY: tuple[str, tuple[str, ...], bool, str] = ("~dtor~", (), False, "")

#: Node kinds that can occupy a vtable slot. Constructors, fields, and
#: everything else are structurally excluded (a constructor is never
#: virtual in C++; this mirrors castxml's own ``tag in ("Method",
#: "Destructor")`` gate in ``dumper_castxml.py``).
_METHOD_KIND = "CXXMethodDecl"
_DESTRUCTOR_KIND = "CXXDestructorDecl"
#: A virtual conversion operator (``operator int() const``, ...) is a
#: separate clang node kind, not a ``CXXMethodDecl`` -- confirmed with a
#: real clang build that ``struct A { virtual operator int() const; };``
#: emits a ``CXXConversionDecl`` carrying the same ``name``/``mangledName``/
#: ``type``/``virtual`` shape a method does (``name`` is the spelled target,
#: e.g. ``"operator int"``, which already makes two different conversion
#: targets distinct signatures for free). Handled identically to
#: ``_METHOD_KIND`` everywhere below (Codex review, fresh evidence: this
#: kind was silently excluded entirely, so a virtual conversion operator
#: never entered the vtable regardless of virtuality).
_CONVERSION_KIND = "CXXConversionDecl"

#: A physical vtable-slot identity: unique per introducing declaration
#: within one parse (``id(child)``, Python object identity -- not clang's
#: own hex-string "id" attribute, which this module never needs).
_SlotKey = object
_Signature = tuple[str, tuple[str, ...], bool, str]


#: A ``*`` immediately followed by a qualifier word, with NO space in
#: between -- clang's actual spelling for the FIRST trailing qualifier on a
#: pointer (confirmed against real clang: ``"int *const"``,
#: ``"int *volatile"``, ``"int *__restrict"``, never ``"int * const"``). A
#: SECOND stacked qualifier word IS space-separated from the first
#: (``"int *const volatile"``, ``"int *const __restrict"``) -- this
#: asymmetry is why a naive fixed-space trailing-suffix check silently
#: never matched the single-qualifier case at all (Codex review, fresh
#: evidence -- a real, deeper bug in an earlier version of this function
#: that only happened to pass its own tests because they never exercised a
#: genuine ``"T* const"`` positive-match case end to end). Rather than
#: special-casing "first word glued, rest space-separated," this pattern
#: normalizes the asymmetry away up front: inserting a space after every
#: ``*`` immediately followed by a letter/underscore makes every trailing
#: qualifier word uniformly space-separated, so one plain trailing-word
#: strip loop below handles every stacking combination correctly.
_POINTER_QUALIFIER_GLUE = re.compile(r"\*(?=[A-Za-z_])")


def _normalize_param_type(qualtype: str) -> str:
    """Strip a *top-level* cv/``__restrict`` qualifier from a parameter's
    spelling, the same normalization the Itanium mangler itself performs on
    a by-value parameter (confirmed empirically: ``void f(const int)`` and
    ``void f(int)`` both mangle to the identical ``...fEi`` tail;
    ``void a(int* const p)``, ``void d(int* volatile p)``, and
    ``void e(int* __restrict p)`` all mangle without any qualifier marker
    on the pointer). A *pointee*-level qualifier (``const int *``,
    ``const int &``) is never top-level and must NOT be stripped --
    confirmed it DOES survive mangling (``...bEPKi``).

    Two shapes, both confirmed against real clang spellings, after
    ``_POINTER_QUALIFIER_GLUE`` normalizes away clang's glued-vs-separated
    inconsistency for the first vs. subsequent trailing qualifier word:

    - a LEADING ``"const "``/``"volatile "`` word applies to a plain
      (non-pointer, non-reference) value type -- stripped, looping to
      handle both stacked together (``"const volatile int"``);
    - a TRAILING ``" const"``/``" volatile"``/``" __restrict"`` word applies
      to the pointer itself (``"int * const"`` after glue-normalization,
      ``"int * const __restrict"``) -- looped so a stack of these strips
      down to the bare pointer, checked by exact suffix so a *nested*
      one-level-in pointer-to-const-pointer (``"int *const *"``, mangles to
      ``PKPi`` -- the K survives) is never touched, since that string
      doesn't end with any of these words at all (the glue regex only
      matches a ``*`` immediately followed by a letter, not one followed by
      another ``*``).

    A base declaring ``__restrict`` accepts a derived override that drops
    it (``__restrict`` is a hint, not part of the type for
    overload/override purposes), so it's stripped the same as `const`/
    `volatile` rather than kept as part of the identity.

    Deliberately conservative beyond these two shapes: a type this doesn't
    fully canonicalize just stays as its own (still internally consistent)
    key rather than risking a wrong strip -- the goal is base and derived
    agreeing on a matching key for a genuinely equal signature, not a
    complete canonical-type printer.
    """
    s = _POINTER_QUALIFIER_GLUE.sub("* ", qualtype.strip())
    changed = True
    while changed:
        changed = False
        for word in (" const", " volatile", " __restrict"):
            if s.endswith(word):
                s = s[: -len(word)].rstrip()
                changed = True
                break
    if "*" not in s and "&" not in s:
        changed = True
        while changed:
            changed = False
            for prefix in ("const ", "volatile "):
                if s.startswith(prefix):
                    s = s[len(prefix) :]
                    changed = True
    return s


def _param_type_spelling(child: dict[str, Any]) -> str:
    """A ``ParmVarDecl``'s type, preferring clang's desugared spelling.

    A parameter typed through an alias (``using I = int; virtual void
    f(I);``) reports ``qualType: "I"`` with the resolved ``int`` only in a
    separate ``desugaredQualType`` field -- confirmed with a real clang
    build: the base's `f(I)` and a derived override's plain `f(int)` mangle
    to an *identical* parameter encoding (typedefs are transparent to
    Itanium mangling), but reading only `qualType` made them compare as
    different signatures (Codex review, fresh evidence -- the same
    qualType-vs-desugaredQualType gap ``_base_qualnames`` already had to
    handle for base specifiers, reproduced here for parameter types).
    """
    type_obj = child.get("type")
    if not isinstance(type_obj, dict):
        return ""
    return str(type_obj.get("desugaredQualType") or type_obj.get("qualType") or "")


def _method_signature_key(node: dict[str, Any]) -> _Signature | None:
    """``(name, param_qualtypes, is_variadic, qualifier_tail)`` identity for
    a ``CXXMethodDecl``/``CXXConversionDecl``.

    Deliberately excludes the return type (covariant returns are a
    different spelling for the SAME slot, never a different slot).
    ``qualifier_tail`` keeps the full cv/ref-qualifier suffix
    (``"const"``, ``"volatile"``, ``"&"``, ``"const &&"``, ...) since all
    of it participates in override identity — reducing this to a single
    ``is_const`` boolean (an earlier version of this function) incorrectly
    treated a ref-qualifier or `volatile`-qualifier mismatch as a match
    (Codex review, fresh evidence). ``is_variadic`` is tracked separately
    from ``param_qualtypes`` because a variadic parameter (``...``) is NOT
    a ``ParmVarDecl`` child at all -- confirmed with a real clang build
    that ``virtual void g(int, ...);`` and a derived, genuinely unrelated
    `void g(int);` both report the identical single `ParmVarDecl` list,
    with the `...` visible only inside the outer function `qualType`
    string (and in the two methods' distinct manglings, `...gEiz` vs
    `...gEi`) -- omitting it let an unrelated fixed-arity overload replace
    a variadic base slot (Codex review, fresh evidence). ``None`` for an
    unnamed node (shouldn't occur for a real method, but keeps this total
    rather than raising).
    """
    name = node.get("name")
    if not name:
        return None
    params = tuple(
        _normalize_param_type(_param_type_spelling(child))
        for child in node.get("inner", []) or []
        if isinstance(child, dict) and child.get("kind") == "ParmVarDecl"
    )
    type_obj = node.get("type")
    qual_type = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
    paren_end = _top_level_param_list_close(qual_type)
    # Everything after the parameter list's OWN matching close paren is the
    # cv/ref/exception-spec suffix; splitting off a trailing `noexcept(...)`
    # defensively before keeping the rest avoids folding noexcept's own
    # operand into the qualifier identity (not observed in this clang
    # version's output, but cheap to guard). Whitespace-collapsed so
    # "const  &" and "const &" (if either ever occurs) compare equal.
    tail = qual_type[paren_end + 1 :] if paren_end != -1 else ""
    qualifier_tail = " ".join(tail.split("noexcept", 1)[0].split())
    # The ellipsis, when present, is always the last token inside the
    # parameter-list parens (C++ forbids a fixed parameter after `...`), so
    # checking the text immediately before the matched closing paren is
    # exact -- no need to parse the parameter list itself.
    is_variadic = (
        qual_type[:paren_end].rstrip().endswith("...") if paren_end != -1 else False
    )
    return (str(name), params, is_variadic, qualifier_tail)


def _top_level_param_list_close(qual_type: str) -> int:
    """Index of the parameter list's OWN matching close paren in a
    function's ``qualType`` spelling, or ``-1`` if none is found.

    NOT ``qual_type.rfind(")")`` -- a C++14+ ref-qualified/exception-spec
    declaration (``"void () & throw()"``) has its OWN trailing ``()`` from
    the exception specification, which is textually LAST, so a plain
    right-to-left search finds `throw()`'s close paren instead of the
    parameter list's (confirmed against real clang output: both a base
    ``virtual void g() & throw();`` and an unrelated derived
    ``void g() && throw();`` reduce to an IDENTICAL empty qualifier tail
    under the naive `rfind` search, since the last `)` sits at the very end
    of the string for both -- the ref-qualifier difference that should keep
    them distinct signatures is silently discarded, misclassifying the
    derived declaration as an override that replaces the base's slot
    in-place (Codex review, fresh evidence); the same masking also breaks
    variadic detection when a parenthesized exception spec follows `...`).
    Mirrors ``_function_qualifiers`` (``dumper_clang.py``) exactly: find the
    FIRST top-level ``(`` (skipping over ``<...>``/``[...]`` nesting, e.g. a
    template-typed return type or an array-typed parameter), then walk
    forward counting paren depth until it closes -- correctly stepping over
    a nested parenthesized sub-expression inside the parameter list itself
    (a function-pointer parameter) without stopping early.
    """
    bracket = 0
    for idx, ch in enumerate(qual_type):
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(" and bracket == 0:
            depth = 1
            j = idx + 1
            while j < len(qual_type) and depth:
                if qual_type[j] == "(":
                    depth += 1
                elif qual_type[j] == ")":
                    depth -= 1
                j += 1
            return j - 1
    return -1


def _has_override_attr(node: dict[str, Any]) -> bool:
    return any(
        isinstance(child, dict) and child.get("kind") == "OverrideAttr"
        for child in node.get("inner", []) or []
    )


def _base_qualnames(node: dict[str, Any]) -> list[str]:
    """Direct + virtual base qualified names, in ``bases`` array order.

    Prefers ``type.desugaredQualType`` over ``type.qualType`` when clang
    supplies both: an ordinary unqualified base spelling inside the SAME
    namespace as the derived class (`struct C : A {...}` where `A` is
    `ns::A`), or a type-alias base (`struct D : AliasA {...}`), reports the
    written, non-canonical spelling in `qualType` and only carries the
    fully-qualified form in `desugaredQualType` -- confirmed with real
    clang builds of both shapes. `desugaredQualType` is absent (not merely
    identical) whenever a base is already written fully-qualified, so
    preferring it never loses information.

    Doesn't distinguish virtual from non-virtual bases -- castxml's own
    inherited-slot walk (``_inherited_vtable_slots``) doesn't either, since
    every base contributes to the derived class's *set of virtual methods
    it must provide slots for*, regardless of how that base is placed at
    runtime.
    """
    out: list[str] = []
    for b in node.get("bases", []) or []:
        if not isinstance(b, dict):
            continue
        type_obj = b.get("type")
        if not isinstance(type_obj, dict):
            continue
        bname = str(type_obj.get("desugaredQualType") or type_obj.get("qualType") or "")
        if bname:
            out.append(bname)
    return out


def _collect_virtual_slots(
    qualname: str,
    records_by_qualname: dict[str, dict[str, Any]],
    seen: set[str],
) -> tuple[dict[_SlotKey, str], dict[_Signature, list[_SlotKey]]]:
    """``(slots, sig_index)`` for *qualname*'s vtable.

    ``slots`` is the ordered physical-slot-key -> mangled-name occupant map
    (the actual vtable content, one entry per real vtable-group slot).
    ``sig_index`` is signature -> every currently-live physical key that
    signature resolves to (kept as a list, not a single key, so two
    unrelated bases sharing a signature stay two distinct slots until a
    genuine override collapses them -- see module docstring).

    Recurses into bases first (their own slots/sig_index seed the result,
    unioned rather than overwritten), then walks this record's own
    children in declaration order: a signature match replaces every
    candidate physical key's occupant in place (preserving each one's own
    insertion position, same as castxml), a genuinely new virtual creates
    one fresh physical key and appends.
    """
    if qualname in seen:
        return {}, {}
    seen.add(qualname)
    node = records_by_qualname.get(qualname)
    if node is None:
        return {}, {}

    slots: dict[_SlotKey, str] = {}
    sig_index: dict[_Signature, list[_SlotKey]] = {}
    for base_qualname in _base_qualnames(node):
        base_slots, base_sig_index = _collect_virtual_slots(
            base_qualname, records_by_qualname, seen
        )
        slots.update(base_slots)
        for sig, keys in base_sig_index.items():
            existing = sig_index.setdefault(sig, [])
            for key in keys:
                if key not in existing:
                    existing.append(key)

    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        mangled = str(child.get("mangledName", "")) or str(child.get("name", ""))
        if not mangled:
            continue
        sig_or_none: _Signature | None
        if kind == _DESTRUCTOR_KIND:
            sig_or_none = _DTOR_SLOT_KEY
            candidates = sig_index.get(sig_or_none, [])
            if not (child.get("virtual") or candidates):
                continue
        elif kind in (_METHOD_KIND, _CONVERSION_KIND):
            sig_or_none = _method_signature_key(child)
            if sig_or_none is None:
                continue
            candidates = sig_index.get(sig_or_none, [])
            is_virtual = (
                bool(child.get("virtual"))
                or _has_override_attr(child)
                or bool(candidates)
            )
            if not is_virtual:
                continue
        else:
            continue
        resolved_sig: _Signature = sig_or_none

        if candidates:
            for key in candidates:
                slots[key] = mangled
        else:
            key = id(child)
            slots[key] = mangled
            sig_index.setdefault(resolved_sig, []).append(key)

    return slots, sig_index


def build_vtable(
    qualname: str, records_by_qualname: dict[str, dict[str, Any]]
) -> list[str]:
    """Ordered mangled-name vtable for the record named *qualname*.

    ``records_by_qualname`` is the caller's own ``"::".join(scope + [name])
    -> node`` index over every parsed ``CXXRecordDecl``/``RecordDecl`` in
    this translation unit (``dumper_clang.py``'s ``_record_index()``,
    which itself prefers a complete definition over a forward-declaration
    stub sharing the same qualname).
    """
    slots, _ = _collect_virtual_slots(qualname, records_by_qualname, set())
    return list(slots.values())


#: Non-type template parameter types whose Clang JSON ``value`` (an
#: evaluated integer, e.g. ``3``) is CONFIRMED to print identically to how a
#: base reference spells the same argument (``"A<3>"``) -- verified against
#: a real clang build. Deliberately narrow: ``bool``'s own encoding does
#: NOT round-trip this way (``template <bool B> struct A; ... A<true>``
#: reports ``value: -1``, not ``1``, and the base reference still spells it
#: ``"A<true>"`` -- confirmed empirically, Codex review, fresh evidence), and
#: neither an enum (spelled by its enumerator name, e.g. ``"A<Color::Red>"``,
#: never its integer value) nor a pointer/floating-point/structural
#: (C++20) non-type argument has any reason to share this property. Only a
#: parameter whose declared type is exactly one of these plain builtin
#: integer spellings is trusted; every other non-type parameter makes the
#: whole specialization unindexable (see :func:`_specialization_spelling`).
_SAFE_NONTYPE_INT_TYPES = frozenset(
    {
        "int",
        "unsigned int",
        "long",
        "unsigned long",
        "long long",
        "unsigned long long",
        "short",
        "unsigned short",
    }
)


def _template_param_kinds(class_template_decl: dict[str, Any]) -> list[str | None]:
    """Per-position "trusted plain-integer non-type parameter" marker for a
    ``ClassTemplateDecl``'s own parameter list, in declaration order --
    matching the order ``TemplateArgument`` children of one of its
    specializations are emitted in (confirmed with a real clang build).

    Each entry is the parameter's own type spelling when it's a
    non-type parameter declared with one of :data:`_SAFE_NONTYPE_INT_TYPES`,
    or ``None`` for a type parameter (a type argument carries its own
    ``type.qualType`` directly -- this list is never consulted for it) or an
    untrusted non-type parameter (``bool``, an enum, a pointer, ...). Stops
    at the first non-parameter child: the parameter list always precedes
    the pattern's own body/specializations in ``inner`` order.
    """
    kinds: list[str | None] = []
    for child in class_template_decl.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind == "NonTypeTemplateParmDecl":
            type_obj = child.get("type")
            spelling = (
                str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
            )
            kinds.append(spelling if spelling in _SAFE_NONTYPE_INT_TYPES else None)
        elif kind in ("TemplateTypeParmDecl", "TemplateTemplateParmDecl"):
            kinds.append(None)
        else:
            break
    return kinds


def _index_template_param_kinds(root: dict[str, Any]) -> dict[str, list[str | None]]:
    """``qualified template name -> per-position param-kind list`` (see
    :func:`_template_param_kinds`) over every ``ClassTemplateDecl`` in the
    AST, scope-tracked the identical way :func:`build_specialization_index`
    tracks it for a specialization -- so a specialization found under a
    given scope+name looks its owning template's parameter list up under
    that SAME scope+name (a specialization is always either nested inside
    its own ``ClassTemplateDecl`` or a sibling of it at the same scope
    depth, confirmed with a real clang build for both an implicit and an
    explicit specialization). A redeclaration of the same template (e.g.
    seen again through a second ``#include``) shares an identical parameter
    list, so first-registration-wins is safe.
    """
    idx: dict[str, list[str | None]] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateDecl" and name:
            qualname = "::".join((*scope, name)) if scope else name
            idx.setdefault(qualname, _template_param_kinds(node))
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return idx


def _specialization_spelling(
    node: dict[str, Any], name: str, param_kinds: list[str | None] | None
) -> str | None:
    """A ``ClassTemplateSpecializationDecl``'s own ``Name<Arg1, Arg2>``
    spelling, reconstructed from its direct ``TemplateArgument`` children --
    matching what clang's own type printer produces on a base-reference
    site's ``type.qualType``/``desugaredQualType`` (confirmed with real
    clang builds, see :func:`build_specialization_index`'s docstring for the
    exact repros this reproduces).

    ``None`` when any argument isn't reproducible with confidence: a type
    argument's own ``type.qualType`` is always trusted, but a non-type
    argument's raw ``value`` is trusted ONLY when *param_kinds* confirms
    (by position) that the corresponding template parameter is one of
    :data:`_SAFE_NONTYPE_INT_TYPES` -- a ``bool``/enum/pointer/other
    non-type argument's ``value`` does not reliably print the same way the
    referring site spells it (see that set's own docstring), so the whole
    specialization is left unindexed rather than guessed at. Also ``None``
    for a template-template argument or a pack expansion (no ``type`` or
    ``value`` field on that argument at all) or when there are no arguments
    at all. The caller skips indexing entirely in every ``None`` case,
    degrading to the same already-accepted "unresolvable base"
    false-negative every other unresolvable-base shape in this module
    already degrades to -- never a false positive.
    """
    args: list[str] = []
    idx = 0
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict) or child.get("kind") != "TemplateArgument":
            continue
        type_obj = child.get("type")
        if isinstance(type_obj, dict) and type_obj.get("qualType"):
            args.append(str(type_obj["qualType"]))
            idx += 1
            continue
        if "value" in child:
            safe = param_kinds[idx] if param_kinds and idx < len(param_kinds) else None
            if safe is None:
                return None
            args.append(str(child["value"]))
            idx += 1
            continue
        return None
    if not args:
        return None
    return f"{name}<{', '.join(args)}>"


def build_specialization_index(root: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``qualified spelling -> node`` index over every concrete
    ``ClassTemplateSpecializationDecl`` reachable from the parsed AST, for
    :func:`build_vtable`'s base-lookup recursion (via
    ``dumper_clang.py``'s ``_ClangAstParser._base_lookup_index``, which
    merges this with its own ``_record_index()``).

    ``dumper_clang.py``'s own categorizing walk (``self._records``, and
    therefore its ``_record_index()``) only ever collects
    ``CXXRecordDecl``/``RecordDecl`` nodes -- a concrete template
    specialization used as a base (``struct D : A<int> {...};``) is a
    DIFFERENT clang node kind entirely (``ClassTemplateSpecializationDecl``,
    confirmed with a real clang build: nested inside its own
    ``ClassTemplateDecl``, sibling to the uninstantiated pattern's own
    ``CXXRecordDecl``, and never visited by that walk's ``kind in
    ("CXXRecordDecl", "RecordDecl")`` check), so it was never reachable from
    the base-lookup index at all. An old ``D`` deriving from ``A<int>``
    resolved to an empty `vtable`/no vptr, and adding a no-keyword override
    in a new ``D`` then made the vtable appear to gain its FIRST entry -- a
    false breaking ``VPTR_INTRODUCED`` (Codex review, fresh evidence).

    Unlike an ordinary record, a specialization's own ``name`` is always the
    BARE primary-template name (``"A"``, never ``"A<int>"``, confirmed with
    a real clang build) -- the template-argument spelling only exists on the
    REFERRING site's own ``type.qualType``/``desugaredQualType`` (what
    ``_base_qualnames`` already reads for an ordinary base). So this
    reconstructs the matching spelling itself from each direct
    ``TemplateArgument`` child, via :func:`_specialization_spelling` -- a
    type argument's own ``type.qualType``, or a non-type argument's own
    ``value`` -- joined with ``", "``, confirmed against real clang output
    to exactly reproduce the base-reference spelling for both a namespaced
    two-type-argument specialization (``"ns::A<int, double>"``) and a
    non-type-argument one (``"A<3>"``). A specialization carrying any OTHER
    kind of argument (template-template, pack expansion -- no ``type`` or
    ``value`` field on that argument) is skipped entirely rather than
    guessed at: an unindexed specialization degrades to the same
    already-accepted "unresolvable base" false-negative every other
    unresolvable-base shape in this module already degrades to (see the
    module docstring's own "known limitation" section) -- never a false
    positive.

    Scope-tracked the same way ``dumper_clang.py``'s own ``_walk`` tracks it
    for an ordinary record (extended on ``NamespaceDecl``/``CXXRecordDecl``/
    ``RecordDecl``/``LinkageSpecDecl``, via the shared ``_SCOPE_NODE_KINDS``
    -- a ``ClassTemplateDecl``/``ClassTemplateSpecializationDecl`` itself is
    deliberately NOT scope-forming here, matching ``_SCOPE_NODE_KINDS``'s
    own membership) so a namespaced template's specialization indexes under
    its fully-qualified spelling, not a bare one. A dedicated whole-AST walk
    (independent of ``dumper_clang.py``'s own categorizing walk, the same
    shape as ``dumper_clang_expr._index_decl_id_qualified_names``) since a
    specialization is never collected by that walk at all.

    A forward-declared explicit specialization (``template<> struct
    A<int>;``) followed later by its complete definition (``template<>
    struct A<int> { ... };``) emits TWO ``ClassTemplateSpecializationDecl``
    nodes sharing the identical spelling -- confirmed with a real clang
    build, the same forward-decl-shadows-definition shape
    ``dumper_clang._ClangAstParser._record_index`` already guards against
    for an ordinary record. A first-registration-wins policy here would
    permanently keep the empty forward-decl stub whenever it's walked
    first (Codex review, fresh evidence), so a complete definition always
    wins over a forward-declaration stub for the same spelling, regardless
    of walk order, via the shared :func:`_is_record_definition`.
    """
    param_kinds_by_qualname = _index_template_param_kinds(root)
    idx: dict[str, dict[str, Any]] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateSpecializationDecl" and name:
            template_qualname = "::".join((*scope, name)) if scope else name
            spelling = _specialization_spelling(
                node, name, param_kinds_by_qualname.get(template_qualname)
            )
            if spelling is not None:
                qualname = "::".join((*scope, spelling)) if scope else spelling
                existing = idx.get(qualname)
                if existing is None or (
                    not _is_record_definition(existing) and _is_record_definition(node)
                ):
                    idx[qualname] = node
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return idx
