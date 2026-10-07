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

"""Function lifecycle detectors reading through ``SemanticIRIndex`` (ADR-063
6B, function-lifecycle cohort: ``FUNC_BECAME_INLINE``/``FUNC_LOST_INLINE``,
``FUNC_DELETED``/``FUNC_DELETED_DWARF``, and the per-declaration half of
``CTOR_OVERLOAD_AMBIGUITY_RISK``).

Each answer is read off a function occurrence's ``is_inline``/
``is_deleted``/``deleted_from_dwarf``/``is_explicit``/``access``/
``parameter_type_spellings``/``parameter_defaults`` facts
(``model/semantic_ir_function_signature.py``). Export-table evidence,
surface visibility and class grouping are not declaration facts and stay
with the caller in ``diff_symbols.py``.

**This module may not read those values off a ``Function``**:
``scripts/semantic_ir_cutover.py`` forbids ``is_inline``/``is_deleted``/
``deleted_from_dwarf``/``params``/``functions``/``function_map`` reads here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.identity import EntityId
    from ..model.semantic_ir import CanonicalEntity

__all__ = [
    "converting_ctor_signature",
    "deletion_kind",
    "inline_changes",
    "is_deleted",
]


def _value(entity: CanonicalEntity | None, name: str) -> Any:
    if entity is None:
        return None
    fact = getattr(entity, name)
    return fact.value if fact.is_present else None


def is_deleted(entity: CanonicalEntity | None) -> bool | None:
    """Whether *entity* is ``= delete``'d; ``None`` when not established."""
    value = _value(entity, "is_deleted")
    return value if isinstance(value, bool) else None


def deletion_kind(
    old: CanonicalEntity | None, new: CanonicalEntity | None
) -> ChangeKind | None:
    """``FUNC_DELETED``/``FUNC_DELETED_DWARF`` when *new* is deleted and
    *old* established that it was not; ``None`` otherwise."""
    if is_deleted(new) is not True or is_deleted(old) is not False:
        return None
    if _value(new, "deleted_from_dwarf"):
        return ChangeKind.FUNC_DELETED_DWARF
    return ChangeKind.FUNC_DELETED


def inline_changes(
    mangled: str,
    name: str,
    old: CanonicalEntity | None,
    new: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    still_exported: bool,
) -> list[Change]:
    """``FUNC_BECAME_INLINE``/``FUNC_LOST_INLINE``; *still_exported* is the
    caller's export-table answer for the new side."""
    o, n = _value(old, "is_inline"), _value(new, "is_inline")
    if o is None or n is None or o == n:
        return []
    if n:
        return [
            make_change(
                ChangeKind.FUNC_BECAME_INLINE,
                symbol=mangled,
                description=(
                    f"Function became inline, symbol still exported: {name}"
                    if still_exported
                    else f"Function became inline (symbol may be removed from DSO): {name}"
                ),
                old_value="non-inline",
                new_value="inline",
                entity_id=entity_id,
            )
        ]
    return [
        make_change(
            ChangeKind.FUNC_LOST_INLINE,
            symbol=mangled,
            name=name,
            old="inline",
            new="non-inline",
            entity_id=entity_id,
        )
    ]


#: Word-boundary-anchored so a class whose own name merely *contains*
#: "const"/"volatile" (``myconst``) is not corrupted by the strip (Codex
#: review).
_CV_QUALIFIER_RE = re.compile(r"\b(?:const|volatile)\b")


def converting_ctor_signature(
    name: str, entity: CanonicalEntity | None
) -> tuple[str, ...] | None:
    """The parameter-type key of *entity* when it is a converting
    constructor of a class named *name*, else ``None``.

    "Converting constructor": public, not deleted, definitively
    non-explicit (``is_explicit`` established ``False``; unknown is
    skipped), callable with exactly one argument, and not a copy/move
    constructor (its first parameter's type, stripped of cv and ``&``, is
    not the class itself)."""
    if is_deleted(entity) is not False or _value(entity, "is_explicit") is not False:
        return None
    if _value(entity, "access") != "public":
        return None
    types = _value(entity, "parameter_type_spellings")
    defaults = _value(entity, "parameter_defaults")
    if not types or defaults is None or len(defaults) != len(types):
        return None
    if sum(1 for d in defaults if d is None) > 1:
        return None
    arg_type = " ".join(_CV_QUALIFIER_RE.sub("", types[0]).replace("&", "").split())
    if arg_type == name:
        return None
    return tuple(types)
