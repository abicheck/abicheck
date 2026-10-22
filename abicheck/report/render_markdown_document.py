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

"""Markdown's whole-document ``ReportDocument`` construction and projection:
review digest and the full-mode (``to_markdown``'s default view) report.

ADR-061 Phase 2 item 1: Markdown is the prose format whose renderer did not
used to fully consume one canonical, JSON-shaped ``ReportDocument`` -- unlike
JSON, SARIF, JUnit, ``--stat`` (``reporter_markdown.to_stat`` already builds
one via ``report/render_text.py``'s ``render_stat_document``), and HTML
(``html_report.build_html_document`` + ``report/render_html_document.py``).
This module (plus its sibling, ``render_markdown_alternate.py``, which owns
``--report-mode leaf``/``root-cause`` -- split out once the combined module
passed the architecture check's new-file 800-line ceiling, the same reason
``render_html.py``/``render_html_document.py`` are two files) is Markdown's
counterpart to that HTML split. Two views land here:

- ``-o review=...``'s digest (``build_review_digest_document``/
  ``render_review_digest_document``) -- already had the cleanest compute/
  render pair in ``reporter_markdown.py`` (``compute_review_digest`` ->
  ``render_markdown.render_review_digest``) of anything in that module, and
  every field is already JSON-safe, so the document wrap is a direct fold.
- The full-mode report (``markdown_compute.build_markdown_document``/
  ``render_markdown_document``, ``report_mode == "full"``) -- ``to_markdown``'s
  default view.

Both ``build_*`` functions fold a dataclass (or several) into a JSON-shaped
mapping and wrap it as a ``ReportDocument``; the matching ``render_*``
reconstructs the dataclass(es) from the document's own mapping (a document
round trip turns every dataclass into a plain mapping and tuple into a list,
same as HTML's ``_*_from_mapping`` helpers) and calls the existing,
unmodified ``render_markdown.py`` render functions -- no rendering logic
duplicated here, with one exception: the full-mode report's per-``Change``
sections (severity-grouped changes, "Not Evaluated") need a JSON-safe row
shape ``Change`` itself isn't (mirroring HTML's ``ChangeRow``, which solved
the identical problem for HTML's own per-row table data) --
``_change_row``/``_row_contract_tag``/``_render_change_row``/
``_render_change_row_oneline`` below are that row type's compute/render
halves, kept local to this module rather than added to ``render_markdown.py``
(which has essentially no headroom left under its own 800-line cap) since
``_format_change_md``/``_format_change_md_oneline``/``_format_leaf_type_change``
there keep serving their other existing caller (the scoped-gate text append
in ``cli_compare_fold.py``) unconverted and unchanged. ``_not_evaluated_
mapping``/``_render_not_evaluated_lines`` are the identical split for the
"## Not Evaluated (Contract)" section every view (this module's full mode
and ``render_markdown_alternate.py``'s leaf/root-cause modes alike) shares
verbatim -- ``render_markdown_alternate.py`` imports these row/section
helpers from here rather than duplicating them (a same-package,
one-directional dependency, not a cycle).

Lives in this package, not the near-full flat ``reporter_markdown.py``
(``architecture/debt.yaml``'s ``no_growth`` baseline there had ~18 lines of
headroom before ``to_markdown``'s own full-mode body collapsed into a
five-line call to this module, freeing most of it back) or a new flat
``abicheck/reporter_*.py`` sibling (``architecture/modules.yaml``'s
``frozen_root_families`` closes that flat namespace to new members, same as
``report/scoped_gate.py``). Imports ``reporter_markdown.py`` (a
``layers.report.legacy_paths`` member, same layer as this file) statically
at module level -- ADR-063 T10 moved the entry points that used to call
*into* this module (``to_markdown``/``to_review_digest``) out of
``reporter_markdown.py`` and into ``report/dispatch_markdown.py``, which
retired the only edge that ran the other way, so the ``importlib``
indirection this module previously needed to avoid closing that into a
real cycle is gone too.

Every view's byte-for-byte output is unchanged by this split --
``tests/test_golden_output.py``/``tests/test_golden_review_digest.py`` pin
the exact text every ``build_*``/``render_*`` pair here must keep
reproducing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# ADR-063 T10: static now that `reporter_markdown.py` no longer imports
# anything from this module (see this module's own docstring) -- the
# `_reporter_markdown()` importlib indirection this file used to need is
# retired; every call site below keeps the same `rm.compute_*(...)` shape
# it always had, just resolved through this module-level import.
from .contract_conflicts_markdown import (
    ContractConflictRow,
    ContractConflictsSection,
    render_contract_conflicts_section,
)
from .cross_source_evolution import CROSS_SOURCE_EVOLUTION_MD_TAGS
from .disposition_audit import (
    DispositionAudit,
    render_disposition_audit_section,
)
from .document import ReportDocument
from .edge_coverage_section import (
    edge_coverage_section_from_mapping,
)
from .kind_rollup import KindRollup, render_kind_rollups
from .pattern_modulations_markdown import render_pattern_modulations_from_mapping
from .render_edge_coverage import render_edge_coverage_markdown
from .render_markdown import (
    ConfidenceSection,
    EnvironmentDriftEntry,
    EnvironmentDriftSection,
    HeadlineTable,
    ImpactRootEntry,
    ImpactTable,
    LibraryFilesSection,
    OutOfSurfaceNote,
    PolicySection,
    RecommendationSection,
    RedundancyNote,
    RttiNote,
    SeverityRow,
    SeveritySummary,
    SuppressedEntry,
    SuppressionNote,
    render_confidence_section,
    render_environment_drift_section,
    render_footer,
    render_headline_table,
    render_impact_table,
    render_library_files_section,
    render_out_of_surface_note,
    render_policy_section,
    render_recommendation_section,
    render_redundancy_note,
    render_rtti_note,
    render_severity_summary,
    render_suppression_note,
)
from .review_digest_document import (
    build_review_digest_document as build_review_digest_document,
    render_review_digest_document as render_review_digest_document,
)
from .surface_changes import (
    SurfaceChangeSection,
    render_surface_changes_section,
)
from .view import render_show_only_cli_hint

# ---------------------------------------------------------------------------
# Full-mode report (to_markdown's default view)
# ---------------------------------------------------------------------------


def _row_contract_tag(row: Mapping[str, Any]) -> str | None:
    """The ``<relevance> (<reason_code>), assurance: <level>`` tag for one
    already-JSON-safe ``_change_row``, or ``None`` when the row was never
    contract-stamped. Shared by every row-based renderer below that shows a
    contract decision (mirrors ``render_markdown._contract_decision_text``,
    the ``Change``-based equivalent)."""
    if row.get("contract_relevance") is None:
        return None
    tag = str(row["contract_relevance"])
    if row.get("contract_reason_code"):
        tag += f" ({row['contract_reason_code']})"
    if row.get("contract_assurance") is not None:
        tag += f", assurance: {row['contract_assurance']}"
    return tag


def _row_cross_source_evolution_suffix(row: Mapping[str, Any]) -> str:
    """``_change_row``-based counterpart of ``cross_source_evolution.
    cross_source_evolution_md_suffix`` -- same tag, read from the JSON-safe
    row instead of a live ``Change``."""
    cse = row.get("cross_source_evolution")
    if cse is None:
        return ""
    tag = CROSS_SOURCE_EVOLUTION_MD_TAGS.get(cse, cse)
    return f"\n  > Cross-source hygiene: {tag}"


def _row_use_cases_suffix(row: Mapping[str, Any]) -> str:
    """``compare --use-cases``'s per-finding note; empty when no use case
    reaches the finding or the attribution never ran."""
    names = row.get("affected_use_cases")
    return f"\n  > Affects use cases: {', '.join(names)}" if names else ""


def _render_change_row_oneline(row: Mapping[str, Any]) -> str:
    line = f"- **{row['kind']}**: {row['description']}"
    correlated = row.get("correlated_change_kind")
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    line += _row_cross_source_evolution_suffix(row)
    return line + _row_use_cases_suffix(row)


def _render_change_row(row: Mapping[str, Any]) -> str:
    old_val, new_val = row.get("old_value"), row.get("new_value")
    old_new = ""
    if old_val is not None and new_val is not None:
        old_new = f" (`{old_val}` → `{new_val}`)"
    elif old_val is not None:
        old_new = f" (`{old_val}`)"
    elif new_val is not None:
        old_new = f" (`{new_val}`)"
    line = f"- **{row['kind']}**: {row['description']}{old_new}"
    loc = row.get("source_location")
    if loc:
        line += f" — `{loc}`"
    impact = row.get("impact")
    if impact:
        line += f"\n  > {impact}"
    caused_count = row.get("caused_count") or 0
    if caused_count > 0:
        line += f"\n  > {caused_count} derived change(s) collapsed"
    affected = row.get("affected_symbols")
    if affected:
        names = ", ".join(f"`{s}`" for s in affected[:5])
        suffix = f" (+{len(affected) - 5} more)" if len(affected) > 5 else ""
        line += f"\n  > Affected symbols: {names}{suffix}"
    tag = _row_contract_tag(row)
    if tag is not None:
        line += f"\n  > Contract: {tag}"
    correlated = row.get("correlated_change_kind")
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    line += _row_cross_source_evolution_suffix(row)
    return line + _row_use_cases_suffix(row)


def _render_not_evaluated_lines(d: Mapping[str, Any] | None) -> list[str]:
    """Render a ``_not_evaluated_mapping()`` result -- the "## Not Evaluated
    (Contract)" section every view shares verbatim
    (``render_markdown.render_not_evaluated_section``'s own heading/preamble
    text, kept identical here since that function takes a real
    ``NotEvaluatedSection`` of ``Change`` objects rather than JSON-safe rows).
    """
    if d is None:
        return []
    lines: list[str] = [
        "## 🔍 Not Evaluated (Contract)",
        "",
        "> These findings were detected but **not scored** by compatibility",
        "> policy: each is either proven outside the declared contract or",
        "> unresolved for want of evidence. They contribute nothing",
        "> to the verdict or the gate. Incomplete evidence is reported",
        "> separately on the contract-coverage axis, which has its own exit",
        "> code — uncertainty is never silently treated as compatible.",
        "",
    ]
    for entry in d["entries"]:
        lines.append(_render_change_row_oneline(entry["row"]))
        lines.append(f"  > Contract: {entry['label']}{entry['suffix']}")
    lines.append("")
    return lines


def _contract_conflicts_section_from_mapping(
    d: Mapping[str, Any] | None,
) -> ContractConflictsSection | None:
    if d is None:
        return None
    return ContractConflictsSection(
        rows=tuple(ContractConflictRow(**row) for row in d.get("rows", ()))
    )


def _render_disposition_audit_from_mapping(d: Any) -> list[str]:
    """Rebuild and render the audit section from a document mapping.

    Shared by all three Markdown modes; ``None``/absent renders nothing, so a
    document built before this field existed still projects cleanly.
    """
    if not isinstance(d, Mapping):
        return []
    return render_disposition_audit_section(DispositionAudit.from_dict(d))


def _render_surface_changes_from_mapping(d: Any) -> list[str]:
    """Rebuild and render the surface-changes section from a document
    mapping. Shared by all three Markdown modes, mirroring
    :func:`_render_disposition_audit_from_mapping`; ``None``/absent renders
    nothing, so a document built before this field existed still projects
    cleanly."""
    if not isinstance(d, Mapping):
        return []
    return render_surface_changes_section(SurfaceChangeSection.from_dict(d))


def _render_review_groups_from_mapping(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return []
    lines = ["", "## Related review groups", ""]
    for index, group in enumerate(value, 1):
        if not isinstance(group, Mapping):
            continue
        lines += [
            f"### {index}. `{group.get('display_name', '?')}`",
            "",
            f"- **Observed:** {group.get('transition', 'unknown transition')}",
            f"- **Implication:** {group.get('consequence', 'see member findings')}",
            f"- **Action:** {group.get('action', 'review member findings')}",
            f"- **Gate contribution:** {group.get('gating_findings', 0)} finding(s)",
            f"- **Members:** {', '.join(f'`{item}`' for item in group.get('member_finding_ids', []))}",
            f"- **Exact symbols:** {', '.join(f'`{item}`' for item in group.get('exact_symbols', []))}",
            "",
        ]
    return lines


def _suppression_note_from_mapping(
    d: Mapping[str, Any] | None,
) -> SuppressionNote | None:
    if d is None:
        return None
    return SuppressionNote(
        suppressed_count=d["suppressed_count"],
        entries=tuple(SuppressedEntry(**e) for e in d["entries"]),
    )


def _impact_table_from_mapping(d: Mapping[str, Any] | None) -> ImpactTable | None:
    if d is None:
        return None
    return ImpactTable(
        root_entries=tuple(ImpactRootEntry(**e) for e in d["root_entries"]),
        direct_removals=d["direct_removals"],
    )


def _kind_rollups_from_mapping(raw: object) -> tuple[KindRollup, ...]:
    """The document's rollup mappings back as :class:`KindRollup` values."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        KindRollup(
            kind=r["kind"],
            count=r["count"],
            sample_symbols=tuple(r["sample_symbols"]),
        )
        for r in raw
    )


