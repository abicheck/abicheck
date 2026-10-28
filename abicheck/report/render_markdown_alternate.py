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

"""Markdown's ``--view root-cause`` ``ReportDocument`` pair.

ADR-061 Phase 2 item 1's last two Markdown views. Split out of
``render_markdown_document.py`` once that module (review digest + full mode
+ these two views combined) passed the architecture check's new-file 800-line
ceiling -- the same reason ``render_html.py``/``render_html_document.py``
are two files rather than one, and ``render_markdown_document.py``'s own
docstring already documents that convention. ``render_markdown_document.py``
keeps review digest, the full-mode (``to_markdown``'s default view) pair,
and the row-shape helpers (``_change_row``/``_row_contract_tag``/
``_render_change_row``/``_render_change_row_oneline``/
``_not_evaluated_mapping``/``_render_not_evaluated_lines``) every view here
reuses rather than duplicating; this module imports them from there rather
than back -- a same-package, one-directional dependency, not a cycle.

``build_leaf_document``/``render_leaf_document`` (``--report-mode leaf``) and
``build_root_cause_document``/``render_root_cause_document``
(``--report-mode root-cause``, G29 Phase 3 slice 4, ADR-052) are this
module's two view pairs. Both share one opening block --
``_view_preamble_mapping``/``_render_view_preamble`` -- the compute/render
split of what used to be ``reporter_markdown._view_preamble`` (title/verdict
table, coverage-warning banner, optional recommendation section, the
``--show-only`` filter note); that pre-split function is retired, since
nothing calls it any more. ``root-cause`` mode's own grouping
(``report.root_cause_section.compute_root_cause_section`` -> ``RootCauseSectionData``)
was already JSON-safe before this change (every finding is pre-formatted to
a plain string at compute time), so its document fold is a direct
``dataclasses.asdict()`` with no new row type of its own --
``_root_cause_section_from_mapping`` is only the reconstruction half a
document round trip requires (tuple -> list -> tuple).

Reaches ``reporter_markdown.py`` (a ``layers.report.legacy_paths`` member,
same layer as this file) via ``_reporter_markdown()``, imported from
``render_markdown_document.py`` rather than duplicated -- ADR-063 T10 made
that helper a plain static import there (``reporter_markdown.py`` no
longer imports anything from this module or ``render_markdown_document.py``
at all; ``_to_markdown_leaf``/``_to_markdown_root_cause`` moved to
``report/dispatch_markdown.py``), so the ``importlib`` indirection this
module used to inherit from that helper is gone.

Every view's byte-for-byte output is unchanged by this split -- leaf mode
has no dedicated golden suite but is covered by
``tests/test_checker_reporter_branches.py``'s substring assertions, and
``tests/test_golden_root_cause.py`` pins root-cause mode's exact text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .disposition_audit import (
    DispositionAudit,
    render_disposition_audit_section,
)
from .document import ReportDocument
from .markdown_text import md_text
from .pattern_modulations_markdown import (
    render_pattern_modulations_from_mapping,
)
from .render_markdown import (
    OutOfSurfaceNote,
    RecommendationSection,
    RedundancyNote,
    RootCauseGroupData,
    RootCauseSectionData,
    SeverityRow,
    SeveritySummary,
    render_footer,
    render_impact_table,
    render_out_of_surface_note,
    render_recommendation_section,
    render_redundancy_note,
    render_root_cause_section,
    render_severity_summary,
    render_suppression_note,
)
from .render_markdown_document import (
    _impact_table_from_mapping,
    _suppression_note_from_mapping,
)
from .surface_changes import (
    SurfaceChangeSection,
    render_surface_changes_section,
)
from .view import render_show_only_cli_hint

# ---------------------------------------------------------------------------
# Shared preamble (--report-mode leaf / root-cause)
# ---------------------------------------------------------------------------


def _render_view_preamble(d: Mapping[str, Any]) -> list[str]:
    """Render :func:`_view_preamble_mapping`'s fields -- byte-identical to
    ``reporter_markdown._view_preamble``'s pre-split lines."""
    lines: list[str] = [
        f"# ABI Report: {d['library']} ({d['view_label']})",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | {md_text(d['old_version'], code=True, table=True)} |",
        f"| **New version** | {md_text(d['new_version'], code=True, table=True)} |",
        f"| **Verdict** | {d['verdict_emoji']} `{d['verdict_label']}` |",
        "",
    ]
    if d["coverage_warnings"]:
        lines += [f"> ⚠️ {w}" for w in d["coverage_warnings"]]
        lines.append("")
    if d["recommendation"] is not None:
        lines += render_recommendation_section(
            RecommendationSection(**d["recommendation"])
        )
    if d["show_only_note"] is not None:
        note = d["show_only_note"]
        # Codex review (PR #1154 second follow-up: "Render repeated show
        # groups as repeated view options") -- render each `;`-joined
        # internal group as its own `--view show=...` token.
        cli_hint = render_show_only_cli_hint(note["show_only"])
        lines.append(
            f"> Filtered by: {md_text(cli_hint, code=True)} "
            f"({note['shown']} of {note['total']} changes shown)"
        )
        lines.append("")
    audit = d.get("disposition_audit")
    if isinstance(audit, Mapping):
        lines += render_disposition_audit_section(DispositionAudit.from_dict(audit))
    surface = d.get("surface_changes")
    if isinstance(surface, Mapping):
        lines += render_surface_changes_section(SurfaceChangeSection.from_dict(surface))
    return lines


