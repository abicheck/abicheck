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

"""Type-spelling qualifier helpers for the ``clang -ast-dump=json`` backend.

Split out of ``dumper_clang.py``, which sits at the 2000-line hard cap the
AI-readiness gate enforces. These four functions answer one question between
them — *does this declaration's own (top-level) type carry qualifier X?* —
against clang's **printed** type spelling, which is the only form the JSON
AST offers:

- :func:`_desugared_qualtype` unwraps a typedef, since a qualifier can hide
  behind an alias;
- :func:`_last_top_level_ptr_end` finds the depth-0 pointer boundary, so a
  qualifier belonging to a pointee is not read as the declaration's own;
- :func:`_field_own_cv_source` is the substring those two produce together;
- :func:`_clang_param_is_restrict` is one concrete question asked of it.

Pure and leaf: takes plain AST dicts, imports nothing from ``dumper_clang``,
and is unit-testable with no clang installed.
"""

from __future__ import annotations

import re
from typing import Any


def _desugared_qualtype(node: dict[str, Any]) -> str:
    """The fully-desugared type spelling, when clang provides one.

    A field declared through a typedef to a cv-qualified type
    (``typedef const int T; struct S { T x; };``) renders ``qualType`` as
    the bare alias ``"T"`` — the real ``"const int"`` is only visible via
    the separate ``desugaredQualType`` key clang emits precisely when a
    type alias needs unwrapping. A plain (non-aliased) field carries no
    ``desugaredQualType`` key at all (confirmed empirically), so falling
    back to ``qualType`` is exact, not merely a guess, for every other
    case. Used only for the const/volatile regex check below — the
    field's own displayed ``type`` spelling stays the sugared form users
    actually wrote (Codex review, PR #582: mirrors dumper_castxml's
    Typedef-indirection walk for the identical reason — a regex on the
    display spelling alone misses a qualifier hidden behind an alias).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        desugared = type_obj.get("desugaredQualType")
        if isinstance(desugared, str) and desugared:
            return desugared
        return str(type_obj.get("qualType", ""))
    return ""


def _last_top_level_ptr_end(type_str: str) -> int:
    """Index just past the last depth-0 ``*`` in *type_str*, or -1 if none.

    A ``*`` nested inside a template argument list, function-parameter
    list, or array subscript doesn't count — the value itself isn't a
    pointer at that syntactic position. Depth tracking mirrors
    ``name_classification._has_top_level_ptr_or_ref``.
    """
    depth = 0
    last = -1
    for i, ch in enumerate(type_str):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "*" and depth == 0:
            last = i + 1
    return last


def _field_own_cv_source(desugared: str) -> str:
    """Substring of *desugared* that describes the FIELD's OWN const/
    volatile qualifier, as opposed to its pointee's.

    A pointer typedef's desugared spelling puts a POINTEE qualifier before
    the ``*`` (``const int *`` — pointer to const int, the pointer itself
    is NOT const) and the pointer VALUE's own qualifier as a suffix after
    it, with no space (``int *const`` — confirmed against real clang
    output). Scanning the whole string for ``const``/``volatile`` (as an
    earlier version of ``_make_field`` did) misread the pointee's
    qualifier as the field's own, so a field typed through
    ``typedef const int *P;`` was wrongly marked ``is_const=True`` even
    though ``P`` itself is a plain, non-const pointer (Codex review, PR
    #582 — a pointer-typedef sibling of the scalar-typedef case
    ``_desugared_qualtype`` already handles). A non-pointer type has no
    such ambiguity — the whole spelling describes the field itself.
    """
    end = _last_top_level_ptr_end(desugared)
    return desugared[end:] if end >= 0 else desugared


def _declarator_group(type_str: str) -> str | None:
    """Contents of *type_str*'s parenthesized declarator group, or ``None``.

    C's declarator precedence parenthesizes a pointer whose pointee is an
    array or a function, so clang spells those as ``int (*restrict)[3]`` and
    ``void (*)(int *restrict)`` — in both, the declared entity's own ``*``
    is inside the parentheses and NOTHING at depth 0 describes it. This
    returns that group's contents (``"*restrict"``, ``"*"``) so a caller can
    ask its question there instead.

    The group is identified as the first depth-0 parenthesized group whose
    contents begin with ``*``. That distinguishes it from the trailing
    parameter list of a function pointer (``(int *restrict)``, which begins
    with a type name) and from an array extent (``[3]``, not parenthesized
    at all) — confirmed against real clang 18 output for the pointer-to-
    array, pointer-to-function, pointer-to-pointer-to-array, and
    array-of-pointers spellings.

    ``None`` when the spelling has no such group, i.e. an ordinary
    unparenthesized declarator like ``int *restrict``, where depth 0 is
    exactly where the answer lives.
    """
    depth = 0
    i = 0
    while i < len(type_str):
        ch = type_str[i]
        if ch in "<([":
            if ch == "(" and depth == 0:
                j = i + 1
                while j < len(type_str) and type_str[j] == " ":
                    j += 1
                if j < len(type_str) and type_str[j] == "*":
                    inner = 1
                    k = i + 1
                    while k < len(type_str) and inner:
                        if type_str[k] in "<([":
                            inner += 1
                        elif type_str[k] in ">)]":
                            inner -= 1
                        k += 1
                    return type_str[i + 1 : k - 1]
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        i += 1
    return None


def _clang_param_is_restrict(node: dict[str, Any]) -> bool:
    """Whether *node* (a ``ParmVarDecl``) is a ``restrict``-qualified pointer.

    Matches :meth:`abicheck.dumper_castxml._CastxmlParser._resolve_cv_restrict`'s
    semantics — the parameter's OWN (top-level) qualification, with typedef
    indirection followed — so the two backends produce a directly comparable
    bool rather than a backend-specific encoding:

    - **Typedef indirection** is why this reads
      :func:`_desugared_qualtype` rather than :func:`_qualtype`: a parameter
      declared through ``typedef int *restrict rptr;`` renders ``qualType``
      as the bare alias ``"rptr"``, with the real ``"int *restrict"`` only
      in ``desugaredQualType`` (confirmed against real clang 18 output,
      same mechanism ``_desugared_qualtype``'s own docstring documents for
      const/volatile). castxml's walk follows its ``Typedef`` chain for
      the identical reason.
    - **Top-level only** is why only the span after the last depth-0 ``*``
      is searched: ``int *restrict *`` qualifies the INNER pointer, leaving
      the parameter itself unqualified, while ``int **restrict`` qualifies
      the parameter (both spellings confirmed empirically). Scanning the
      whole spelling would conflate the two.
    - **A parenthesized declarator wins over the top level**, which is the
      one rule that is not obvious from the plain-pointer case. When C's
      declarator precedence forces the parameter's own ``*`` into
      parentheses, everything at depth 0 belongs to something else — the
      pointee (``int *(*restrict)[3]``, whose leading ``*`` is the array
      element's) or the callback's parameter list (``void (*)(int
      *restrict)``, whose ``restrict`` is the CALLBACK ARGUMENT's).
      :func:`_declarator_group` finds the group that actually holds the
      parameter's pointer and the search happens inside it, so both of
      those answer correctly. Two review rounds each found one of them:
      scanning the whole spelling reported a false True for the callback,
      and requiring a depth-0 ``*`` reported a false False for the pointer
      to an array — a real, legal, restrict-qualified object pointer that
      castxml does see. Every spelling in this docstring was confirmed
      against real clang 18 output.

    A function-pointer parameter answers False, and that costs nothing even
    though it looks like the same shape as the array case: a pointer to a
    *function* may not be ``restrict``-qualified at all (C11 6.7.3p2 —
    ``restrict`` qualifies a pointer to an *object* type), so its declarator
    group can never legally carry the qualifier and no distinction between
    the two declarator kinds is needed here. Verified rather than reasoned
    about: both ``void (*restrict cb)(int)`` and ``int *(*restrict cb)(void)``
    are rejected outright by clang in C and C++ alike ("pointer to function
    type ... may not be 'restrict' qualified").

    Both the C spelling (``restrict``) and the C++ ones (``__restrict`` /
    ``__restrict__``, which clang normalizes to ``__restrict``) are
    recognized; the word-boundary anchors keep a type merely *named*
    ``restrict_like`` from matching.
    """
    desugared = _desugared_qualtype(node)
    scope = _declarator_group(desugared)
    if scope is None:
        scope = desugared
    end = _last_top_level_ptr_end(scope)
    if end < 0:
        return False
    return bool(re.search(r"\b(?:__)?restrict(?:__)?\b", scope[end:]))
