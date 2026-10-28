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

"""The ordered section facts of the native HTML report body.

Split out of ``abicheck/html_report.py`` (ADR-061 Phase 2 item 1's compute
half for HTML sections) so that module stays under the production size cap.
Each section is a JSON-shaped mapping its ``render_html_document`` projection
formats without deciding anything. A changes section holds the gating rows
plus the non-gating rows the shared budget keeps, and states how many it cut
(``report/gating_budget.py``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence

from .gating_budget import cap_non_gating
from .html_change_rows import compute_full_change_rows
from .render_html import NotEvaluatedRow, NotEvaluatedSectionData

__all__ = ["build_sections_data", "compute_not_evaluated_section"]


def compute_not_evaluated_section(
    not_evaluated: list[object],
    relevance_of: Callable[[object], object] | None = None,
) -> NotEvaluatedSectionData:
    """Collect the rows of the ADR-049 D1 "Not Evaluated (Contract)" table.

    Resolving each finding's contract relevance is the caller's own predicate
    (``contract_finding_relevance.contract_relevance_of``), threaded in rather than
    imported here so this stays a plain projection of already-decided facts.
    """
    rows = []
    for ch in not_evaluated:
        relevance = relevance_of(ch) if relevance_of is not None else None
        rows.append(
            NotEvaluatedRow(
                symbol=getattr(ch, "symbol", "") or "",
                kind_value=str(getattr(getattr(ch, "kind", None), "value", "")),
                relevance=str(getattr(relevance, "value", "") or ""),
                reason=str(getattr(ch, "contract_reason_code", "") or ""),
                correlated=str(getattr(ch, "correlated_change_kind", None) or ""),
            )
        )
    return NotEvaluatedSectionData(rows=tuple(rows))


def build_sections_data(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    *,
    not_evaluated: list[object] | None = None,
    relevance_of: Callable[[object], object] | None = None,
    evidence_tiers: Sequence[str] = (),
    use_cases: Mapping[str, tuple[str, ...]] | None = None,
    non_gating: frozenset[int] = frozenset(),
) -> list[dict[str, object]]:
    """Build the ordered list of section facts for the native HTML report
    body -- the JSON-shaped counterpart of the pre-split
    ``_build_sections_html``, which built markup directly. Each entry names
    its own ``kind`` so ``report.render_html.render_html_document`` can
    dispatch without making any decision of its own.

    *not_evaluated* (ADR-049 D1) renders last, in its own non-verdict
    section: those findings were never scored by compatibility policy, so
    filing them under Removed/Changed/Added would contradict the verdict
    banner at the top of the same page. Defaults to nothing, so a run without
    `--contract` produces the identical document it always did.

    *evidence_tiers* is threaded into `compute_full_change_rows` so an
    UNATTRIBUTED finding's impact text carries the same evidence caveat the
    JSON/Markdown views already do (Codex review).
    """
    sections: list[dict[str, object]] = []
    for title, anchor, css_class, items in (
        ("⛔ Removed Symbols", "removed", "section-removed", removed),
        ("⚠️ Changed Symbols", "changed", "section-changed", changed),
        ("✅ Added Symbols", "added", "section-added", added),
        ("🔕 Suppressed Changes", "suppressed", "section-suppressed", suppressed),
    ):
        if items:
            # A gating row is always itemised; a non-gating one beyond the
            # shared budget is counted, never silently dropped (see
            # report/gating_budget.py).
            shown, omitted = cap_non_gating(items, non_gating)
            sections.append(
                {
                    "kind": "changes",
                    "title": title,
                    "anchor": anchor,
                    "css_class": css_class,
                    "total": len(items),
                    "omitted": omitted,
                    "rows": [
                        dataclasses.asdict(row)
                        for row in compute_full_change_rows(
                            shown, evidence_tiers, use_cases
                        )
                    ],
                }
            )
    if suppressed_count and not suppressed:
        sections.append({"kind": "suppressed_placeholder", "count": suppressed_count})
    if not_evaluated:
        sections.append(
            {
                "kind": "not_evaluated",
                "data": dataclasses.asdict(
                    compute_not_evaluated_section(not_evaluated, relevance_of)
                ),
            }
        )
    return sections
