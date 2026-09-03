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

"""Declarator-grouping recognition and pointer-to-member/trailing-qualifier
normalization for :mod:`abicheck.model.signature_normalization`
(ADR-063 Phase 2).

Split out of ``signature_normalization.py`` (its only caller) purely to keep
that file under the AI-readiness gate's 800-line production maximum -- this
module's own contents are otherwise that module's, not a separate design
decision, the identical reason ``signature_normalization.py`` itself was
split out of ``model/identity.py`` one round earlier. See
``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 2 section for the
full review history (Codex/CodeRabbit, PR #941) behind each piece here.

Leaf module: imports nothing above ``model`` and no sibling module at all,
per ADR-063 D10 -- the same leaf-module contract ``model/identity.py`` and
``signature_normalization.py`` themselves state. In particular this module
does NOT import back from ``signature_normalization.py``: that module's own
``canonicalize_function_signature_param_type`` calls into this module (via
``_find_member_pointer_qualifier``/``_split_at_trailing_param_list``/
``_canonicalize_member_qualifiers``), so the reverse import would be a real
cycle between two sibling leaf modules -- ``_CV_WORD_RE`` is consequently
duplicated here rather than shared, since it is the one piece both modules
independently need.
"""

from __future__ import annotations

import re

__all__ = [
    "_CALLING_CONVENTIONS",
    "_is_declarator_group",
    "_find_member_pointer_qualifier",
    "_split_at_trailing_param_list",
    "_canonicalize_member_qualifiers",
]

_CV_WORD_RE = re.compile(r"\b(?:const|volatile)\b")

