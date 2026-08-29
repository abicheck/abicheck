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

This module's own template-specialization scope/spelling reconstruction
(``build_specialization_index`` and everything it depended on) moved to
``extract.headers.clang.templates`` (ADR-061 Phase 5 item 1's fourth and
final entity module) — re-exported at the tail of this module so every
existing direct import of one of those names off this module keeps
resolving. See that module's own docstring for the full account, including
why the re-export has to be a plain module-level import here while the new
module's own read of this module's ``is_record_definition`` has to stay
function-local (the two modules import each other, so at least one edge
must be lazy to avoid a real circular-import deadlock).

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


def is_record_definition(node: dict[str, Any]) -> bool:
    """Whether a record node is a definition (has a body) vs. a forward decl.

    Shared by ``_record_index()``, :func:`build_specialization_index` below,
    and ``RecordVtableIndex`` -- all need the "prefer a definition over a
    forward-decl stub sharing the qualname" tie-break. Public (Codex, PR
    #940); ``_is_record_definition`` below is an alias.
    """
    if node.get("completeDefinition"):
        return True
    return any(
        isinstance(c, dict)
        and c.get("kind") in ("FieldDecl", "AccessSpecDecl", "CXXMethodDecl")
        for c in node.get("inner", []) or []
    )


_is_record_definition = is_record_definition  # back-compat alias

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
    # cv/ref/exception-spec suffix. An exception specification -- either a
    # `noexcept(...)` or a C++14-and-earlier DYNAMIC one (`throw(int)`,
    # `throw()`) -- is not part of override identity: a C++14+ override may
    # legally NARROW a base's dynamic spec (confirmed with a real clang
    # build: `virtual void f() throw(int);` overridden by `void f()
    # throw() override;` compiles fine), so a mismatched exception spec
    # must not make an otherwise-identical override compare as a different
    # signature (Codex review, fresh evidence: the previous version only
    # stripped `noexcept`, so `throw(int)` vs `throw()` produced two
    # different qualifier tails and the override appended as a spurious
    # second slot instead of replacing the inherited one). Cuts at
    # whichever of the two markers appears first, so this is safe
    # regardless of which one is textually present -- a function's own
    # exception spec is always exactly one of them, never both, but
    # nothing here assumes it is that in advance. Whitespace-collapsed so
    # "const  &" and "const &" (if either ever occurs) compare equal.
    tail = qual_type[paren_end + 1 :] if paren_end != -1 else ""
    cut = len(tail)
    for marker in ("noexcept", "throw"):
        marker_idx = tail.find(marker)
        if marker_idx != -1:
            cut = min(cut, marker_idx)
    qualifier_tail = " ".join(tail[:cut].split())
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
    base_qualnames = _base_qualnames(node)
    for base_qualname in base_qualnames:
        base_slots, base_sig_index = _collect_virtual_slots(
            base_qualname, records_by_qualname, seen
        )
        slots.update(base_slots)
        for sig, keys in base_sig_index.items():
            existing = sig_index.setdefault(sig, [])
            for key in keys:
                if key not in existing:
                    existing.append(key)

    # True when at least one of this record's OWN bases couldn't be
    # resolved to a node at all (as opposed to resolving but contributing
    # no virtuals) -- e.g. a template-dependent base in an unparsed header,
    # or a specialization `_specialization_spelling` declined to index
    # (an untrusted non-type argument like `bool`, confirmed real and
    # reproducible: Codex review, fresh evidence). In that case an own
    # member that carries an EXPLICIT `virtual`/`override` marker but
    # matches no known candidate is genuinely ambiguous -- it might be a
    # real new virtual, or it might be overriding something declared on
    # the invisible base, which this module has no way to see. Treating it
    # as unconditionally new (the previous behavior) produces a real,
    # reproducible false positive: an old snapshot with the unresolvable
    # base alone has an empty vtable, a new snapshot adding only the
    # explicit override gets one new slot, and the pair diffs as
    # `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED` even though nothing
    # observable actually changed. Suppressing the addition here converts
    # that into an accepted false negative (an own virtual member on such
    # a class is silently left out of the vtable) -- the same
    # false-negative-over-false-positive trade this module's degradation
    # posture already makes everywhere else (see the module docstring's
    # "known limitation" section, which this generalizes: that section's
    # own claim that an unresolved-base override is "never a false
    # positive" was true only for an IMPLICIT override -- an explicit one
    # WAS reachable as a false positive before this fix).
    any_base_unresolved = any(
        records_by_qualname.get(bq) is None for bq in base_qualnames
    )

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
        elif not any_base_unresolved:
            key = id(child)
            slots[key] = mangled
            sig_index.setdefault(resolved_sig, []).append(key)
        # else: an own member with no known candidate on a class with an
        # unresolved base -- ambiguous, suppressed (see comment above).

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


# ── template-specialization parsing (ADR-061 Phase 5 item 1) ──────────────
#
# Moved to ``extract.headers.clang.templates`` — re-exported (not just
# referenced) so every existing import of these names straight off this
# module (``from abicheck.dumper_clang_vtable import
# _index_template_param_defaults``, used directly by several tests) keeps
# resolving unchanged.
from .extract.headers.clang.templates import (  # noqa: E402,F401
    _SAFE_NONTYPE_INT_TYPES,
    _index_template_param_defaults,
    _index_template_param_kinds,
    _index_template_param_names,
    _register_template_param_metadata,
    _specialization_spelling,
    _template_param_defaults,
    _template_param_kinds,
    _template_param_names,
    build_specialization_index as _extract_build_specialization_index,
)


def build_specialization_index(
    root: dict[str, Any],
    param_kinds_by_qualname: dict[str, list[str | None]] | None = None,
    param_defaults_by_qualname: dict[str, list[str | None]] | None = None,
    param_names_by_qualname: dict[str, list[str | None]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Back-compat wrapper -- see
    :func:`abicheck.extract.headers.clang.templates.build_specialization_index`
    for the full contract. The extract implementation takes
    ``is_record_definition`` as a required keyword-only parameter (this
    module's own layering forbids it importing that predicate itself at
    module scope, see the module docstring above); this wrapper supplies
    it, so a direct import of ``build_specialization_index`` off this
    module keeps its pre-move, four-positional-argument call signature
    (Codex review, PR #940)."""
    return _extract_build_specialization_index(
        root,
        param_kinds_by_qualname,
        param_defaults_by_qualname,
        param_names_by_qualname,
        is_record_definition=is_record_definition,
    )
