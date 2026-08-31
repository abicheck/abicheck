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

"""Function-parameter-type canonicalization for identity/overload-
discriminator purposes (ADR-063 Phase 2).

Split out of ``model/identity.py`` (its only caller) purely to keep that
file under the AI-readiness gate's 800-line production maximum -- this
module's own contents are otherwise identity.py's, not a separate design
decision. See ``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 2
section for the full history of the several review rounds (Codex, PR #941)
that grew this from a single ``canonicalize_type_name`` call into the
dedicated cv/array-decay logic here. A second sibling leaf module,
``declarator_qualifiers.py``, was split out of THIS module in turn once it
hit the same 800-line cap (fifteenth round) -- it holds the declarator-
grouping/pointer-to-member/trailing-qualifier machinery that has no
recursive dependency back into this module's own
``canonicalize_function_signature_param_type``; see that module's own
docstring for why the import direction is one-way.

Leaf module: imports only ``name_classification.canonicalize_type_name``
(a dependency-free sibling) and its own sibling ``declarator_qualifiers``
(also a leaf, importing nothing back here), nothing above ``model``, per
ADR-063 D10 -- the same leaf-module contract ``model/identity.py`` itself
states.
"""

from __future__ import annotations

import re

from ..name_classification import canonicalize_type_name
from .declarator_qualifiers import (
    _canonicalize_member_qualifiers,
    _find_member_pointer_qualifier,
    _is_declarator_group,
    _split_at_trailing_param_list,
)

__all__ = ["canonicalize_function_signature_param_type"]

# `restrict`/`__restrict`/`__restrict__` -- a qualifier attached to a
# specific pointer, positioned exactly where a `const`/`volatile` on that
# same pointer would be, and it turns out to be POSITION-SENSITIVE the
# identical way: real-compiler verification (`g++ -c`, GCC's own Itanium
# mangler) confirms `void f(int *)` and `void f(int * restrict)` are the
# SAME function (restrict on the parameter's own outermost, by-value
# pointer position drops from the mangled name, `_Z1fPi` both ways --
# GCC even refuses to compile that pair as a legal overload set, exactly
# the "same function" signal) -- but `void f(int **)` and
# `void f(int * restrict *)` mangle to two DIFFERENT, simultaneously-
# declarable symbols (`_Z1fPPi` vs `_Z1fPrPi`). So restrict is folded
# into this same strippable-word set, reusing the SAME outermost-vs-
# pointee position discipline `const`/`volatile` already have throughout
# this module -- not stripped unconditionally (Codex review, PR #941,
# eighteenth round: the sixteenth round's own "restrict never affects
# mangling, strip it everywhere" fix turned out to be the wrong
# generalization, verified wrong by direct compilation rather than mere
# assertion -- restrict does NOT behave like a pure no-op token, it
# behaves like cv).
_CV_WORD_RE = re.compile(r"\b(?:const|volatile|restrict|__restrict__|__restrict)\b")

# Clang's own ``qualType`` spelling for a calling-convention-decorated
# function-pointer declarator does NOT use the leading ``__cdecl``-style
# keyword ``_CALLING_CONVENTIONS``/``_is_declarator_group`` recognize --
# instead it renders the attribute AFTER the declarator's own trailing
# parameter list, e.g. ``void (*)(int) __attribute__((cdecl))`` for the
# identical type MSVC/castxml spell ``void (__cdecl *)(int)``
# (`dumper_clang_attributes.py`'s own `_CLANG_ATTR_TOKENS` mapping already
# recognizes these same attribute-node kinds for a FunctionDecl's own
# top-level contract attributes; this is the textual, parameter-type-
# spelling equivalent, for the mangling-free signature-fallback identity
# this module computes). Without normalizing this too, two backends
# observing the identical DWARF-only or header-only declaration would
# fragment into two different `EntityId`s purely because one produces
# CastXML-style leading-keyword text and the other Clang-style trailing-
# attribute text (Codex review, PR #941, seventeenth round).
_CALLING_CONVENTION_ATTR_RE = re.compile(
    r"__attribute__\s*\(\(\s*(cdecl|stdcall|fastcall|thiscall|vectorcall)\s*\)\)"
)

# Whether *prefix* already carries a leading calling-convention keyword,
# used to decide whether to inject the equivalent one for a trailing
# ``__attribute__(...)`` spelling (see the attribute-injection code
# below). Matched as a genuine WHOLE TOKEN positioned immediately after
# the declarator's own opening paren (allowing leading whitespace) --
# never a bare substring test anywhere in `prefix` -- since `prefix`
# also contains the return type, and a return type can legitimately
# CONTAIN one of these keywords as a substring of an unrelated identifier
# (e.g. `my__cdecl_result`) without that identifier being a calling-
# convention keyword at all. A prior revision used `any(cc in prefix for
# cc in _CALLING_CONVENTIONS)`, which matched that substring, wrongly
# concluded a convention keyword was already present, and both skipped
# injecting the real one AND still stripped the trailing attribute text
# -- silently merging two distinct callback types (Codex review, PR #941,
# twentieth round).
_CALLING_CONVENTION_KEYWORD_RE = re.compile(
    r"\s*(?:__cdecl|__stdcall|__fastcall|__thiscall|__vectorcall)\b"
)

