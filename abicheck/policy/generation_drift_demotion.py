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

"""Demote spelling-only drift between operands of different extractor
generations (ADR-062 D2 fallback; bug class
``storage.cross_generation_spelling_drift``).

``storage/generation_migrations.py`` rewrites every spelling change a
generation bump *declared*. A change nobody declared still diffs an older
baseline's spelling against a fresh one. When the two operands were extracted
by different generations, a type-spelling finding whose old and new values
differ only in an identifier the old snapshot never declares (and that is not
a C/C++ builtin) is far more likely an extractor respelling than a real type
change, so it is demoted to ``COMPATIBLE_WITH_RISK`` with the reason
"extractor-generation drift; re-dump baseline". It is annotated, never
removed (ADR-067 record before disposing): the finding, its old and new
values and the rule stay in the report.

Never applies when both operands share a generation, never to a finding a
prior rule already modulated, and never when the punctuation/qualifier
skeleton differs (``T*`` -> ``T&`` stays a break), when a builtin is renamed
(``int`` -> ``long``), or when the old identifier names a type the old
snapshot declares (``Foo`` -> ``Bar``).

A generation that declared an array-extent shift (its ``SPELLING_MIGRATIONS``
row's ``extent_shift``, passed in by the workflow) also demotes a finding
whose spellings differ only by every differing ``[N]`` growing by exactly
that shift: castxml spelled an extent as its last index before generation 15.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.name_heuristics import (
    NameHeuristicEffect,
    StructuralFact,
    register_name_heuristic,
)
from ..model.snapshot import AbiSnapshot

__all__ = [
    "GENERATION_DRIFT_REASON",
    "GENERATION_DRIFT_RULE",
    "demote_generation_spelling_drift",
    "is_extent_shift_drift",
    "is_spelling_only_drift",
]

GENERATION_DRIFT_RULE = "extractor_generation_drift"
GENERATION_DRIFT_REASON = "extractor-generation drift; re-dump baseline"

#: Kinds whose old/new values are type spellings.
_SPELLING_KINDS = frozenset(
    {
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
        ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
        ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
    }
)

#: Builtin and keyword spellings: renaming one is a real type change.
_BUILTINS = frozenset(
    {
        "void",
        "bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "const",
        "volatile",
        "restrict",
        "__restrict",
        "struct",
        "class",
        "union",
        "enum",
        "typename",
        "_Bool",
        "_Complex",
        "__int128",
        "size_t",
        "ptrdiff_t",
        "nullptr_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "intptr_t",
        "uintptr_t",
    }
)

_IDENT = re.compile(r"[A-Za-z_]\w*")
_TOKEN = re.compile(r"[A-Za-z_]\w*|\S")


def _declared_names(snapshot: AbiSnapshot) -> frozenset[str]:
    declarations = snapshot.declarations
    names: set[str] = set()
    spelled = [t.name for t in declarations.types]
    spelled += [e.name for e in declarations.enums]
    spelled += list(declarations.typedefs)
    for name in spelled:
        names.update(_IDENT.findall(name))
    return frozenset(names)


def is_spelling_only_drift(old: str, new: str, old_declared: Iterable[str]) -> bool:
    """Whether *old* -> *new* differs only by renaming identifiers that are
    neither builtins nor names *old_declared* holds, keeping the skeleton."""
    old_t, new_t = _TOKEN.findall(old), _TOKEN.findall(new)
    if len(old_t) != len(new_t) or old_t == new_t:
        return False
    declared = frozenset(old_declared)
    for a, b in zip(old_t, new_t, strict=True):
        if a == b:
            continue
        if not (_IDENT.fullmatch(a) and _IDENT.fullmatch(b)):
            return False  # punctuation, qualifier or literal skeleton changed
        if a in _BUILTINS or b in _BUILTINS or a in declared:
            return False
    return True


def is_extent_shift_drift(old: str, new: str, shift: int) -> bool:
    """Whether *old* -> *new* differs only in array extents (``[N]``), each
    differing one grown by exactly *shift*, keeping every other token."""
    if shift <= 0:
        return False
    old_t, new_t = _TOKEN.findall(old), _TOKEN.findall(new)
    if len(old_t) != len(new_t) or old_t == new_t:
        return False
    for index, (a, b) in enumerate(zip(old_t, new_t, strict=True)):
        if a == b:
            continue
        if not (a.isdigit() and b.isdigit()):
            return False
        if (
            index == 0
            or old_t[index - 1] != "["
            or old_t[index + 1 : index + 2] != ["]"]
        ):
            return False
        if int(b) - int(a) != shift:
            return False
    return True


def _spelling_pair_drifts(pair: tuple[str, str], *, declared: Iterable[str]) -> bool:
    return is_spelling_only_drift(pair[0], pair[1], declared)


def _generations_differ(generations: tuple[int, int]) -> bool:
    return generations[0] != generations[1]


#: The spelling check is a lowering-only name heuristic: the operands'
#: differing extractor generations are the structural fact that confirms it.
GENERATION_SPELLING_DRIFT = register_name_heuristic(
    "generation_spelling_drift",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    lowers_from=tuple(sorted(k.name for k in _SPELLING_KINDS)),
    description=(
        "a type spelling that differs only by renaming non-builtin, "
        "undeclared identifiers between operands of different extractor "
        "generations is extractor drift, not a type change"
    ),
    matcher=_spelling_pair_drifts,
    confirmed_by=StructuralFact(
        "policy.generation_drift_demotion.generations_differ", _generations_differ
    ),
    helpers=(is_spelling_only_drift, _declared_names),
    vocabularies=("_BUILTINS",),
    patterns=(_IDENT, _TOKEN),
)


def demote_generation_spelling_drift(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_generation: int,
    new_generation: int,
    *,
    extent_shift: int = 0,
) -> list[Change]:
    """Annotate each spelling-only finding between operands of different
    extractor generations as ``COMPATIBLE_WITH_RISK``. Mutates and returns
    *changes*; a no-op when the generations match. *extent_shift* is the
    array-extent respelling the generations in between declared."""
    if old_generation == new_generation:
        return changes
    declared: frozenset[str] | None = None
    for change in changes:
        if (
            change.effective_verdict is not None
            or change.kind not in _SPELLING_KINDS
            or not isinstance(change.old_value, str)
            or not isinstance(change.new_value, str)
        ):
            continue
        if declared is None:
            declared = _declared_names(old)
        if not is_extent_shift_drift(
            change.old_value, change.new_value, extent_shift
        ) and not GENERATION_SPELLING_DRIFT.confirmed(
            (change.old_value, change.new_value),
            (old_generation, new_generation),
            declared=declared,
        ):
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = GENERATION_DRIFT_RULE
        change.modulation_reason = (
            f"{GENERATION_DRIFT_REASON} (old operand extractor generation "
            f"{old_generation or 'unstated'}, new {new_generation or 'unstated'}; "
            "no SPELLING_MIGRATIONS rule covers this spelling)"
        )
    return changes
