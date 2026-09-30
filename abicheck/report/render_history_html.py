# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""HTML projection of a longitudinal history (``project history -o html``).

Input is the history's own published mapping
(``LongitudinalHistoryResult.to_dict()``, the same value ``-o json`` writes),
so the page can say nothing the JSON does not. Every mark on the timeline is
one recorded lifecycle event, every release-to-release label is one recorded
pairwise comparison, and every shaded interval is one recorded coverage gap.

Accessibility: each mark carries a text label as well as a colour and a
shape, the SVG has a title and description, and the complete event list is
rendered as a plain table beside it, so nothing is available only as a
picture.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

from ..html_template import _CSS, render_document, render_footer

#: Timeline rows drawn before the rest are summarised; the events table
#: below the chart is never capped.
MAX_TIMELINE_ENTITIES = 60

_LEFT = 220
_RIGHT_PAD = 40
_TOP = 44
_ROW = 34
_MIN_STEP = 110
_PLOT_WIDTH = 720

_EVENT_LABEL = {
    "first_observed": "present",
    "introduced": "added",
    "reintroduced": "re-added",
    "deprecated": "deprecated",
    "removed": "removed",
}

_HISTORY_CSS = """
.hist-legend { display:flex; flex-wrap:wrap; gap:16px; padding:10px 16px; font-size:.85em; color:#455a64; }
.hist-svg-wrap { overflow-x:auto; padding:6px 16px 12px; }
.hist-svg text { font-family: inherit; font-size:12px; fill:#263238; }
.hist-svg .sub { font-size:11px; fill:#607d8b; }
.hist-gap { fill:#ede7f6; }
.hist-axis { stroke:#cfd8dc; }
table.hist { width:100%; border-collapse:collapse; font-size:.88em; }
table.hist th { background:#fafafa; padding:7px 12px; text-align:left; border-bottom:1px solid #e0e0e0; }
table.hist td { padding:6px 12px; border-bottom:1px solid #f0f0f0; vertical-align:top; }
"""

_COLOR = {
    "add": "#2e7d32",
    "deprecated": "#ef6c00",
    "removed": "#c62828",
    "present": "#78909c",
}


def _versions(history: Mapping[str, Any]) -> list[str]:
    return [str(e["version"]) for e in history.get("entries") or ()]


def _x(index: int, step: float) -> float:
    return _LEFT + index * step


def _entities(
    events: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str, str, list[Mapping[str, Any]]]]:
    """Entities with at least one lifecycle change, in first-change order.

    An entity only ever ``first_observed`` has no history to draw; it is
    counted in the summary instead.
    """
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        by_key.setdefault(str(event["entity_key"]), []).append(event)
    rows = []
    for key, evs in by_key.items():
        if all(e["event"] == "first_observed" for e in evs):
            continue
        evs = sorted(evs, key=lambda e: int(e["index"]))
        rows.append(
            (key, str(evs[-1]["display_name"]), str(evs[0]["entity_kind"]), evs)
        )
    rows.sort(
        key=lambda r: (
            min(int(e["index"]) for e in r[3] if e["event"] != "first_observed"),
            r[1],
        )
    )
    return rows


def _presence_segments(
    evs: Sequence[Mapping[str, Any]], last: int
) -> list[tuple[int, int, int | None]]:
    """``(start, end, deprecated_at)`` spans during which the entity exists."""
    segments: list[tuple[int, int, int | None]] = []
    start: int | None = None
    deprecated: int | None = None
    for e in evs:
        idx = int(e["index"])
        kind = e["event"]
        if kind in ("first_observed", "introduced", "reintroduced"):
            start, deprecated = idx, None
        elif kind == "deprecated":
            if start is None:
                start = 0
            deprecated = idx
        elif kind == "removed":
            segments.append((0 if start is None else start, idx, deprecated))
            start, deprecated = None, None
    if start is not None:
        segments.append((start, last, deprecated))
    return segments


def _marker(kind: str, x: float, y: float) -> str:
    if kind in ("introduced", "reintroduced"):
        return f"<circle cx='{x:.1f}' cy='{y}' r='6' fill='{_COLOR['add']}'/>"
    if kind == "deprecated":
        return f"<rect x='{x - 6:.1f}' y='{y - 6}' width='12' height='12' fill='{_COLOR['deprecated']}'/>"
    if kind == "removed":
        return (
            f"<path d='M{x - 6:.1f} {y - 6} L{x + 6:.1f} {y + 6} M{x + 6:.1f} {y - 6} "
            f"L{x - 6:.1f} {y + 6}' stroke='{_COLOR['removed']}' stroke-width='3' fill='none'/>"
        )
    return f"<circle cx='{x:.1f}' cy='{y}' r='4' fill='#ffffff' stroke='{_COLOR['present']}' stroke-width='2'/>"


