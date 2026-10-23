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

"""The compiled-target-ISA root cause: one ``TARGET_ISA_CHANGED`` finding when
the two builds require different instruction-set targets, and annotation of
the layout and symbol findings such a change plausibly causes.

Consequences are *annotated*, never moved out of the result: a struct-size
change caused by ``-mavx512f`` still appears, with a clause naming the root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.target_isa import (
    TargetIsa,
    parse_note_target_isa,
    parse_producer_target_isa,
    target_isa_from_sources,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["annotate_target_isa_consequences", "target_isa_of"]

#: Findings a compiled-target change plausibly causes: type layout (a wider
#: vector member moves fields and resizes records) and symbol churn (a changed
#: mangling or inlined-symbol set shows up as removed/added or renamed symbols).
_CONSEQUENCE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_LIKELY_RENAMED,
        ChangeKind.SYMBOL_RENAMED_BATCH,
    }
)


def target_isa_of(snapshot: AbiSnapshot) -> TargetIsa | None:
    """The ISA target a snapshot's build requires, read from its recorded
    producer string and ELF ISA-needed note (``None`` when neither is present)."""
    producer: str | None = None
    if snapshot.build_mode is not None:
        producer = snapshot.build_mode.provenance.raw_producer
    note_levels = (
        snapshot.elf.gnu_properties if snapshot.elf is not None else frozenset()
    )
    return target_isa_from_sources(
        parse_producer_target_isa(producer),
        parse_note_target_isa(note_levels),
    )


@registry.detector("target_isa")
def _diff_target_isa(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Report when the old and new builds require different ISA targets.

    Silent when either side's target is unknown: an absent producer and note
    is missing evidence, not a baseline to compare against. ``arch`` is
    compared only when both sides record one, so a note-only side never reads
    as a change of ``-march=`` value.
    """
    o = target_isa_of(old)
    n = target_isa_of(new)
    if o is None or n is None:
        return []
    arch_differs = o.arch is not None and n.arch is not None and o.arch != n.arch
    if o.features == n.features and not arch_differs:
        return []

    added = sorted(n.features - o.features)
    dropped = sorted(o.features - n.features)
    if added:
        direction = "raised"
        detail = (
            f"the new build requires {', '.join(added)}, which the old build did not"
        )
    elif dropped:
        direction = "lowered"
        detail = f"the new build no longer requires {', '.join(dropped)}"
    else:
        direction = "changed"
        detail = "the required feature set is the same; only the -march value differs"
    if added and dropped:
        detail += f"; it also drops {', '.join(dropped)}"
    old_desc = o.describe()
    new_desc = n.describe()
    return [
        make_change(
            ChangeKind.TARGET_ISA_CHANGED,
            symbol="<target-isa>",
            old=old_desc,
            new=new_desc,
            old_value=old_desc,
            new_value=new_desc,
            detail=f"{direction}: {detail}",
        )
    ]


def annotate_target_isa_consequences(changes: list[Change]) -> None:
    """Annotate each layout and symbol finding a ``TARGET_ISA_CHANGED`` root plausibly
    causes with a clause naming that root. Only descriptions change: no finding
    is removed, moved or re-classified."""
    roots = [c for c in changes if c.kind is ChangeKind.TARGET_ISA_CHANGED]
    if not roots:
        return
    root = roots[0]
    consequences = [
        c for c in changes if c.kind in _CONSEQUENCE_KINDS and c is not root
    ]
    if not consequences:
        return
    clause = (
        f" Possibly a consequence of the compiled target ISA change "
        f"({root.old_value} → {root.new_value}): the builds target different "
        "instruction sets, which can change layout and symbol names."
    )
    for change in consequences:
        if clause.strip() not in change.description:
            change.description += clause
