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
``CommentModel``/``Finding`` types and shared formatting helpers already
factored out for ``pr_comment_scan.py``) and ``pr_comment_scan.py`` itself
(``scan_note``). ``pr_comment.py`` re-exports :func:`render_comment` and this
module's other externally-referenced names for its existing callers
(``cli_pr_comment.py``, several ``tests/test_pr_comment*.py`` modules).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone

from .pr_comment_base import CommentModel, Finding, _esc
from .pr_comment_scan import scan_note

# Hidden marker used to find-and-update the sticky comment across runs.
MARKER = "<!-- abicheck-sticky-report -->"

DETAIL_LEVELS = ("summary", "standard", "full")

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


def _md_url(url: str) -> str:
    """Percent-encode characters that would break a markdown ``(url)`` target."""
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


#: Scoped-verdict (--used-by/--required-symbol) headline per verdict string —
#: this is what the actual exit code reflects, so it takes priority over the
#: raw (unscoped) bucket counts, which stay informational context below.
_SCOPED_HEADER: dict[str, tuple[str, str]] = {
    "BREAKING": ("❌", "ABI BREAKING (scoped)"),
    "API_BREAK": ("⚠️", "API break (scoped)"),
    "COMPATIBLE": ("✅", "Compatible (scoped)"),
}


#: Reviewer-facing headline for a Breaking bucket whose members are *only*
#: policy-gated COMPATIBLE findings (a severity-config category promoted to
#: ``error``) — not a genuine ABI/API incompatibility. Keyed by the exact
#: frozenset of categories responsible; a bucket that also holds a genuine
#: ``abi_breaking``/``potential_breaking`` finding never reaches this table
#: (see ``_header``), since that finding *is* accurately a break.
_POLICY_ONLY_HEADER: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"addition"}): ("⛔", "Public API expansion requires approval"),
    frozenset({"quality_issues"}): ("⛔", "Quality policy violation"),
    frozenset({"addition", "quality_issues"}): (
        "⛔",
        "Policy violation blocks this PR",
    ),
}