# Bounds the recursion below (Codex review, PR #952): unbounded nested-
# declarator recursion raises an uncaught RecursionError on an
# adversarial/corrupt snapshot's parameter type, crashing compare().
_MAX_PARAM_TYPE_NESTING_DEPTH = 64

# A leading `::` (explicit global-namespace lookup) is DELIBERATELY left
# untouched, genuinely distinguishing -- despite a nineteenth-round
# attempt (`_GLOBAL_SCOPE_RE`, since reverted) to strip it unconditionally
# on the theory that `::dep::Thing` and `dep::Thing` always name the
# identical type. That theory is false in general and was falsified by
# direct compilation, not merely re-derived from principle: given
#     namespace dep { struct Thing; }
#     namespace local { namespace dep { struct Thing; } void f(dep::Thing*); }
# Clang's own `qualType` for `f`'s parameter prints the BARE, unqualified
# `dep::Thing *` -- with NO leading `::` -- even though it resolves to
# `local::dep::Thing`, a type that is DISTINCT from the global
# `::dep::Thing` a sibling declaration `void g(::dep::Thing*)` in the
# same namespace prints WITH the leading `::` (Codex review, PR #941,
# twenty-first round, reproduced independently via `clang -Xclang
# -ast-dump=json` before reverting). Since this module has no scope-tree
# information -- it operates purely on already-printed type-name text --
# it cannot tell "a genuinely global entity, spelled either way" apart
# from "an unqualified name that happens to resolve to a DIFFERENT,
# locally-shadowing entity of the same spelling"; Clang's own choice to
# include or omit the leading `::` is exactly the signal that
# distinguishes them, so erasing it can silently merge two non-
# interchangeable types. The nineteenth round's own motivating evidence
# (`tests/test_dumper_scoping_dependency_retention.py::
# test_globally_qualified_signature_spelling_still_matches`) is for a
# DIFFERENT subsystem entirely -- matching a signature's type reference
# against an already-KNOWN declared type's own `qualified_name` within
# ONE snapshot's dependency-scoping pass -- not for comparing two
# independently-observed SIGNATURES for cross-producer/cross-revision
# identity, which is this function's own job; that evidence does not
# establish that castxml and Clang ever disagree on this leading `::`
# for one identical real declaration, and no such evidence has been
# found. Absent that evidence, the conservative, keep-it-distinguishing
# choice is correct, mirroring how a similarly narrow-scoped generalization
# mistake in the sixteenth/eighteenth rounds' own restrict handling was
# corrected the identical way: revert to the position that matches
# confirmed compiler behavior rather than a plausible-sounding theory.