# A declarator-grouping paren's content, up to its own sigil: an optional
# MSVC calling-convention keyword, then either a bare pointer/reference
# (`*`, `&`) or a pointer-to-member's qualified-name prefix (`C::*`,
# `ns::C::*`, `C<int>::*` -- one or more `identifier[<template-args>]::`
# segments) then the sigil. Used to tell a declarator-grouping paren
# (transparent, not a real nesting level) from a genuine parameter-list
# paren (opaque): a parameter list's first token is always a type, which
# can itself start with an identifier, but is never a calling-convention
# keyword and never immediately followed by `::` then only a bare sigil --
# both shapes are unique to a declarator (Codex review, PR #941: the
# ninth round added the qualified-name-prefix form, so `void (C::*
# const)(int)` -- cv on a pointer-to-member's own outermost sigil -- was
# found at depth 0; the tenth round added the calling-convention keyword,
# since a real MSVC/PE calling-convention decoration -- e.g. `void
# (__cdecl * const)(int)` -- otherwise defeated the same transparency
# test the identical way; the eleventh round added the template-argument
# list, since a real nested-name-specifier's segment can itself be a
# template-id, e.g. `void (C<int>::* const)(int)`, which a plain
# `identifier::` match can't recognize -- checked with a manual scanner,
# not a single regex, since a template-argument list can nest arbitrarily
# deep, `Box<Pair<int, int>>::`, which `re`'s non-recursive matching
# cannot balance). The convention keyword and any template-argument list
# are matched, not consumed/erased -- they stay in the returned prefix
# verbatim, genuine parts of the type, same as the plain qualified-name
# prefix already does.
_CALLING_CONVENTIONS = (
    "__cdecl",
    "__stdcall",
    "__fastcall",
    "__thiscall",
    "__vectorcall",
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")


def _is_declarator_group(s: str, start: int) -> bool:
    """Whether ``s[start:]`` is a declarator-grouping paren's own content
    (see :data:`_CALLING_CONVENTIONS`'s module-level comment for the full
    shape) -- called with *start* right after the paren's own ``(``.
    """
    n = len(s)
    i = start
    while i < n and s[i] == " ":
        i += 1
    for conv in _CALLING_CONVENTIONS:
        if s.startswith(conv, i):
            i += len(conv)
            while i < n and s[i] == " ":
                i += 1
            break
    while True:
        j = i
        while j < n and s[j] == " ":
            j += 1
        m = _IDENTIFIER_RE.match(s, j)
        if not m:
            break
        k = m.end()
        if k < n and s[k] == "<":
            depth = 0
            p = k
            while p < n:
                if s[p] == "<":
                    depth += 1
                elif s[p] == ">":
                    depth -= 1
                    if depth == 0:
                        p += 1
                        break
                p += 1
            else:
                return False  # unmatched '<' -- malformed, bail out
            k = p
        while k < n and s[k] == " ":
            k += 1
        if not s.startswith("::", k):
            break
        i = k + 2
    while i < n and s[i] == " ":
        i += 1
    return i < n and s[i] in "*&"


def _extract_top_level_cv(s: str) -> tuple[bool, bool, str]:
    """Depth-aware scan: finds ``const``/``volatile`` tokens at nesting
    depth 0 only (outside any ``<...>``/``(...)``/``[...]``), reports which
    were found, and returns *s* with those depth-0 tokens removed -- a
    cv-looking word sitting INSIDE a parenthesized region (a
    ``noexcept(expr)`` argument's own text, a template argument) is
    untouched, since it belongs to that expression, not to this
    declarator's own trailing qualifier sequence (Codex review, PR #941,
    thirteenth round: an earlier revision used a plain, depth-blind
    ``re.search``/``re.sub`` over the whole trailing region, which wrongly
    reached inside a non-literal ``noexcept(expr)``'s own argument, e.g.
    ``noexcept(Foo<const int>)``).
    """
    depth = 0
    has_const = False
    has_volatile = False
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "<([":
            depth += 1
            out.append(ch)
            i += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
            out.append(ch)
            i += 1
        elif depth == 0 and (m := _CV_WORD_RE.match(s, i)):
            if m.group() == "const":
                has_const = True
            else:
                has_volatile = True
            i = m.end()
        else:
            out.append(ch)
            i += 1
    rest = re.sub(r"\s+", " ", "".join(out)).strip()
    return has_const, has_volatile, rest


_MEMBER_POINTER_TAIL_RE = re.compile(r"(?:\s*(?:const|volatile))*\s*")


def _find_member_pointer_qualifier(prefix: str) -> tuple[int, int] | None:
    """If *prefix* (the text up to and including a declarator's own
    outermost sigil) ends in a BARE, non-parenthesized data-member-pointer
    qualifier -- one or more ``Identifier::`` segments belonging to the
    member pointer's own class, e.g. the ``C::`` in ``int C::*`` -- return
    ``(qualifier_start, qualifier_end)`` spanning that qualifier run (not
    including any cv token between it and the sigil). Returns ``None`` for
    every other shape: an ordinary pointer whose pointee happens to be
    namespace-qualified (``ns::Foo *`` -- the ``::`` there is nowhere near
    the sigil, since a whole identifier separates them) does not match,
    and neither does a PARENTHESIZED member-pointer/-function declarator
    group (``(C::*)``) -- the caller only invokes this when the
    surrounding text has no unmatched open paren, since inside one the
    "base" this would split off is a return type/fragment, not a real
    standalone pointee type.

    Detection relies on a reliable, ``canonicalize_type_name``-specific
    marker: a member pointer's own qualifier is always followed by a
    single space before whatever comes next (``"C:: *"``, ``"C:: const
    *"``) -- unlike ordinary namespace qualification within a type's own
    spelling, which never has a space after ``::`` (``"ns::Foo"``). A
    chained qualifier (``ns::C::``) has no space at ITS OWN internal
    ``::`` either, only at the final, outermost one -- so the LAST ``::``
    followed by a space reliably marks the end of the member pointer's
    own qualifier, and is walked backward from to find where multi-segment
    qualifiers begin (template arguments are not supported in this bare,
    unparenthesized position -- an accepted, narrower limitation than the
    parenthesized case's own manual scanner, since a templated class as
    the target of an unparenthesized bare data-member-pointer is a
    genuinely rare combination).

    Used to correctly canonicalize the pointee cv-qualifier on a bare
    data-member-pointer parameter (Codex review, PR #941, fifteenth
    round): ``canonicalize_type_name``'s own east-const regex -- which
    this module cannot modify, since ``name_classification.py`` is a
    frozen, no-growth legacy file -- does not know how to normalize a
    leading cv-qualifier across a ``ClassName::`` infix, and MISPLACES it
    depending on which side it started on: ``"int const C::*"`` stays
    ``"int const C:: *"`` (cv kept attached to the base, matching this
    function's own canonical output), but ``"const int C::*"`` becomes
    ``"int C:: const *"`` -- the cv-word shoved in BETWEEN the qualifier
    and the sigil, where it looks like (but is not) the pointer's own
    by-value qualifier. Both are the identical pointer-to-const-int-member
    type and must canonicalize identically; the caller re-collects any cv
    word found on either side of the qualifier and re-derives the pointee
    base type from both combined.
    """
    if not prefix or prefix[-1] not in "*&":
        return None
    marker = prefix.rfind(":: ")
    if marker == -1:
        return None
    qualifier_end = marker + 2
    tail = prefix[qualifier_end : len(prefix) - 1]
    if _MEMBER_POINTER_TAIL_RE.fullmatch(tail) is None:
        return None
    i = marker
    qualifier_start = -1
    while True:
        j = i - 1
        while j >= 0 and (prefix[j].isalnum() or prefix[j] == "_"):
            j -= 1
        if j == i - 1:
            return None  # no identifier immediately before "::"
        qualifier_start = j + 1
        if j >= 1 and prefix[j - 1 : j + 1] == "::":
            i = j - 1
            continue
        break
    return qualifier_start, qualifier_end


def _split_at_trailing_param_list(suffix: str) -> tuple[str, str] | None:
    """If *suffix* (the text after a declarator's own outermost sigil)
    contains a top-level ``(...)`` group -- the declarator's own trailing
    parameter list, e.g. the ``(int)`` in ``void (*)(int)`` -- return
    ``(head, params_and_after)`` split so ``params_and_after`` starts
    exactly at that paren's ``(``. Returns ``None`` when *suffix* has no
    top-level ``(`` at all (a bare pointer, with no trailing declarator to
    split off).
    """
    depth = 0
    for i, ch in enumerate(suffix):
        if ch == "(" and depth == 0:
            return suffix[:i], suffix[i:]
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
    return None


_NOEXCEPT_RE = re.compile(r"\bnoexcept\b(?:\s*\(\s*([^()]*)\s*\))?")


def _canonicalize_noexcept(s: str) -> str:
    """Normalize the constant ``noexcept`` spellings this trailing region
    can carry: bare ``noexcept``, ``noexcept(true)``, and ``noexcept(1)``
    (all the "non-throwing" type, since a ``noexcept`` argument is
    contextually converted to ``bool``) collapse to the single canonical
    spelling ``"noexcept"``; ``noexcept(false)``/``noexcept(0)``
    (equivalent, for type purposes, to no exception-specification at all)
    are dropped entirely, same as an already-absent specifier. Any other
    ``noexcept(expr)`` -- a non-literal constant expression this function
    cannot evaluate -- is left untouched, verbatim.

    ``0``/``1`` are recognized alongside ``true``/``false`` because
    Clang's own ``qualType`` genuinely emits these integer-literal
    spellings verbatim (confirmed via ``clang -Xclang -ast-dump=json``:
    ``void (int) noexcept(1)``) rather than folding them to the
    boolean-literal spelling -- and direct compilation confirms
    ``noexcept(1)``/``noexcept(true)`` are the identical type, as are
    ``noexcept(0)``/no-specifier/``noexcept(false)`` (Codex review, PR
    #941, twenty-first round). Deliberately narrow to exactly these two
    integer literals, not "any nonzero integer" -- a non-0/1 integer
    constant in this position triggers a narrowing-conversion diagnostic
    in real compilers and is not a spelling this module has confirmed
    evidence for.
    """
    m = _NOEXCEPT_RE.search(s)
    if not m:
        return s
    arg = m.group(1)
    if arg is None or arg.strip() in ("true", "1"):
        replacement = "noexcept"
    elif arg.strip() in ("false", "0"):
        replacement = ""
    else:
        return s
    return re.sub(r"\s+", " ", s[: m.start()] + replacement + s[m.end() :]).strip()


def _canonicalize_member_qualifiers(s: str) -> str:
    """Canonicalize a pointer-to-member-function's own trailing
    specifiers -- the cv-qualifiers, ref-qualifier, and (post-C++17)
    ``noexcept``-specifier that can follow its parameter list, e.g. the
    ``const`` in ``void (C::*)(int) const`` or the ``noexcept`` in
    ``void (*)(int) noexcept``. These qualify the POINTED-TO member
    function itself: genuine, standard-mandated overload/type
    discriminators (``void (C::*)(int) const`` and ``void (C::*)(int)``
    are two different, non-interchangeable pointer-to-member types; since
    C++17 a ``noexcept``/non-``noexcept`` function is likewise a distinct
    type), unlike the pointer's own by-value qualifier already stripped
    separately -- so this function only ever REORDERS ``const``/
    ``volatile`` relative to each other (the same "eliminate ordering by
    construction" treatment ``entity_id_for_function``'s own
    ``is_const``/``is_volatile`` booleans already give the outer
    function's member-cv), while every other specifier -- ref-qualifier,
    ``noexcept``, and anything else this function does not need to
    individually name -- passes through verbatim, in its original
    relative order (Codex review, PR #941, tenth round: an earlier
    revision blanket-stripped every depth-0 cv token found anywhere after
    the sigil, which wrongly erased this genuinely-distinguishing trailing
    region entirely; eleventh round: the fix for that then reconstructed
    the trailing region from ONLY cv/ref, which silently dropped
    ``noexcept`` instead -- a second, different over-merge of the same
    class, `void (*)(int) noexcept` and `void (*)(int)` wrongly collapsing
    to one identity. `dcl.fct`'s own grammar already fixes cv-qualifier-
    seq first among these trailing specifiers, so a real producer's
    placement never needs inferring -- only cv needs reordering relative
    to itself; everything else keeps whatever order it was already
    spelled in; twelfth round: preserving ``noexcept`` verbatim was
    necessary but not sufficient -- since C++17, a function type's
    exception specification collapses to exactly two kinds for TYPE
    purposes, "non-throwing" (bare ``noexcept``/``noexcept(true)``) and
    "potentially-throwing" (no specifier at all, or ``noexcept(false)``),
    so those pairs are the SAME type and must canonicalize identically,
    the same "verbatim preservation isn't canonicalization" gap the
    by-value cv/array-decay fixes each already closed for their own
    shape. Only the two literal, constant-expression spellings are
    normalized; any other, non-literal ``noexcept(expr)`` is left
    completely untouched -- evaluating an arbitrary constant expression is
    out of scope, the same "don't solve the fully general grammar" limit
    this module already draws elsewhere; thirteenth round: extracting
    ``const``/``volatile`` via a plain, depth-blind ``re.search`` over the
    WHOLE trailing region wrongly reached inside a non-literal
    ``noexcept(expr)``'s own argument too -- a ``const``/``volatile``
    token that is part of THAT expression's own text, e.g.
    ``noexcept(Foo<const int>)``, is not this declarator's own
    cv-qualifier at all, and extracting it both corrupted the expression
    (mutating text this function has no business touching, since it
    cannot evaluate it) and could merge two genuinely different overloads
    that happen to share a nested "const" by coincidence. Fixed with a
    depth-aware scan, :func:`_extract_top_level_cv`, mirroring
    ``signature_normalization._strip_cv_tokens_outside_nesting``'s own
    outside-nesting discipline: only a cv word sitting at depth 0 --
    outside any ``(...)``/``<...>``/``[...]`` -- is ever this
    declarator's own trailing qualifier).
    """
    stripped = s.strip()
    if not stripped:
        return ""
    has_const, has_volatile, rest = _extract_top_level_cv(stripped)
    rest = _canonicalize_noexcept(rest)
    parts = [
        p
        for p, present in (("const", has_const), ("volatile", has_volatile))
        if present
    ]
    if rest:
        parts.append(rest)
    return " ".join(parts)
