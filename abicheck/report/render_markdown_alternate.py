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

"""Markdown's ``--report-mode leaf``/``root-cause`` ``ReportDocument`` pair.

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
(``reporter_markdown.compute_root_cause_section`` -> ``RootCauseSectionData``)
was already JSON-safe before this change (every finding is pre-formatted to
a plain string at compute time), so its document fold is a direct
``dataclasses.asdict()`` with no new row type of its own --
``_root_cause_section_from_mapping`` is only the reconstruction half a
document round trip requires (tuple -> list -> tuple).

Reaches ``reporter_markdown.py`` (a ``layers.report.legacy_paths`` member,
same layer as this file) via ``importlib`` (``_reporter_markdown()``,
imported from ``render_markdown_document.py`` rather than duplicated), same
as that module: ``reporter_markdown.py`` imports this module's entry points
via function-local (not module-level) imports (``_to_markdown_leaf``/
``_to_markdown_root_cause``), so a static, module-level import back here
would close a real cycle ``check_ai_readiness.py``'s
``import-cycle-growth`` gate flags.

Every view's byte-for-byte output is unchanged by this split -- leaf mode
has no dedicated golden suite but is covered by
``tests/test_checker_reporter_branches.py``'s substring assertions, and
``tests/test_golden_root_cause.py`` pins root-cause mode's exact text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .disposition_audit import (
    DispositionAudit,
    compute_disposition_audit,
    render_disposition_audit_section,
)
from .document import ReportDocument
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
    _change_row,
    _impact_table_from_mapping,
    _not_evaluated_mapping,
    _opt_asdict,
    _render_change_row,
    _render_not_evaluated_lines,
    _reporter_markdown,
    _suppression_note_from_mapping,
)


def _render_leaf_type_change_row(row: Mapping[str, Any]) -> list[str]:
    """Row-based counterpart of ``render_markdown._format_leaf_type_change``
    (``--report-mode leaf``'s ``### {symbol} — {desc}`` type-change entry).
    Kept here rather than in ``render_markdown_document.py`` since only leaf
    mode uses it -- mirrors that module's own ``_row_contract_tag`` helper,
    imported below rather than duplicated.
    """
    from .render_markdown_document import _row_contract_tag

    lines = [f"### {row['symbol']} — {row['description']}"]
    affected = row.get("affected_symbols")
    if affected:
        lines.append(f"\n**Affected interfaces ({len(affected)}):**")
        for sym in affected[:10]:
            lines.append(f"- `{sym}`")
        if len(affected) > 10:
            lines.append(f"- ... ({len(affected) - 10} more)")
    caused_count = row.get("caused_count") or 0
    if caused_count > 0:
        lines.append(f"\n> {caused_count} derived change(s) collapsed")
    tag = _row_contract_tag(row)
    if tag is not None:
        lines.append(f"\n> Contract: {tag}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Shared preamble (--report-mode leaf / root-cause)
# ---------------------------------------------------------------------------


def _view_preamble_mapping(
    result: Any,
    view_label: str,
    *,
    show_only: str | None,
    show_recommendation: bool,
    severity_config: Any = None,
) -> tuple[dict[str, Any], list[Any]]:
    """JSON-safe fields for the opening block ``--report-mode leaf``/
    ``root-cause`` share (title/verdict table, coverage-warning banner,
    optional recommendation section, ``--show-only`` filter note) plus the
    (possibly filtered) changes to render -- the compute half of
    ``reporter_markdown._view_preamble``'s split. See
    :func:`_render_view_preamble` for the render half."""
    rm = _reporter_markdown()

    changes = list(result.changes)
    show_only_note: dict[str, Any] | None = None
    if show_only:
        changes = rm.apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        show_only_note = {
            "show_only": show_only,
            "shown": len(changes),
            "total": len(result.changes),
        }
        changes = rm._suppress_dangling_correlation_notes(changes)

    d: dict[str, Any] = {
        "library": result.library,
        "view_label": view_label,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict_emoji": rm._VERDICT_EMOJI[result.verdict],
        "verdict_label": rm._VERDICT_LABEL[result.verdict],
        "coverage_warnings": (
            list(result.coverage_warnings) if result.coverage_warnings else []
        ),
        "recommendation": (
            asdict(rm.compute_recommendation_section(result))
            if show_recommendation
            else None
        ),
        "show_only_note": show_only_note,
        # ADR-067 D3: both alternate modes carry the same counts the full
        # view and the digest do -- computed here, in the one preamble the
        # two share, rather than twice at their own call sites.
        "disposition_audit": compute_disposition_audit(
            result, severity_config
        ).to_dict(),
    }
    return d, changes


def _render_view_preamble(d: Mapping[str, Any]) -> list[str]:
    """Render :func:`_view_preamble_mapping`'s fields -- byte-identical to
    ``reporter_markdown._view_preamble``'s pre-split lines."""
    lines: list[str] = [
        f"# ABI Report: {d['library']} ({d['view_label']})",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | `{d['old_version']}` |",
        f"| **New version** | `{d['new_version']}` |",
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
        lines.append(
            f"> Filtered by: `--show-only {note['show_only']}` "
            f"({note['shown']} of {note['total']} changes shown)"
        )
        lines.append("")
    audit = d.get("disposition_audit")
    if isinstance(audit, Mapping):
        lines += render_disposition_audit_section(DispositionAudit.from_dict(audit))
    return lines


# ---------------------------------------------------------------------------
# Leaf mode (--report-mode leaf)
# ---------------------------------------------------------------------------


def build_leaf_document(
    result: Any,
    *,
    show_impact: bool = False,
    show_only: str | None = None,
    show_recommendation: bool = False,
    severity_config: Any = None,
) -> ReportDocument:
    """``--report-mode leaf`` (root type changes with affected-interface
    lists) as a ``ReportDocument``. See this module's own docstring for
    scope."""
    rm = _reporter_markdown()
    preamble, changes = _view_preamble_mapping(
        result,
        "leaf-change view",
        show_only=show_only,
        show_recommendation=show_recommendation,
        severity_config=severity_config,
    )

    from ..checker import _ROOT_TYPE_CHANGE_KINDS
    from ..report_model import ReportModel

    # ADR-049 D1 (see reporter_markdown._to_markdown_leaf's own note): leaf
    # mode groups purely by ChangeKind, so the not-evaluated partition has to
    # happen before that grouping, not after.
    not_evaluated = ReportModel.classify_not_evaluated(changes)
    excluded_ids = {id(c) for c in not_evaluated}
    scored = [c for c in changes if id(c) not in excluded_ids]

    type_changes = [c for c in scored if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in scored if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    leaf_sections = rm.compute_leaf_type_sections(type_changes, result.policy)
    not_evaluated_section = rm.compute_not_evaluated(not_evaluated)

    d: dict[str, Any] = {
        **preamble,
        "report_mode": "leaf",
        "severity_summary": (
            asdict(
                rm.compute_severity_summary(
                    changes,
                    severity_config,
                    all_changes=list(result.changes),
                    policy=result.policy,
                    kind_sets=result._effective_kind_sets(),
                    policy_file=result.policy_file,
                )
            )
            if severity_config is not None
            else None
        ),
        "type_sections": [
            {"heading": s.heading, "rows": [_change_row(c) for c in s.changes]}
            for s in leaf_sections.sections
        ],
        "non_type_changes": (
            [_change_row(c) for c in non_type_changes] if non_type_changes else None
        ),
        "not_evaluated": _not_evaluated_mapping(not_evaluated_section),
        "empty_message": (
            None
            if changes
            else (
                "_No changes match the current filter._"
                if (show_only and result.changes)
                else "_No ABI changes detected._"
            )
        ),
        "redundancy_note": _opt_asdict(rm.compute_redundancy_note(result)),
        "suppression_note": _opt_asdict(rm.compute_suppression_note(result)),
        "out_of_surface_note": _opt_asdict(rm.compute_out_of_surface_note(result)),
        "show_impact": show_impact,
        "impact_table": (
            _opt_asdict(rm.compute_impact_table(result, displayed_changes=changes))
            if show_impact
            else None
        ),
    }
    return ReportDocument.from_mapping(d)


def render_leaf_document(doc: ReportDocument) -> str:
    """Project a leaf-mode ``ReportDocument`` to its Markdown text."""
    d: dict[str, Any] = doc.to_mapping()
    lines = _render_view_preamble(d)

    if d["severity_summary"] is not None:
        summary_rows = tuple(
            SeverityRow(**row) for row in d["severity_summary"]["rows"]
        )
        lines += render_severity_summary(SeveritySummary(rows=summary_rows))

    for section in d["type_sections"]:
        lines += [section["heading"], ""]
        for row in section["rows"]:
            lines += _render_leaf_type_change_row(row)

    if d["non_type_changes"] is not None:
        lines += ["## Non-Type Changes", ""]
        for row in d["non_type_changes"]:
            lines.append(_render_change_row(row))
        lines.append("")

    lines += _render_not_evaluated_lines(d["not_evaluated"])

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
    lines += render_footer()
    return "\n".join(lines)


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
            )
            for g in d["groups"]
        )
    )


