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

from abicheck.model.identity_literals import quoted_literal_spans

__all__ = ["return_type"]


def _literal_end_at(literal_spans: list[tuple[int, int]], i: int) -> int | None:
    """The end index of a quoted-literal span in *literal_spans* starting
    exactly at *i*, or ``None``. A quoted C++ string/char literal is opaque
    data, not declarator structure -- a literal like ``"("`` legitimately
    contains an unbalanced paren character that isn't a real group boundary
    (confirmed by direct compilation: ``template<class T> decltype("(")
    f(T);``'s qualType is ``'decltype("(") (T)'``, whose bracket-depth scan
    below, without this skip, never sees its counting return to zero and
    swallows the real trailing ``(T)`` parameter-list group along with it --
    CodeRabbit review, PR #943, on a later round).
    """
    for start, end in literal_spans:
        if start == i:
            return end
        if start > i:
            break
    return None


def _top_level_paren_spans(s: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans (``end`` exclusive, closing ``)`` included) of
    every TOP-LEVEL parenthesized group in *s*, ignoring anything nested
    inside ``<...>``/``[...]`` or inside a quoted string/char literal.
    Shared by :func:`return_type` and its own spiral-declarator recursion
    below.

    Bracket depth (for ``<...>``/``[...]``) and paren depth are tracked
    TOGETHER, in one pass, rather than paren-tracking only kicking in once
    bracket depth is already zero: a bare ``<``/``>`` is only ever counted
    as a bracket while paren depth is ALSO zero (i.e. we are not currently
    inside any already-open parenthesized group). This matters because a
    non-type template argument containing a relational/shift operator
    must, per the grammar, be wrapped in its own parens to disambiguate it
    from the closing ``>`` -- so once such a paren group has opened, any
    ``<``/``>`` inside it can only be that operator, never a genuine
    template bracket, regardless of what unrelated ``<...>`` surrounds the
    whole expression. Confirmed by direct compilation that clang spells
    ``template<class T> std::enable_if_t<(sizeof(T) < 4), int> f(T);``'s
    qualType as ``"std::enable_if_t<(sizeof(T) < 4), int> (T)"`` -- an
    earlier version of this function tracked bracket depth only at the
    OUTER scanning level (switching to a paren-only inner loop once a "("
    was seen with bracket already 0, and never touching bracket state
    again until that inner loop's own parens closed): the relational
    ``<`` here was reached with bracket already at 1 (from
    ``enable_if_t<``'s own opening), so it never entered that inner loop
    at all and instead incremented the SAME bracket counter to 2, which
    the qualType's one remaining ``>`` could only ever bring back down to
    1 -- permanently stuck above zero, so the real trailing ``(T)`` was
    never recognized as a top-level group at all (CodeRabbit review, PR
    #943, on a later round).
    """
    literal_spans = quoted_literal_spans(s)
    spans: list[tuple[int, int]] = []
    bracket = 0
    paren = 0
    span_start = 0
    is_top_level_span = False
    i = 0
    n = len(s)
    while i < n:
        literal_end = _literal_end_at(literal_spans, i)
        if literal_end is not None:
            i = literal_end
            continue
        ch = s[i]
        if ch == "(":
            if paren == 0:
                span_start = i
                is_top_level_span = bracket == 0
            paren += 1
        elif ch == ")":
            if paren > 0:
                paren -= 1
                if paren == 0 and is_top_level_span:
                    spans.append((span_start, i + 1))
        elif paren == 0:
            if ch in "<[":
                bracket += 1
            elif ch in ">]":
                bracket = max(0, bracket - 1)
        # else (paren > 0 and ch is one of <[>]): ignore entirely -- inside
        # an already-open paren group, a bare </>/[/] cannot be a genuine
        # template/array bracket relevant to closing THIS group, whose own
        # balance only ever depends on matching "(" and ")".
        i += 1
    return spans


_EXCEPTION_SPEC_KEYWORD_RE = re.compile(r"\b(?:noexcept|throw|__attribute__)\s*$")
_LEADING_EXCEPTION_SPEC_RE = re.compile(r"^\s*\b(?:noexcept|throw)\b")
_DECLTYPE_OPERAND_RE = re.compile(r"\bdecltype\s*$")


def _find_top_level_arrow(s: str) -> int | None:
    """Index just past a top-level ``->`` in *s* (paren depth 0 AND
    bracket depth 0), or ``None``. A TRAILING return type's own arrow is
    always at this depth -- never inside ``(...)``/``[...]``, or inside a
    quoted string/char literal -- so this cannot be confused with an
    unrelated ``->`` a nested type alias might spell (there is no
    realistic construct where a *second*, nested trailing-return arrow
    could appear at this same depth).

    Bracket depth is only ever tracked while paren depth is ALSO zero, for
    the identical reason :func:`_top_level_paren_spans` tracks the two
    together -- confirmed by direct compilation that a parameter whose
    dependent type contains a paren-wrapped relational operator, e.g.
    ``auto f(std::enable_if_t<(sizeof(T) < 4), int>) -> T;``'s qualType
    ``"auto (std::enable_if_t<(sizeof(T) < 4), int>) -> T"``, left the
    OLD version's bracket counter stuck above zero for the rest of the
    string (the relational ``<`` reached with bracket already 1 from
    ``enable_if_t<``, with only one real ``>`` left to close it), so the
    real trailing ``-> T`` was never found at all -- the identical
    corruption CodeRabbit's review found in the sibling paren-span
    scanner, here for the arrow search instead (PR #943, on a later
    round).
    """
    literal_spans = quoted_literal_spans(s)
    bracket = 0
    paren = 0
    i = 0
    n = len(s)
    while i < n:
        literal_end = _literal_end_at(literal_spans, i)
        if literal_end is not None:
            i = literal_end
            continue
        ch = s[i]
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif paren == 0:
            if ch in "<[":
                bracket += 1
            elif ch in ">]":
                bracket = max(0, bracket - 1)
            elif ch == "-" and i + 1 < n and s[i + 1] == ">" and bracket == 0:
                return i + 2
        i += 1
    return None


def _is_spiral_wrapper_prefix(interior: str, leading: str) -> bool:
    """Whether a first top-level group's *interior* is a SPIRAL-declarator
    wrapper (a pointer, reference, or pointer-to-member declarator around a
    nested parameter list) rather than unrelated return-type text (e.g. a
    ``decltype``'s own parenthesized operand). *leading* is the text
    immediately BEFORE the group, used to rule out the latter.

    The wrapper's declarator prefix -- everything before its own first
    nested top-level group -- is exactly one of ``*``, ``&``, ``&&``, ``^``
    (a Clang Blocks-extension block-pointer declarator), or a
    POINTER-TO-MEMBER declarator, ``<qualified-class-name>::*`` (e.g.
    ``C::*``, or a qualified/templated class name like ``Ns::C<int>::*``)
    -- confirmed by direct compilation that clang spells a function
    returning a pointer to member function as ``int (C::*(T))(int)``,
    whose first group's interior is ``C::*(T)``: a bare leading-sigil check
    (``*``/``&`` only) missed this shape entirely, falling through to the
    scan-from-the-end branch and discarding the returned function's own
    parameter list -- the identical hazard the pointer/reference case
    already fixed, just for a class-qualified sigil (Codex review, PR
    #943, on a later round). The block-pointer sigil is the identical
    hazard again, one level further: confirmed by direct compilation
    (``clang -fblocks``) that ``int (^f(int))(int)`` is spelled
    ``"int (^(int))(int)"``, structurally identical to the pointer case
    but with ``^`` instead of ``*`` (Codex review, PR #943, on a later
    round still).
    """
    # A group whose text immediately precedes it is the bare token
    # `decltype` (no space, since that's how clang always prints it) is
    # ALWAYS that operator's own parenthesized operand, never a spiral
    # wrapper -- regardless of what the operand's own text happens to
    # start with. This is a sound, general rule (not another
    # sigil-specific patch): `decltype(...)`'s parens can never be
    # anything else, so nothing inside them is ever declarator structure,
    # only expression text that may coincidentally share a spiral
    # wrapper's shape. Confirmed by direct compilation that
    # `decltype(&(S::x)) f();` and the `S::y` sibling are legal, distinct
    # declarations (`qualType`s `"decltype(&(S::x)) ()"` /
    # `"decltype(&(S::y)) ()"`) -- an address-of a parenthesized
    # member-access expression, whose leading sigil `&` and EMPTY
    # remainder after its own nested group (`(S::x)`) exactly matched a
    # genuine reference-returning spiral declarator with no parameters
    # (e.g. `int (&f())();`, `qualType` `"int (&())()"`) with nothing left
    # to tell them apart by remainder alone -- the prior fix's
    # remainder-based check (below) cannot distinguish an EMPTY remainder
    # from a genuine one, since both look identical; only the `decltype`
    # prefix reveals the group is an operand (Codex review, PR #943, on a
    # later round, the address-of sibling of the dereferenced-cast case
    # already closed below).
    if _DECLTYPE_OPERAND_RE.search(leading):
        return False
    spans = _top_level_paren_spans(interior)
    if not spans:
        return False
    prefix = interior[: spans[0][0]].strip()
    if prefix not in ("*", "&", "&&", "^") and not prefix.endswith("::*"):
        return False
    # A sigil immediately followed by a parenthesized group is not enough
    # on its own -- a `decltype` operand can spell an unrelated expression
    # that happens to start with the identical shape, e.g. a dereferenced
    # C-style cast: `decltype(*(typename T::x *)0)`'s first_interior is
    # `*(typename T::x *)0`, whose prefix is the bare sigil `*` too.
    # Confirmed by direct compilation that this and its `T::y` sibling are
    # legal, distinct overloads (Codex review, PR #943, on a later round)
    # -- treating it as a spiral wrapper discarded the entire dependent
    # operand via `_excise_own_param_list`, collapsing both to the
    # identical `"decltype (*()0) (T)"`. What follows a GENUINE spiral
    # wrapper's first nested group is never arbitrary expression text: it
    # is either nothing, the original function's own exception
    # specification, or a further-nested spiral level's own parameter
    # list (verbatim ``(...)``) -- never a bare token like the `0` above.
    # (Now redundant with the `decltype` check above for this specific
    # case, since a bare dereferenced cast is always a `decltype` operand
    # too, but kept as a second, independent line of defense for any
    # non-`decltype` construct this hasn't been confirmed to cover.)
    remainder = interior[spans[0][1] :].strip()
    if not remainder:
        return True
    if _LEADING_EXCEPTION_SPEC_RE.match(remainder):
        return True
    return remainder.startswith("(")


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

    Whatever immediately follows the FIRST top-level group in *s*
    (``s[first_end:]``) is either the ORIGINAL function's own exception
    specification (discarded outright -- it describes the original
    function, not its return type) or a further-nested RETURNED function's
    own parameter list (kept verbatim -- real, distinguishing return-type
    content). The two cannot be told apart by ``len(spans)`` alone: a
    complex condition (``noexcept(noexcept(T()))``) is itself parenthesized,
    so it produces a SECOND top-level span in *s* exactly like a genuine
    further-nested spiral level does (confirmed by direct compilation of
    both ``int (*g(int) noexcept)(int);`` -- one span -- and
    ``template<class T> int (*g(T) noexcept(noexcept(T())))(int);`` -- two
    spans, since the ``(noexcept(...))`` condition is itself a balanced
    top-level group). The real discriminator, checked AFTER discarding any
    leading exception specification, is whether anything real is left: a
    further-nested spiral level's own parameter list starts directly with
    ``(`` once the exception spec (if any) is stripped (confirmed via
    ``int (*(*h(T))(T))(T)``'s first_interior, ``*(*(T))(T)``, whose second
    top-level span ``(T)`` is real, kept content, with no exception spec in
    front of it) -- only THEN is spans[0] itself a further wrapper needing
    recursion; otherwise spans[0] is already the actual, bottommost own
    parameter list, excised outright regardless of how many top-level
    groups its own trailing exception condition happened to introduce.
    Confirmed still leaking before this rule existed (Codex review, PR
    #943, on a later round): a span-count-only decision correctly excised a
    *simple* trailing ``noexcept`` but, for the complex-condition case,
    mistook spans[0] for "needs recursion" (since the exception span made
    ``len(spans) == 2``) and so preserved spans[0] itself -- the original
    function's own actual parameter list -- verbatim in what is reported as
    its return type.
    """
    spans = _top_level_paren_spans(s)
    if not spans:
        return s
    first_start, first_end = spans[0]
    remainder = s[first_end:]
    if _LEADING_EXCEPTION_SPEC_RE.match(remainder):
        remainder = ""
    if remainder.strip().startswith("("):
        inner = _excise_own_param_list(s[first_start + 1 : first_end - 1])
        return s[:first_start] + "(" + inner + ")" + remainder
    return s[:first_start] + "()" + remainder


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
       must be preserved, not discarded. Everything after the first
       group (``tail``, below) is kept VERBATIM, including any trailing
       exception specification OR GNU ``__attribute__((...))`` clause
       (e.g. a calling convention): confirmed by direct compilation that
       a trailing ``noexcept`` here binds to the RETURNED function type,
       not to the original function itself (``int (*a())() noexcept;``
       -- ``a()`` itself is not noexcept, but calling through the
       returned function pointer is), so it is real return-type content,
       not something to strip. The original function's OWN exception
       specification, if any, is discarded separately, inside
       :func:`_excise_own_param_list`'s base case (Codex review, PR #943,
       across two rounds -- the first round wrongly stripped this
       trailing spec as if it were always the outer function's own). A
       trailing GNU attribute at this position is kept for the identical
       reason and is NOT stripped, despite an attribute never being part
       of an ORDINARY function's type (see branch 3 below): confirmed by
       direct compilation, on an ``i386`` target where the distinction is
       observable, that ``int (__attribute__((stdcall)) *h())();`` and
       the calling-convention-attribute-free ``int (*h())();`` produce
       DIFFERENT qualTypes (``"int (*())() __attribute__((stdcall))"``
       vs. ``"int (*())()"``) for a real ABI difference (stdcall vs.
       cdecl) -- and that writing the identical attribute at the very
       END of the whole declaration instead (``int (*h())()
       __attribute__((stdcall));``) produces the BYTE-IDENTICAL qualType,
       meaning clang's own printer cannot be used to tell whether such an
       attribute binds to the outer function or the returned one. Given
       that ambiguity, silently stripping it (an earlier version of this
       branch did, Codex review, PR #943, on a still later round) risked
       erasing a genuine, ABI-breaking calling-convention difference,
       which is a strictly worse failure mode for an ABI checker than
       reporting one that turns out to be the outer function's own.
    3. Otherwise, the function's own real top-level parameter list is the
       LAST top-level parenthesized group that is not itself an
       exception-specification's or GNU attribute's own argument-clause
       group (a ``noexcept(...)``/``throw(...)``/``__attribute__((...))``
       immediately preceding it) -- everything before that group,
       verbatim, is the return type. An ordinary (non-spiral) function's
       OWN trailing attribute is therefore naturally excluded here (it
       sits in the group's own SUFFIX, never the prefix that becomes
       ``return_type``) without needing branch 2's "ambiguous, so keep it"
       treatment -- confirmed by direct compilation that ``int f(int)
       __attribute__((sysv_abi));``'s qualType is ``"int (int)
       __attribute__((sysv_abi))"``, whose attribute clause's own
       argument group is a SECOND top-level span that a naive
       scan-from-end mistook for the parameter list, swallowing the real
       ``(int)`` group into the reported return type (Codex review, PR
       #943, on a later round). Scanning from the END, not assuming the
       FIRST group is always the parameter list, is what keeps this
       branch correct for two more real, confirmed cases: a
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
    if _is_spiral_wrapper_prefix(first_interior, qualtype[:first_start]):
        leading = qualtype[:first_start].strip()
        inner = _excise_own_param_list(first_interior)
        tail = qualtype[first_end:]
        return (leading + " (" + inner + ")" + tail).strip()

    real_start = spans[-1][0]
    for start, _end in reversed(spans):
        if not _EXCEPTION_SPEC_KEYWORD_RE.search(qualtype[:start]):
            real_start = start
            break
    return qualtype[:real_start].strip()
