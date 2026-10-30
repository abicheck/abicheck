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

"""Which kept finding each deduplicated copy folded into (ADR-067 D5).

The dedup pass collapses a finding into the one it duplicates and keeps the
survivor. For a source-replay twin the pairing is name-based and only the pass
that made it knows it (:mod:`abicheck.compare.source_replay_twins`), so the
pass hands each pair to the ledger here. Acknowledgment matching then reads the
link back: a dropped twin is one observation with its survivor, and is
acknowledged through that survivor, never on its own.

The link is kept on the ledger (``DispositionLedger._dedup_survivors``) rather
than on the change, so no change field or report schema moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.change import Change
    from .disposition_ledger import DispositionLedger
    from .finding_disposition.run_state import PipelineContext

__all__ = ["deduplicate_recording_survivors", "dedup_survivor"]


def deduplicate_recording_survivors(
    changes: list[Change], ctx: PipelineContext
) -> list[Change]:
    """Run the cross-detector dedup pass and link each replay twin to its survivor.

    The returned list is exactly what :func:`deduplicate_cross_detector` returns.
    With a ledger on *ctx*, every dropped replay copy is linked to the kept
    finding that replaced it, before the pipeline records the drop as
    ``deduplicated``.
    """
    from ..compare.finding_dedup import deduplicate_cross_detector

    pairs: list[tuple[Change, Change]] = []
    kept = deduplicate_cross_detector(
        changes, ctx.baseline_or_empty, ctx.new, replay_twin_pairs=pairs
    )
    ledger = ctx.disposition_ledger
    if ledger is not None:
        for dropped, survivor in pairs:
            ledger._dedup_survivors[id(dropped)] = (dropped, survivor)  # noqa: SLF001
    return kept


def dedup_survivor(
    ledger: DispositionLedger, change: object
) -> tuple[int, object] | None:
    """The record index and object of the finding *change* was folded into.

    ``None`` when *change* was not a linked replay twin, or when its survivor has
    no record on *ledger* (a survivor replaced by a substitute object cannot be
    matched to its twin, so the twin is then its own observation).
    """
    link = ledger._dedup_survivors.get(id(change))  # noqa: SLF001
    if link is None or link[0] is not change:
        return None
    survivor = link[1]
    index = ledger._seen_ids.get(id(survivor))  # noqa: SLF001
    if index is None:
        return None
    return index, survivor
