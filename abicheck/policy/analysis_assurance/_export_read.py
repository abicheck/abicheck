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

"""Assurance gap for an export table recorded as not completely read (ADR-079).

Each platform block records ``export_read`` (``model/export_read.py``). A
side whose block records ``partial`` or ``failed`` was compared against a
table that does not hold every export: a removal it cannot establish is
demoted (``policy/partial_export_read.py``) and its absences are not
observations. The run reads ``partial`` and names the side and platform,
never ``complete``. ``unrecorded`` (a document without the fact) is reported as-is and
is not a gap by itself: the pre-ADR-079 rule still reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...model.export_read import ExportReadState

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding

__all__ = [
    "EXPORT_READ_DIMENSION",
    "EXPORT_READ_NOTE_PREFIX",
    "export_read_findings",
    "export_read_gap",
    "export_read_notes",
    "recorded_export_reads",
]

_GAP_STATES = frozenset({ExportReadState.PARTIAL, ExportReadState.FAILED})
#: How every export-read gap note starts, so a consumer can pick the notes the
#: rollup already built out of ``AnalysisAssurance.notes`` instead of
#: rebuilding them.
EXPORT_READ_NOTE_PREFIX = "export table not completely read ("
EXPORT_READ_DIMENSION = "export table completeness (ADR-079)"


def recorded_export_reads(snap: AbiSnapshot | None) -> dict[str, str]:
    """``{platform: recorded export_read}`` for each block *snap* carries."""
    if snap is None:
        return {}
    out: dict[str, str] = {}
    for platform in ("elf", "pe", "macho"):
        meta = getattr(snap, platform, None)
        if meta is not None:
            state = getattr(meta, "export_read", ExportReadState.UNRECORDED)
            out[platform] = ExportReadState(state).value
    return out


def export_read_notes(states: Mapping[str, Mapping[str, str]]) -> list[str]:
    """One note per side/platform whose recorded read is ``partial`` or
    ``failed`` -- the assurance note, and the Markdown coverage-gap row."""
    return [
        f"{EXPORT_READ_NOTE_PREFIX}{side} snapshot, {platform}: {state}); "
        "a symbol absent from it is not established as absent, and a removal "
        "resting on that absence is not scored as a break"
        for side, by_platform in states.items()
        for platform, state in sorted(by_platform.items())
        if ExportReadState(state) in _GAP_STATES
    ]


def export_read_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per side/platform whose recorded read is a gap."""
    sides: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    return [
        UnobservedFinding(EXPORT_READ_DIMENSION, side, note)
        for side, snap in sides
        for note in export_read_notes({side: recorded_export_reads(snap)})
    ]


def export_read_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str], dict[str, dict[str, str]]]:
    """``(gap, notes, states)``: whether either side's export table is
    recorded as partially read or failed, a note per such table, and every
    side's recorded states (the report's ``analysis_assurance.export_read``)."""
    states = {"old": recorded_export_reads(old), "new": recorded_export_reads(new)}
    notes = export_read_notes(states)
    return bool(notes), notes, states
