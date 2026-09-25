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

"""Depth-aware string helpers for parameter-type spellings.

Split out of :mod:`abicheck.model.signature_normalization` (which stays the
one owner of the canonicalization *rules*) so that module stays under the
model package's production file ceiling: these are its bracket-aware
scanning primitives -- strip a qualifier outside nesting, decay a top-level
array, split on top-level commas, find a matching parenthesis -- with no
canonicalization policy of their own. A leaf: imports nothing first-party.
"""

from __future__ import annotations

import re

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
    # Fast path: with no ``[`` anywhere there is no top-level bracket either,
    # so the scan below would return the input unchanged. The vast majority
    # of parameter spellings take this path, and the per-character loop was
    # a measurable self-time hot spot on large header surfaces.
    if "[" not in canonical_type:
        return canonical_type
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
