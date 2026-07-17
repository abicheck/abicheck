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
      * const"``) regardless of template-ness. A *leading* const there
      (``"int const *"``, ``"const std::vector<int> *"``) qualifies the
      pointee, not the pointer, and must NOT be stripped — collapsing it
      would hide a real type change (the pointer itself is still writable;
      only what it points to changed) behind a misleading "variable became
      const" (Codex review, x2: the original non-template pointee-const
      case, and the templated-base variant of the same issue).
    """
    if _has_top_level_pointer_or_ref(canonical_type):
        return _TRAILING_CONST_RE.sub("", canonical_type)
    stripped = _LEADING_CONST_TOKEN_RE.sub("", canonical_type)
    return _TRAILING_CONST_RE.sub("", stripped)
