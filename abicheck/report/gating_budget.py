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

"""Which rendered findings are non-gating, and the size cap that applies to them.

The product rule the Markdown report already follows (``kind_rollup.py``): a
gating finding is always itemised, a non-gating one may be summarised once a
list grows past :data:`~abicheck.report.markdown_budget.ITEM_LIMIT`, and the
report says how many were left out. HTML and the Markdown root-cause view had
no such cap, so a 5,000-addition report rendered 1.5 MB of HTML and 640 KB of
root-cause Markdown. This module is the one place that decides "non-gating"
for those presentation caps, so the two renderers cannot disagree about it.

"Non-gating" is the ADR-049 D1 gate contribution of the finding, resolved once
by the caller: a finding that contributes ``0`` to the run's exit (a demoted
category, a contract-excluded finding, an accepted break) is cappable; anything
that gates, and anything not resolved here, is kept. A cap only ever hides a
finding that cannot change the exit code, and every cut is counted.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, TypeVar, cast

from ..policy.gate_contribution import resolved_gate_contribution
from .markdown_budget import ITEM_LIMIT

if TYPE_CHECKING:
    from ..policy.severity import SeverityConfig
    from .finding import ReportFinding

_T = TypeVar("_T")


def non_gating_change_ids(
    findings: Iterable[ReportFinding],
    severity_config: object | None,
    accepted: frozenset[int] = frozenset(),
) -> frozenset[int]:
    """``id()`` of every change among *findings* that contributes nothing to the gate.

    *accepted* is :func:`~abicheck.policy.acknowledgment_breaks.accepted_break_ids`
    for the run: an accepted break is off the gate by disposition, so it is
    cappable like any other non-gating finding.
    """
    return frozenset(
        id(finding.change)
        for finding in findings
        if id(finding.change) in accepted
        or resolved_gate_contribution(
            finding.change,
            cast("SeverityConfig | None", severity_config),
            verdict=finding.verdict,
            category=finding.category,
        )
        == 0
    )


def cap_non_gating(
    items: Sequence[_T],
    non_gating: frozenset[int],
    limit: int = ITEM_LIMIT,
) -> tuple[list[_T], int]:
    """``(shown, omitted)``: every gating item, plus the first *limit* non-gating ones.

    Order is preserved. *items* are matched by ``id()``, so they must be the
    same objects the *non_gating* set was built from. ``shown`` and ``omitted``
    partition *items* exactly: ``len(shown) + omitted == len(items)``.
    """
    shown: list[_T] = []
    omitted = 0
    kept_non_gating = 0
    for item in items:
        if id(item) not in non_gating:
            shown.append(item)
        elif kept_non_gating < limit:
            kept_non_gating += 1
            shown.append(item)
        else:
            omitted += 1
    return shown, omitted


def more_non_gating_line(
    omitted: int, *, json_field: str = "changes", prefix: str = ""
) -> str | None:
    """The disclosure for *omitted* non-gating findings, or ``None`` when none were cut.

    *json_field* names the machine-readable list that holds every finding.
    """
    if omitted <= 0:
        return None
    return (
        f"{prefix}… and {omitted} more non-gating finding(s) not itemised here; "
        f"every finding is in the machine-readable report (JSON `{json_field}`)."
    )


__all__ = [
    "cap_non_gating",
    "more_non_gating_line",
    "non_gating_change_ids",
]
