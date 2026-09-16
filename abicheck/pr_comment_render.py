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

"""Sticky GitHub PR-comment rendering — ``CommentModel`` -> markdown.

Split out of ``pr_comment.py`` (over the file-size soft limit) as its own
module: that file's own "Parsing — JSON report -> CommentModel" / "Rendering
-- CommentModel -> markdown" section divider already marked this exact
boundary. :func:`render_comment` takes an already-built
:class:`~abicheck.pr_comment_base.CommentModel` and has no dependency on
``pr_comment.py``'s own report-parsing half (``build_model`` and friends),
so the two halves split along a real seam rather than an arbitrary line
range -- confirmed directly: nothing here calls back into
``pr_comment.py``'s own functions, only into ``pr_comment_base.py`` (the
``CommentModel``/``Finding`` types and shared formatting helpers). The
``scan``-report adapter this module used to call into
(``pr_comment_scan.scan_note``) was deleted with the ``scan`` command
(ADR-068 Phase 6) -- its dead ``model.mode == "scan"`` branch below went with
it too, since the one surviving producer of ``mode="scan"``
(``pr_comment._from_no_baseline``, the ``compare --no-baseline`` audit
report) never sets any of the ``scan_*`` fields that branch read.
``pr_comment.py`` re-exports :func:`render_comment` and this module's other
externally-referenced names for its existing callers (``cli_pr_comment.py``,
several ``tests/test_pr_comment*.py`` modules).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone

from .pr_comment_base import (
    CommentModel,
    Finding,
    _component_key,
    _component_tag,
    _esc,
)
from .pr_comment_sections import (
    _BODY_BUDGET,
    GITHUB_COMMENT_LIMIT as GITHUB_COMMENT_LIMIT,
    _background_note,
    _change_summary_block,
    _detail_link,
    _evidence_block,
    _gate_note,
    _incomplete_note,
    _library_notes,
    _md_url,
    _scoped_notes,
    _shortening_plan,
    _suppression_note,
    _truncate_to_budget,
)
from .report.pr_comment_headline import (
    # The per-verdict glyph table lives with the headline that owns the
    # verdict vocabulary; the per-target/per-library results table below
    # reads it so the two can never disagree about what a verdict looks
    # like.
    _VERDICT_EMOJI,
    # Re-exported under its historical name for `pr_comment.py`'s own
    # re-export chain and the test modules that import it from there.
    headline as _header,
)

__all__ = [
    "MARKER",
    "DETAIL_LEVELS",
    "GITHUB_COMMENT_LIMIT",
    "render_comment",
    "_header",
]

# Hidden marker used to find-and-update the sticky comment across runs.
MARKER = "<!-- abicheck-sticky-report -->"

DETAIL_LEVELS = ("summary", "standard", "full")

# Per-detail row caps for the "standard" level (full = uncapped).
_STANDARD_ROW_CAP = 25
_SAFE_SYMBOLS_PER_KIND = 12
# Member symbols listed inline in an aggregated (API-grouped) Breaking/Review row.
_GROUP_MEMBERS_INLINE = 8


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
        groups.setdefault(_component_key(f, _api_group(f.symbol)), []).append(f)
    return groups


def _flat_row(f: Finding) -> str:
    """Render one finding as a per-symbol table row.

    The Symbol column shows the demangled signature (`_demangle_symbol`)
    when one was recovered, with the raw mangled linker symbol kept as
    evidence in the Detail column — a maintainer thinks in the signature,
    not the mangled name, but the mangled form is still the real linker
    fact behind a binary-level finding. Full-detail rows also carry the
    report's own `impact` field (`change_registry.py`'s per-kind `impact=`
    template) when present, labelled **Impact:** — deliberately not
    "**Fix:**" (Codex review): an `impact=` entry is a free-form
    explanation of consequences, not a guaranteed repair step — e.g.
    `symbol_version_defined_removed`'s entry only says old binaries get a
    link error, `struct_size_changed`'s only confirms the layout break is
    visible at binary level — so labelling every one of them as a "fix"
    would misrepresent entries that carry no remediation at all.
    """
    loc = f" · `{_esc(f.location)}`" if f.location else ""
    cell = (_esc(f.detail) + loc) if f.detail else _esc(f.location or "—")
    if f.mangled:
        cell += f"<br>linker: `{_esc(f.mangled)}`"
    if f.impact:
        cell += f"<br>**Impact:** {_esc(f.impact)}"
    return f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}`{_component_tag(f)} | {cell} |"


def _group_row(key: str, members: list[Finding]) -> str:
    """Render an API family as a single aggregated row (kinds, key, members)."""
    kinds = ", ".join(f"`{_esc(k)}`" for k in dict.fromkeys(m.kind for m in members))
    syms = [m.symbol for m in members]
    shown = syms[:_GROUP_MEMBERS_INLINE]
    more = (
        f" +{len(syms) - _GROUP_MEMBERS_INLINE} more"
        if len(syms) > _GROUP_MEMBERS_INLINE
        else ""
    )
    members_cell = ", ".join(f"`{_esc(x)}`" for x in shown) + more
    return f"| {kinds} | `{_esc(key)}` ({len(members)}) | {members_cell} |"


#: Default total member rows in the "All grouped members" block when no
#: size budget applies. Bounded on purpose: this block is a *route* to the
#: complete detail, and an unbounded one makes a large report overflow the
#: comment budget at every row budget, collapsing the whole body to the
#: summary level -- strictly less information than the grouped rows it was
#: added to complete (measured: a 1000-finding standard-detail body fell
#: from ~15 KB of tables to a 440-byte summary).
_MEMBER_BLOCK_CAP = 200


def _group_members_block(
    groups: OrderedDict[str, list[Finding]],
    row_cap: int | None = None,
    report_url: str | None = None,
) -> list[str]:
    """Complete member lists for every aggregated row in a standard table.

    Standard detail rolls an API family up to one row listing member
    *symbols only*; without this block, everything else about those
    findings -- description, old/new values, location, impact -- was
    unreachable from the comment at that detail level, and past
    ``_GROUP_MEMBERS_INLINE`` even the names were ("+22 more"). A grouped
    row is allowed to be a summary; it is not allowed to be a dead end.

    Every aggregated family is included, not only the ones whose member
    list was itself cut: a two-member group shows both names inline and
    still loses both findings' detail, which is the same failure at a
    smaller size.
    """
    aggregated = [
        (k, m)
        for k, m in groups.items()
        if len(m) > 1 and len({f.symbol for f in m}) > 1
    ]
    if not aggregated:
        return []
    total = sum(len(m) for _, m in aggregated)
    cap = _MEMBER_BLOCK_CAP if row_cap is None else row_cap
    out = [
        f"<details><summary>All grouped members ({total})</summary>",
        "",
        "| Change | Symbol | Detail |",
        "|---|---|---|",
    ]
    shown = 0
    for _key, members in aggregated:
        for member in members:
            if shown >= cap:
                break
            out.append(_flat_row(member))
            shown += 1
        if shown >= cap:
            break
    if total > shown:
        out.append(_omitted_row(total - shown, report_url))
    out += ["", "</details>", ""]
    return out


def _omitted_row(n: int, report_url: str | None) -> str:
    """A table row stating exactly how many rows were left out, and where the
    rest are. The count is of *omitted rows*, computed from the full list
    before the cap, so it is never an estimate."""
    where = (
        f" — see the [full report]({_md_url(report_url)})"
        if report_url
        else " — see the full JSON report"
    )
    return f"| … | … | _{n} more not shown{where}_ |"


def _findings_table(
    title: str,
    findings: list[Finding],
    detail: str,
    *,
    open_default: bool,
    count: int | None = None,
    row_cap: int | None = None,
    report_url: str | None = None,
) -> list[str]:
    # `count` overrides the header's displayed number when it can diverge
    # from `len(findings)` -- currently only the analysis-incomplete bucket,
    # whose exact total (`model.incomplete_total`) can exceed the itemized
    # list when the report cap truncated some or all of it (Codex review).
    # Every other caller leaves this `None` and gets `len(findings)`, same
    # as before.
    n = count if count is not None else len(findings)
    if n == 0:
        return []
    is_open = " open" if (detail == "full" or open_default) else ""
    out = [
        f"<details{is_open}><summary>{title} ({n})</summary>",
        "",
        "| Change | Symbol | Detail |",
        "|---|---|---|",
    ]
    if detail == "full":
        # Full detail keeps every change as its own per-symbol row (no
        # rollup), capped only by the size budget the caller resolved
        # (`row_cap`) rather than by a global downgrade to a different
        # detail level -- see `render_comment`.
        shown = findings if row_cap is None else findings[:row_cap]
        out += [_flat_row(f) for f in shown]
        if len(findings) > len(shown):
            out.append(_omitted_row(len(findings) - len(shown), report_url))
        out += ["</details>", ""]
        return out
    # Standard: roll up by enclosing API so mass changes stay scannable —
    # singletons render as a normal per-symbol row, families aggregate.
    groups = _group_by_api(findings)
    cap = _STANDARD_ROW_CAP if row_cap is None else min(_STANDARD_ROW_CAP, row_cap)
    # Rows are built for *every* group first and only then capped, because
    # a group is not a row: a family whose members all name one symbol
    # expands to one row per member (see below). Capping the group keys
    # instead let a section emit more rows than `row_cap` allowed and made
    # the omission notice count *groups* while the section header counts
    # findings -- two different quantities under one number (CodeRabbit
    # review). Each row carries the number of findings it represents, so
    # the notice can state an exact finding count.
    rendered: list[tuple[str, int, str]] = []
    for key in groups:
        members = groups[key]
        # Aggregate only when the rollup actually summarises *several
        # entities*. A family whose members are all findings about the one
        # symbol (a changed return type and a changed parameter list on the
        # same function) has nothing to summarise: the aggregated row would
        # read "`foo_init`, `foo_init`" and drop both findings' own values,
        # which is strictly worse than the two flat rows it replaced.
        if len(members) == 1 or len({m.symbol for m in members}) == 1:
            rendered += [(_flat_row(m), 1, key) for m in members]
        else:
            rendered.append((_group_row(key, members), len(members), key))
    shown_rows = rendered[:cap]
    out += [row for row, _, _ in shown_rows]
    omitted = sum(n for _, n, _ in rendered[len(shown_rows) :])
    if omitted:
        out.append(_omitted_row(omitted, report_url))
    out += ["</details>", ""]
    shown_keys = list(dict.fromkeys(key for _, _, key in shown_rows))
    out += _group_members_block(
        OrderedDict((k, groups[k]) for k in shown_keys), row_cap, report_url
    )
    return out


def _safe_section(
    findings: list[Finding],
    detail: str,
    row_cap: int | None = None,
    report_url: str | None = None,
) -> list[str]:
    if not findings:
        return []
    is_open = " open" if detail == "full" else ""
    # "Safe" reads as an absolute guarantee it isn't — these are compatible
    # quality/behavioral findings (COMPATIBLE_KINDS minus additions, which
    # get their own "➕ Public API additions" section above), not a claim
    # that nothing here is worth a look.
    out = [
        f"<details{is_open}><summary>ℹ️ Informational findings ({len(findings)})</summary>",
        "",
    ]
    if detail == "full":
        out += ["| Change | Symbol | Detail |", "|---|---|---|"]
        # Full detail here obeys the same row budget every other section
        # does; without it this one section emitted every finding while the
        # budget was being tightened around it, which is how a body stayed
        # over the limit at a budget that should have fitted (CodeRabbit
        # review).
        shown = findings if row_cap is None else findings[:row_cap]
        for f in shown:
            out.append(
                f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}`{_component_tag(f)} | "
                f"{_esc(f.detail)} |"
            )
        if len(findings) > len(shown):
            out.append(_omitted_row(len(findings) - len(shown), report_url))
    else:
        groups: OrderedDict[str, list[str]] = OrderedDict()
        for f in findings:
            # Keyed by component too, for the same reason `_group_by_api` is:
            # one `func_added` line listing the same name once per target is
            # a count, not an attribution.
            groups.setdefault(_component_key(f, f.kind), []).append(f.symbol)
        parts: list[str] = []
        for kind, syms in groups.items():
            shown_syms = syms[:_SAFE_SYMBOLS_PER_KIND]
            more = (
                f" _(+{len(syms) - _SAFE_SYMBOLS_PER_KIND})_"
                if len(syms) > _SAFE_SYMBOLS_PER_KIND
                else ""
            )
            joined = ", ".join(f"`{_esc(x)}`" for x in shown_syms)
            parts.append(f"`{_esc(kind)}`: {joined}{more}")
        out.append(" · ".join(parts))
    out += ["", "</details>", ""]
    return out


def _release_table(
    model: CommentModel, detail: str, row_cap: int | None = None
) -> list[str]:
    rows = model.library_rows
    if not rows:
        return []
    is_open = " open" if detail == "full" else ""
    ordered = sorted(rows, key=lambda r: (-r[2], -r[3], -r[4], r[0]))
    cap = row_cap if detail == "full" else _STANDARD_ROW_CAP
    if detail != "full" and row_cap is not None:
        cap = min(_STANDARD_ROW_CAP, row_cap)
    shown = ordered if cap is None else ordered[:cap]
    # One table, two operands. In release mode a row is a library inside one
    # comparison; in aggregate mode it is a whole target's own report folded
    # in (`report/pr_comment_aggregate.py`). The shape -- name, verdict, and
    # the same three bucket counts -- is identical, so the table is shared
    # and only its nouns change; a second near-copy is how the two would
    # drift.
    unit = "Target" if model.mode == "aggregate" else "Library"
    title = f"Per-{unit.lower()} results ({len(rows)})"
    out = [
        f"<details{is_open}><summary>{title}</summary>",
        "",
        f"| {unit} | Verdict | Breaking | Review | Safe |",
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
    # Codex review: `model.subject` can come straight from an untrusted
    # scanned-artifact basename (the Action's `run.sh` passes it through
    # `--subject`) -- a crafted filename containing a backtick or newline
    # could terminate this code span and inject arbitrary Markdown into the
    # sticky comment otherwise. `_esc` (used everywhere else a value is
    # rendered inside a code span) neutralizes both.
    if model.no_baseline_audit:
        # No comparison ran at all (`old_acquisition_state: declared_absent`
        # in the report) -- "vs `baseline`" would claim one did (Codex
        # review, PR #1210, round 4).
        context = f"{head_ref} — audit, no baseline · `{_esc(model.policy)}` · `{_esc(model.subject)}`"
    elif model.mode == "aggregate":
        # There is no single baseline here: each folded target was compared
        # against its own, and naming one would claim a shared operand this
        # document does not have. `old_label` carries the honest phrase (see
        # `report/pr_comment_aggregate.py`), rendered as prose rather than
        # in a code span so it cannot read as a path.
        context = (
            f"{head_ref} — {_esc(model.old_label)} · `{_esc(model.policy)}` · "
            f"`{_esc(model.subject)}`"
        )
    else:
        context = (
            f"{head_ref} vs `{_esc(model.old_label)}` · `{_esc(model.policy)}` · "
            f"`{_esc(model.subject)}`"
        )
    counts_line = f"**{b} breaking** · {r} needs review · {s} safe"
    # The incomplete count is a distinct axis (analysis quality, not
    # compatibility — see module docstring) and only shown when non-zero, so
    # every existing report's summary line is unchanged.
    if model.has_incomplete:
        counts_line += f" · {model.incomplete_total} analysis incomplete"
    return [
        MARKER,
        "",
        f"## {emoji} abicheck — {title}",
        "",
        context,
        "",
        counts_line,
        "",
    ]


def _incomplete_findings_for_table(model: CommentModel) -> list[Finding]:
    """`model.incomplete`, or -- when the report cap truncated *every*
    analysis-incomplete finding, leaving the itemized list empty even though
    `model.incomplete_total` is exact and positive (Codex review) -- one
    synthetic placeholder row, so `_findings_table` (called with
    ``count=model.incomplete_total``) still renders a section instead of
    silently vanishing next to a truncation note claiming the counts above
    are exact.
    """
    if model.incomplete or model.incomplete_total <= 0:
        return model.incomplete
    n = model.incomplete_total
    word = "finding" if n == 1 else "findings"
    return [
        Finding(
            kind="",
            symbol="(truncated)",
            detail=(
                f"{n} analysis-incomplete {word} were cut by the report cap "
                "before any could be itemized; see the full JSON report for "
                "detail."
            ),
        )
    ]


def _body_sections(
    model: CommentModel,
    detail: str,
    row_cap: int | None = None,
    report_url: str | None = None,
) -> list[str]:
    if model.mode == "release":
        # Codex review (CLI-audit P2 follow-up): the release path's early
        # return used to skip `model.incomplete` entirely — the headline and
        # `_header_block`'s "· N analysis incomplete" count already surfaced
        # a release-level contract-coverage gap (see `_release_contract_
        # coverage_findings`), but the actual `Finding.detail` naming which
        # libraries were affected was unreachable in the rendered body, full
        # detail included. Same table compare mode uses for its own
        # incomplete bucket, appended after the per-library results table.
        return _release_table(model, detail, row_cap) + _findings_table(
            "🛑 Analysis incomplete",
            _incomplete_findings_for_table(model),
            detail,
            open_default=model.has_incomplete,
            count=model.incomplete_total,
            row_cap=row_cap,
            report_url=report_url,
        )
    # An aggregate model carries both: the per-target rollup (authoritative
    # counts, including for a member whose own shape reports only in
    # aggregate) and the folded per-finding sections below it.
    out: list[str] = (
        _release_table(model, detail, row_cap) if model.mode == "aggregate" else []
    )
    cats = model.breaking_categories
    breaking_title = (
        "❌ Breaking"
        if (not cats or "abi_breaking" in cats or "potential_breaking" in cats)
        else "⛔ Blocked by policy (compatible)"
    )
    out += _findings_table(
        breaking_title,
        model.breaking,
        detail,
        open_default=bool(model.breaking),
        row_cap=row_cap,
        report_url=report_url,
    )
    out += _findings_table(
        "🛑 Analysis incomplete",
        _incomplete_findings_for_table(model),
        detail,
        open_default=(not model.breaking and model.has_incomplete),
        count=model.incomplete_total,
        row_cap=row_cap,
        report_url=report_url,
    )
    out += _findings_table(
        "⚠️ Needs review",
        model.review,
        detail,
        open_default=(not model.breaking and bool(model.review)),
        row_cap=row_cap,
        report_url=report_url,
    )
    # New public-API surface gets its own section — a per-symbol table with
    # kind/detail/location, the same treatment Breaking/Needs review get —
    # rather than being folded anonymously into the generic quality-findings
    # "Safe" list below. A reviewer approving new exports wants to see what
    # was added, not just a bare symbol count.
    additions = [f for f in model.safe if f.category == "addition"]
    quality = [f for f in model.safe if f.category != "addition"]
    out += _findings_table(
        "➕ Public API additions",
        additions,
        detail,
        open_default=False,
        row_cap=row_cap,
        report_url=report_url,
    )
    out += _safe_section(quality, detail, row_cap, report_url)
    return out


def _footer_block(
    ts: datetime,
    run_label: str | None,
    short_sha: str,
    report_url: str | None = None,
    report_artifact_url: str | None = None,
) -> list[str]:
    """Footer links.

    *report_url* is the workflow run; *report_artifact_url*, when supplied,
    is a direct link to the uploaded HTML/JSON report artifact. They are
    labelled distinctly ("View workflow run" vs "Download full report")
    because they are different destinations doing different jobs, and the
    artifact link is rendered only when a caller passes one -- the Action
    passes it only from an upload step that actually succeeded, so a link
    here always corresponds to an artifact that exists.
    """
    footer = f"<sub>Updated {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    if run_label:
        footer += f" · {run_label}"
    if short_sha:
        footer += f" · commit {short_sha}"
    if report_url:
        footer += f" · [View workflow run]({_md_url(report_url)})"
    if report_artifact_url:
        footer += f" · [Download full report]({_md_url(report_artifact_url)})"
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
    row_cap: int | None = None,
    report_artifact_url: str | None = None,
) -> str:
    """Render the comment body at one detail level and per-section row budget.

    Everything before the detail sections is *unconditional*: the headline
    and its verdict/gate meaning, head/baseline identity, the exact
    authoritative counts, the evidence and scope limitations, and the
    disposition summary. Only the itemized finding tables shrink, and they
    shrink by row budget rather than by discarding a whole detail level, so
    a report with a thousand findings still shows individually-detailed rows
    instead of collapsing to twenty-five grouped ones.
    """
    lines = _header_block(model, short_sha)
    if condensed:
        note = "> ℹ️ _Shortened to fit GitHub's comment size limit"
        note += _detail_link(report_url, report_artifact_url).rstrip(".") + "._"
        lines += [note, ""]
    lines += _evidence_block(model)
    lines += _library_notes(model)
    lines += _gate_note(model)
    lines += _incomplete_note(model)
    lines += _background_note(model)
    lines += _scoped_notes(model)
    lines += _suppression_note(model)
    lines += _change_summary_block(model)
    if detail != "summary":
        lines += _body_sections(
            model, detail, row_cap, report_artifact_url or report_url
        )
    lines += _footer_block(ts, run_label, short_sha, report_url, report_artifact_url)
    return "\n".join(lines)


def render_comment(
    model: CommentModel,
    *,
    sha: str = "",
    detail: str = "standard",
    run_label: str | None = None,
    timestamp: datetime | None = None,
    report_url: str | None = None,
    report_artifact_url: str | None = None,
) -> str:
    """Render the full sticky-comment markdown body (including :data:`MARKER`).

    The body is kept under GitHub's 65,536-character comment limit by
    *section-aware* shortening: the per-section row budget is tightened
    first (keeping per-symbol detail), and only once no budget fits is the
    detail level itself downgraded, with a hard, line-aligned truncation as
    the last resort. Every attempt preserves the headline, identity, exact
    counts, evidence and scope limitations, disposition summary and the
    links to the complete results.
    """
    if detail not in DETAIL_LEVELS:
        detail = "standard"
    ts = timestamp or datetime.now(timezone.utc)
    short_sha = (sha or "")[:7]
    body = ""
    for i, (level, budget) in enumerate(_shortening_plan(detail)):
        body = _render_body(
            model,
            short_sha,
            ts,
            level,
            run_label,
            report_url,
            condensed=(i > 0),
            row_cap=budget,
            report_artifact_url=report_artifact_url,
        )
        if len(body) <= _BODY_BUDGET:
            return body
    return _truncate_to_budget(body, report_url, report_artifact_url, _BODY_BUDGET)
