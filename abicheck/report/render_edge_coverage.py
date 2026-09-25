# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Markdown and HTML projections of the "Relationship coverage" section
(evidence-entity-model Phase 4, I4). Formats
:class:`~abicheck.report.edge_coverage_section.EdgeCoverageSection` and
decides nothing: which rows appear was decided by its compute half."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .edge_coverage_section import EdgeCoverageRow, EdgeCoverageSection

__all__ = ["render_edge_coverage_html", "render_edge_coverage_markdown"]

_TITLE = "Relationship Coverage"
_LEAD = (
    "A relationship missing from the evidence (an export, a debug type, a "
    "header declaration, a source-graph edge) is proven absent only where "
    "its producer covered the scope; otherwise it is unknown."
)


def _counts(row: EdgeCoverageRow) -> list[tuple[str, str, str, str]]:
    if not row.answers:
        return [("—", "—", "—", "—")]
    return [(pop, str(p), str(a), str(u)) for pop, p, a, u in row.answers]


def render_edge_coverage_markdown(section: EdgeCoverageSection | None) -> list[str]:
    if section is None:
        return []
    lines = [f"## {_TITLE}", "", f"> {_LEAD}", ""]
    if not section.rows:
        lines += [
            f"Every producer covered its scope ({section.kinds_checked} "
            "relationship kinds checked): each reported absence is proven.",
            "",
        ]
        return lines
    lines += [
        "| Side | Relationship | Subjects | Present | Proven absent | Unknown | Producers |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in section.rows:
        producers = "<br>".join(row.producers)
        for pop, present, absent, unknown in _counts(row):
            lines.append(
                f"| {row.side} | `{row.edge_kind}` ({row.evidence_class}) | {pop} "
                f"| {present} | {absent} | {unknown} | {producers} |"
            )
    lines.append("")
    return lines


def render_edge_coverage_html(section: EdgeCoverageSection | None) -> str:
    if section is None:
        return ""
    h = html.escape
    if not section.rows:
        body = (
            f"<p>Every producer covered its scope ({section.kinds_checked} "
            "relationship kinds checked): each reported absence is proven.</p>"
        )
    else:
        cells = []
        for row in section.rows:
            producers = "<br>".join(h(p) for p in row.producers)
            for pop, present, absent, unknown in _counts(row):
                cells.append(
                    f"<tr><td>{h(row.side)}</td><td><code>{h(row.edge_kind)}</code> "
                    f"({h(row.evidence_class)})</td><td>{h(pop)}</td><td>{present}</td>"
                    f"<td>{absent}</td><td>{unknown}</td><td>{producers}</td></tr>"
                )
        body = (
            "<table class='summary-table'><thead><tr><th>Side</th>"
            "<th>Relationship</th><th>Subjects</th><th>Present</th>"
            "<th>Proven absent</th><th>Unknown</th><th>Producers</th></tr></thead>"
            f"<tbody>{''.join(cells)}</tbody></table>"
        )
    return (
        f"<div class='summary-section'><h3>🧭 {_TITLE}</h3>"
        f"<p>{h(_LEAD)}</p>{body}</div>"
    )