def _strip_cv_tokens_outside_nesting(s: str) -> str:
    """Blank out every ``const``/``volatile``/``restrict`` (any of its
    three spellings) token in *s* that sits at nesting depth 0 (outside
    any ``<...>``/``(...)``/``[...]``), then collapse the resulting
    whitespace. The one primitive both branches of
    :func:`canonicalize_function_signature_param_type` reduce to -- the
    by-value case applies it to the whole string, the pointer case applies
    it only to the suffix after the parameter's outermost pointer/
    reference sigil (see that function's own docstring for why those are
    the two, and only the two, safe places to strip). ``restrict`` shares
    this exact position discipline with ``const``/``volatile`` -- it is
    NOT unconditionally mangling-inert (see ``_CV_WORD_RE``'s own comment
    for the direct-compilation evidence).
    """
    depth = 0
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
            i = m.end()
        else:
            out.append(ch)
            i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _decay_top_level_array(canonical_type: str) -> str:
    """Best-effort single-dimension array-to-pointer decay for a function
    *parameter* type: ``T[]``/``T[N]`` -> ``T *`` (the bound is dropped --
    it plays no part in the adjusted parameter type at all, so ``T[]``,
    ``T[3]``, and ``T[4]`` must all canonicalize identically -- and any
    element-level cv-qualifier survives verbatim as the decayed pointer's
    pointee cv, e.g. ``const int [3]`` -> ``const int *``). Codex review,
    PR #941: an earlier revision of this module treated a top-level ``[``
    as "pointer-shaped enough not to strip its cv" but never performed the
    decay itself, so ``int []``/``int [3]``/``int [4]``/``int *`` -- all
    the identical adjusted parameter type -- still canonicalized to four
    different strings.

    Deliberately narrow: a genuinely *multi-dimensional* array parameter
    (``T[][N]``, which adjusts to ``T(*)[N]``, a pointer to an array, not
    a plain pointer) is left entirely unchanged rather than attempted --
    correctly re-spelling that adjusted type needs declarator-rewriting
    (inserting a grouping ``(*)``) this function does not implement, and
    it is a genuinely rare shape for a real ABI-relevant function
    parameter. Likewise left unchanged whenever a top-level ``(`` appears
    before the bracket at all -- a *parenthesized* declarator
    (``int (*)[3]``, "pointer to array of 3 ints") already has its own
    outermost ``*``, and the trailing ``[3]`` there names the *pointee's*
    array bound, not the parameter's own top-level shape; naively decaying
    it would wrongly append a second, spurious ``*``. Both are accepted,
    documented limitations, not a silent gap -- the same "don't solve the
    fully general C declarator grammar, scope to the shapes review
    evidence actually names" discipline ``_strip_cv_in_segment``'s own
    docstring already applies to the strict/non-strict split it makes.
    """
    depth = 0
    bracket_positions: list[int] = []
    has_top_level_paren = False
    for i, ch in enumerate(canonical_type):
        if ch == "(" and depth == 0:
            has_top_level_paren = True
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth = max(0, depth - 1)
        elif ch == "[" and depth == 0:
            bracket_positions.append(i)
    if len(bracket_positions) != 1 or has_top_level_paren:
        return canonical_type
    prefix = canonical_type[: bracket_positions[0]].rstrip()
    return f"{prefix} *"


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas that sit at nesting depth 0 (outside any
    ``<...>``/``(...)``/``[...]``) -- the boundaries between a parameter
    list's own individual parameters, as opposed to a comma nested inside
    one parameter's own type (a template-argument list, a nested callback's
    own parameter list).
    """
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for ch in s:
        if ch in "<([":
            depth += 1
            current.append(ch)
        elif ch in ">)]":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _find_matching_paren(s: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx* (which must
    itself be ``"("``), tracking only paren nesting -- ``s[open_idx]`` is
    always ``(`` at every call site. Defensively returns ``len(s)`` for a
    malformed, unmatched string rather than raising.
    """
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(s)


def _normalize_param_list_contents(inner: str, depth: int) -> str:
    """Canonicalize each individual parameter inside one parameter list's
    raw ``(...)`` content -- the by-value cv rule this whole module exists
    to apply is not unique to this function's own top-level parameter; a
    callback or member-function-pointer parameter's OWN parameters are
    exactly as much "a function's parameter list" as the outer one is, and
    C++ drops their top-level by-value cv from the function type the same
    way. An empty list and a bare ``void`` are the identical "no
    parameters" adjusted type (Codex review, PR #941, thirteenth round:
    an earlier revision returned each spelling unchanged instead of
    unifying them, so ``void (*)()`` and ``void (*)(void)`` -- one
    identical adjusted callback type -- canonicalized to two different
    strings), so both collapse to the same canonical empty form. A
    variadic ``...`` marker (which is not itself a parameter type at all)
    is left untouched.
    """
    if inner.strip() == "" or inner.strip().lower() == "void":
        return ""
    normalized = []
    for part in _split_top_level_commas(inner):
        p = part.strip()
        normalized.append(
            p
            if (p == "" or p == "...")
            else canonicalize_function_signature_param_type(p, _depth=depth + 1)
        )
    return ", ".join(normalized)


def _normalize_nested_param_lists(s: str, depth: int) -> str:
    """Canonicalize the contents of the one top-level ``(...)`` parameter
    list *s* is -- a declarator's own trailing parameter list, e.g. the
    ``(int)`` in ``void (*)(int)`` or ``void (C::*)(int)``. *s* is always
    exactly that: from its own opening paren to its matching closing paren
    (its only caller slices it out via :func:`_find_matching_paren`
    first), so no scan for a top-level paren is needed here.

    Reaches arbitrary nesting depth (a callback parameter that itself
    takes a callback parameter) through :func:`_normalize_param_list_
    contents`, which recurses back into
    :func:`canonicalize_function_signature_param_type` for each individual
    parameter -- each of ITS own trailing parameter lists (if any) then
    makes its own, identically-shaped call back into this function. Each
    recursive call operates on a strictly shorter substring, which is what
    guarantees termination (Codex review, PR #941, ninth round: an earlier
    revision left a callback parameter's own parameter list entirely
    opaque, so ``void (*)(int)`` and ``void (*)(const int)`` -- the
    identical adjusted callback type -- canonicalized to two different
    strings).
    """
    return "(" + _normalize_param_list_contents(s[1:-1], depth) + ")"