def _environment_drift_from_mapping(
    d: Mapping[str, Any] | None,
) -> EnvironmentDriftSection | None:
    if d is None:
        return None
    return EnvironmentDriftSection(
        entries=tuple(EnvironmentDriftEntry(**e) for e in d["entries"])
    )


def render_markdown_document(doc: ReportDocument) -> str:
    """Project a full-mode ``ReportDocument`` to its Markdown text."""
    d: dict[str, Any] = doc.to_mapping()
    lines: list[str] = render_headline_table(HeadlineTable(**d["headline"]))
    lines += render_rtti_note(
        None if d["rtti_note"] is None else RttiNote(**d["rtti_note"])
    )
    lines += render_confidence_section(
        None if d["confidence"] is None else ConfidenceSection(**d["confidence"])
    )
    lines += render_edge_coverage_markdown(
        edge_coverage_section_from_mapping(d.get("edge_coverage"))
    )
    lines += render_contract_conflicts_section(
        _contract_conflicts_section_from_mapping(d.get("contract_conflicts"))
    )
    lines += render_policy_section(PolicySection(**d["policy"]))
    if d["recommendation"] is not None:
        lines += render_recommendation_section(
            RecommendationSection(**d["recommendation"])
        )
    if d["severity_summary"] is not None:
        summary_rows = tuple(
            SeverityRow(**row) for row in d["severity_summary"]["rows"]
        )
        lines += render_severity_summary(SeveritySummary(rows=summary_rows))
    if d["show_only_note"] is not None:
        note = d["show_only_note"]
        # Codex review (PR #1154 second follow-up: "Render repeated show
        # groups as repeated view options") -- render each `;`-joined
        # internal group as its own `--view show=...` token rather than
        # echoing the raw internal separator into a suggested invocation.
        cli_hint = render_show_only_cli_hint(note["show_only"])
        lines.append(
            f"> Filtered by: `{cli_hint}` "
            f"({note['shown']} of {note['total']} changes shown)"
        )
        lines.append("")
    if d["library_files"] is not None:
        lines += render_library_files_section(LibraryFilesSection(**d["library_files"]))
    if d.get("env_matrix_source_sha256") is not None:
        lines.append(f"- Deployment floor digest: `{d['env_matrix_source_sha256']}`")
        lines.append("")
    for group in d["severity_groups"]:
        lines += [group["heading"], ""]
        if group["note_lines"]:
            lines += list(group["note_lines"])
            lines.append("")
        fmt = _render_change_row_oneline if group["oneline"] else _render_change_row
        for row in group["rows"]:
            lines.append(fmt(row))
        lines += render_kind_rollups(_kind_rollups_from_mapping(group.get("rollups")))
        lines.append("")
    lines += _render_not_evaluated_lines(d["not_evaluated"])
    lines += render_environment_drift_section(
        _environment_drift_from_mapping(d["environment_drift"])
    )
    if d["empty_message"] is not None:
        lines.append(d["empty_message"])
    lines += _render_disposition_audit_from_mapping(d.get("disposition_audit"))
    lines += render_pattern_modulations_from_mapping(d.get("pattern_modulations"))
    lines += _render_review_groups_from_mapping(d.get("review_groups"))
    if not d.get("review_groups"):
        lines += _render_surface_changes_from_mapping(d.get("surface_changes"))
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
        lines.append("")
        lines += render_impact_table(_impact_table_from_mapping(d["impact_table"]))
    lines += render_footer()
    # Every Markdown report format ends with a trailing newline (POSIX
    # convention; a bare `"\n".join(lines)` never carried one).
    text = "\n".join(lines).rstrip() + "\n"
    if d["demangle"]:
        from ..demangle import demangle_text

        text = demangle_text(text, escape_table_pipes=True)  # Foo::operator|
    return text
