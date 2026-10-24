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

import re
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
from .markdown_budget import (
    DEMANGLED_NAME_LIMIT,
    EXAMPLE_LIMIT,
    NOTE_ITEM_LIMIT,
    clip,
    clip_name,
    example_names,
    more_line,
    split_items,
)
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
from .review_groups import gating_totals
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


def _row_symbol_suffix(row: Mapping[str, Any]) -> str:
    """Name the row's symbol when its description omits it: the folded
    review groups list only example names, so the row is the one place
    every finding's symbol still appears."""
    symbol = row.get("symbol")
    if symbol and symbol not in row["description"]:
        return f" — `{clip_name(symbol)}`"
    return ""


def _render_change_row_oneline(row: Mapping[str, Any]) -> str:
    line = clip(f"- **{row['kind']}**: {row['description']}")
    line += _row_symbol_suffix(row)
    correlated = row.get("correlated_change_kind")
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    line += _row_cross_source_evolution_suffix(row)
    return line + _row_use_cases_suffix(row)


def _render_change_row(row: Mapping[str, Any]) -> str:
    old_val, new_val = row.get("old_value"), row.get("new_value")
    if old_val is not None:
        old_val = clip_name(old_val)
    if new_val is not None:
        new_val = clip_name(new_val)
    old_new = ""
    if old_val is not None and new_val is not None:
        old_new = f" (`{old_val}` → `{new_val}`)"
    elif old_val is not None:
        old_new = f" (`{old_val}`)"
    elif new_val is not None:
        old_new = f" (`{new_val}`)"
    line = f"- **{row['kind']}**: {clip(row['description'])}{old_new}"
    line += _row_symbol_suffix(row)
    loc = row.get("source_location")
    if loc:
        line += f" — `{clip_name(loc)}`"
    impact = row.get("impact")
    if impact:
        line += f"\n  > {impact}"
    caused_count = row.get("caused_count") or 0
    if caused_count > 0:
        line += f"\n  > {caused_count} derived change(s) collapsed"
    affected = row.get("affected_symbols")
    if affected:
        names = ", ".join(f"`{clip_name(s)}`" for s in affected[:EXAMPLE_LIMIT])
        hidden = len(affected) - EXAMPLE_LIMIT
        suffix = f" (+{hidden} more)" if hidden > 0 else ""
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
    # Never gating, so bounded like every other non-gating list.
    shown, hidden = split_items(d["entries"], NOTE_ITEM_LIMIT)
    for entry in shown:
        lines.append(_render_change_row_oneline(entry["row"]))
        lines.append(clip(f"  > Contract: {entry['label']}{entry['suffix']}"))
    if (more := more_line(hidden, "changes")) is not None:
        lines.append(more)
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


# Review-group caps for the Markdown view. The JSON ``review_groups`` block
# keeps every group with its members and exact symbols; these only bound the
# prose. Without them a ``std::`` surface yields one group per symbol, each
# with multi-KB demangled names: 1000 symbols was a 650 KB section. Line,
# name and example limits are the shared ``markdown_budget`` ones.
_REVIEW_FAMILY_LIMIT = 50  # families rendered before "... and N more"


def _review_namespace_root(display_name: str) -> str:
    """The namespace a review group's name lives under (``std`` for
    ``std::__cxx11::basic_string<...>::f(...)``), ignoring template arguments
    and parameter lists so ``foo<std::string>`` is not read as ``foo<std``."""
    if display_name.startswith("_Z"):
        # A mangled display name (a function-local static has no qualified
        # name) still has a namespace: demangle before reading it, so the
        # family label agrees with the demangled examples beside it.
        from ..demangle import demangle

        demangled = demangle(display_name)
        if not demangled:
            return "(undemangled)"
        display_name = demangled
    base = re.split(r"[<(]", display_name, maxsplit=1)[0]
    if "::" not in base:
        return "(global)"
    return base.split("::", 1)[0] or "(global)"


