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

__all__ = ["depth_projection_gap"]


def depth_projection_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)``: whether either side's projection lost evidence."""
    sides = [s for s in (old, new) if s is not None]
    # dict.fromkeys: first-seen order, de-duplicated.
    gaps = list(
        dict.fromkeys(
            item
            for snap in sides
            for item in getattr(snap, "projection_evidence_gaps", ())
        )
    )
    if not gaps:
        return False, []
    records = [s.projection_unavailable for s in sides]
    depth = next((r[0] for r in records if r is not None), "the requested depth")
    return True, [
        f"not assessed at depth {depth}: {item}; a change only this evidence "
        "shows is not reported"
        for item in gaps
    ]
