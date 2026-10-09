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
from ..model.execution_cache import memoized
from ..model.type_indirection import unresolved_pair_verdict
from ..name_classification import canonicalize_type_name, cv_qualifiers_only_differ

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.identity import EntityId
    from ..model.semantic_ir import CanonicalEntity

__all__ = [
    "ParameterView",
    "override_changes",
    "parameter_default_changes",
    "parameter_rename_changes",
    "parameter_view",
    "pointer_level_changes",
    "pointee_qualifier_changes",
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
                # Adding restrict tightens the *caller's* obligation (no
                # overlapping arguments) for an already-compiled caller;
                # removing it only drops an optimizer assumption.
                ChangeKind.PARAM_RESTRICT_ADDED
                if new_is
                else ChangeKind.PARAM_RESTRICT_CHANGED,
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


def _qualifier_levels(spelling: str) -> tuple[frozenset[tuple[str, int]], int] | None:
    """``({(qualifier, level)}, indirection levels)`` of a canonical spelling.

    Level 0 qualifies the innermost pointee, level *k* the *k*-th pointer or
    reference; qualifiers at the top level (the parameter object itself,
    absent from the function's type) are dropped. Template arguments are
    opaque. ``None`` when the spelling has no top-level indirection.
    """
    canon = canonicalize_type_name(spelling)
    tokens = canon.replace("*", " * ").replace("&", " & ").split()
    level = 0
    depth = 0
    found: set[tuple[str, int]] = set()
    for tok in tokens:
        depth += tok.count("<") - tok.count(">")
        if depth > 0 or "<" in tok or ">" in tok:
            continue
        if tok in ("*", "&", "&&"):
            level += 1
        elif tok in ("const", "volatile"):
            found.add((tok, level))
    if level == 0:
        return None
    return frozenset(q for q in found if q[1] < level), level


def pointee_qualifier_changes(
    mangled: str,
    name: str,
    old: ParameterView,
    new: ParameterView,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``PARAM_POINTEE_QUALIFIER_ADDED``/``_CHANGED`` -- the source-level half
    of a cv change behind a parameter's pointer or reference.

    Such a change keeps the calling convention, so ``FUNC_PARAMS_CHANGED``
    deliberately ignores it (``cv_qualifiers_only_differ``). It is not
    nothing at the source level, and the two directions differ:

    * ``T *`` -> ``const T *`` (a qualifier gained by the single pointee): every
      direct call still compiles, but a consumer that stores the function in a
      ``void (*)(T *)`` pointer no longer does (C constraint violation, C++
      error) -- a risk conditional on how consumers use the entry point.
    * a qualifier lost, or gained at a deeper level (``char **`` ->
      ``const char **`` is not an implicit conversion): direct callers break.
    """
    changes: list[Change] = []
    for i in range(_count(old.types, new.types)):
        assert old.types is not None and new.types is not None
        t_old, t_new = old.types[i], new.types[i]
        delta = _pointee_qualifier_delta(t_old, t_new)
        if delta is None:
            continue
        added, removed = delta
        single_level = (_qualifier_levels(t_new) or (frozenset(), 0))[1] == 1
        widening_only = (
            not removed and single_level and all(lvl == 0 for _, lvl in added)
        )
        changes.append(
            make_change(
                ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED
                if widening_only
                else ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED,
                symbol=mangled,
                name=name,
                detail=old.label(i),
                old=t_old,
                new=t_new,
                entity_id=entity_id,
            )
        )
    return changes


@memoized(maxsize=16384)
def _pointee_qualifier_delta(
    old: str | None, new: str | None
) -> tuple[frozenset[tuple[str, int]], frozenset[tuple[str, int]]] | None:
    """``(added, removed)`` pointee qualifiers between two spellings of the
    same indirection depth that differ only in cv; ``None`` otherwise."""
    if not old or not new or old == new or not cv_qualifiers_only_differ(old, new):
        return None
    o, n = _qualifier_levels(old), _qualifier_levels(new)
    if o is None or n is None or o[1] != n[1] or o[0] == n[0]:
        return None
    return n[0] - o[0], o[0] - n[0]


def return_pointee_qualifier_changes(
    mangled: str,
    name: str,
    r_old: str | None,
    r_new: str | None,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``FUNC_RETURN_POINTEE_QUALIFIER_ADDED``/``_REMOVED`` -- the return-type
    mirror of :func:`pointee_qualifier_changes`, with the direction reversed
    (a return value flows *out* to the caller):

    * a qualifier gained at any level (``char *`` -> ``const char *``): a
      caller binding the result to a mutable pointer breaks.
    * qualifiers only lost: every direct call still converts implicitly; a
      consumer holding the function in a pointer of the old type breaks --
      a risk conditional on consumer use.
    """
    delta = _pointee_qualifier_delta(r_old, r_new)
    if delta is None:
        return []
    return [
        make_change(
            ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_ADDED
            if delta[0]
            else ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_REMOVED,
            symbol=mangled,
            name=name,
            old=r_old,
            new=r_new,
            entity_id=entity_id,
        )
    ]


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
