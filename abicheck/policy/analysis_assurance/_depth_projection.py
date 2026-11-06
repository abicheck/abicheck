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

"""Assurance gap for evidence a ``--depth`` projection could not rebuild.

``policy/depth_projection.py`` records on each projected snapshot the
evidence it removed and had no stored substitute for
(``AbiSnapshot.projection_evidence_gaps``, filled by
``policy/depth_projection_gaps.py``). A change only that evidence shows is
not assessed, so the run reads ``partial`` and names it rather than reading
as a clean comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding, gap_and_notes

__all__ = [
    "DEPTH_PROJECTION_DIMENSION",
    "depth_projection_findings",
    "depth_projection_gap",
]

DEPTH_PROJECTION_DIMENSION = "evidence a --depth projection could not rebuild"


def depth_projection_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per lost evidence item, attributed to the first side that
    lost it (items are de-duplicated across sides, first-seen order)."""
    pairs: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    sides = [(label, s) for label, s in pairs if s is not None]
    first_side: dict[str, Side] = {}
    for label, snap in sides:
        for item in getattr(snap, "projection_evidence_gaps", ()):
            first_side.setdefault(item, label)
    if not first_side:
        return []
    records = [s.projection_unavailable for _, s in sides]
    depth = next((r[0] for r in records if r is not None), "the requested depth")
    return [
        UnobservedFinding(
            DEPTH_PROJECTION_DIMENSION,
            side,
            f"not assessed at depth {depth}: {item}; a change only this evidence "
            "shows is not reported",
        )
        for item, side in first_side.items()
    ]


def depth_projection_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)``: whether either side's projection lost evidence."""
    return gap_and_notes(depth_projection_findings(old, new))
