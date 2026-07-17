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

"""Public-variable comparison helpers: alignment changes and top-level
const/reference-aware type-spelling normalization.

Leaf module (must not import from ``diff_symbols`` to avoid an import
cycle). ``diff_symbols._check_variable`` is the sole caller.
"""

from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .model import Variable
from .name_classification import _find_matching_close


def _check_variable_alignment(
    mangled: str, v_old: Variable, v_new: Variable
) -> list[Change]:
    """Emit a change when a variable's declared alignment changed.

    Tri-state: None = not captured (older snapshots / dumpers without
    alignment support) — skip rather than compare.
    """
    if v_old.alignment_bits is None or v_new.alignment_bits is None:
        return []
    if v_old.alignment_bits == v_new.alignment_bits:
        return []
    return [
        make_change(
            ChangeKind.VAR_ALIGNMENT_CHANGED,
            symbol=mangled,
            name=v_old.name,
            old=str(v_old.alignment_bits),
            new=str(v_new.alignment_bits),
        )
    ]


_TRAILING_CONST_RE = re.compile(r"\s*\bconst\b\s*$")
_LEADING_CONST_TOKEN_RE = re.compile(r"^\s*\bconst\b\s*")
_CV_TOKEN_RE = re.compile(r"\b(?:const|volatile)\b")


def _has_top_level_pointer_or_ref(canonical_type: str) -> bool:
    """True if *canonical_type* has a ``*``/``&`` outside any ``<...>``
    template-argument bracket.

    A plain substring search for ``*``/``&`` would also match one nested
    *inside* a template argument (e.g. ``std::vector<int *>`` — a by-value
    vector of pointers, not itself a pointer), wrongly routing a pure
    top-level const flip on that by-value variable into the
    pointer/reference branch below, which only strips a *trailing* const —
    but the top-level const here is leading (Codex review).
    """
    depth = 0
    for ch in canonical_type:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            return True
    return False


