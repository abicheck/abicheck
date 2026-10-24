# Copyright 2026 Nikolay Petrov
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

"""Per-change HTML table rows: the compute half of the changes table.

:func:`compute_full_change_rows` resolves every registry lookup and raw display
field a row needs, so the renderer in :mod:`render_html` decides nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compare.finding_identity import report_finding_id
from .finding import impact_text_for
from .policy_facts import category, kind_str, severity
from .render_html import ChangeRow


def compute_full_change_rows(
    changes: Iterable[object],
    evidence_tiers: Sequence[str] = (),
    use_cases: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[ChangeRow, ...]:
    """Resolve every fact a changes-table row needs for one change: the four
    registry-lookup decisions (kind string, category, impact text, ABICC
    severity band) plus every raw display field `render_changes_table`
    read directly off a live `Change` before ADR-061 Phase 2 item 1 closed
    for HTML.

    Building this once per render, rather than reading `Change` attributes
    mid-render, is what lets that function -- and the whole-document
    `render_html_document` -- become pure `ReportDocument` projections: a
    `ChangeRow` is an ordinary, JSON-round-trippable value, so this replaces
    the previous `id(change)`-keyed `ChangeRowFactsById` lookup table (needed
    only because `Change` is not hashable) with a plain ordered tuple.

    *evidence_tiers* lets an UNATTRIBUTED finding's impact text carry the
    same evidence caveat the JSON/Markdown views already do (Codex review).

    *use_cases* is the per-finding ``compare --use-cases`` attribution
    (``use_cases_by_finding_for``): ``None`` when no attribution ran, so the
    row carries no use-case field at all; otherwise each row carries the
    names reaching its finding, empty when none does.
    """
    rows = []
    for ch in changes:
        ks = kind_str(ch)
        relevance = getattr(ch, "contract_relevance", None)
        assurance = getattr(ch, "contract_assurance", None)
        decision = getattr(ch, "compatibility_decision", None)
        rows.append(
            ChangeRow(
                kind=ks,
                category=category(ks),
                impact=impact_text_for(ch, evidence_tiers),
                severity=severity(ks),
                symbol=getattr(ch, "symbol", "") or "",
                description=getattr(ch, "description", "") or "",
                old_value=str(getattr(ch, "old_value", "") or ""),
                new_value=str(getattr(ch, "new_value", "") or ""),
                source_location=getattr(ch, "source_location", None) or None,
                affected_symbols=tuple(getattr(ch, "affected_symbols", None) or ()),
                caused_count=getattr(ch, "caused_count", 0) or 0,
                contract_relevance=(
                    str(relevance.value) if relevance is not None else None
                ),
                contract_reason_code=(
                    getattr(ch, "contract_reason_code", None) or None
                ),
                contract_assurance=(
                    str(assurance.value) if assurance is not None else None
                ),
                compatibility_decision=(
                    str(decision.value) if decision is not None else None
                ),
                contract_evidence_refs=tuple(
                    getattr(ch, "contract_evidence_refs", None) or ()
                ),
                correlated_change_kind=(
                    getattr(ch, "correlated_change_kind", None) or None
                ),
                affected_use_cases=(
                    None
                    if use_cases is None
                    else tuple(use_cases.get(report_finding_id(ch), ()))
                ),
            )
        )
    return tuple(rows)