def _review_group_families(
    groups: list[Mapping[str, Any]],
) -> list[tuple[tuple[str, str], dict[str, Any]]]:
    """Collapse groups that share a namespace root and an observed transition.

    Consequence and action are not part of the key: for ordinary findings the
    consequence is that finding's own description, which names the symbol, so
    keying on it would leave every symbol in its own family again. They are
    shown only when every member of the family agrees; otherwise the family
    says so and points at the JSON. Each family keeps counts and a few example
    names; nothing is dropped, only folded."""
    from ..demangle import demangle_batch

    # One c++filt call for every mangled name, so _review_namespace_root's
    # per-name demangle() is a cache hit instead of a process per group.
    demangle_batch(
        [
            name
            for name in (str(g.get("display_name", "")) for g in groups)
            if name.startswith("_Z")
        ]
    )
    families: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        name = str(group.get("display_name", "?"))
        key = (
            _review_namespace_root(name),
            str(group.get("transition", "unknown transition")),
        )
        consequence = str(group.get("consequence", "see member findings"))
        action = str(group.get("action", "review member findings"))
        family = families.setdefault(
            key,
            {
                "groups": 0,
                "gating": 0,
                "members": 0,
                "examples": [],
                "consequence": consequence,
                "action": action,
                "uniform": True,
            },
        )
        family["uniform"] = (
            family["uniform"]
            and consequence == family["consequence"]
            and action == family["action"]
        )
        family["groups"] += 1
        family["gating"] += gating_totals((group,))[0]
        family["members"] += len(group.get("member_finding_ids", []) or [])
        if len(family["examples"]) < EXAMPLE_LIMIT:
            family["examples"].append(name)
        if family["groups"] == 1:
            family["first"] = group
    return sorted(families.items(), key=lambda item: (-item[1]["gating"], item[0]))


def _capped_list(items: Any) -> str:
    return example_names(list(items or []))


def _render_review_groups_from_mapping(value: Any) -> list[str]:
    """Related review groups, family-collapsed and capped (see the constants
    above). The bound comes from the folding, not from the verdict: a family
    keeps counts and a few names whatever the run's verdict, so a NO_CHANGE
    run stays small without dropping the only line that names a change."""
    if not isinstance(value, list) or not value:
        return []
    groups = [group for group in value if isinstance(group, Mapping)]
    if not groups:
        return []
    families = _review_group_families(groups)
    gating, _ = gating_totals(groups)
    family_word = "family" if len(families) == 1 else "families"
    summary = (
        f"{len(groups)} review group(s) across {len(families)} {family_word}; "
        f"{gating} gating finding(s). Per-group members and exact symbols are "
        "in the JSON `review_groups`."
    )
    lines = ["", "## Related review groups", "", summary, ""]
    for index, ((root, transition), family) in enumerate(
        families[:_REVIEW_FAMILY_LIMIT], 1
    ):
        if family["groups"] == 1:
            # A lone group keeps its own name and member lines, as before, so a
            # small report reads the same; only a family of several is folded.
            group = family["first"]
            lines += [
                clip(f"### {index}. `{clip_name(group.get('display_name', '?'))}`"),
                "",
                clip(f"- **Observed:** {transition}"),
                clip(f"- **Implication:** {family['consequence']}"),
                clip(f"- **Action:** {family['action']}"),
                f"- **Gate contribution:** {family['gating']} finding(s)",
                clip(f"- **Members:** {_capped_list(group.get('member_finding_ids'))}"),
                clip(
                    f"- **Exact symbols:** {_capped_list(group.get('exact_symbols'))}"
                ),
                "",
            ]
            continue
        examples = example_names(family["examples"], total=family["groups"])
        if family["uniform"]:
            consequence, action = family["consequence"], family["action"]
        else:
            consequence = action = "varies by member; see the JSON `review_groups`"
        lines += [
            clip(
                f"### {index}. `{clip_name(root)}` "
                f"({family['groups']} group(s), {family['members']} member finding(s))"
            ),
            "",
            clip(f"- **Observed:** {transition}"),
            clip(f"- **Implication:** {consequence}"),
            clip(f"- **Action:** {action}"),
            f"- **Gate contribution:** {family['gating']} finding(s)",
            clip(f"- **Examples:** {examples}"),
            "",
        ]
    hidden = len(families) - _REVIEW_FAMILY_LIMIT
    if hidden > 0:
        lines += [
            f"… and {hidden} more famil{'y' if hidden == 1 else 'ies'} omitted; "
            "see the JSON `review_groups`.",
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

        text = demangle_text(  # escape: Foo::operator|
            text, escape_table_pipes=True, max_demangled=DEMANGLED_NAME_LIMIT
        )
    return text
