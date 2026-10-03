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

"""A record's layout facts on its ``SemanticIR`` occurrences (ADR-063 6B).

The record-layout cohort moves ``TYPE_SIZE_CHANGED``/``TYPE_ALIGNMENT_CHANGED``
onto ``CanonicalEntity.size_bits``/``alignment_bits``. Those facts start life
where every other IR fact does, in the normalizer, but a record's layout can
be established *after* normalization: the direct-clang backend leaves
``size_bits`` unset and ``dumper_layout_backfill`` fills it from DWARF. And a
document written before the two fields existed carries none. Both are the
same operation -- fill an occurrence's missing layout from the ``RecordType``
the snapshot holds for that identity -- so it lives here once, in ``model``,
where the normalizer (``extract``), the dumper and the load path
(``storage``) can all reach it.

Only a *missing* fact is filled, never an established one overwritten: the
IR is the authority for what it recorded. And a fill needs an unambiguous
source -- two ``RecordType`` objects sharing one ``EntityId`` with different
layouts fill nothing, since picking either would invent which one the IR
meant.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from .availability import FactStatus
from .entities import RecordType
from .fact import Fact
from .identity import EntityId, EntityKind
from .occurrence import OccurrenceId
from .semantic_ir import CanonicalEntity, SemanticIR

__all__ = [
    "LEGACY_LAYOUT_DIAGNOSTIC",
    "layout_fact",
    "entity_layout",
    "record_layout_facts",
    "sync_snapshot_record_layout",
    "with_record_layout",
]

#: Stamped on a layout fact decoded from a pre-layout (v1) ``SemanticIR``
#: document. It marks "this document never recorded layout" -- distinct from a
#: producer that ran and established none -- and survives re-encoding, so a
#: document that passes through another container (a ``ProjectSnapshot``
#: section) still reaches :func:`with_record_layout` carrying it.
LEGACY_LAYOUT_DIAGNOSTIC = "layout not recorded before semantic_ir document version 2"

_LAYOUT_FIELDS = ("size_bits", "alignment_bits")


def layout_fact(value: int | None) -> Fact[int]:
    """``PRESENT`` for an established value, ``NOT_COLLECTED`` for ``None``."""
    if value is None:
        return Fact.not_collected()
    return Fact.present(value)


def record_layout_facts(record: RecordType) -> tuple[Fact[int], Fact[int]]:
    """``(size_bits, alignment_bits)`` facts for one record."""
    return layout_fact(record.size_bits), layout_fact(record.alignment_bits)


def _fillable(fact: Fact[int]) -> bool:
    # Only "never collected": a FAILED/UNSUPPORTED fact is a producer's own
    # statement about this record and is not overridden by another source.
    return fact.status is FactStatus.NOT_COLLECTED


def with_record_layout(ir: SemanticIR, records: Iterable[RecordType]) -> SemanticIR:
    """*ir* with every record occurrence's missing layout filled from *records*.

    A record contributes only through its own ``entity_id``; a record without
    one has no occurrence to fill. Returns *ir* itself when nothing changes.
    """
    by_id: dict[EntityId, tuple[int | None, int | None] | None] = {}
    for record in records:
        eid = record.entity_id
        if eid is None or eid.kind is not EntityKind.TYPE:
            continue
        layout = (record.size_bits, record.alignment_bits)
        if eid in by_id and by_id[eid] != layout:
            by_id[eid] = None  # two different layouts under one identity
        else:
            by_id.setdefault(eid, layout)
    if not by_id:
        return ir
    changed: dict[OccurrenceId, CanonicalEntity] = {}
    for occ_id, entity in ir.occurrences.items():
        known = by_id.get(occ_id.entity_id)
        if known is None:
            continue
        size, align = (
            layout_fact(value) if _fillable(current) and value is not None else current
            for value, current in zip(known, (entity.size_bits, entity.alignment_bits))
        )
        if size is not entity.size_bits or align is not entity.alignment_bits:
            changed[occ_id] = dataclasses.replace(
                entity, size_bits=size, alignment_bits=align
            )
    if not changed:
        return ir
    return dataclasses.replace(ir, occurrences={**ir.occurrences, **changed})


def entity_layout(entity: CanonicalEntity | None) -> tuple[Fact[int], Fact[int]]:
    """``(size_bits, alignment_bits)`` of *entity*, ``NOT_COLLECTED`` for none."""
    if entity is None:
        return Fact.not_collected(), Fact.not_collected()
    return entity.size_bits, entity.alignment_bits


def sync_snapshot_record_layout(snapshot: object) -> None:
    """Fill *snapshot*'s ``semantic_ir`` record layout from its own ``types``.

    Called at the two places a snapshot's IR and records meet --
    ``AbiSnapshot.__post_init__`` and the storage decode, which assigns
    ``semantic_ir`` after construction -- so a pre-layout document, a
    DWARF-backfilled clang dump and a hand-built snapshot all reach the
    checker with the same facts. Typed ``object`` so ``model/snapshot.py``
    can call it without an import cycle.
    """
    ir = getattr(snapshot, "semantic_ir", None)
    if ir is None:
        return
    synced = with_record_layout(ir, snapshot.declarations.types)  # type: ignore[attr-defined]
    if synced is not ir:
        snapshot.semantic_ir = synced  # type: ignore[attr-defined]
