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
newer section version, or failed at extraction. The detectors reading it
are not comparable (``compare/section_readers.py``); a change only that
section shows is not assessed, so the run reads ``partial`` and names the
section rather than reading as a clean comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = ["unreadable_section_gap"]


def unreadable_section_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)``: whether either side holds a section it could not use."""
    found: dict[str, str] = {}
    for side, snap in (("old", old), ("new", new)):
        if snap is None:
            continue
        for section, diagnostic in getattr(snap, "unreadable_sections", ()):
            found.setdefault(f"{side}:{section}", diagnostic)
    return bool(found), [
        f"not comparable ({key.split(':', 1)[0]} snapshot): {diagnostic}; every "
        "analysis reading that section is not evaluated"
        for key, diagnostic in found.items()
    ]
