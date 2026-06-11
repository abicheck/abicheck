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

"""Sticky GitHub PR-comment rendering.

Renders a single, updatable PR comment from an abicheck JSON report
(``compare``, ``compare-release`` or ``appcompat`` mode). The comment is a
*content* channel and is independent of the check's red/green gate: ABI/API
breaks turn the step red via exit codes (see ``action/run.sh``), while this
comment groups every finding into three buckets so a reviewer sees, in one
place:

* **Breaking** — clear ABI breaks (and gated source breaks);
* **Needs review** — source breaks / risk a human should sign off on;
* **Safe** — additions and policy/surface-scoped compatible removals.

"Safe" here is a pure mirror of the severity the checker already assigned
(``severity`` field in the JSON, which honours public-surface scoping and the
active policy) — this module never re-classifies anything.

The body carries a hidden HTML :data:`MARKER` so the action can find and
update the same comment across runs, and surfaces the scanned commit SHA in
both the header and the footer.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .checker_policy import ADDITION_KINDS

# Kind value strings that constitute new public-API surface (the severity
# "addition" category). Sourced from the authoritative ADDITION_KINDS so kinds
# that don't end in "_added" (e.g. type_field_added_compatible,
# experimental_graduated) are classified correctly.
_ADDITION_KIND_VALUES = frozenset(k.value for k in ADDITION_KINDS)

# Hidden marker used to find-and-update the sticky comment across runs.
MARKER = "<!-- abicheck-sticky-report -->"

DETAIL_LEVELS = ("summary", "standard", "full")
POST_MODES = ("always", "changes", "never")

# Severity tokens emitted in the JSON report (`reporter._effective_severity_label`)
# routed into the three reviewer-facing buckets.
_SEVERITY_BUCKET = {
    "breaking": "breaking",
    "api_break": "review",
    "risk": "review",
    "compatible": "safe",
    "unknown": "review",
}

# Verdict strings → reviewer bucket, for release-global findings (bundle /
# probe-matrix) that carry no per-item severity, only a section verdict.
_VERDICT_BUCKET = {
    "BREAKING": "breaking",
    "API_BREAK": "review",
    "COMPATIBLE_WITH_RISK": "review",
    "COMPATIBLE": "safe",
    "NO_CHANGE": "safe",
}

# Per-detail row caps for the "standard" level (full = uncapped).
_STANDARD_ROW_CAP = 25
_SAFE_SYMBOLS_PER_KIND = 12
# Member symbols listed inline in an aggregated (API-grouped) Breaking/Review row.
_GROUP_MEMBERS_INLINE = 8

# GitHub rejects issue/PR comment bodies longer than 65,536 characters. Render
# within a budget below that; if the body overflows, downgrade the detail level
# (full → standard → summary) and finally hard-truncate so we never exceed it.
GITHUB_COMMENT_LIMIT = 65536
_BODY_BUDGET = 64000
_DETAIL_DOWNGRADE = {
    "full": ("full", "standard", "summary"),
    "standard": ("standard", "summary"),
    "summary": ("summary",),
}

_VERDICT_EMOJI = {
    "BREAKING": "❌",
    "API_BREAK": "⚠️",
    "COMPATIBLE_WITH_RISK": "⚠️",
    "COMPATIBLE": "✅",
    "NO_CHANGE": "✅",
    "ERROR": "🛑",
}


@dataclass
class Finding:
    """A single change, normalised for the comment."""

    kind: str
    symbol: str
    detail: str = ""
    location: str | None = None


@dataclass
class CommentModel:
    """Mode-agnostic view of a report, ready to render.

    A plain data container aggregating the report's header fields, the three
    reviewer buckets, and the release-mode rollup.
    """

    mode: str  # "compare" | "release" | "appcompat"
    subject: str
    old_label: str
    new_label: str
    policy: str
    breaking: list[Finding] = field(default_factory=list)
    review: list[Finding] = field(default_factory=list)
    safe: list[Finding] = field(default_factory=list)
    # release mode only: (library, verdict, n_breaking, n_review, n_safe)
    library_rows: list[tuple[str, str, int, int, int]] = field(default_factory=list)
    removed_libraries: list[str] = field(default_factory=list)
    added_libraries: list[str] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int, int]:
        """(breaking, needs-review, safe) totals across the report."""
        if self.mode == "release":
            return (
                sum(r[2] for r in self.library_rows),
                sum(r[3] for r in self.library_rows),
                sum(r[4] for r in self.library_rows),
            )
        return len(self.breaking), len(self.review), len(self.safe)

    @property
    def total_changes(self) -> int:
        """Total number of changes across all three buckets."""
        b, r, s = self.counts
        return b + r + s


# ---------------------------------------------------------------------------
# Parsing — JSON report → CommentModel
# ---------------------------------------------------------------------------


def _basename(path: object) -> str:
    s = str(path or "").rstrip("/")
    return s.rsplit("/", 1)[-1] or str(path or "")


def _detail_text(change: dict[str, object]) -> str:
    desc = str(change.get("description", "") or "").strip()
    old, new = change.get("old_value"), change.get("new_value")
    if old not in (None, "") and new not in (None, ""):
        delta = f"{old} → {new}"
        return f"{desc} ({delta})" if desc else delta
    return desc


def _severity_levels(report: dict[str, object]) -> dict[str, str]:
    """Resolved per-category severity levels from the report, or ``{}``.

    Present when the comparison ran with a severity config (``--severity-*`` /
    preset). A category set to ``error`` turns the check red, so the comment
    must file that category's findings under Breaking to match — this covers
    ``severity-addition: error`` and any preset/extra-arg path uniformly.
    """
    sev = report.get("severity")
    if isinstance(sev, dict):
        cfg = sev.get("config")
        if isinstance(cfg, dict):
            return {str(k): str(v) for k, v in cfg.items()}
    return {}


def _finding_category(severity: str, kind: str) -> str:
    """Map a finding's severity label + kind to a severity-config category."""
    if severity == "breaking":
        return "abi_breaking"
    if severity in ("api_break", "risk"):
        return "potential_breaking"
    if kind in _ADDITION_KIND_VALUES:
        return "addition"
    return "quality_issues"