# ---------------------------------------------------------------------------
# Leaf mode (--report-mode leaf)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Root-cause mode (--report-mode root-cause)
# ---------------------------------------------------------------------------


def _root_cause_section_from_mapping(
    d: Mapping[str, Any] | None,
) -> RootCauseSectionData | None:
    if d is None:
        return None
    return RootCauseSectionData(
        groups=tuple(
            RootCauseGroupData(
                root_display=g["root_display"],
                count=g["count"],
                finding_lines=tuple(g["finding_lines"]),
                omitted=g.get("omitted", 0),
            )
            for g in d["groups"]
        ),
        omitted_groups=d.get("omitted_groups", 0),
        omitted_findings=d.get("omitted_findings", 0),
    )


def render_root_cause_document(doc: ReportDocument) -> str:
    """Project a root-cause-mode ``ReportDocument`` to its Markdown text."""
    d: dict[str, Any] = doc.to_mapping()
    lines = _render_view_preamble(d)

    if d["severity_summary"] is not None:
        summary_rows = tuple(
            SeverityRow(**row) for row in d["severity_summary"]["rows"]
        )
        lines += render_severity_summary(SeveritySummary(rows=summary_rows))

    lines += render_root_cause_section(
        _root_cause_section_from_mapping(d["root_cause"])
    )

    if d["empty_message"] is not None:
        lines.append(d["empty_message"])

    lines += render_redundancy_note(
        None if d["redundancy_note"] is None else RedundancyNote(**d["redundancy_note"])
    )
    lines += render_suppression_note(
        _suppression_note_from_mapping(d["suppression_note"])
    )
    lines += render_out_of_surface_note(
        None
        if d["out_of_surface_note"] is None
        else OutOfSurfaceNote(**d["out_of_surface_note"])
    )
    if d["show_impact"]:
        lines += render_impact_table(_impact_table_from_mapping(d["impact_table"]))
    # ADR-067: a rule that demoted a breaking finding is a disposition, so it
    # is disclosed in *this* document too, not only the full one. The
    # canonical `ReportDocument` has carried `pattern_modulations` all along
    # -- only this projection was missing, so `--view root-cause` rendered an
    # accepted result naming neither the rule nor its reason (Codex review,
    # PR #1284). Same row renderer the full document uses, so the two cannot
    # disagree about a modulation's columns.
    lines += render_pattern_modulations_from_mapping(d.get("pattern_modulations"))
    lines += render_footer()
    # New defect 5 fix: every Markdown report format must end with a
    # trailing newline (POSIX text-file convention), matching
    # `render_markdown_document.render_markdown_document`'s identical fix
    # and `render_markdown.render_review_digest`'s pre-existing
    # `.rstrip() + "\n"` pattern.
    return "\n".join(lines).rstrip() + "\n"