def canonicalize_function_signature_param_type(name: str, *, _depth: int = 0) -> str:
    """The canonical form of a function *parameter* type, for
    identity/overload-discriminator purposes -- as opposed to
    ``canonicalize_type_name``, which normalizes only cross-producer
    spelling differences and deliberately keeps every cv-qualifier,
    including a top-level by-value one, as real, distinguishing content.

    Drops a top-level BY-VALUE cv-qualifier (``int`` -> ``const int``):
    per the C++ standard, that qualifier is dropped from the function's
    own type for linkage/mangling purposes -- ``void f(int)`` and ``void
    f(const int)`` name the very same function (see
    ``name_classification.func_signature_cv_only_differ``'s own docstring
    for the citation). **Deliberately narrower than reusing that sibling
    module's own private ``_strip_cv_qualifiers`` helper** (which is what
    a first attempt at this reached for, since Codex's own finding named
    it): that helper is permissive at the true top level, stripping a
    *pointee* cv-qualifier too (``"const char *"`` -> ``"char *"``) --
    correct for *"is this an already-matched declaration's param change
    worth reporting as ABI-breaking"* (``diff_symbols._params_differ``'s
    own question), but wrong for *this* function's job. A pointee
    cv-qualifier on a pointer/reference parameter is a genuine, standard-
    mandated overload discriminator (``void f(char *)`` and ``void
    f(const char *)`` are two simultaneously-declarable, independently-
    mangled overloads, not one declaration): collapsing them here would
    silently merge two distinct functions into one identity, reintroducing
    exactly the sibling-overload-collision class this whole primitive
    exists to prevent.

    An array parameter is decayed first (:func:`_decay_top_level_array`),
    since ``T[]``/``T[N]`` are themselves pointer-shaped once adjusted
    (Codex review, PR #941) -- except the two shapes that function itself
    declines to decay (multi-dimensional, or a parenthesized declarator),
    which fall through here unchanged and untouched, exactly like the
    genuinely no-pointer-at-all case below.

    Once a type carries a real top-level ``*``/``&`` (after decay), a
    plain top-level pointee cv-qualifier stays (``const char *`` above),
    but a cv-qualifier trailing the pointer's OWN, outermost sigil is
    still by-value -- it qualifies the pointer value itself, not what it
    points to, and the standard drops that exactly like any other
    top-level parameter qualifier: ``void f(int *)`` and ``void f(int *
    const)`` name the same function (Codex review, PR #941 -- fresh
    evidence that an earlier revision here skipped *all* cv processing as
    soon as it saw a pointer, unable to distinguish the pointer's own
    trailing qualifier from a genuinely different pointee one). So the
    split is: everything up to and including the LAST top-level ``*``/
    ``&`` is kept verbatim (it may itself contain an earlier pointer's own
    now-meaningful qualifier, e.g. ``int * const *`` -- "pointer to a
    const-qualified pointer to int" -- which is NOT the outermost sigil
    and remains genuinely distinguishing, unlike ``int **``); only the
    suffix after it is stripped. When there is no top-level ``*``/``&``
    *and* no top-level ``[`` either -- truly no pointer, reference, or
    array of any kind -- every cv token outside ``<...>``/``(...)``/
    ``[...]`` nesting is by-value and safe to strip (a cv-qualifier nested
    in a template argument names a genuinely different type, e.g.
    ``Box<const int>`` vs. ``Box<int>``, the same rule
    ``canonicalize_type_name`` itself already documents). But when a
    top-level ``[`` survives *undecayed* (one of the two shapes
    :func:`_decay_top_level_array` declines above), this function returns
    the type completely unchanged instead -- deliberately not falling
    through to the by-value stripper: an undecayed array's own
    element-level cv-qualifier (``const int [3][4]``'s ``const``) sits
    *before* its bracket, at nesting depth 0, exactly where the by-value
    stripper would wrongly treat it as a stripped-away, non-distinguishing
    qualifier. Since this shape is already an accepted, undecayed
    limitation, actively stripping a real pointee-level qualifier from it
    would be a strictly worse, newly-introduced correctness regression on
    top of the already-documented incompleteness, not merely leaving that
    incompleteness as it was found.

    ``canonicalize_type_name`` is applied a *second* time, after decay --
    not merely once up front -- because its own east-const normalization
    (``"const T"`` -> ``"T const"``) never fires on a bracket-containing
    string in the first place (that regex's base-type group excludes
    ``[``/``]``), so ``"const int [3]"`` reaches the decay step with its
    leading ``const`` untouched and still leading after decay
    (``"const int *"``), while a direct ``"const int *"`` spelling is
    already east-const-normalized before decay ever runs
    (``"int const *"``) -- two spellings of the identical adjusted
    parameter type that would otherwise canonicalize to two different
    strings (own test suite, ``test_element_cv_becomes_pointee_cv``,
    caught this before it shipped).

    >>> canonicalize_function_signature_param_type("int")
    'int'
    >>> canonicalize_function_signature_param_type("const int")
    'int'
    >>> canonicalize_function_signature_param_type("volatile unsigned long long")
    'unsigned long long'
    >>> canonicalize_function_signature_param_type("char const*")
    'char const *'
    >>> canonicalize_function_signature_param_type("const char *")
    'char const *'
    >>> canonicalize_function_signature_param_type("const std::vector<const int>")
    'std::vector<const int>'
    >>> canonicalize_function_signature_param_type("int []")
    'int *'
    >>> canonicalize_function_signature_param_type("int [3]")
    'int *'
    >>> canonicalize_function_signature_param_type("const int [3]")
    'int const *'
    >>> canonicalize_function_signature_param_type("int * const")
    'int *'
    >>> canonicalize_function_signature_param_type("int * const *")
    'int *const *'
    >>> canonicalize_function_signature_param_type("int [3][4]")
    'int [3][4]'
    >>> canonicalize_function_signature_param_type("const int [3][4]")
    'const int [3][4]'

    A parenthesized declarator's own grouping parens (``void (*)(int)``, a
    pointer to a function; ``int (*)[3]``, a pointer to an array) are
    transparent for this purpose, not a real nesting level: the ``*``
    inside them is still the parameter's own outermost, by-value-qualified
    sigil, exactly like an unparenthesized ``int *``. An opening ``(`` is
    recognized as declarator grouping (and so does not itself count as
    nesting depth) whenever the next non-space character is ``*``/``&`` --
    a real function-parameter-list paren never starts that way, since a
    parameter list's first token is always a type, not a bare sigil. So
    ``void f(void (*)(int))`` and ``void f(void (* const)(int))`` name the
    same function -- the qualifier on the callback parameter's own pointer
    is by-value, just like ``int *``/``int * const`` (Codex review, PR
    #941: an earlier revision here treated every ``(`` as opaque, so a
    parenthesized declarator's own trailing cv-qualifier was silently
    preserved instead of stripped, fragmenting two spellings of one
    identical parameter type). This also reaches the previously-unchanged
    pointer-to-array case (``int (*)[3]``) the same way, for the identical
    reason -- its own outermost pointer's cv-qualifier, if any, is by-value
    too; only the trailing array bound inside the parens (the *pointee's*
    shape) stays untouched, same as always.

    >>> canonicalize_function_signature_param_type("void (*)(int)")
    'void ( * )(int)'
    >>> canonicalize_function_signature_param_type("void (* const)(int)")
    'void ( * )(int)'
    >>> canonicalize_function_signature_param_type("int (*)[3]")
    'int ( * )[3]'
    >>> canonicalize_function_signature_param_type("int (* const)[3]")
    'int ( * )[3]'

    A *pointer-to-member-function* declarator (``void (C::* const)(int)``)
    has the identical shape one level deeper: its own outermost sigil is
    ``*``, preceded by the member's qualified-name prefix (``C::``) inside
    the same declarator-grouping parens, rather than a bare sigil. That
    qualified-name prefix is recognized too, so its own trailing
    cv-qualifier is by-value and dropped the same way. And a declarator's
    trailing parameter list (the ``(int)`` above) is itself exactly as much
    "a function's parameters" as this function's own top-level one -- so
    each of ITS parameters gets this identical by-value treatment too,
    recursively, to any nesting depth (a callback parameter that itself
    takes a callback parameter): ``void (*)(int)`` and
    ``void (*)(const int)`` are the same adjusted callback type, not two.

    >>> canonicalize_function_signature_param_type("void (C::*)(int)")
    'void (C:: * )(int)'
    >>> canonicalize_function_signature_param_type("void (C::* const)(int)")
    'void (C:: * )(int)'
    >>> canonicalize_function_signature_param_type("void (*)(const int)")
    'void ( * )(int)'
    >>> canonicalize_function_signature_param_type("void (*)(char *)")
    'void ( * )(char *)'
    >>> canonicalize_function_signature_param_type("void (*)(const char *)")
    'void ( * )(char const *)'

    An MSVC/PE calling-convention keyword (``__cdecl``, ``__stdcall``, ...)
    can also precede a declarator's own sigil -- recognized the identical
    transparent way, and kept verbatim (it is genuine, distinguishing
    content, not something dropped). And a pointer-to-member-function's
    own TRAILING cv/ref-qualifiers -- the ones that follow its parameter
    list, e.g. ``const`` in ``void (C::*)(int) const`` -- are different
    from the pointer's own by-value qualifier before the parameter list:
    they qualify the POINTED-TO member function itself, a genuine,
    standard-mandated discriminator (``void (C::*)(int) const`` and
    ``void (C::*)(int)`` are two different, non-interchangeable types), so
    they are only reordered for cv, never dropped.

    >>> canonicalize_function_signature_param_type("void (__cdecl * const)(int)")
    'void (__cdecl * )(int)'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) const")
    'void (C:: * )(int) const'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) volatile const")
    'void (C:: * )(int) const volatile'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) &&")
    'void (C:: * )(int) & &'

    A nested-name-specifier's own segment can itself be a template-id
    (``C<int>::``), not only a plain identifier -- recognized the same
    transparent way, to any template-argument nesting depth. And any
    OTHER trailing specifier this function does not individually name --
    a ``noexcept``-specifier being the practically important one, since
    C++17 makes it part of the function type -- passes through verbatim
    rather than being dropped: only ``const``/``volatile`` are ever
    reordered here, never anything else.

    >>> canonicalize_function_signature_param_type("void (C<int>::* const)(int)")
    'void (C<int>:: * )(int)'
    >>> canonicalize_function_signature_param_type("void (*)(int) noexcept")
    'void ( * )(int) noexcept'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) noexcept const")
    'void (C:: * )(int) const noexcept'

    Preserving ``noexcept`` verbatim is necessary but not sufficient: since
    C++17 a function type's exception specification is exactly one of two
    kinds for TYPE purposes -- "non-throwing" (bare ``noexcept``,
    ``noexcept(true)``, or ``noexcept(1)`` -- a ``noexcept`` argument is
    contextually converted to ``bool``, and Clang's own ``qualType``
    genuinely emits the integer-literal spelling verbatim) and
    "potentially-throwing" (no specifier at all, ``noexcept(false)``, or
    ``noexcept(0)``) -- so those pairs canonicalize identically, not
    merely both survive. Only these four literal spellings are
    recognized; any other, non-literal ``noexcept(expr)`` is left
    untouched (evaluating an arbitrary constant expression is out of
    scope).

    >>> canonicalize_function_signature_param_type("void (*)(int) noexcept(true)")
    'void ( * )(int) noexcept'
    >>> canonicalize_function_signature_param_type("void (*)(int) noexcept(false)")
    'void ( * )(int)'
    >>> canonicalize_function_signature_param_type("void (*)(int) noexcept(1)")
    'void ( * )(int) noexcept'
    >>> canonicalize_function_signature_param_type("void (*)(int) noexcept(0)")
    'void ( * )(int)'

    An empty parameter list and a bare ``void`` are the identical "no
    parameters" adjusted type. And a ``const``/``volatile`` token that
    sits INSIDE a non-literal ``noexcept(expr)``'s own argument -- e.g.
    ``Foo<const int>`` below -- is that expression's own content, not
    this declarator's own trailing qualifier, and is never extracted.

    >>> canonicalize_function_signature_param_type("void (*)(void)")
    'void ( * )()'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) noexcept(Foo<const int>)")
    'void (C:: * )(int) noexcept(Foo<const int>)'

    An opaque (non-declarator-group) paren can also appear BEFORE the
    parameter's own actual pointer sigil, not only after it -- a real
    producer spelling for a type in an anonymous namespace,
    ``(anonymous namespace)::Foo``. That paren must not be mistaken for a
    declarator's trailing parameter list; a genuine pointee cv-qualifier
    on the sigil that follows it still distinguishes.

    >>> canonicalize_function_signature_param_type("(anonymous namespace)::Foo const *")
    '(anonymous namespace)::Foo const *'
    >>> canonicalize_function_signature_param_type("(anonymous namespace)::Foo *")
    '(anonymous namespace)::Foo *'

    A bare (non-parenthesized) data-member-pointer parameter, e.g.
    ``int C::*`` (pointer to an ``int`` member of ``C``), has a pointee
    cv-qualifier that must canonicalize the same regardless of whether it
    was spelled before or after the base type -- unlike the ordinary
    pointee case above, ``canonicalize_type_name`` MISPLACES a leading
    ``const`` across the ``C::`` infix rather than leaving it be, so this
    needs its own correction (only ``const``/``volatile`` positioning
    relative to the base type is fixed; if either was already reordered
    relative to the OTHER, e.g. ``volatile`` vs. ``const volatile``, that
    residual gap is the same, pre-existing, `canonicalize_type_name`-wide
    limitation ordinary non-member pointee cv already has -- out of scope
    here).

    >>> canonicalize_function_signature_param_type("const int C::*")
    'int const C:: *'
    >>> canonicalize_function_signature_param_type("int const C::*")
    'int const C:: *'

    ``restrict``/``__restrict``/``__restrict__`` shares the exact same
    outermost-vs-pointee position discipline ``const``/``volatile``
    already have here: dropped on the parameter's own outermost, by-value
    pointer position (real-compiler mangling confirms ``void f(int *)``
    and ``void f(int * restrict)`` are the SAME function), but preserved,
    genuinely distinguishing, on an inner pointer level (``void f(int
    **)`` and ``void f(int * restrict *)`` mangle to two different
    symbols) -- restrict is not unconditionally mangling-inert.

    >>> canonicalize_function_signature_param_type("int *")
    'int *'
    >>> canonicalize_function_signature_param_type("int *restrict")
    'int *'
    >>> canonicalize_function_signature_param_type("int *__restrict")
    'int *'
    >>> canonicalize_function_signature_param_type("int * restrict *") == canonicalize_function_signature_param_type("int * *")
    False
    >>> canonicalize_function_signature_param_type("void (*)(int *restrict)")
    'void ( * )(int *)'

    Clang's own ``qualType`` spelling for a calling-convention-decorated
    function-pointer declarator trails the attribute AFTER the parameter
    list (``__attribute__((cdecl))``) rather than using the leading
    ``__cdecl``-style keyword MSVC/castxml spell it with -- both spellings
    of the identical type now converge on one canonical form.

    >>> canonicalize_function_signature_param_type("void (__cdecl *)(int)")
    'void (__cdecl * )(int)'
    >>> canonicalize_function_signature_param_type("void (*)(int) __attribute__((cdecl))")
    'void (__cdecl * )(int)'
    >>> canonicalize_function_signature_param_type("void (C::*)(int) __attribute__((thiscall))")
    'void (__thiscall C:: * )(int)'

    A return type that merely CONTAINS a calling-convention keyword as a
    substring of an unrelated identifier (``my__cdecl_result``) must not
    be mistaken for a declarator that already has one -- the trailing
    attribute is still injected as the declarator's own leading keyword.

    >>> canonicalize_function_signature_param_type("my__cdecl_result (*)(int) __attribute__((stdcall))")
    'my__cdecl_result (__stdcall * )(int)'

    An explicit leading ``::`` (global-namespace lookup) is left
    genuinely distinguishing, NOT stripped -- direct compilation confirms
    Clang's own ``qualType`` can print the bare, unqualified spelling
    (no leading ``::``) for a type that resolves to a locally-shadowing
    entity distinct from the true global one a sibling declaration
    prints WITH the leading ``::`` -- so erasing that qualifier can
    silently merge two non-interchangeable types. See the module-level
    comment above (a since-reverted ``_GLOBAL_SCOPE_RE`` once stripped
    it) for the full worked counterexample.

    >>> canonicalize_function_signature_param_type("::dep::Thing *")
    '::dep::Thing *'
    >>> canonicalize_function_signature_param_type("dep::Thing *")
    'dep::Thing *'
    >>> canonicalize_function_signature_param_type("::dep::Thing *") != canonicalize_function_signature_param_type("dep::Thing *")
    True
    >>> canonicalize_function_signature_param_type("(anonymous namespace)::Foo *")
    '(anonymous namespace)::Foo *'
    """
    if _depth > _MAX_PARAM_TYPE_NESTING_DEPTH:
        return name
    canonical = canonicalize_type_name(
        _decay_top_level_array(canonicalize_type_name(name))
    )
    depth = 0
    # True for a paren currently open on `transparent_parens` that groups a
    # declarator's own sigil (see the docstring above) -- popped, not
    # depth-counted, so a sigil inside one is still found at depth 0.
    transparent_parens: list[bool] = []
    last_top_level_sigil = -1
    has_top_level_bracket = False
    # Once the declarator's own sigil has ALREADY been found and a
    # genuine (opaque) top-level paren is then seen -- its trailing
    # parameter list -- nothing after that paren is eligible to become a
    # NEW "last top-level sigil". Without this, a trailing ref-qualifier
    # on a pointer-to-member-function (`void (C::*)(int) &&`) -- itself a
    # `*`/`&`-shaped token sitting at depth 0, textually after the
    # parameter list closes -- would wrongly override the declarator's
    # own already-found sigil and corrupt the prefix/suffix split (Codex
    # review, PR #941, tenth round). The `last_top_level_sigil != -1`
    # guard matters: an opaque paren can also appear BEFORE any
    # declarator sigil has been found at all -- a real, observed
    # producer spelling for an anonymous-namespace-qualified type,
    # `(anonymous namespace)::Foo const *` -- and that paren is not a
    # declarator's parameter list at all, just qualifier text preceding
    # the parameter's actual pointer sigil; arming the flag on ANY
    # top-level opaque paren, sigil-found or not, wrongly locked out that
    # later real `*` entirely, falling through to the by-value branch and
    # merging `Foo const *` with `Foo *` (CodeRabbit review, PR #941,
    # fourteenth round: caught with the codebase's own real
    # `"(anonymous namespace)::T"` spelling convention, not a hypothetical
    # one).
    seen_top_level_opaque_paren = False
    for i, ch in enumerate(canonical):
        if ch == "(":
            transparent = _is_declarator_group(canonical, i + 1)
            transparent_parens.append(transparent)
            if not transparent:
                if depth == 0 and last_top_level_sigil != -1:
                    seen_top_level_opaque_paren = True
                depth += 1
            continue
        if ch == ")":
            was_transparent = transparent_parens.pop() if transparent_parens else False
            if not was_transparent:
                depth = max(0, depth - 1)
            continue
        if ch == "[" and depth == 0:
            has_top_level_bracket = True
        if ch in "<[":
            depth += 1
        elif ch in ">]":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0 and not seen_top_level_opaque_paren:
            last_top_level_sigil = i
    if last_top_level_sigil == -1:
        # No real pointer/reference sigil. An undecayed top-level array
        # (multi-dimensional, or a parenthesized declarator) is returned
        # untouched -- see this function's own docstring for why stripping
        # it would be actively wrong, not merely incomplete. Otherwise
        # this is a genuine by-value scalar/class type, safe to strip.
        return (
            canonical
            if has_top_level_bracket
            else _strip_cv_tokens_outside_nesting(canonical)
        )
    prefix = canonical[: last_top_level_sigil + 1]
    # A bare (non-parenthesized) data-member-pointer's pointee cv needs
    # its own independent re-canonicalization -- see
    # _find_member_pointer_qualifier's own docstring for why. Only applies
    # when the split-off "base" has no unmatched open paren: a
    # parenthesized member-function-pointer/-array declarator group
    # (`(C::*)`) hits this same code path (`(`/`)` don't affect depth,
    # since they're transparent), and its own "base" (a return type
    # fragment like `void (`) is not a real standalone type to
    # re-canonicalize.
    qualifier_span = _find_member_pointer_qualifier(prefix)
    if qualifier_span is not None:
        qualifier_start, qualifier_end = qualifier_span
        base = prefix[:qualifier_start].rstrip()
        if base.count("(") == base.count(")"):
            tail_cv = " ".join(re.findall(r"const|volatile", prefix[qualifier_end:]))
            combined_base = f"{base} {tail_cv}".strip() if tail_cv else base
            qualifier_text = prefix[qualifier_start:qualifier_end]
            sigil_char = prefix[-1]
            prefix = (
                f"{canonicalize_type_name(combined_base)} {qualifier_text} {sigil_char}"
            )
    raw_suffix = canonical[last_top_level_sigil + 1 :]
    split = _split_at_trailing_param_list(raw_suffix)
    if split is None:
        # A bare pointer with no trailing declarator (e.g. "int * const")
        # -- every depth-0 cv token here is the pointer's own, by-value.
        suffix = _strip_cv_tokens_outside_nesting(raw_suffix)
    else:
        # A declarator's own trailing parameter list is present (a
        # callback or pointer-to-member-function). Everything BEFORE it is
        # still the pointer's own by-value qualifier region (stripped);
        # the parameter list's own contents get the identical nested
        # by-value treatment; anything AFTER the parameter list's closing
        # paren is the POINTED-TO member function's own cv/ref qualifiers
        # -- genuinely distinguishing, only reordered, never stripped.
        head, params_and_after = split
        head = _strip_cv_tokens_outside_nesting(head)
        close = _find_matching_paren(params_and_after, 0)
        params = _normalize_nested_param_lists(params_and_after[: close + 1], _depth)
        tail = params_and_after[close + 1 :]
        # Clang's trailing `__attribute__((cdecl))`-style calling-
        # convention spelling (see `_CALLING_CONVENTION_ATTR_RE`'s own
        # comment) is not itself a member/pointed-to-function qualifier --
        # normalize it into the SAME leading-keyword position
        # `_is_declarator_group` already recognizes, so both spellings of
        # one identical type converge on one `prefix`, before whatever
        # remains of `tail` goes through the ordinary member-qualifier
        # canonicalization below.
        attr_match = _CALLING_CONVENTION_ATTR_RE.search(tail)
        if attr_match is not None:
            tail = tail[: attr_match.start()] + tail[attr_match.end() :]
            open_idx = prefix.rfind("(")
            if open_idx != -1 and not _CALLING_CONVENTION_KEYWORD_RE.match(
                prefix, open_idx + 1
            ):
                keyword = f"__{attr_match.group(1)}"
                prefix = re.sub(
                    r"\s+",
                    " ",
                    prefix[: open_idx + 1] + f"{keyword} " + prefix[open_idx + 1 :],
                )
        member_quals = _canonicalize_member_qualifiers(tail)
        # `head` (a bare pointer-declarator's own closing paren, plus any
        # by-value cv already stripped out of it) is joined directly onto
        # `params` -- no separator space -- since `head` always ends in
        # the declarator group's own ")" immediately followed by the
        # parameter list's own "(", with nothing to separate.
        suffix = head + params
        if member_quals:
            suffix = f"{suffix} {member_quals}"
    return f"{prefix} {suffix}".rstrip() if suffix else prefix
