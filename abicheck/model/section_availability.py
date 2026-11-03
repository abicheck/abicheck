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

"""Section-level availability of a snapshot (ADR-062 D15).

A stored snapshot is split into sections (`binary`, `declarations`,
`types`, ...). For each one a reader must tell apart:

* ``present`` -- collected. Its payload is evidence, *including* an empty
  payload: a collected-empty section is ``present`` and its emptiness is a
  real observation.
* ``not_collected`` -- the producer never collected it. Its decoded fields
  hold defaults, not evidence, so it must never read as empty.
* ``failed`` -- the producer attempted it and the extractor failed.
* ``blocked`` -- it was stored, but this build cannot use it (an unknown
  must-understand name, or a newer ``section_schema_version``). Decided by
  the reading build, never stored.
* ``unrecorded`` -- absent from a stored sectioned document that has no
  availability entry for it (a document from before the table existed, or a
  hand-edited one). Read as today: its fields take their defaults. Reported
  so the fallback is never silent; it does not narrow the comparison.

`AbiSnapshot.section_availability` lists only the sections that are *not*
``present``; a section it does not name was collected (a live snapshot, a
flat legacy document and every section a sectioned document holds). The
storage loader and `storage.wire_extensions.record_section_availability`
fill it together with ``unreadable_sections``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["SectionAvailability", "SectionStatus"]


class SectionStatus(StrEnum):
    """The section-level availability vocabulary (D3 statuses plus the two
    reader-side states, ``blocked`` and ``unrecorded``)."""

    PRESENT = "present"
    NOT_COLLECTED = "not_collected"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNRECORDED = "unrecorded"

    @property
    def narrows(self) -> bool:
        """Whether the section's fields stand in for evidence that does not
        exist, so every analysis reading it is not comparable. ``unrecorded``
        does not: it falls back to the pre-table reading."""
        return self in (
            SectionStatus.NOT_COLLECTED,
            SectionStatus.FAILED,
            SectionStatus.BLOCKED,
        )


@dataclass(frozen=True)
class SectionAvailability:
    """One section's availability on one snapshot, with its diagnostics."""

    section: str
    status: SectionStatus
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "status": str(self.status),
            "diagnostics": list(self.diagnostics),
        }