def _last_sigil_in_range(canonical_type: str, start: int, end: int) -> int | None:
    """Index of the last ``*``/``&`` in ``canonical_type[start:end]`` outside
    any ``<...>`` bracket, or None. Same "top-level" definition as
    ``_has_top_level_pointer_or_ref``, just reporting a position within a
    sub-range instead of a whole-string boolean.
    """
    depth = 0
    pos = None
    for i in range(start, end):
        ch = canonical_type[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            pos = i
    return pos


def _first_top_level_paren_span(canonical_type: str) -> tuple[int, int] | None:
    """``(open_idx, close_idx)`` of the first top-level (non-``<...>``-
    nested) ``(...)`` group, or None if there is none."""
    depth = 0
    for i, ch in enumerate(canonical_type):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            close = _find_matching_close(canonical_type, i)
            return i, min(close, len(canonical_type) - 1)
    return None


def _declarator_sigil_pos(canonical_type: str) -> int | None:
    """Index of the variable's OWN declarator ``*``/``&``, as opposed to one
    belonging to a parameter or array size nested further in the spelling.

    A function-/array-pointer variable's canonical spelling is always
    ``BaseType ( *quals)(...)``/``BaseType ( *quals)[...]`` — the FIRST
    top-level ``(...)`` group is structurally this declarator (the actual
    parameter list or array dimensions, if present, always comes after it).
    Searching the whole string for the LAST top-level sigil instead (as an
    earlier version of this function did) picks up a parameter's own
    pointer sigil for a callback/function-pointer parameter (``"void (
    *const)(int *)"`` — the last ``*`` is the parameter's, not the outer
    declarator's), so its trailing ``const`` never gets recognized as the
    variable's own (Codex review, PR #589). Falls back to the whole
    string's last top-level sigil for a bare pointer with no parens at all
    (``"int * const"``).
    """
    span = _first_top_level_paren_span(canonical_type)
    if span is not None:
        open_idx, close_idx = span
        pos = _last_sigil_in_range(canonical_type, open_idx + 1, close_idx)
        if pos is not None:
            return pos
    return _last_sigil_in_range(canonical_type, 0, len(canonical_type))


def _strip_trailing_declarator_const(canonical_type: str) -> str:
    """Strip a top-level pointer/reference declarator's own trailing
    ``const`` — whether at the absolute end of the string (a bare pointer,
    ``"int * const"``) or immediately before the closing paren/bracket of a
    function- or array-pointer declarator (``"void ( *const)()"``, ``"int
    ( *const)[5]"`` — a variable whose type itself is a function or array
    pointer, canonicalized with the qualifier directly after the ``*``, not
    at the string's end). Only a run of pure cv tokens between the sigil and
    that close counts; anything else there (a real parameter/element type)
    means this isn't the simple ``"(*quals)"`` declarator shape, so fall
    back to the plain end-of-string case rather than risk stripping
    something that isn't actually this declarator's own qualifier
    (CodeRabbit review, PR #589).
    """
    pos = _declarator_sigil_pos(canonical_type)
    if pos is not None:
        span_end = len(canonical_type)
        for k in range(pos + 1, len(canonical_type)):
            if canonical_type[k] in ")]":
                span_end = k
                break
        span = canonical_type[pos + 1 : span_end]
        if (
            span_end < len(canonical_type)
            and re.fullmatch(r"(?:\s|const|volatile)*", span)
            and _CV_TOKEN_RE.search(span)
        ):
            # .strip(): canonicalize_type_name never puts whitespace directly
            # after the sigil or directly before the declarator's closing
            # bracket (e.g. an unchanged volatile-only declarator canonicalizes
            # to "( *volatile)", not "( * volatile)") — removing "const" from
            # a combined "const volatile"/"volatile const" span leaves a
            # separator space stranded at whichever end "const" vacated,
            # which must be trimmed to match that convention or an unrelated,
            # unchanged volatile qualifier makes the two sides spuriously
            # compare unequal (Codex review, PR #589).
            new_span = re.sub(r"\bconst\b", "", span).strip()
            return canonical_type[: pos + 1] + new_span + canonical_type[span_end:]
    return _TRAILING_CONST_RE.sub("", canonical_type)


def _without_top_level_const(canonical_type: str) -> str:
    """Strip the *top-level* ``const`` from an already-canonicalized type name.

    ``canonicalize_type_name`` normalizes a leading ``const T`` to ``T
    const`` (moving the qualifier immediately after what it qualifies) —
    but only when the base type has no template args (``"<...>"``); for a
    templated base it deliberately leaves the spelling untouched, so a
    top-level const on e.g. ``std::vector<int>`` stays leading
    (``"const std::vector<int>"``), not trailing.

    So which end is "top-level" depends on whether a pointer/reference
    sigil is present, not on template-ness:

    - No ``*``/``&`` at all: the *whole object* is what's qualified, so a
      const at *either* end (leading, for a templated base; trailing, the
      east-const form for a non-template base) is the top-level qualifier
      — strip whichever is present.
    - A ``*``/``&`` present: the top-level (pointer-itself) qualifier is
      always the trailing token (``"int * const"``, or ``"std::vector<int>
      * const"``) regardless of template-ness — see
      ``_strip_trailing_declarator_const`` for the function-/array-pointer
      declarator's own variant of "trailing". A *leading* const there
      (``"int const *"``, ``"const std::vector<int> *"``) qualifies the
      pointee, not the pointer, and must NOT be stripped — collapsing it
      would hide a real type change (the pointer itself is still writable;
      only what it points to changed) behind a misleading "variable became
      const" (Codex review, x2: the original non-template pointee-const
      case, and the templated-base variant of the same issue).
    """
    if _has_top_level_pointer_or_ref(canonical_type):
        return _strip_trailing_declarator_const(canonical_type)
    stripped = _LEADING_CONST_TOKEN_RE.sub("", canonical_type)
    return _TRAILING_CONST_RE.sub("", stripped)