def _header(model: CommentModel) -> tuple[str, str]:
    if (
        model.mode == "scan"
        and model.scan_audit_only
        and not model.breaking
        and not model.review
        and not model.has_incomplete
    ):
        # An audit-only `scan` (no `--against` baseline at all) ran no
        # comparison, so every compatibility bucket is necessarily empty —
        # the generic "✅ No ABI changes" wording below would misreport that
        # as "we compared and found nothing" rather than "there was nothing
        # to compare".
        return "✅", "Scan audit — no baseline to compare"
    if model.scoped_verdict is not None:
        # `contract_coverage_blocking` is checked FIRST, ahead of the scoped
        # header itself (Codex review, two rounds): the contract-coverage
        # axis folds into the real exit code unconditionally, on top of
        # *any* other verdict including a scoped one — a
        # --used-by/--required-symbol run reporting a scoped COMPATIBLE
        # verdict does not silence an orthogonal coverage failure that
        # already turned the real exit code non-zero. This must return
        # directly here, not merely skip the scoped-header return and fall
        # through to the rest of the function: under scoping, the *raw*
        # `b`/`r`/`s` bucket counts below are the full, unscoped library
        # diff (kept only as informational context, per this module's own
        # design) and are not part of the actual gate — falling through
        # would let an unrelated full-library break the scoped consumer
        # never even sees win the headline instead of correctly naming the
        # orthogonal coverage axis that is what's actually failing.
        if model.contract_coverage_blocking:
            return "🛑", "Source analysis incomplete"
        header = _SCOPED_HEADER.get(model.scoped_verdict)
        if header is not None:
            return header
    b, r, s = model.counts
    if model.removed_libraries:
        return "❌", "LIBRARY REMOVED"
    if b:
        # A finding is only accurately reported as "ABI BREAKING" when the
        # Breaking bucket holds a genuine abi_breaking/potential_breaking
        # finding — never infer that wording from bucket membership alone.
        # Severity-config promotion (ADR-042: compatibility and gate
        # decisions are separate axes) can populate Breaking with a
        # COMPATIBLE addition/quality finding that policy chose to block;
        # that is a policy violation, not an ABI/API break.
        cats = model.breaking_categories
        if "abi_breaking" in cats:
            return "❌", "ABI BREAKING"
        if "potential_breaking" in cats:
            # "potential_breaking" covers both "api_break" (a real source
            # break) and "risk" (a risk promoted to blocking) — they share a
            # severity-config knob but are not the same claim, so resolve
            # via the raw severities rather than wording every gated risk as
            # a "source API break" (Codex review, PR #595).
            sevs = model.breaking_severities
            if "api_break" in sevs:
                return "⛔", "Source API break blocks this PR"
            if "risk" in sevs:
                return "⛔", "Compatibility risk blocks this PR"
            return "⛔", "Source API break blocks this PR"
        policy_header = _POLICY_ONLY_HEADER.get(cats)
        if policy_header is not None:
            return policy_header
        # No per-category tracking for this bucket (shouldn't normally
        # happen — every mode populates breaking_categories) — fall back to
        # the conservative default rather than under-stating a red check.
        return "❌", "ABI BREAKING"
    if model.has_incomplete and model.incomplete_blocking:
        # A hard evidence-policy failure fails the run the same way a
        # genuine break does (ADR-033 D7 / a gated coverage risk), so it
        # takes the same headline priority as `b` above — ahead of a
        # separate, merely-advisory review finding. Say so explicitly rather
        # than folding it into the generic "Review recommended" wording,
        # which would read as "this PR changed the API," not "we couldn't
        # check this PR."
        return "🛑", "Source analysis incomplete"
    if r:
        # A real compatibility finding always keeps headline priority over a
        # merely-advisory coverage gap (Codex review) — an ungated
        # `layer_coverage_asymmetric` must not bury a genuine source-API
        # change behind "Analysis coverage reduced". `_incomplete_note`
        # still calls the coverage gap out separately.
        #
        # Name the actual reason instead of the generic "Review recommended"
        # whenever every review-bucket finding agrees on one severity — a
        # source-level API change (binary ABI unaffected) reads very
        # differently from a risk finding, and a reviewer shouldn't have to
        # open the section to tell which one this is.
        review_sevs = frozenset(f.severity for f in model.review if f.severity)
        if review_sevs == {"api_break"}:
            return "⚠️", "Source API changed; binary ABI unchanged"
        if review_sevs == {"risk"}:
            return "⚠️", "Compatibility risk — review recommended"
        return "⚠️", "Review recommended"
    if model.has_incomplete:
        return "⚠️", "Analysis coverage reduced"
    if s:
        return "✅", "No compatibility impact detected"
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
    return f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}` | {cell} |"


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


def _findings_table(
    title: str,
    findings: list[Finding],
    detail: str,
    *,
    open_default: bool,
    count: int | None = None,
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
        out.append(
            _flat_row(members[0]) if len(members) == 1 else _group_row(key, members)
        )
    if len(keys) > _STANDARD_ROW_CAP:
        out.append(f"| … | … | _{len(keys) - _STANDARD_ROW_CAP} more_ |")
    out += ["</details>", ""]
    return out


def _safe_section(findings: list[Finding], detail: str) -> list[str]:
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
    # Codex review: `model.subject` can come straight from an untrusted
    # scanned-artifact basename (the Action's `run.sh` passes it through
    # `--subject`) -- a crafted filename containing a backtick or newline
    # could terminate this code span and inject arbitrary Markdown into the
    # sticky comment otherwise. `_esc` (used everywhere else a value is
    # rendered inside a code span) neutralizes both.
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


def _library_notes(model: CommentModel) -> list[str]:
    out: list[str] = []
    if model.removed_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.removed_libraries)
        out += [f"> ⛔ Libraries removed: {listed}", ""]
    if model.added_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.added_libraries)
        out += [f"> ➕ New libraries: {listed}", ""]
    return out


def _suppression_note(model: CommentModel) -> list[str]:
    """ "Reporting must survive suppression": a reviewer must see *that*
    findings were withheld/reclassified, not just the post-suppression
    buckets above (which, for a fully-suppressed diff, could otherwise read
    as "no ABI changes at all")."""
    parts: list[str] = []
    if model.suppressed_count:
        n = model.suppressed_count
        parts.append(
            f"🔇 {n} finding{'s' if n != 1 else ''} suppressed by `--suppress`"
        )
    if model.reclassified_count:
        n = model.reclassified_count
        parts.append(
            f"🔀 {n} finding{'s' if n != 1 else ''} reclassified by `--policy`"
        )
    lines: list[str] = []
    if model.disposition_audit is not None:
        # ADR-067 D3: the raw-versus-effective counts come first and are not
        # conditional on anything having been suppressed -- "0 breaking" must
        # never be the only number a reviewer sees.
        from .report.disposition_audit import (
            DispositionAudit,
            render_disposition_audit_comment_lines,
        )

        lines += render_disposition_audit_comment_lines(
            DispositionAudit.from_dict(model.disposition_audit)
        )
    if not parts:
        return lines
    return lines + [
        f"> ℹ️ {' · '.join(parts)} — see the full JSON report for details.",
        "",
    ]


def _scoped_notes(model: CommentModel) -> list[str]:
    """`compare --used-by`/`--required-symbol(s)` scoping banner + summary.

    States which verdict the exit code actually reflects whenever it disagrees
    with the full-library breaking/review/safe buckets rendered below, then
    lists each app's/contract's own scoped result (Codex review) — otherwise a
    reviewer sees only the alarming full-library findings with no indication
    the gate is scoped and currently passing (or vice versa).
    """
    if model.scoped_verdict is None:
        return []
    out: list[str] = []
    if model.full_verdict is not None and model.full_verdict != model.scoped_verdict:
        out += [
            f"> ℹ️ **Scoped verdict: {model.scoped_verdict}** — this is what the "
            f"exit code reflects. The full library (all changes below) is "
            f"`{model.full_verdict}`.",
            "",
        ]
    for app in model.used_by_summaries:
        missing_symbols = app.get("missing_symbols")
        n_missing = len(missing_symbols) if isinstance(missing_symbols, list) else 0
        out.append(
            f"- `--used-by {_esc(app.get('app'))}`: **{_esc(app.get('verdict'))}** "
            f"(missing {n_missing} symbol(s), "
            f"{app.get('relevant_change_count', 0)} relevant change(s))"
        )
    if model.required_symbol_summary is not None:
        rs = model.required_symbol_summary
        missing_entrypoints = rs.get("missing_entrypoints")
        n_missing_ep = (
            len(missing_entrypoints) if isinstance(missing_entrypoints, list) else 0
        )
        out.append(
            f"- `--required-symbol` contract: **{_esc(rs.get('verdict'))}** "
            f"(missing {n_missing_ep} "
            f"entrypoint(s), {rs.get('relevant_change_count', 0)} relevant change(s))"
        )
    if out:
        out.append("")
    return out


#: Human-readable label for a severity-config category, used in policy-block
#: messaging ("addition"/"quality_issues" are the only categories that can
#: populate a *policy-only* Breaking bucket — see `_POLICY_ONLY_HEADER`).
_CATEGORY_LABEL = {"addition": "addition", "quality_issues": "quality"}


def _gate_note(model: CommentModel) -> list[str]:
    """Explain a policy-only block (ADR-042): compatibility and gate
    decisions are separate axes, so a COMPATIBLE addition/quality finding
    can still fail the check under a strict severity config. Rendered only
    when the Breaking bucket holds no genuine incompatibility, so a
    reviewer isn't left thinking ABI/API compatibility itself is broken.
    """
    if model.removed_libraries or model.scoped_verdict is not None:
        return []
    b, r, _ = model.counts
    cats = model.breaking_categories
    if not b or "abi_breaking" in cats or "potential_breaking" in cats or not cats:
        return []
    names = ", ".join(f"`{_CATEGORY_LABEL.get(c, c)}`" for c in sorted(cats))
    if r:
        # A separate, ungated api_break/risk finding sits in "Needs review" —
        # asserting whole-report "Compatibility: COMPATIBLE" here would
        # overstate it (Codex review, PR #595), so scope the claim to just
        # the Breaking bucket's own entries and point at the other section.
        return [
            f"> ℹ️ **Gate: BLOCKED** by severity policy — {names} is "
            f"configured as `error`. These entries are themselves COMPATIBLE "
            f'(not an ABI/API break) — see "Needs review" below for other '
            f"findings that may affect compatibility.",
            "",
        ]
    return [
        f"> ℹ️ **Compatibility: COMPATIBLE** — existing binaries/consumers are "
        f"unaffected; this is not an ABI/API break. **Gate: BLOCKED** by "
        f"severity policy — {names} is configured as `error`.",
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


def _incomplete_note(model: CommentModel) -> list[str]:
    """Explain the analysis-incomplete bucket when it did *not* win the
    headline — a genuine breaking finding, or (for a merely-advisory
    coverage gap) a real review finding, took priority instead (see
    `_header`) — so a reviewer looking at an "ABI BREAKING" or "Review
    recommended" headline still learns the analysis itself was also
    degraded, rather than discovering it only in the collapsed details
    section below.
    """
    if not model.has_incomplete:
        return []
    if not model.breaking and not (model.review and not model.incomplete_blocking):
        return []
    n = model.incomplete_total
    word = "finding" if n == 1 else "findings"
    return [
        f"> 🛑 {n} analysis-coverage {word} below — some real changes may not "
        f"be detectable with the evidence this comparison had available.",
        "",
    ]


def _body_sections(model: CommentModel, detail: str) -> list[str]:
    if model.mode == "release":
        # Codex review (CLI-audit P2 follow-up): the release path's early
        # return used to skip `model.incomplete` entirely — the headline and
        # `_header_block`'s "· N analysis incomplete" count already surfaced
        # a release-level contract-coverage gap (see `_release_contract_
        # coverage_findings`), but the actual `Finding.detail` naming which
        # libraries were affected was unreachable in the rendered body, full
        # detail included. Same table compare mode uses for its own
        # incomplete bucket, appended after the per-library results table.
        return _release_table(model, detail) + _findings_table(
            "🛑 Analysis incomplete",
            _incomplete_findings_for_table(model),
            detail,
            open_default=model.has_incomplete,
            count=model.incomplete_total,
        )
    cats = model.breaking_categories
    breaking_title = (
        "❌ Breaking"
        if (not cats or "abi_breaking" in cats or "potential_breaking" in cats)
        else "⛔ Blocked by policy (compatible)"
    )
    out: list[str] = []
    if model.mode == "scan":
        out += scan_note(model)
    out += _findings_table(
        breaking_title, model.breaking, detail, open_default=bool(model.breaking)
    )
    out += _findings_table(
        "🛑 Analysis incomplete",
        _incomplete_findings_for_table(model),
        detail,
        open_default=(not model.breaking and model.has_incomplete),
        count=model.incomplete_total,
    )
    out += _findings_table(
        "⚠️ Needs review",
        model.review,
        detail,
        open_default=(not model.breaking and bool(model.review)),
    )
    # New public-API surface gets its own section — a per-symbol table with
    # kind/detail/location, the same treatment Breaking/Needs review get —
    # rather than being folded anonymously into the generic quality-findings
    # "Safe" list below. A reviewer approving new exports wants to see what
    # was added, not just a bare symbol count.
    additions = [f for f in model.safe if f.category == "addition"]
    quality = [f for f in model.safe if f.category != "addition"]
    out += _findings_table(
        "➕ Public API additions", additions, detail, open_default=False
    )
    out += _safe_section(quality, detail)
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
        note += (
            f" — see the [full report]({_md_url(report_url)})._" if report_url else "._"
        )
        lines += [note, ""]
    lines += _library_notes(model)
    lines += _gate_note(model)
    lines += _incomplete_note(model)
    lines += _scoped_notes(model)
    lines += _suppression_note(model)
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