def build_root_cause_document(
    result: Any,
    *,
    show_only: str | None = None,
    show_recommendation: bool = False,
    show_impact: bool = False,
    severity_config: Any = None,
    contract_evaluation: bool = False,
) -> ReportDocument:
    """``--report-mode root-cause`` (G29 Phase 3 slice 4, ADR-052) as a
    ``ReportDocument``. See this module's own docstring for scope --
    ``compute_root_cause_section``'s own ``RootCauseSectionData`` is already
    JSON-safe (every finding is pre-formatted to a plain string at compute
    time), so this fold needs no new row type of its own."""
    rm = _reporter_markdown()
    preamble, changes = _view_preamble_mapping(
        result,
        "root-cause view",
        show_only=show_only,
        show_recommendation=show_recommendation,
        severity_config=severity_config,
    )

    # G29 Phase 3 slice 3 follow-up: merge --used-by/--required-symbol
    # scoped-only findings into the same root-cause groups (see
    # reporter_markdown._to_markdown_root_cause's own note) -- resolved
    # before the severity table so a scoped run whose only gating issue is
    # one of these can pass the scoped counts below.
    scoped_only, missing_labels, blocks, missing_kind = (
        rm._resolve_scoped_gate_findings(result, severity_config, show_only)
    )

    root_cause_section = rm.compute_root_cause_section(
        changes,
        scoped_only,
        missing_labels,
        blocks,
        missing_kind,
        contract_evaluation=contract_evaluation,
    )
    has_root_cause_entries = root_cause_section is not None

    d: dict[str, Any] = {
        **preamble,
        "report_mode": "root-cause",
        "severity_summary": (
            asdict(
                rm.compute_severity_summary(
                    changes,
                    severity_config,
                    all_changes=list(result.changes),
                    policy=result.policy,
                    kind_sets=result._effective_kind_sets(),
                    policy_file=result.policy_file,
                    scoped_counts=getattr(result, "scoped_severity_counts", None),
                    scoped_blocking_categories=getattr(
                        result, "scoped_blocking_categories", None
                    ),
                )
            )
            if severity_config is not None
            else None
        ),
        "root_cause": (
            asdict(root_cause_section) if root_cause_section is not None else None
        ),
        # Codex review (see reporter_markdown._to_markdown_root_cause's own
        # note): a scoped-only change or missing-contract label can be the
        # *only* displayed finding, so gating the empty message purely on
        # `changes` would produce a contradictory report.
        "empty_message": (
            None
            if (changes or has_root_cause_entries)
            else (
                "_No changes match the current filter._"
                if (show_only and result.changes)
                else "_No ABI changes detected._"
            )
        ),
        "redundancy_note": _opt_asdict(rm.compute_redundancy_note(result)),
        "suppression_note": _opt_asdict(rm.compute_suppression_note(result)),
        "out_of_surface_note": _opt_asdict(rm.compute_out_of_surface_note(result)),
        "show_impact": show_impact,
        "impact_table": (
            _opt_asdict(rm.compute_impact_table(result, displayed_changes=changes))
            if show_impact
            else None
        ),
    }
    return ReportDocument.from_mapping(d)


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
    lines += render_footer()
    return "\n".join(lines)
