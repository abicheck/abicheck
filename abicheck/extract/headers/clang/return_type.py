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

"""Recovers a clang function ``qualType``'s real return-type spelling.

Split out of ``functions.py`` (which grew past the AI-readiness gate's
800-line production soft cap) purely to keep that module legible -- this
is genuinely one self-contained primitive (:func:`return_type` plus its
three private helpers), with a single call site in ``functions.py``
(``parse_functions``'s own ``ret_type = return_type(qualtype) or "void"``).
"""

from __future__ import annotations

import re

__all__ = ["return_type"]


def _top_level_paren_spans(s: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans (``end`` exclusive, closing ``)`` included) of
    every TOP-LEVEL parenthesized group in *s*, ignoring anything nested
    inside ``<...>``/``[...]``. Shared by :func:`return_type` and its own
    spiral-declarator recursion below.
    """
    spans: list[tuple[int, int]] = []
    bracket = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "<[":
            bracket += 1
            i += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
            i += 1
        elif ch == "(" and bracket == 0:
            depth = 1
            j = i + 1
            while j < n and depth:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


_EXCEPTION_SPEC_KEYWORD_RE = re.compile(r"\b(?:noexcept|throw)\s*$")
_TRAILING_BARE_NOEXCEPT_RE = re.compile(r"\s*\bnoexcept\s*$")


def _strip_trailing_exception_spec(s: str) -> str:
    """Remove a trailing ``noexcept(...)``/``throw(...)`` (or bare
    ``noexcept``) exception specification from *s*, if one is present at
    the very end.

    Needed by the SPIRAL-declarator branch specifically: unlike the
    scan-from-the-end branch (which never includes anything after the
    real parameter list at all), the spiral branch appends the RETURNED
    function's own trailing group verbatim, which can itself be followed
    by the OUTER function's own exception specification -- confirmed by
    direct compilation: ``template<class T> int (*f(T))(int)
    noexcept(noexcept(T()));``'s ``qualType`` is ``"int (*(T))(int)
    noexcept(noexcept(T()))"``. Left unstripped, this pollutes
    ``return_type`` with exception-specification text, which would
    fabricate a spurious return-type-changed finding whenever only the
    exception-specification condition changes (Codex review, PR #943, on
    a later round -- the identical hazard the ordinary, non-spiral
    ``noexcept`` correction closed, here for the spiral branch's own
    trailing group instead of its parameter-list-selection logic).
    """
    spans = _top_level_paren_spans(s)
    if spans:
        last_start, last_end = spans[-1]
        if last_end == len(s.rstrip()):
            keyword = _EXCEPTION_SPEC_KEYWORD_RE.search(s[:last_start])
            if keyword:
                return s[: keyword.start()].rstrip()
    return _TRAILING_BARE_NOEXCEPT_RE.sub("", s)


def _find_top_level_arrow(s: str) -> int | None:
    """Index just past a top-level ``->`` in *s* (paren depth 0 AND
    bracket depth 0), or ``None``. A TRAILING return type's own arrow is
    always at this depth -- never inside ``(...)``/``<...>``/``[...]`` --
    so this cannot be confused with an unrelated ``->`` a nested type
    alias might spell (there is no realistic construct where a *second*,
    nested trailing-return arrow could appear at this same depth).
    """
    bracket = 0
    paren = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif (
            ch == "-" and i + 1 < n and s[i + 1] == ">" and bracket == 0 and paren == 0
        ):
            return i + 2
        i += 1
    return None


def _is_spiral_wrapper_prefix(interior: str) -> bool:
    """Whether a first top-level group's *interior* is a SPIRAL-declarator
    wrapper (a pointer, reference, or pointer-to-member declarator around a
    nested parameter list) rather than unrelated return-type text (e.g. a
    ``decltype``'s own parenthesized operand).

    The wrapper's declarator prefix -- everything before its own first
    nested top-level group -- is exactly one of ``*``, ``&``, ``&&``, or a
    POINTER-TO-MEMBER declarator, ``<qualified-class-name>::*`` (e.g.
    ``C::*``, or a qualified/templated class name like ``Ns::C<int>::*``)
    -- confirmed by direct compilation that clang spells a function
    returning a pointer to member function as ``int (C::*(T))(int)``,
    whose first group's interior is ``C::*(T)``: a bare leading-sigil check
    (``*``/``&`` only) missed this shape entirely, falling through to the
    scan-from-the-end branch and discarding the returned function's own
    parameter list -- the identical hazard the pointer/reference case
    already fixed, just for a class-qualified sigil (Codex review, PR
    #943, on a later round).
    """
    spans = _top_level_paren_spans(interior)
    prefix = interior[: spans[0][0]].strip() if spans else interior.strip()
    return prefix in ("*", "&", "&&") or prefix.endswith("::*")


def _excise_own_param_list(s: str) -> str:
    """Recursive helper for :func:`return_type`'s SPIRAL-declarator branch.

    A function-pointer (or function-reference) return type is itself
    spelled as a function-type declarator wrapping the ORIGINAL function's
    own parameter list one level deeper -- e.g. ``typename T::x (*(T))(T)``
    for ``template<class T> typename T::x (*f(T))(T);`` (confirmed by
    direct compilation): the FIRST top-level group, ``(*(T))``, is not
    itself the parameter list -- it wraps a pointer declarator around the
    real one, ``(T)``, nested one level inside; a further, trailing group
    is the RETURNED function type's OWN parameter list, which must be kept
    verbatim (not excised) since it is real, distinguishing return-type
    content -- confirmed by direct compilation that ``typename S::x
    (*f(T))(int)`` and ``typename S::x (*f(T))(double)`` are legal,
    coexisting overloads (CodeRabbit review, PR #943). Whichever top-level
    group in *s* has no further top-level group following it, at that
    nesting depth, is the actual parameter list (the recursion bottoms out
    there); its contents are excised (parens kept, as an empty marker) so
    the return-type discriminator captures the declarator SHAPE (the
    ``*``/``&`` and any further nested groups) without also duplicating
    ``f``'s own ordinary parameter list, which ``entity_id_for_function``
    already discriminates on separately via `param_types`. Recurses one
    level for each further layer of pointer/reference-to-function nesting,
    so an arbitrarily deep spiral (pointer to function returning pointer
    to function returning ...) still bottoms out correctly.
    """
    spans = _top_level_paren_spans(s)
    if not spans:
        return s
    first_start, first_end = spans[0]
    if len(spans) == 1:
        return s[:first_start] + "()" + s[first_end:]
    inner = _excise_own_param_list(s[first_start + 1 : first_end - 1])
    return s[:first_start] + "(" + inner + ")" + s[first_end:]


def return_type(qualtype: str) -> str:
    """The return type spelling of a function ``qualType`` (``ret (params)…``).

    Resolved in three steps, in this order, each added for a real
    confirmed collision (Codex/CodeRabbit review, PR #943, across
    several rounds):

    1. **A TRAILING return type** (``auto f(T) -> typename T::x``) is
       checked FIRST, via a top-level ``->`` (see
       :func:`_find_top_level_arrow`): everything after it is the return
       type, taken verbatim, with no further group-parsing -- clang spells
       the leading part as the bare placeholder ``auto``, which is not the
       actual return type (confirmed by direct compilation: two overloads
       differing only in their trailing return collapsed onto the
       identical spelling before this branch existed). Checking this
       FIRST, rather than after locating a "parameter list" group, is what
       keeps a trailing return type that itself contains parentheses
       (``auto f(T) -> decltype((T::x))``) from having those parentheses
       mistaken for a second parameter-list group -- confirmed by direct
       compilation that this and the ``T::y`` sibling both compile with no
       redefinition error, yet an earlier version of this function (which
       located "the" parameter list before ever checking for an arrow)
       reduced both to the identical ``"auto (T) -> decltype"``, discarding
       the dependent operand entirely.
    2. **A function-pointer/reference return type**
       (``typename T::x (*f(T))(T)``): clang spells this as a SPIRAL
       declarator, ``typename T::x (*(T))(T)``, detected by the first
       top-level group's own interior starting with a pointer/reference
       sigil (``*``/``&``) -- see :func:`_excise_own_param_list`'s own
       docstring for why a RECURSIVE excision (not merely picking a
       group) is required here specifically: the returned function
       type's own parameter list is real, distinguishing content that
       must be preserved, not discarded.
    3. Otherwise, the function's own real top-level parameter list is the
       LAST top-level parenthesized group that is not itself an
       exception-specification's own group (a ``noexcept(...)``/
       ``throw(...)`` immediately preceding it) -- everything before that
       group, verbatim, is the return type. Scanning from the END, not
       assuming the FIRST group is always the parameter list, is what
       keeps this branch correct for two more real, confirmed cases: a
       dependent return type containing its OWN parenthesized
       sub-expression (``decltype((T::x)) f(T)``, where that first group
       is return-type text, not a parameter-list wrapper at all), and an
       ordinary function's `noexcept(expr)` exception specification
       (``int () noexcept(cond())``, whose own trailing group must not be
       mistaken for a second parameter list).
    """
    arrow_idx = _find_top_level_arrow(qualtype)
    if arrow_idx is not None:
        return qualtype[arrow_idx:].strip()

    spans = _top_level_paren_spans(qualtype)
    if not spans:
        return qualtype.strip()

    first_start, first_end = spans[0]
    first_interior = qualtype[first_start + 1 : first_end - 1]
    if _is_spiral_wrapper_prefix(first_interior):
        leading = qualtype[:first_start].strip()
        inner = _excise_own_param_list(first_interior)
        tail = _strip_trailing_exception_spec(qualtype[first_end:])
        return (leading + " (" + inner + ")" + tail).strip()

    real_start = spans[-1][0]
    for start, _end in reversed(spans):
        if not _EXCEPTION_SPEC_KEYWORD_RE.search(qualtype[:start]):
            real_start = start
            break
    return qualtype[:real_start].strip()
