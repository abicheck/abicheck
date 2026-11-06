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

"""Assurance gap for stored sections a load could not use (ADR-062 D14/D15).

``AbiSnapshot.unreadable_sections`` names each section that carries a
must-understand extension this build does not understand, is written at a
newer section version, failed at extraction, or was not collected. The
detectors reading it are not comparable (``compare/section_readers.py``); a
change only that section shows is not assessed, so the run reads ``partial``
and names the section rather than reading as a clean comparison.

``AbiSnapshot.section_availability`` holds the same sections with their
status (`model/section_availability.py`), plus ``unrecorded`` ones: absent
from a stored document that predates the availability table. Those read as
before the table existed, so they are noted and listed but never lower the
status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...model.section_availability import SectionStatus

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding, gap_and_notes

__all__ = [
    "UNREADABLE_SECTION_DIMENSION",
    "section_availability_entries",
    "unreadable_section_findings",
    "unreadable_section_gap",
]

UNREADABLE_SECTION_DIMENSION = "stored sections a load could not use"


def unreadable_section_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """Each section a side could not use (status-lowering), then each
    ``unrecorded`` section (informational: never lowers status)."""
    found: dict[tuple[Side, str], str] = {}
    unrecorded: list[UnobservedFinding] = []
    sides: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    for side, snap in sides:
        if snap is None:
            continue
        for section, diagnostic in getattr(snap, "unreadable_sections", ()):
            found.setdefault((side, section), diagnostic)
        unrecorded.extend(
            UnobservedFinding(
                UNREADABLE_SECTION_DIMENSION,
                side,
                f"section {entry.section!r} of the {side} snapshot has no recorded "
                "availability and is absent from the stored document; read as its "
                "defaults (a document from before the section availability table)",
                lowers_status=False,
            )
            for entry in getattr(snap, "section_availability", ())
            if entry.status is SectionStatus.UNRECORDED
        )
    return [
        UnobservedFinding(
            UNREADABLE_SECTION_DIMENSION,
            side,
            f"not comparable ({side} snapshot): {diagnostic}; every "
            "analysis reading that section is not evaluated",
        )
        for (side, _section), diagnostic in found.items()
    ] + unrecorded


def unreadable_section_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)``: whether either side holds a section it could not use."""
    return gap_and_notes(unreadable_section_findings(old, new))


def section_availability_entries(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[dict[str, Any], ...]:
    """Every section that is not ``present`` on either side, as the report's
    ``analysis_assurance.section_availability`` entries."""
    return tuple(
        {"side": side, **entry.to_dict()}
        for side, snap in (("old", old), ("new", new))
        if snap is not None
        for entry in getattr(snap, "section_availability", ())
    )