def _bucket_changes(
    changes: object,
    gate_api_break: bool = False,
    levels: dict[str, str] | None = None,
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    breaking: list[Finding] = []
    review: list[Finding] = []
    safe: list[Finding] = []
    target = {"breaking": breaking, "review": review, "safe": safe}
    levels = levels or {}
    if isinstance(changes, list):
        for c in changes:
            if not isinstance(c, dict):
                continue
            sev = str(c.get("severity", "unknown"))
            kind = str(c.get("kind", ""))
            bucket = _SEVERITY_BUCKET.get(sev, "review")
            # fail-on-api-break turns the check red on API breaks (only) …
            if gate_api_break and sev == "api_break":
                bucket = "breaking"
            # … and a severity category set to error turns it red too.
            if levels.get(_finding_category(sev, kind)) == "error":
                bucket = "breaking"
            loc = c.get("source_location")
            target[bucket].append(
                Finding(
                    kind=kind,
                    symbol=str(c.get("symbol", "")),
                    detail=_detail_text(c),
                    location=str(loc) if loc else None,
                )
            )
    return breaking, review, safe


def _from_compare(
    report: dict[str, object], gate_api_break: bool = False
) -> CommentModel:
    breaking, review, safe = _bucket_changes(
        report.get("changes"), gate_api_break, _severity_levels(report)
    )
    return CommentModel(
        mode="compare",
        subject=str(report.get("library", "library")),
        old_label=str(report.get("old_version", "old")),
        new_label=str(report.get("new_version", "new")),
        policy=str(report.get("policy", "strict_abi")),
        breaking=breaking,
        review=review,
        safe=safe,
    )


def _from_appcompat(
    report: dict[str, object], gate_api_break: bool = False
) -> CommentModel:
    breaking, review, safe = _bucket_changes(
        report.get("relevant_changes"), gate_api_break, _severity_levels(report)
    )
    missing = report.get("missing_symbols")
    if isinstance(missing, list):
        for sym in missing:
            breaking.append(
                Finding(
                    kind="symbol_missing",
                    symbol=str(sym),
                    detail="required symbol not provided by new library",
                )
            )
    # A missing required version tag is breaking for the app too (appcompat
    # treats it the same as a missing symbol), so it must register as a change.
    missing_versions = report.get("missing_versions")
    if isinstance(missing_versions, list):
        for ver in missing_versions:
            breaking.append(
                Finding(
                    kind="version_missing",
                    symbol=str(ver),
                    detail="required symbol version not provided by new library",
                )
            )
    return CommentModel(
        mode="appcompat",
        subject=_basename(report.get("application", "application")),
        old_label=_basename(report.get("old_library", "old")),
        new_label=_basename(report.get("new_library", "new")),
        policy="strict_abi",
        breaking=breaking,
        review=review,
        safe=safe,
    )


def _append_release_global_row(
    rows: list[tuple[str, str, int, int, int]],
    name: str,
    verdict: object,
    findings: object,
    gate_api_break: bool = False,
    levels: dict[str, str] | None = None,
) -> None:
    """Fold a release-global check (bundle / probe-matrix) into the rows.

    These findings live at the top level of the report and fold into the
    release verdict; without this a clean per-library release that breaks only
    at the bundle/matrix level would report zero changes and skip the comment.
    Findings are bucketed by the section's own verdict, but each carries its
    ``kind`` — so when a compatible section is gated, the additions and quality
    issues are classified per finding and only the gated category is promoted to
    Breaking, matching what ``_fold_release_global_severity`` actually computes.
    """
    if not isinstance(findings, list) or not findings:
        return
    verdict_map = (
        {**_VERDICT_BUCKET, "API_BREAK": "breaking"}
        if gate_api_break
        else _VERDICT_BUCKET
    )
    bucket = verdict_map.get(str(verdict or ""), "review")
    levels = levels or {}
    n = len(findings)
    # A risk section under potential_breaking=error turns the check red.
    if bucket == "review" and levels.get("potential_breaking") == "error":
        bucket = "breaking"
    # A compatible section is additions + quality; classify each finding by its
    # kind and promote only the gated category to Breaking (addition and quality
    # gates are not interchangeable).
    if bucket == "safe":
        add_err = levels.get("addition") == "error"
        qual_err = levels.get("quality_issues") == "error"
        if add_err or qual_err:
            nb = sum(
                1
                for f in findings
                if isinstance(f, dict)
                and (
                    add_err
                    if str(f.get("kind", "")) in _ADDITION_KIND_VALUES
                    else qual_err
                )
            )
            rows.append((name, str(verdict or "?"), nb, 0, n - nb))
            return
    rows.append(
        (
            name,
            str(verdict or "?"),
            n if bucket == "breaking" else 0,
            n if bucket == "review" else 0,
            n if bucket == "safe" else 0,
        )
    )


def _release_lib_row(
    lib: dict[str, object], gate_api_break: bool, levels: dict[str, str]
) -> tuple[str, str, int, int, int]:
    """Per-library (name, verdict, breaking, review, safe) counts.

    Source breaks count as breaking when fail-on-api-break is set or
    potential_breaking is gated to error; risk only when potential_breaking is
    error; additions and quality issues only when their own category is gated to
    error. Otherwise source breaks + risk are review and additions + quality are
    safe. A library whose comparison errored carries no count fields, so it is
    counted as one breaking finding to reflect the failed comparison.
    """
    name = str(lib.get("library", "?"))
    verdict = str(lib.get("verdict", "?"))
    if verdict == "ERROR":
        return name, verdict, 1, 0, 0
    src = _as_int(lib.get("source_breaks"))
    risk = _as_int(lib.get("risk_changes"))
    # compatible_additions is the *total* compatible count; quality_issues is the
    # subset that is not an addition. Fall back to treating all as additions when
    # the (older) report omits quality_issues.
    quality = _as_int(lib.get("quality_issues"))
    additions = max(_as_int(lib.get("compatible_additions")) - quality, 0)
    pot_err = levels.get("potential_breaking") == "error"
    add_err = levels.get("addition") == "error"
    qual_err = levels.get("quality_issues") == "error"

    nb = _as_int(lib.get("breaking"))
    nr = 0
    ns = 0
    nb, nr = (nb + src, nr) if (gate_api_break or pot_err) else (nb, nr + src)
    nb, nr = (nb + risk, nr) if pot_err else (nb, nr + risk)
    nb, ns = (nb + additions, ns) if add_err else (nb, ns + additions)
    nb, ns = (nb + quality, ns) if qual_err else (nb, ns + quality)
    return name, verdict, nb, nr, ns


def _from_release(
    report: dict[str, object], gate_api_break: bool = False
) -> CommentModel:
    rows: list[tuple[str, str, int, int, int]] = []
    levels = _severity_levels(report)
    libraries = report.get("libraries")
    if isinstance(libraries, list):
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            rows.append(_release_lib_row(lib, gate_api_break, levels))
    n_libs = len(rows)
    _append_release_global_row(
        rows,
        "(bundle checks)",
        report.get("bundle_verdict"),
        report.get("bundle_findings"),
        gate_api_break,
        levels,
    )
    _append_release_global_row(
        rows,
        "(build-config matrix)",
        report.get("matrix_verdict"),
        report.get("matrix_findings"),
        gate_api_break,
        levels,
    )
    removed = report.get("unmatched_old")
    added = report.get("unmatched_new")
    return CommentModel(
        mode="release",
        subject=f"{n_libs} librar{'y' if n_libs == 1 else 'ies'}",
        old_label=_basename(report.get("old_dir", "old")),
        new_label=_basename(report.get("new_dir", "new")),
        policy="strict_abi",
        library_rows=rows,
        removed_libraries=[str(x) for x in removed]
        if isinstance(removed, list)
        else [],
        added_libraries=[str(x) for x in added] if isinstance(added, list) else [],
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def build_model(
    report: dict[str, object], gate_api_break: bool = False
) -> CommentModel:
    """Detect the report shape and normalise it into a :class:`CommentModel`.

    When *gate_api_break* is set (the action's ``fail-on-api-break``), API/source
    breaks are filed under Breaking so the comment matches the now-red check.
    """
    if isinstance(report.get("libraries"), list):
        return _from_release(report, gate_api_break)
    if "application" in report or isinstance(report.get("relevant_changes"), list):
        return _from_appcompat(report, gate_api_break)
    return _from_compare(report, gate_api_break)


def should_post(model: CommentModel, on: str) -> bool:
    """Whether a comment should be posted given the ``--on`` policy."""
    if on == "never":
        return False
    if on == "always":
        return True
    # on == "changes"
    return (
        model.total_changes > 0
        or bool(model.removed_libraries)
        or bool(model.added_libraries)
    )


# ---------------------------------------------------------------------------
# Rendering — CommentModel → markdown
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    # Sanitise for a single markdown table cell: escape pipes, neutralise
    # backticks (which would break the surrounding code span) and flatten
    # newlines. C/C++ symbols never contain backticks, so this is defensive.
    return (
        str(value)
        .replace("|", "\\|")
        .replace("`", "ˋ")
        .replace("\n", " ")
        .strip()
    )


def _md_url(url: str) -> str:
    """Percent-encode characters that would break a markdown ``(url)`` target."""
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def _header(model: CommentModel) -> tuple[str, str]:
    b, r, s = model.counts
    if model.removed_libraries:
        return "❌", "LIBRARY REMOVED"
    if b:
        return "❌", "ABI BREAKING"
    if r:
        return "⚠️", "Review recommended"
    if s:
        return "✅", "Compatible — safe changes only"
    return "✅", "No ABI changes"


def _strip_templates(s: str) -> str:
    """Drop balanced ``<...>`` template arguments (best-effort, for grouping)."""
    out: list[str] = []
    depth = 0
    for ch in s:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _api_group(symbol: str) -> str:
    """Enclosing API (namespace/type or free-function family) of a symbol.

    Strips template arguments and the parameter list, then drops the trailing
    ``::name`` so overloads, template instantiations and members of the same
    type/namespace collapse to one key. Free functions collapse their overloads
    to the bare name; distinct names stay distinct.
    """
    s = _strip_templates(symbol).strip()
    paren = s.find("(")
    if paren != -1:
        s = s[:paren].strip()
    if "::" in s:
        s = s.rsplit("::", 1)[0].strip()
    return s or symbol.strip()


def _group_by_api(findings: list[Finding]) -> OrderedDict[str, list[Finding]]:
    """Group findings by their enclosing API, preserving first-seen order."""
    groups: OrderedDict[str, list[Finding]] = OrderedDict()
    for f in findings:
        groups.setdefault(_api_group(f.symbol), []).append(f)
    return groups


def _flat_row(f: Finding) -> str:
    """Render one finding as a per-symbol table row."""
    loc = f" · `{_esc(f.location)}`" if f.location else ""
    cell = (_esc(f.detail) + loc) if f.detail else _esc(f.location or "—")
    return f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}` | {cell} |"


def _group_row(key: str, members: list[Finding]) -> str:
    """Render an API family as a single aggregated row (kinds, key, members)."""
    kinds = ", ".join(f"`{_esc(k)}`" for k in dict.fromkeys(m.kind for m in members))
    syms = [m.symbol for m in members]
    shown = syms[:_GROUP_MEMBERS_INLINE]
    more = f" +{len(syms) - _GROUP_MEMBERS_INLINE} more" if len(syms) > _GROUP_MEMBERS_INLINE else ""
    members_cell = ", ".join(f"`{_esc(x)}`" for x in shown) + more
    return f"| {kinds} | `{_esc(key)}` ({len(members)}) | {members_cell} |"


def _findings_table(
    title: str,
    findings: list[Finding],
    detail: str,
    *,
    open_default: bool,
) -> list[str]:
    if not findings:
        return []
    is_open = " open" if (detail == "full" or open_default) else ""
    out = [
        f"<details{is_open}><summary>{title} ({len(findings)})</summary>",
        "",
        "| Change | Symbol | Detail |",
        "|---|---|---|",
    ]
    if detail == "full":
        # Full detail keeps every change as its own per-symbol row (no rollup).
        out += [_flat_row(f) for f in findings]
        out += ["</details>", ""]
        return out
    # Standard: roll up by enclosing API so mass changes stay scannable —
    # singletons render as a normal per-symbol row, families aggregate.
    groups = _group_by_api(findings)
    keys = list(groups)
    for key in keys[:_STANDARD_ROW_CAP]:
        members = groups[key]
        out.append(_flat_row(members[0]) if len(members) == 1 else _group_row(key, members))
    if len(keys) > _STANDARD_ROW_CAP:
        out.append(f"| … | … | _{len(keys) - _STANDARD_ROW_CAP} more_ |")
    out += ["</details>", ""]
    return out


def _safe_section(findings: list[Finding], detail: str) -> list[str]:
    if not findings:
        return []
    is_open = " open" if detail == "full" else ""
    out = [f"<details{is_open}><summary>✅ Safe ({len(findings)})</summary>", ""]
    if detail == "full":
        out += ["| Change | Symbol | Detail |", "|---|---|---|"]
        for f in findings:
            out.append(f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}` | {_esc(f.detail)} |")
    else:
        groups: OrderedDict[str, list[str]] = OrderedDict()
        for f in findings:
            groups.setdefault(f.kind, []).append(f.symbol)
        parts: list[str] = []
        for kind, syms in groups.items():
            shown = syms[:_SAFE_SYMBOLS_PER_KIND]
            more = (
                f" _(+{len(syms) - _SAFE_SYMBOLS_PER_KIND})_"
                if len(syms) > _SAFE_SYMBOLS_PER_KIND
                else ""
            )
            joined = ", ".join(f"`{_esc(x)}`" for x in shown)
            parts.append(f"`{_esc(kind)}`: {joined}{more}")
        out.append(" · ".join(parts))
    out += ["", "</details>", ""]
    return out


def _release_table(model: CommentModel, detail: str) -> list[str]:
    rows = model.library_rows
    if not rows:
        return []
    is_open = " open" if detail == "full" else ""
    ordered = sorted(rows, key=lambda r: (-r[2], -r[3], -r[4], r[0]))
    cap = None if detail == "full" else _STANDARD_ROW_CAP
    shown = ordered if cap is None else ordered[:cap]
    out = [
        f"<details{is_open}><summary>Per-library results ({len(rows)})</summary>",
        "",
        "| Library | Verdict | Breaking | Review | Safe |",
        "|---|---|---|---|---|",
    ]
    for name, verdict, nb, nr, ns in shown:
        em = _VERDICT_EMOJI.get(verdict, "•")
        out.append(f"| `{_esc(name)}` | {em} {_esc(verdict)} | {nb} | {nr} | {ns} |")
    if cap is not None and len(ordered) > cap:
        out.append(f"| … | … | | | _{len(ordered) - cap} more_ |")
    out += ["</details>", ""]
    return out


def _header_block(model: CommentModel, short_sha: str) -> list[str]:
    emoji, title = _header(model)
    b, r, s = model.counts
    head_ref = f"**Head `{short_sha}`**" if short_sha else "**Head**"
    context = (
        f"{head_ref} vs `{model.old_label}` · `{model.policy}` · `{model.subject}`"
    )
    return [
        MARKER,
        "",
        f"## {emoji} abicheck — {title}",
        "",
        context,
        "",
        f"**{b} breaking** · {r} needs review · {s} safe",
        "",
    ]


def _library_notes(model: CommentModel) -> list[str]:
    out: list[str] = []
    if model.removed_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.removed_libraries)
        out += [f"> ⛔ Libraries removed: {listed}", ""]
    if model.added_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.added_libraries)
        out += [f"> ➕ New libraries: {listed}", ""]
    return out


