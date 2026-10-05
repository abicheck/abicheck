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

"""The parameter-level detector family, reading through ``SemanticIRIndex``
(ADR-063 6B, parameter cohort: ``PARAM_DEFAULT_VALUE_*``, ``PARAM_RENAMED``,
``PARAM_POINTER_LEVEL_CHANGED``/``RETURN_POINTER_LEVEL_CHANGED``,
``PARAM_RESTRICT_CHANGED``, ``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST``,
``FUNC_OVERRIDE_SPECIFIER_*``).

Each finding is decided from the per-parameter facts a function occurrence
carries (``CanonicalEntity.parameter_names``/``parameter_defaults``/
``parameter_type_spellings``/``parameter_pointer_depths``/
``parameter_restrict``/``parameter_va_list``/``return_pointer_depth``/
``is_override``, filled by ``model/semantic_ir_function_signature.py``).

**What stays with the caller.** Evidence-tier and producer gates that are
facts about the *snapshot*, not about a declaration -- "both sides
confirmed header-aware", "same default-value producer"
(``fact_provenance.fact_producer``), "both override facts backed by a known
producer", the fingerprint-reliability check -- remain in
``diff_symbols.py``, which passes their per-pair answer in. They read
snapshot metadata, which this migration does not move.

**This module may not read a parameter off ``Function``/``Param``**:
``scripts/semantic_ir_cutover.py`` forbids ``params``/``default``/
``pointer_depth``/``return_pointer_depth``/``is_restrict_fact``/
``is_va_list_fact`` reads here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.type_indirection import unresolved_pair_verdict

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model.identity import EntityId
    from ..model.semantic_ir import CanonicalEntity

__all__ = [
    "ParameterView",
    "override_changes",
    "parameter_default_changes",
    "parameter_rename_changes",
    "parameter_view",
    "pointer_level_changes",
    "restrict_changes",
    "va_list_changes",
]


def _depth_comparable(old: str | None, new: str | None) -> bool:
    """Whether two spellings' recorded pointer depths may be compared.

    A wholly unknown spelling never is. A partially unresolved one
    (``"?*"``) records only a lower bound, so it is compared only when the
    resolved structure already proves the depths differ
    (``model.type_indirection``) -- ``int`` -> ``"?*"`` is reported,
    ``int **`` -> ``"?*"`` is not (the ``?`` may be ``int *``)."""
    verdict = unresolved_pair_verdict(old, new, pointer_depth=True)
    return True if verdict is None else verdict


class ParameterView:
    """One side's per-parameter facts, aligned by position; an attribute is
    ``None`` when its fact is not established."""

    __slots__ = (
        "names",
        "types",
        "defaults",
        "depths",
        "restrict",
        "va_list",
        "return_type",
        "return_depth",
        "is_override",
    )

    def __init__(self, entity: CanonicalEntity | None) -> None:
        def value(name: str) -> Any:
            if entity is None:
                return None
            fact = getattr(entity, name)
            return fact.value if fact.is_present else None

        self.names: tuple[str, ...] | None = value("parameter_names")
        self.types: tuple[str, ...] | None = value("parameter_type_spellings")
        self.defaults: tuple[str | None, ...] | None = value("parameter_defaults")
        self.depths: tuple[int, ...] | None = value("parameter_pointer_depths")
        self.restrict: tuple[str, ...] | None = value("parameter_restrict")
        self.va_list: tuple[str, ...] | None = value("parameter_va_list")
        self.return_type: str | None = value("return_type_spelling")
        self.return_depth: int | None = value("return_pointer_depth")
        self.is_override: bool | None = value("is_override")

    def label(self, i: int) -> str:
        """``detail`` for parameter *i*: its name, or its position."""
        name = self.names[i] if self.names is not None and i < len(self.names) else ""
        return str(name or i)


def parameter_view(entity: CanonicalEntity | None) -> ParameterView:
    return ParameterView(entity)


def _count(*seqs: tuple[Any, ...] | None) -> int:
    """How many positions every given sequence covers (``zip`` semantics)."""
    if any(s is None for s in seqs):
        return 0
    return min(len(s) for s in seqs if s is not None)


def parameter_default_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
    value_comparison_unreliable: Callable[[str, str], bool],
) -> list[Change]:
    """``PARAM_DEFAULT_VALUE_REMOVED``/``PARAM_DEFAULT_VALUE_CHANGED``.

    *value_comparison_unreliable* answers the caller's snapshot-level
    fingerprint-reliability check for one ``(old, new)`` value pair."""
    changes: list[Change] = []
    for i in range(_count(old.defaults, new.defaults)):
        assert old.defaults is not None and new.defaults is not None
        d_old, d_new = old.defaults[i], new.defaults[i]
        if d_old is not None and d_new is None:
            changes.append(
                make_change(
                    ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
                    symbol=mangled,
                    name=name,
                    detail=old.label(i),
                    old_value=d_old,
                    new_value=None,
                    entity_id=entity_id,
                )
            )
        elif d_old is not None and d_new is not None and d_old != d_new:
            if value_comparison_unreliable(d_old, d_new):
                continue
            changes.append(
                make_change(
                    ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                    symbol=mangled,
                    name=name,
                    detail=old.label(i),
                    old_value=d_old,
                    new_value=d_new,
                    entity_id=entity_id,
                )
            )
    return changes


def parameter_rename_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``PARAM_RENAMED``: same position, same spelling, a different
    (non-empty) name on both sides."""
    changes: list[Change] = []
    for i in range(_count(old.types, new.types, old.names, new.names)):
        assert old.types and new.types and old.names and new.names
        n_old, n_new = old.names[i], new.names[i]
        if old.types[i] == new.types[i] and n_old and n_new and n_old != n_new:
            changes.append(
                make_change(
                    ChangeKind.PARAM_RENAMED,
                    symbol=mangled,
                    name=name,
                    detail=str(i),
                    old=n_old,
                    new=n_new,
                    entity_id=entity_id,
                )
            )
    return changes


