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

"""Assurance gap for a header-scoped dump that fell back to the export table.

When ``--header`` was given but the header backend failed, the format
adapter records ``AbiSnapshot.scope_fallback`` (``header-backend-unavailable``)
and keeps only the export table: the supplied headers' declarations, and
every header-level check (concepts, template defaults, field layout), were
never assessed. When the headers parsed but no declaration could be bound
to an export (``export-binding-unresolved``: a template-only header, or a
mangling scheme the binding pass cannot relate), the header surface is kept
and only the export binding is missing. Either is a coverage gap, never a
verdict, so the rollup reads ``partial``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding, gap_and_notes

__all__ = [
    "SCOPE_FALLBACK_DIMENSION",
    "scope_fallback_findings",
    "scope_fallback_gap",
]

SCOPE_FALLBACK_DIMENSION = (
    "header declarations (header scoping fell back to the export table)"
)


def _sides(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[tuple[Side, AbiSnapshot | None], ...]:
    return (("old", old), ("new", new))


def _finding_text(label: str, snap: AbiSnapshot) -> str:
    code = snap.scope_fallback
    if getattr(snap, "from_headers", False):
        # The headers parsed and were kept (``export-binding-unresolved``):
        # types and templates were assessed, export binding was not.
        return (
            f"{SCOPE_FALLBACK_DIMENSION}: the {label} snapshot's header "
            f"declarations could not be bound to any export ({code}), so its "
            "exported functions and variables are compared from the export "
            "table only; header types and templates were assessed"
        )
    return (
        f"{SCOPE_FALLBACK_DIMENSION}: the {label} snapshot was dumped with "
        f"headers but fell back to export-table mode ({code}), "
        "so header-level changes on that side are not assessed"
    )


def scope_fallback_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per side whose dump discarded its headers, or kept them
    without binding any declaration to an export."""
    return [
        UnobservedFinding(SCOPE_FALLBACK_DIMENSION, label, _finding_text(label, snap))
        for label, snap in _sides(old, new)
        if snap is not None and getattr(snap, "scope_fallback", None)
    ]


def scope_fallback_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)`` view of :func:`scope_fallback_findings`."""
    return gap_and_notes(scope_fallback_findings(old, new))