def _body_sections(model: CommentModel, detail: str) -> list[str]:
    if model.mode == "release":
        return _release_table(model, detail)
    out = _findings_table(
        "❌ Breaking", model.breaking, detail, open_default=bool(model.breaking)
    )
    out += _findings_table(
        "⚠️ Needs review",
        model.review,
        detail,
        open_default=(not model.breaking and bool(model.review)),
    )
    out += _safe_section(model.safe, detail)
    return out


def _footer_block(
    ts: datetime, run_label: str | None, short_sha: str, report_url: str | None = None
) -> list[str]:
    footer = f"<sub>Updated {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    if run_label:
        footer += f" · {run_label}"
    if short_sha:
        footer += f" · commit {short_sha}"
    if report_url:
        footer += f" · [full report]({_md_url(report_url)})"
    footer += "</sub>"
    return [footer, ""]


def _render_body(
    model: CommentModel,
    short_sha: str,
    ts: datetime,
    detail: str,
    run_label: str | None,
    report_url: str | None,
    *,
    condensed: bool,
) -> str:
    """Render the comment body at one detail level (optionally condensed)."""
    lines = _header_block(model, short_sha)
    if condensed:
        note = "> ℹ️ _Condensed to fit GitHub's comment size limit"
        note += f" — see the [full report]({_md_url(report_url)})._" if report_url else "._"
        lines += [note, ""]
    lines += _library_notes(model)
    if detail != "summary":
        lines += _body_sections(model, detail)
    lines += _footer_block(ts, run_label, short_sha, report_url)
    return "\n".join(lines)


