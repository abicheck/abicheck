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
"""Per-target accounting axis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._result import TargetAccounting

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _target_accounting(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[TargetAccounting, bool, list[str]]:
    """Roll up P0.2's root-target scoping (``BuildEvidence.target_scope``)
    across both sides, plus a second signal: whether either side requested a
    root target that never resolved (Finding, P1 review -- see
    :class:`TargetAccounting`'s own docstring for the full history).

    ``requested``/``resolved`` are the union (order-preserving, deduplicated)
    of both sides' ``TargetScope.requested``/``.resolved`` label lists --
    mirroring the additive rollup :func:`_translation_units` already uses
    for numeric L4 accounting, adapted for label sets since target names
    (not counts) are what a consumer needs to see here. A side whose
    ``TargetScope`` carries an empty ``requested`` list is treated the same
    as ``target_scope is None`` -- ``BuildEvidence.target_scope``'s own
    docstring documents this as "root-target scoping was not requested",
    matching the convention ``buildsource.build_evidence.l3_coverage_fields``
    already uses for the identical field.
    """
    requested: list[str] = []
    resolved: list[str] = []
    seen_requested: set[str] = set()
    seen_resolved: set[str] = set()
    transitive_total: int | None = None
    any_unresolved = False
    notes: list[str] = []
    for side, pack in (("old", old_pack), ("new", new_pack)):
        be = getattr(pack, "build_evidence", None)
        ts = getattr(be, "target_scope", None) if be is not None else None
        if ts is None or not ts.requested:
            continue
        for t in ts.requested:
            if t not in seen_requested:
                seen_requested.add(t)
                requested.append(t)
        for t in ts.resolved:
            if t not in seen_resolved:
                seen_resolved.add(t)
                resolved.append(t)
        if ts.transitive_count is not None:
            transitive_total = (transitive_total or 0) + ts.transitive_count
        unresolved = sorted(set(ts.requested) - set(ts.resolved))
        if unresolved:
            any_unresolved = True
            notes.append(
                f"{side} side's requested root target(s) did not all resolve: "
                f"{', '.join(unresolved)}"
            )

    if not requested and not resolved and transitive_total is None:
        return TargetAccounting(), False, notes

    return (
        TargetAccounting(
            requested=tuple(requested),
            resolved=tuple(resolved),
            transitive_count=transitive_total,
        ),
        any_unresolved,
        notes,
    )
