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


"""Ordinal part 1 of ``symbols.py``'s entry list -- not a separate owner.

``symbols.py`` remains the taxonomy and the single public name
(``SYMBOLS_ENTRIES``); see its docstring for this taxonomy's scope, its
boundary against the other four, and the methodology the entries were
categorized by. This file holds a contiguous slice of that one list and
claims no responsibility of its own, so nothing should import it directly.

The split is by declaration-order line position, not by concern, purely so
each file stays under ADR-061's 800-line ceiling -- the same reason and the
same shape as ``kind_names_{1,2,3}.py``, whose own docstring records that an
ordinal split is the right tool when the content is a data table rather than
behavior. Partitioning *this* list by a named sub-concern would be a
different change: it would move the D9 ownership boundary that
``symbols``/``types``/``platform``/``build``/``source`` already draws, and
the "Adding a new ChangeKind" procedure names those five modules by name.
"""

from __future__ import annotations

from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

SYMBOLS_ENTRIES_1: list[ChangeKindMeta] = [
    _E(
        "func_became_inline",
        _R,
        impact="A function gained the inline attribute. Becoming inline "
        "does not by itself break anyone: an "
        "already-linked caller still binds while the library keeps exporting "
        "the symbol, and a recompiled caller that odr-uses the function either "
        "sees its definition in the header or resolves the call against that "
        "same export. What breaks callers is the export disappearing once "
        "nothing forces an out-of-line definition (or a C99 `inline` "
        "definition leaving no external one), and that is its own finding "
        "(`func_removed`, `func_visibility_changed`) wherever the export table "
        "is in evidence. What remains is a risk: rebuilt callers carry their "
        "own copy of the body, so a later change to it reaches only rebuilt "
        "code, and a vague-linkage copy in a consumer can take precedence over "
        "the library's.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "func_deleted_dwarf",
        _B,
        impact="Function marked as deleted (= delete) detected via DWARF debug info. "
        "The function was previously callable; callers will fail to link.",
        description_template="Function explicitly deleted (= delete): {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
    ),
]
