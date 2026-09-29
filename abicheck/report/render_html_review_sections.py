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