def pointer_level_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
    params_unconfirmed: bool,
) -> list[Change]:
    """``RETURN_POINTER_LEVEL_CHANGED`` and ``PARAM_POINTER_LEVEL_CHANGED``.

    RD2-5: an unresolved (``"?"``) return or parameter spelling falls back to
    depth 0 and would read as a phantom change, so it is skipped; a
    partially unresolved one (``"?*"``) is compared only where its resolved
    structure proves the depth changed (:func:`_depth_comparable`); parameter
    depths from a stripped symbols-only side are skipped altogether."""
    changes: list[Change] = []
    rd_old, rd_new = old.return_depth, new.return_depth
    if (
        _depth_comparable(old.return_type, new.return_type)
        and rd_old is not None
        and rd_new is not None
        and rd_old != rd_new
        and (rd_old > 0 or rd_new > 0)
    ):
        changes.append(
            make_change(
                ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
                symbol=mangled,
                name=name,
                old=str(rd_old),
                new=str(rd_new),
                entity_id=entity_id,
            )
        )
    if params_unconfirmed:
        return changes
    for i in range(_count(old.types, new.types, old.depths, new.depths)):
        assert old.types and new.types and old.depths and new.depths
        if not _depth_comparable(old.types[i], new.types[i]):
            continue
        d_old, d_new = old.depths[i], new.depths[i]
        if d_old != d_new and (d_old > 0 or d_new > 0):
            changes.append(
                make_change(
                    ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
                    symbol=mangled,
                    name=name,
                    detail=old.label(i),
                    old=str(d_old),
                    new=str(d_new),
                    entity_id=entity_id,
                )
            )
    return changes


def restrict_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``PARAM_RESTRICT_CHANGED``, compared only where both producers
    established the parameter's flag."""
    changes: list[Change] = []
    for i in range(_count(old.restrict, new.restrict)):
        assert old.restrict is not None and new.restrict is not None
        r_old, r_new = old.restrict[i], new.restrict[i]
        if not r_old or not r_new or r_old == r_new:
            continue
        old_is, new_is = r_old == "true", r_new == "true"
        changes.append(
            make_change(
                ChangeKind.PARAM_RESTRICT_CHANGED,
                symbol=mangled,
                name=name,
                detail="added" if new_is else "removed",
                old=old.label(i),
                old_value=f"restrict={old_is}",
                new_value=f"restrict={new_is}",
                entity_id=entity_id,
            )
        )
    return changes


def va_list_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST``, compared only where
    both producers established the parameter's flag."""
    changes: list[Change] = []
    for i in range(_count(old.va_list, new.va_list)):
        assert old.va_list is not None and new.va_list is not None
        v_old, v_new = old.va_list[i], new.va_list[i]
        if not v_old or not v_new or v_old == v_new:
            continue
        t_old = old.types[i] if old.types and i < len(old.types) else None
        t_new = new.types[i] if new.types and i < len(new.types) else None
        ov: str | None
        nv: str | None
        if v_new == "true":
            kind, ov, nv = ChangeKind.PARAM_BECAME_VA_LIST, t_old, "va_list"
        else:
            kind, ov, nv = ChangeKind.PARAM_LOST_VA_LIST, "va_list", t_new
        changes.append(
            make_change(
                kind,
                symbol=mangled,
                name=name,
                detail=old.label(i),
                old_value=ov,
                new_value=nv,
                entity_id=entity_id,
            )
        )
    return changes


def override_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``FUNC_OVERRIDE_SPECIFIER_ADDED``/``_REMOVED`` when both sides
    recorded the specifier (the caller has already applied the producer
    gate)."""
    o, n = old.is_override, new.is_override
    if o is None or n is None or o == n:
        return []
    added = bool(n)
    return [
        make_change(
            ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED
            if added
            else ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED,
            symbol=mangled,
            name=name,
            old_value="no override" if added else "override",
            new_value="override" if added else "no override",
            entity_id=entity_id,
        )
    ]