def _timeline_svg(
    history: Mapping[str, Any],
    rows: list[tuple[str, str, str, list[Mapping[str, Any]]]],
) -> str:
    h = html.escape
    versions = _versions(history)
    last = len(versions) - 1
    step = max(_MIN_STEP, _PLOT_WIDTH / max(last, 1))
    width = int(_LEFT + step * last + _RIGHT_PAD)
    height = _TOP + _ROW * len(rows) + 20
    parts: list[str] = []

    index_of = {v: i for i, v in enumerate(versions)}
    for gap in (history.get("coverage") or {}).get("gaps") or ():
        a = index_of.get(str(gap.get("from_version")))
        b = index_of.get(str(gap.get("to_version")))
        if a is None or b is None:
            continue
        parts.append(
            f"<rect class='hist-gap' x='{_x(a, step):.1f}' y='{_TOP - 16}' "
            f"width='{(b - a) * step:.1f}' height='{height - _TOP + 6}'>"
            f"<title>Coverage gap {h(str(gap.get('from_version')))} to "
            f"{h(str(gap.get('to_version')))}: {h(str(gap.get('detail')))}</title></rect>"
        )
    for i, version in enumerate(versions):
        x = _x(i, step)
        parts.append(
            f"<line class='hist-axis' x1='{x:.1f}' y1='{_TOP - 14}' x2='{x:.1f}' y2='{height - 8}'/>"
        )
        parts.append(
            f"<text x='{x:.1f}' y='18' text-anchor='middle'>{h(version)}</text>"
        )

    for row, (_, name, kind, evs) in enumerate(rows):
        y = _TOP + row * _ROW + 10
        summary = ", ".join(
            f"{_EVENT_LABEL.get(e['event'], e['event'])} in {e['version']}"
            + (" (evidence uncertain)" if e.get("evidence_uncertain") else "")
            for e in evs
        )
        parts.append(f"<g><title>{h(kind)} {h(name)}: {h(summary)}</title>")
        label = name if len(name) <= 30 else name[:29] + "…"
        parts.append(f"<text x='8' y='{y + 4}'>{h(label)}</text>")
        for start, end, deprecated in _presence_segments(evs, last):
            live_end = end if deprecated is None else deprecated
            parts.append(
                f"<line x1='{_x(start, step):.1f}' y1='{y}' x2='{_x(live_end, step):.1f}' y2='{y}' "
                f"stroke='{_COLOR['add']}' stroke-width='4'/>"
            )
            if deprecated is not None and end > deprecated:
                parts.append(
                    f"<line x1='{_x(deprecated, step):.1f}' y1='{y}' x2='{_x(end, step):.1f}' y2='{y}' "
                    f"stroke='{_COLOR['deprecated']}' stroke-width='4' stroke-dasharray='6 4'/>"
                )
        for e in evs:
            x = _x(int(e["index"]), step)
            parts.append(_marker(str(e["event"]), x, y))
            if e["event"] != "first_observed":
                text = _EVENT_LABEL.get(e["event"], str(e["event"]))
                if e.get("evidence_uncertain"):
                    text += "?"
                parts.append(
                    f"<text class='sub' x='{x:.1f}' y='{y + 20}' text-anchor='middle'>{h(text)}</text>"
                )
        parts.append("</g>")

    return (
        f"<svg class='hist-svg' role='img' aria-labelledby='hist-title hist-desc' "
        f"width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<title id='hist-title'>Lifecycle of {len(rows)} API entities across "
        f"{len(versions)} releases</title>"
        "<desc id='hist-desc'>Each row is one function, variable or type. A solid "
        "line means present, a dashed line means deprecated, a circle marks an "
        "addition, a square a deprecation and a cross a removal. Shaded columns "
        "are intervals with possibly missing releases. The table below lists "
        "every event.</desc>" + "".join(parts) + "</svg>"
    )


