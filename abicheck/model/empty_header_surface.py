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

"""Name each side whose parsed header set produced no declarations.

An empty, whitespace-only, comment-only, or system-include-only header parses
cleanly to a surface with nothing of the library's own in it. Every
declaration the other side has then reads as added or removed. That is the
honest result when the header really was emptied, so nothing here suppresses
a finding; the warning only states that the surface came from a header set
that produced nothing, so a reader can tell an emptied API from a wrong
``--header`` path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["EMPTY_HEADER_SURFACE_MARKER", "empty_header_surface_warnings"]

#: Substring every empty-header-surface warning shares, so a consumer can
#: filter ``coverage_warnings`` for this one.
EMPTY_HEADER_SURFACE_MARKER = "zero public declarations"

#: The declaration stores a parsed header surface populates (the same set the
#: evidence-tier detection reads when it asks whether declarations exist).
_HEADER_DECLARATION_FIELDS = ("functions", "variables", "types", "enums", "typedefs")


def _declaration_count(snap: AbiSnapshot) -> int:
    decls = snap.declarations
    return sum(
        len(getattr(decls, name, ()) or ()) for name in _HEADER_DECLARATION_FIELDS
    )


def empty_header_surface_warnings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[str]:
    """One warning per side that parsed headers and declared nothing.

    Only a side that actually parsed headers (``from_headers``) is named: a
    binary-only side with no declarations is a different, already-reported
    reduced-evidence case.
    """
    warnings: list[str] = []
    for side, snap in (("old", old), ("new", new)):
        if snap is None or not getattr(snap, "from_headers", False):
            continue
        if _declaration_count(snap) > 0:
            continue
        warnings.append(
            f"{side}: the {side} header set produced "
            f"{EMPTY_HEADER_SURFACE_MARKER} (empty, comment-only or "
            "system-include-only input). The other side's declarations read as "
            "added or removed; if this header set should not be empty, check "
            "the --header input."
        )
    return warnings
