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

"""Recovers a clang parameter's top-level indirection kind from its spelling.

Split out of ``functions.py`` (which grew past the AI-readiness gate's
800-line production soft cap) purely to keep that module legible -- this is
genuinely one self-contained primitive (:func:`param_kind`), with a single
call site in ``functions.py``'s own parameter-building comprehension,
mirroring this package's ``return_type.py`` split.
"""

from __future__ import annotations

from abicheck.model import ParamKind


def param_kind(type_str: str) -> ParamKind:
    """Best-effort top-level indirection kind from a written type spelling.

    ADR-063 Phase 5 (eleventh batch): clang gives us only a rendered type
    spelling here, not a type-graph node kind the way castxml's
    ``PointerType``/``ReferenceType``/``RValueReferenceType`` tags do (see
    ``extract/headers/castxml/type_resolution.top_level_param_kind``) --
    same spelling-heuristic status as this package's sibling
    ``_pointer_depth``. The caller (``functions.py``) passes the DESUGARED
    spelling (``context.qualtype_desugared``), not the raw ``qualType``, so
    a typedef'd pointer/reference/rvalue-reference is still recognized --
    without that, a typedef'd indirection's alias name carries no
    ``&``/``*`` token at all and this function would silently, wrongly
    return ``VALUE`` (Codex review, PR #1200). Shares its bracket-depth
    blind spot for a declarator-grouped indirection (e.g.
    ``int (*)[3]``, pointer-to-array): the ``*``/``&`` sits between literal
    parens there too, so it is skipped the same way ``_pointer_depth``
    already undercounts that shape to 0. Deliberately consistent with that
    function rather than fixed independently, so the two never disagree
    about the SAME spelling's bracket handling.

    The outermost (rightmost, bracket-depth-0) ``&&``/``&``/``*`` token
    names the top-level kind, since a C++ declarator's outer indirection
    appears rightmost in the type spelling: ``int *&`` is a REFERENCE to a
    pointer (reference is outermost), ``int **`` is a POINTER to a pointer.
    Before this function existed, clang never populated ``Param.kind`` at
    all -- every parameter, pointer or not, read the dataclass's own
    resting ``ParamKind.VALUE`` (see
    ``AbiSnapshot.param_kind_facts_reliable``).
    """
    bracket = 0
    last: str | None = None
    i = 0
    n = len(type_str)
    while i < n:
        ch = type_str[i]
        if ch in "<[(":
            bracket += 1
        elif ch in ">])":
            bracket = max(0, bracket - 1)
        elif bracket == 0:
            if ch == "&" and i + 1 < n and type_str[i + 1] == "&":
                last = "&&"
                i += 1  # consume the second '&' too
            elif ch in "&*":
                last = ch
        i += 1
    if last == "&&":
        return ParamKind.RVALUE_REF
    if last == "&":
        return ParamKind.REFERENCE
    if last == "*":
        return ParamKind.POINTER
    return ParamKind.VALUE
