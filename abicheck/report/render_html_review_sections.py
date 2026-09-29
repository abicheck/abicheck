# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""HTML projections for the versioning-policy and consumer-impact sections.

Both take the JSON-shaped mapping the compute half already produced
(``PolicyAcceptance.to_dict()`` and ``UseCaseImpact.to_dict()``, the same
values the JSON report carries) and only format it. ``None`` renders
nothing: a section the run never evaluated is absent, not empty.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any


def render_versioning_policy_html(acceptance: Mapping[str, Any] | None) -> str:
    """The project's versioning-policy verdict on this release.

    Acceptance is reported beside the compatibility verdict, never in place
    of it, so the card states both the policy and the fact it judged.
    """
    if acceptance is None:
        return ""
    h = html.escape
    accepted = bool(acceptance.get("accepted"))
    css = "section-added" if accepted else "section-removed"
    label = "Accepted" if accepted else "Not accepted"
    return (
        f"<div class='section {css}' id='versioning-policy'>"
        f"<h3>Versioning policy: {label}</h3>"
        "<table class='summary-table'><tbody>"
        f"<tr><th>Promise</th><td><code>{h(str(acceptance.get('promise')))}</code></td></tr>"
        f"<tr><th>Enforcement</th><td><code>{h(str(acceptance.get('enforcement')))}</code></td></tr>"
        f"<tr><th>Result</th><td>{h(str(acceptance.get('detail')))}</td></tr>"
        "</tbody></table>"
        "</div>"
    )


def render_use_case_impact_html(impact: Mapping[str, Any] | None) -> str:
    """Findings attributed to each declared use case.

    This enriches the report and never replaces its global result: the
    unattributed count is always shown, and a finding reached by several
    use cases is listed under each without being counted twice in the
    totals, which come from the attribution itself.
    """
    if impact is None:
        return ""
    h = html.escape
    by_use_case: Mapping[str, Any] = impact.get("by_use_case") or {}
    rows: list[str] = []
    for entry in impact.get("use_cases") or ():
        name = str(entry.get("use_case"))
        changes = by_use_case.get(name) or ()
        if changes:
            status = "<span class='cat-badge'>affected</span>"
            detail = "<br>".join(
                f"<code>{h(str(c.get('symbol')))}</code> ({h(str(c.get('kind')))})"
                for c in changes
            )
        else:
            status = "unaffected"
            detail = "none"
        unresolved = entry.get("unresolved_entrypoints") or ()
        if unresolved:
            detail += (
                "<br><em>unresolved entrypoints: "
                + ", ".join(f"<code>{h(str(e))}</code>" for e in unresolved)
                + "</em>"
            )
        rows.append(
            f"<tr><td><code>{h(name)}</code></td><td>{len(changes)}</td>"
            f"<td>{status}</td><td>{detail}</td></tr>"
        )
    total = impact.get("total_changes", 0)
    unattributed = impact.get("unattributed_changes", 0)
    body = (
        "<table class='changes'><thead><tr><th>Use case</th><th>Changes</th><th>Status</th>"
        "<th>Findings</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        if rows
        else "<p class='empty'>The manifest declares no use cases.</p>"
    )
    return (
        "<div class='section' id='use-case-impact'>"
        f"<h3>Consumer impact ({h(str(impact.get('manifest')))})</h3>"
        f"{body}"
        f"<p class='empty'>{unattributed} of {total} change(s) reached by no "
        "declared entrypoint. This shows no proof of impact, not proof of no "
        "impact.</p>"
        "</div>"
    )


def _surface_declaration_html(entry: Mapping[str, Any]) -> str:
    h = html.escape
    old = entry.get("old_declaration")
    new = entry.get("new_declaration")
    if old and new and old != new:
        decl = f"<code>{h(str(old))}</code> &rarr; <code>{h(str(new))}</code>"
    elif new:
        decl = f"<code>{h(str(new))}</code>"
    elif old:
        decl = f"<code>{h(str(old))}</code>"
    else:
        decl = h(str(entry.get("description") or ""))
    loc = entry.get("source_location")
    if loc:
        decl += f" <span class='empty'>({h(str(loc))})</span>"
    return decl


def render_surface_changes_html(
    section: Mapping[str, Any] | None, *, limit: int | None = None
) -> str:
    """Additions, removals and modifications, each with its declarations.

    Every group is capped independently and states its own omitted count,
    exactly like the Markdown form; a section with nothing in any group
    renders nothing, since the report already says there were no changes.
    """
    from .surface_changes import MAX_COMPACT_SURFACE_ITEMS

    if not section or not section.get("total"):
        return ""
    h = html.escape
    cap = max(0, MAX_COMPACT_SURFACE_ITEMS if limit is None else limit)
    parts: list[str] = []
    for label, key in (
        ("Additions", "additions"),
        ("Removals", "removals"),
        ("Modifications", "modifications"),
    ):
        entries = list(section.get(key) or ())
        parts.append(f"<h4>{label} ({len(entries)})</h4>")
        if not entries:
            parts.append("<p class='empty'>none</p>")
            continue
        shown = entries[:cap]
        rows = "".join(
            f"<tr><td><code>{h(str(e.get('symbol')))}</code></td>"
            f"<td>{h(str(e.get('verdict')))}</td>"
            f"<td>{_surface_declaration_html(e)}</td></tr>"
            for e in shown
        )
        parts.append(
            "<table class='changes'><thead><tr><th>Symbol</th><th>Verdict</th>"
            f"<th>Declaration (old &rarr; new)</th></tr></thead><tbody>{rows}</tbody></table>"
        )
        omitted = len(entries) - len(shown)
        if omitted:
            quantifier = f"{omitted} more" if shown else f"all {omitted}"
            parts.append(
                f"<p class='empty surface-omitted'>&hellip; {quantifier} "
                f"{label.lower()} omitted (the JSON report lists every entry).</p>"
            )
    return (
        "<div class='section' id='surface-changes'>"
        f"<h3>Surface changes ({int(section.get('total') or 0)})</h3>"
        + "".join(parts)
        + "</div>"
    )