def _release_strip(history: Mapping[str, Any]) -> str:
    h = html.escape
    rows = []
    for pair in history.get("pairwise") or ():
        rows.append(
            f"<tr><td>{h(str(pair.get('from_version')))} → {h(str(pair.get('to_version')))}</td>"
            f"<td><strong>{h(str(pair.get('verdict')))}</strong></td>"
            f"<td>{int(pair.get('change_count') or 0)}</td>"
            f"<td>{h(str(pair.get('confidence')))}</td></tr>"
        )
    if not rows:
        return ""
    return (
        "<div class='section' id='releases'><h3>Release steps</h3>"
        "<table class='hist'><thead><tr><th>Step</th><th>Verdict</th><th>Changes</th>"
        "<th>Evidence confidence</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _counts(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in _EVENT_LABEL}
    for e in events:
        counts[str(e["event"])] = counts.get(str(e["event"]), 0) + 1
    return counts


def _events_table(events: Sequence[Mapping[str, Any]]) -> str:
    h = html.escape
    rows = [
        f"<tr><td>{h(str(e['version']))}</td><td>{h(_EVENT_LABEL.get(e['event'], str(e['event'])))}</td>"
        f"<td>{h(str(e['entity_kind']))}</td><td><code>{h(str(e['display_name']))}</code></td>"
        f"<td>{'uncertain' if e.get('evidence_uncertain') else ''}"
        f"{(' ' + h(str(e['detail']))) if e.get('detail') else ''}</td></tr>"
        for e in events
        if e["event"] != "first_observed"
    ]
    if not rows:
        return "<p class='empty'>No API entity was added, deprecated or removed across this history.</p>"
    return (
        "<table class='hist'><thead><tr><th>Release</th><th>Event</th><th>Kind</th>"
        "<th>Entity</th><th>Notes</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _compliance(history: Mapping[str, Any]) -> str:
    h = html.escape
    findings = history.get("deprecation_compliance") or ()
    if not findings:
        return ""
    keys = sorted({k for f in findings for k in f})
    head = "".join(f"<th>{h(k.replace('_', ' '))}</th>" for k in keys)
    body = "".join(
        "<tr>" + "".join(f"<td>{h(str(f.get(k, '')))}</td>" for k in keys) + "</tr>"
        for f in findings
    )
    return (
        "<div class='section' id='deprecation'><h3>Deprecation policy</h3>"
        f"<table class='hist'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_history_html(history: Mapping[str, Any]) -> str:
    """The complete HTML page for one longitudinal history."""
    h = html.escape
    library = str(history.get("library") or "library")
    versions = _versions(history)
    events = list(history.get("events") or ())
    counts = _counts(events)
    rows = _entities(events)
    shown = rows[:MAX_TIMELINE_ENTITIES]
    gaps = (history.get("coverage") or {}).get("gaps") or ()

    summary = (
        f"{counts['introduced']} added, {counts['reintroduced']} re-added, "
        f"{counts['deprecated']} deprecated, {counts['removed']} removed; "
        f"{counts['first_observed']} entities already present in the first release."
    )
    omitted = len(rows) - len(shown)
    timeline = (
        _timeline_svg(history, shown)
        if shown
        else "<p class='empty'>No lifecycle changes to draw.</p>"
    )
    omitted_note = (
        f"<p class='empty'>{omitted} more entities with lifecycle changes are not "
        "drawn; every event is listed in the table below.</p>"
        if omitted
        else ""
    )
    gap_note = (
        "<p class='empty'>"
        + " ".join(
            f"Possible missing release between {h(str(g.get('from_version')))} and "
            f"{h(str(g.get('to_version')))}: {h(str(g.get('detail')))}."
            for g in gaps
        )
        + "</p>"
        if gaps
        else ""
    )
    legend = (
        "<div class='hist-legend'>"
        f"<span><svg width='14' height='14'><circle cx='7' cy='7' r='6' fill='{_COLOR['add']}'/></svg> added</span>"
        f"<span><svg width='14' height='14'><rect x='1' y='1' width='12' height='12' fill='{_COLOR['deprecated']}'/></svg> deprecated</span>"
        f"<span><svg width='14' height='14'><path d='M1 1 L13 13 M13 1 L1 13' stroke='{_COLOR['removed']}' stroke-width='3'/></svg> removed</span>"
        "<span>solid line: present · dashed: deprecated · shaded: possible missing release · ?: evidence uncertain</span>"
        "</div>"
    )
    body = f"""
<div class="header">
  <h1>API History — {h(library)}</h1>
  <div class="meta">{len(versions)} releases: {" → ".join(h(v) for v in versions)}</div>
</div>
<div class="section" id="summary"><h3>Summary</h3><p style="padding:10px 16px;margin:0">{h(summary)}</p></div>
{_release_strip(history)}
<div class="section" id="timeline"><h3>Lifecycle timeline</h3>{legend}
<div class="hist-svg-wrap">{timeline}</div>{omitted_note}{gap_note}</div>
<div class="section" id="events"><h3>Lifecycle events</h3>{_events_table(events)}</div>
{_compliance(history)}
{render_footer("API history")}
"""
    return render_document(
        title=f"API History: {h(library)}", body=body, css=_CSS + _HISTORY_CSS
    )