def _truncate_to_budget(body: str, report_url: str | None) -> str:
    """Hard-cut an over-budget body, appending a truncation note + report link."""
    suffix = "\n\n<sub>… comment truncated to fit GitHub's size limit"
    suffix += (
        f" — see the [full report]({_md_url(report_url)}).</sub>"
        if report_url
        else ".</sub>"
    )
    return body[: max(_BODY_BUDGET - len(suffix), 0)] + suffix


def render_comment(
    model: CommentModel,
    *,
    sha: str = "",
    detail: str = "standard",
    run_label: str | None = None,
    timestamp: datetime | None = None,
    report_url: str | None = None,
) -> str:
    """Render the full sticky-comment markdown body (including :data:`MARKER`).

    The body is kept under GitHub's 65,536-character comment limit: if the
    requested detail overflows, the detail level is downgraded
    (full → standard → summary) and, as a last resort, the body is truncated —
    always pointing at the full report when *report_url* is supplied.
    """
    if detail not in DETAIL_LEVELS:
        detail = "standard"
    ts = timestamp or datetime.now(timezone.utc)
    short_sha = (sha or "")[:7]
    body = ""
    for i, level in enumerate(_DETAIL_DOWNGRADE[detail]):
        body = _render_body(
            model, short_sha, ts, level, run_label, report_url, condensed=(i > 0)
        )
        if len(body) <= _BODY_BUDGET:
            return body
    return _truncate_to_budget(body, report_url)
