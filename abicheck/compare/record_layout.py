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

"""The record-layout detector family, reading through ``SemanticIRIndex``
(ADR-063 6B, cohort 3: ``TYPE_SIZE_CHANGED``/``TYPE_ALIGNMENT_CHANGED``).

**Why this cohort.** Layout is the first record fact the checker decides
findings on that the IR now carries itself (``CanonicalEntity.size_bits``/
``alignment_bits``, snapshot schema v53). Before it, "records carry layout
facts the IR does not model" was the standing reason records stayed on the
flat ``RecordType`` path.

**Authority, per side** (the T3 rule, not a fidelity gate): a side whose
``SemanticIR`` carries record occurrences is read from that IR alone; a side
with none is read from the legacy adapter's projection of its own records.
Nothing adjudicates between the two. The one supplement is a record with no
``entity_id`` at all: no producer could give it an IR occurrence, so it is
projected through the adapter under a synthetic identity that cannot collide
with a real one. Layout the IR lacks because it was established later (DWARF
backfill of a clang record) or never recorded (a pre-v53 document) is filled
at the snapshot boundary (``model/semantic_ir_record_layout.py``), not here.

**This module may not read a record's layout off ``RecordType``**, nor a
snapshot's ``types``: ``scripts/semantic_ir_cutover.py`` forbids
``size_bits``/``alignment_bits``/``types`` reads here. Pairing (which old
record matches which new one) stays with the caller, whose ``TypeMap``
already owns it; this module only answers "what layout does each side's IR
record for that identity".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityKind
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_record_occurrences,
    semantic_ir_covers_kind,
)
from ..model.semantic_ir_record_layout import entity_layout
from .declined_comparisons import record_declined

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.entities import RecordType

__all__ = ["RecordLayoutIndex", "record_layout_changes", "record_layout_index"]


@dataclass(frozen=True)
class RecordLayoutIndex:
    """One side's layout view: its ``SemanticIR`` index, plus the adapter
    occurrence of each record the IR cannot name (keyed by the record
    object, since several such records may share one spelling)."""

    index: SemanticIRIndex
    ir: SemanticIR
    by_record: dict[int, OccurrenceId] = field(default_factory=dict)

    def entity_for(self, record: RecordType) -> CanonicalEntity | None:
        occ_id = self.by_record.get(id(record))
        if occ_id is not None:
            return self.ir.occurrences.get(occ_id)
        if record.entity_id is None:
            return None
        return self.index.entity(record.entity_id)


def record_layout_index(
    semantic_ir: SemanticIR | None, records: Iterable[RecordType]
) -> RecordLayoutIndex:
    """One side's layout index -- see the module docstring for the rule.

    *records* is that side's own record collection, used only for the
    adapter projection (a side with no record occurrences) and for records
    no producer gave an identity. The caller must pass the same record
    objects it later pairs: the adapter half is keyed by object.
    """
    records = list(records)
    if semantic_ir is not None and semantic_ir_covers_kind(
        semantic_ir, EntityKind.TYPE
    ):
        projected = [r for r in records if r.entity_id is None]
        supplement, order = legacy_record_occurrences(projected)
        ir = (
            SemanticIR(
                occurrences={**semantic_ir.occurrences, **supplement.occurrences}
            )
            if projected
            else semantic_ir
        )
    else:
        projected = records
        ir, order = legacy_record_occurrences(projected)
    return RecordLayoutIndex(
        index=SemanticIRIndex(ir),
        ir=ir,
        by_record={id(r): occ for r, occ in zip(projected, order)},
    )


def record_layout_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_index: RecordLayoutIndex,
    new_index: RecordLayoutIndex,
) -> None:
    """Append ``TYPE_SIZE_CHANGED``/``TYPE_ALIGNMENT_CHANGED`` for one
    matched record pair, from each side's index.

    A value is compared only when both sides establish it; when exactly one
    does, the comparison is recorded as declined (T9 accounting) rather than
    passed silently.
    """
    old_size, old_align = entity_layout(old_index.entity_for(t_old))
    new_size, new_align = entity_layout(new_index.entity_for(t_new))
    # This caller knows the matched pair directly, so it can stamp real
    # identity even when record_canonical_names' bare-name bridge can't (an
    # unrelated `a::Widget`/`b::Widget` collision elsewhere -- Codex review).
    qualified = t_new.qualified_name or t_old.qualified_name
    entity_id = t_old.entity_id or t_new.entity_id
    for kind, label, old_fact, new_fact in (
        (ChangeKind.TYPE_SIZE_CHANGED, "size", old_size, new_size),
        (ChangeKind.TYPE_ALIGNMENT_CHANGED, "alignment", old_align, new_align),
    ):
        if not (old_fact.is_present and new_fact.is_present):
            if old_fact.is_present or new_fact.is_present:
                record_declined(
                    qualified or name,
                    f"record {label} established on one side only "
                    f"({old_fact.status.value} / {new_fact.status.value})",
                )
            continue
        if old_fact.value == new_fact.value:
            continue
        changes.append(
            make_change(
                kind,
                symbol=name,
                name=name,
                old=str(old_fact.value),
                new=str(new_fact.value),
                qualified_name=qualified,
                entity_id=entity_id,
            )
        )
