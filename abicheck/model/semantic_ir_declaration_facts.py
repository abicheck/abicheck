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

"""Function and variable declaration facts on their ``SemanticIR``
occurrences (ADR-063 6B, declaration-fact cohort).

``FUNC_DEPRECATED_*``/``VAR_DEPRECATED_*``, ``METHOD_ACCESS_CHANGED``,
``VAR_ACCESS_*`` and ``VAR_ALIGNMENT_CHANGED`` read
``CanonicalEntity.deprecated``/``access``/``declared_alignment_bits``. This
module is the one formula from a parsed ``Function``/``Variable`` to those
facts, and the snapshot-boundary fill for variables (functions are filled by
``semantic_ir_function_signature``, which folds these in).

**Statuses are copied, not invented.** Each declaration already carries a
``Fact`` for the value (``deprecated_fact``, ``access_fact``,
``alignment_bits_fact``) whose status says whether its producer established
it. The IR fact keeps that status, so the migrated detectors gate on exactly
what the legacy ``fact_gate``/``compare_facts`` gates read. Only the value is
re-spelled to fit a ``CanonicalEntity`` fact, which may not carry ``None``
while present: "confirmed not deprecated" is ``()`` and a deprecation is
``(message,)`` -- never ``""``, which is a real bare ``[[deprecated]]``'s
message -- and an ``AccessLevel`` is its ``.value``. A ``Function``'s access has no ``Fact`` sibling and is always
recorded, so it is ``PRESENT``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from .availability import FactStatus
from .declarations import Function, Variable
from .fact import Fact
from .identity import EntityId, EntityKind
from .occurrence import OccurrenceId
from .semantic_ir import CanonicalEntity, SemanticIR

__all__ = [
    "DECLARATION_FIELDS",
    "declaration_facts",
    "sync_snapshot_variable_facts",
    "with_variable_facts",
]

#: The ``CanonicalEntity`` fields this cohort owns.
DECLARATION_FIELDS = ("deprecated", "access", "declared_alignment_bits")


def _access_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def declaration_facts(decl: Function | Variable) -> dict[str, Any]:
    """The :data:`DECLARATION_FIELDS` facts for *decl*."""

    def _respell(fact: Fact[Any] | None, spell: Any) -> Fact[Any]:
        """*fact* with its value re-spelled by *spell*, status and diagnostics
        kept; a missing fact is ``NOT_COLLECTED``."""
        if fact is None:
            return Fact.not_collected()
        if not fact.is_present:
            return Fact(status=fact.status, value=None, diagnostics=fact.diagnostics)
        value = spell(fact.value)
        if value is None:
            return Fact.not_collected()
        return Fact(status=fact.status, value=value, diagnostics=fact.diagnostics)

    if isinstance(decl, Function):
        access: Fact[Any] = Fact.present(_access_value(decl.access))
        alignment: Fact[Any] = Fact.not_collected()
    else:
        access = _respell(decl.access_fact, _access_value)
        alignment = _respell(decl.alignment_bits_fact, lambda v: v)
    return {
        "deprecated": _respell(
            decl.deprecated_fact, lambda v: () if v is None else (v,)
        ),
        "access": access,
        "declared_alignment_bits": alignment,
    }


def with_variable_facts(ir: SemanticIR, variables: Iterable[Variable]) -> SemanticIR:
    """*ir* with every variable occurrence's missing declaration facts filled
    from *variables*; *ir* itself when nothing changes. Same rules as the
    function fill: only ``NOT_COLLECTED`` is filled, and two variables under
    one identity with different facts fill nothing."""
    by_id: dict[EntityId, dict[str, Any] | None] = {}
    for var in variables:
        eid = var.entity_id
        if eid is None or eid.kind is not EntityKind.VARIABLE:
            continue
        facts = declaration_facts(var)
        if eid in by_id and by_id[eid] != facts:
            by_id[eid] = None
        else:
            by_id.setdefault(eid, facts)
    if not by_id:
        return ir
    changed: dict[OccurrenceId, CanonicalEntity] = {}
    for occ_id, entity in ir.occurrences.items():
        known = by_id.get(occ_id.entity_id)
        if known is None:
            continue
        updates = {
            name: value
            for name, value in known.items()
            if getattr(entity, name).status is FactStatus.NOT_COLLECTED
            and value.status is not FactStatus.NOT_COLLECTED
        }
        if updates:
            changed[occ_id] = dataclasses.replace(entity, **updates)
    if not changed:
        return ir
    return dataclasses.replace(ir, occurrences={**ir.occurrences, **changed})


def sync_snapshot_variable_facts(snapshot: object) -> None:
    """Fill *snapshot*'s ``semantic_ir`` variable declaration facts from its
    own variables (see ``sync_snapshot_record_layout`` for where and why)."""
    ir = getattr(snapshot, "semantic_ir", None)
    if ir is None:
        return
    synced = with_variable_facts(ir, snapshot.declarations.variables)  # type: ignore[attr-defined]
    if synced is not ir:
        snapshot.semantic_ir = synced  # type: ignore[attr-defined]
