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
dedicated cv/array-decay logic here.

Leaf module: imports only ``name_classification.canonicalize_type_name``
(a dependency-free sibling), nothing above ``model``, per ADR-063 D10 --
the same leaf-module contract ``model/identity.py`` itself states.
"""

from __future__ import annotations

import re

from ..name_classification import canonicalize_type_name

__all__ = ["canonicalize_function_signature_param_type"]

_CV_WORD_RE = re.compile(r"\b(?:const|volatile)\b")


def _has_top_level_ptr_or_ref(canonical_type: str) -> bool:
    """Whether *canonical_type* is pointer-shaped at nesting depth 0 --
    a ``*``/``&`` sigil, or a top-level ``[`` array declarator (Codex
    review, PR #941: a function PARAMETER's array type always decays to a
    pointer, e.g. ``int []`` -> ``int *``, so any cv-qualifier on the
    element type is pointee-level, exactly like an explicit pointer --
    ``void f(int[])`` and ``void f(const int[])`` are two distinct,
    independently-mangled overloads, not one). Either shape means the
    value itself is a pointer, not merely something passed by value that
    happens to *contain* one (``Box<int *>``, ``std::function<void(int&)>``,
    an array *bound inside* a template argument). A minimal, self-
    contained reimplementation of the identical algorithm
    ``name_classification._has_top_level_ptr_or_ref`` already applies for
    a different purpose -- deliberately duplicated rather than imported,
    since ``name_classification.py`` is a frozen, no-growth legacy module
    (ADR-061 debt ledger, `architecture/debt.yaml`) that new code must not
    grow, and the helper it would be imported from is private besides.
    The array case is added here rather than upstream because this
    module's fallback signature discriminator is built directly from a
    caller-supplied *string* that may still spell an array literally
    (``"int []"``) -- it makes no assumption about whether a given
    producer's own parsed representation already reflects the decay.
    """
    depth = 0
    for ch in canonical_type:
        if ch == "[" and depth == 0:
            return True
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            return True
    return False


def _strip_cv_tokens_outside_nesting(s: str) -> str:
    """Blank out every ``const``/``volatile`` token in *s* that sits at
    nesting depth 0 (outside any ``<...>``/``(...)``/``[...]``), then
    collapse the resulting whitespace. The one primitive both branches of
    :func:`canonicalize_function_signature_param_type` reduce to -- the
    by-value case applies it to the whole string, the pointer case applies
    it only to the suffix after the parameter's outermost pointer/
    reference sigil (see that function's own docstring for why those are
    the two, and only the two, safe places to strip).
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
    as "pointer-shaped enough not to strip its cv" (:func:`_has_top_level_
    ptr_or_ref`) but never performed the decay itself, so ``int []``/
    ``int [3]``/``int [4]``/``int *`` -- all the identical adjusted
    parameter type -- still canonicalized to four different strings.

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


def canonicalize_function_signature_param_type(name: str) -> str:
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
    >>> canonicalize_function_signature_param_type("int (*)[3]")
    'int ( *)[3]'
    """
    canonical = canonicalize_type_name(
        _decay_top_level_array(canonicalize_type_name(name))
    )
    depth = 0
    last_top_level_sigil = -1
    has_top_level_bracket = False
    for i, ch in enumerate(canonical):
        if ch == "[" and depth == 0:
            has_top_level_bracket = True
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
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
    suffix = _strip_cv_tokens_outside_nesting(canonical[last_top_level_sigil + 1 :])
    return f"{prefix} {suffix}".rstrip() if suffix else prefix
