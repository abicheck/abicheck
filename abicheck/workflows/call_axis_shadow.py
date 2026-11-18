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

"""Attach ADR-094 D9's shadow ``call`` axis to a ``DiffResult``.

``compare/call_axis_shadow.py`` computes one axis value per function a
signature finding names; this module pairs each with the verdict the
existing policy gave that function's signature findings and records where
the two disagree. Shadow only: the block is written to
``DiffResult.call_axis_shadow`` after the verdict is final and is never read
back by the verdict, severity, or exit-code derivation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..compare.call_axis_shadow import call_axis_shadow
from ..compare.call_interface import AxisStatus
from ..model.change_catalog.registry import Verdict
from ..policy.reclassify import effective_verdict_for_change

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model.snapshot import AbiSnapshot

__all__ = ["attach_call_axis_shadow", "disagreement_of"]

_RANK = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 1,
    Verdict.COMPATIBLE_WITH_RISK: 2,
    Verdict.API_BREAK: 3,
    Verdict.BREAKING: 4,
}


def disagreement_of(existing: Verdict, status: AxisStatus) -> str | None:
    """Where the call axis and the existing verdict part ways, or ``None``.

    ``breaking_but_call_preserved``: the existing policy calls the function's
    signature change an ABI break, the axis says old callers still call it
    correctly. ``compatible_but_call_broken``: the existing policy calls it
    binary-compatible (anything below ``BREAKING``), the axis says an old
    caller's arguments or result are no longer intact. ``unknown`` never
    disagrees.
    """
    if status is AxisStatus.PRESERVED and existing is Verdict.BREAKING:
        return "breaking_but_call_preserved"
    if status is AxisStatus.BROKEN and existing is not Verdict.BREAKING:
        return "compatible_but_call_broken"
    return None


def attach_call_axis_shadow(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    """Set ``result.call_axis_shadow`` (``None`` without a baseline or a
    signature finding)."""
    if old is None:
        return
    records = call_axis_shadow(old, new, result.changes)
    if not records:
        return
    kinds_of = {r.symbol: r.kinds for r in records}
    by_symbol: dict[str, Verdict] = {}
    for change in result.changes:
        if change.kind not in kinds_of.get(change.symbol, ()):
            continue
        verdict = effective_verdict_for_change(
            change, policy=result.policy, policy_file=result.policy_file
        )
        prior = by_symbol.get(change.symbol)
        if prior is None or _RANK.get(verdict, 0) > _RANK.get(prior, 0):
            by_symbol[change.symbol] = verdict
    entries: list[dict[str, object]] = []
    for record in records:
        existing = by_symbol.get(record.symbol, Verdict.NO_CHANGE)
        entries.append(
            {
                "symbol": record.symbol,
                "change_kinds": [k.value for k in record.kinds],
                "abi": record.abi or None,
                "status": record.axis.status.value,
                "reason": record.axis.reason,
                "existing_verdict": existing.value,
                "disagreement": disagreement_of(existing, record.axis.status),
            }
        )
    result.call_axis_shadow = entries
