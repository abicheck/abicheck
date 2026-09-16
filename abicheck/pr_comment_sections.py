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


"""Comment sections and size budget, split out of the renderer.

:mod:`abicheck.pr_comment_render` sits at the architecture gate's production
file ceiling, so the blocks that grew it -- ADR-072's evidence/coverage and
entity-by-operation sections, the one-line notes between the headline and
the tables, and the section-aware shortening budget -- live here. One module
rather than three: each new flat root module is an entry in
``architecture/modules.yaml``'s no-growth inventory, and this is a file-size
split, not three new responsibilities.

Nothing here decides anything. Every value rendered is read off the
completed report (ADR-072 D1/D4): a confidence the report did not state is
not shown, and routine detector inapplicability is never worded as missing
required coverage. The shortening half encodes ADR-072 D8's one ordering
decision -- exhaust the per-section row budget at the requested detail level
before downgrading the level itself, because a downgrade throws away the
kind of information a reviewer needs while a budget throws away only a tail
whose size it can state exactly.
"""

from __future__ import annotations

from .pr_comment_base import CommentModel, _esc
from .report.evidence_summary import tier_label

# ---------------------------------------------------------------------------
# Size budget
# ---------------------------------------------------------------------------

# GitHub rejects issue/PR comment bodies longer than 65,536 characters. Render
# within a budget below that; if the body overflows, tighten the per-section
# row budget first, then downgrade the detail level (full → standard →
# summary), and finally hard-truncate so we never exceed it.
GITHUB_COMMENT_LIMIT = 65536
_BODY_BUDGET = 64000
_DETAIL_DOWNGRADE = {
    "full": ("full", "standard", "summary"),
    "standard": ("standard", "summary"),
    "summary": ("summary",),
}


def _md_url(url: str) -> str:
    """Percent-encode characters that would break a markdown ``(url)`` target."""
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def _detail_link(report_url: str | None, report_artifact_url: str | None) -> str:
    """ "…see X" clause for a shortening note, preferring the artifact."""
    if report_artifact_url:
        return f" — see the [full report]({_md_url(report_artifact_url)})."
    if report_url:
        return f" — see the [workflow run]({_md_url(report_url)})."
    return "."


#: Progressive per-section row budgets, tried in order before any detail
#: level is downgraded. A global downgrade throws away the *kind* of detail
#: a reviewer needs (per-symbol values, impact, location); a row budget
#: throws away only the tail, and says exactly how much it dropped.
_ROW_BUDGETS: tuple[int | None, ...] = (None, 500, 200, 60, 20, 8)


def _shortening_plan(detail: str) -> list[tuple[str, int | None]]:
    """(detail level, row budget) attempts, widest first.

    Row budgets are exhausted at the requested detail level *before* the
    level itself is downgraded, so ``full`` stays ``full`` for as long as
    any per-symbol rows fit at all.
    """
    plan: list[tuple[str, int | None]] = []
    for level in _DETAIL_DOWNGRADE[detail]:
        if level == "summary":
            plan.append((level, 0))
            continue
        for budget in _ROW_BUDGETS:
            plan.append((level, budget))
    return plan


#: Blocks `_close_open_details` knows how to terminate, innermost-last.
_DETAILS_OPEN = "<details"
_DETAILS_CLOSE = "</details>"


def _close_open_details(body: str) -> str:
    """Balance any ``<details>`` left open by a hard cut.

    A truncated body must still be valid Markdown/HTML: an unclosed
    ``<details>`` swallows the truncation note and the report link that
    follow it, which is precisely the navigation a shortened comment cannot
    afford to lose.
    """
    depth = body.count(_DETAILS_OPEN) - body.count(_DETAILS_CLOSE)
    return body + ("\n" + "\n".join([_DETAILS_CLOSE] * depth) if depth > 0 else "")


def _truncate_to_budget(
    body: str,
    report_url: str | None,
    report_artifact_url: str | None = None,
    budget: int = _BODY_BUDGET,
) -> str:
    """Hard-cut an over-budget body, keeping it structurally valid.

    The cut lands on a line boundary rather than mid-row, so a table row, a
    code span or a ``<details>`` summary is never bisected, and any block
    the cut left open is closed before the truncation note is appended.
    *budget* is passed in rather than read from the module global so the
    caller's own limit is the only one in play.
    """
    suffix = "\n\n<sub>… comment truncated to fit GitHub's size limit"
    suffix += _detail_link(report_url, report_artifact_url)
    suffix += "</sub>"
    head = body[: max(budget - len(suffix), 0)]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    # Closing the blocks the cut left open costs characters of its own, and
    # a deeply nested body can need more than any fixed reserve. Shrink the
    # head until the whole assembled result fits, rather than reserving a
    # guessed amount and hoping.
    while head and len(_close_open_details(head)) + len(suffix) > budget:
        cut = head.rfind("\n")
        head = head[:cut] if cut > 0 else ""
    return _close_open_details(head) + suffix


# ---------------------------------------------------------------------------
# Evidence, coverage and change-summary blocks
# ---------------------------------------------------------------------------

#: How many coverage warnings / detector gaps are listed inline before the
#: rest are rolled into a "+N more" tail. Generous on purpose: these are the
#: facts a reviewer needs to know the analysis was limited, and there are
#: rarely many.
_EVIDENCE_ITEMS_INLINE = 6


def _confidence_line(model: CommentModel) -> str | None:
    """ "Confidence: <value>", or ``None`` when the report stated none.

    Never renders a default. A report carrying no ``confidence`` field gets
    no confidence line at all -- claiming "high" for a run that did not say
    so is indistinguishable, to a reader, from a genuinely high-confidence
    run, which is the one failure this reporting layer must not have.
    """
    ev = model.evidence
    if ev is None or not ev.confidence:
        return None
    return f"Confidence: **{_esc(ev.confidence)}**"


def _evidence_block(model: CommentModel) -> list[str]:
    """Compact evidence/coverage summary, rendered directly under the counts.

    Four independent facts, each shown only when the report carries it:
    the producer's confidence, the evidence sources and analysis depth it
    actually had, the material limitations it recorded
    (``coverage_warnings``), and the detectors that did not run.

    The last two are deliberately worded differently. A ``coverage_warning``
    is the producer saying "this narrowed what I could conclude"; a
    not-evaluated detector is "this did not apply here", which on an ELF
    comparison is the permanent, uninteresting state of the PE and Mach-O
    detectors. Presenting the second as missing *required* coverage would
    manufacture alarm on every clean Linux run.
    """
    ev = model.evidence
    if ev is None or ev.is_empty:
        return []
    out: list[str] = []
    first: list[str] = []
    conf = _confidence_line(model)
    if conf:
        first.append(conf)
    if ev.evidence_tier:
        first.append(f"Analysis depth: `{_esc(ev.evidence_tier)}`")
    if ev.evidence_tiers:
        sources = ", ".join(f"`{_esc(tier_label(t))}`" for t in ev.evidence_tiers)
        first.append(f"Evidence: {sources}")
    if first:
        out += ["> " + " · ".join(first), ""]
    if ev.coverage_warnings:
        shown = list(ev.coverage_warnings[:_EVIDENCE_ITEMS_INLINE])
        out.append(
            f"<details open><summary>🔍 Limits on what was checked "
            f"({len(ev.coverage_warnings)})</summary>"
        )
        out.append("")
        out += [f"- {_esc(w)}" for w in shown]
        hidden = len(ev.coverage_warnings) - len(shown)
        if hidden > 0:
            out.append(f"- _… {hidden} more, see the full report_")
        out += ["", "</details>", ""]
    if ev.detector_gaps:
        # Three distinct states, never merged: a detector whose gate refused
        # it here, one that does not apply to this artifact kind at all (the
        # PE/Mach-O detectors on an ELF run), and one that ran with partial
        # evidence. Only the first and third are ever news; the second is
        # listed for completeness, in a collapsed block, and never worded as
        # missing *required* coverage.
        # `enabled` is checked first on purpose: a disabled detector is
        # reported with `not_evaluated: true` as well (it did not run --
        # that is true but uninformative), and the *reason* it did not run
        # is that it does not apply to this artifact at all. Reading
        # `not_evaluated` first would file every PE/Mach-O detector on an
        # ELF run under the alarming label rather than the accurate one.
        inapplicable = [g for g in ev.detector_gaps if not g.enabled]
        not_run = [g for g in ev.detector_gaps if g.enabled and g.not_evaluated]
        partial = [g for g in ev.detector_gaps if g.enabled and not g.not_evaluated]
        lines: list[str] = []
        for label, gaps in (
            ("did not run", not_run),
            ("not applicable to these artifacts", inapplicable),
            ("partial coverage", partial),
        ):
            if not gaps:
                continue
            shown_gaps = gaps[:_EVIDENCE_ITEMS_INLINE]
            listed = ", ".join(
                f"`{_esc(g.name)}`" + (f" ({_esc(g.reason)})" if g.reason else "")
                for g in shown_gaps
            )
            more = (
                f" _(+{len(gaps) - len(shown_gaps)} more)_"
                if len(gaps) > len(shown_gaps)
                else ""
            )
            lines.append(f"- {label}: {listed}{more}")
        if lines:
            out.append(
                f"<details><summary>Detector applicability "
                f"({len(ev.detector_gaps)})</summary>"
            )
            out += ["", *lines, "", "</details>", ""]
    return out


def _change_summary_block(model: CommentModel) -> list[str]:
    """Entity-by-operation table (``report/change_summary.py``).

    Counts *findings*, and says so: two findings about one function are two
    rows' worth of evidence about one symbol, and labelling the unit
    "symbols" or "changes" would conflate three different quantities the
    report keeps separate. This table is orthogonal to the
    breaking/review/safe counts in the header -- one says *what* changed,
    the other says *what it means* -- so neither is derived from the other.
    """
    summary = model.change_summary
    if summary is None or summary.is_empty:
        return []
    out = [
        f"<details open><summary>📋 What changed ({summary.counted} "
        f"{summary.unit})</summary>",
        "",
        "| Entity | Removed | Changed | Added |",
        "|---|---:|---:|---:|",
    ]
    for row in summary.rows:
        out.append(
            f"| {_esc(row.label)} | {row.removed} | {row.modified} | {row.added} |"
        )
    out.append("")
    if not summary.exact:
        reason = summary.inexact_reason or "the finding list was truncated upstream"
        out.append(f"> ⚠️ Not exact totals — {_esc(reason)}.")
        out.append("")
    out += ["</details>", ""]
    return out


# ---------------------------------------------------------------------------
# One-line notes between the headline and the detail sections
# ---------------------------------------------------------------------------


def _library_notes(model: CommentModel) -> list[str]:
    out: list[str] = []
    if model.removed_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.removed_libraries)
        out += [f"> ⛔ Libraries removed: {listed}", ""]
    if model.added_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.added_libraries)
        out += [f"> ➕ New libraries: {listed}", ""]
    if model.scope_notice is not None:
        out += [f"> 🧭 {_esc(model.scope_notice)}", ""]
    unmatched_old = [x for x in model.unmatched_old if x not in model.removed_libraries]
    unmatched_new = [x for x in model.unmatched_new if x not in model.added_libraries]
    if unmatched_old or unmatched_new:
        # Each member carries its own acquisition state (a failed OLD
        # acquisition is not "the NEW inventory is unproven"; Codex review);
        # the trailing rule is the one D2 statement true of all of them.
        def _named(x: str) -> str:
            state = model.unmatched_states.get(x)
            return f"`{_esc(x)}`" + (
                f" ({_esc(state.replace('_', ' '))})" if state else ""
            )

        parts = []
        if unmatched_old:
            parts.append("OLD-only " + ", ".join(_named(x) for x in unmatched_old))
        if unmatched_new:
            parts.append("NEW-only " + ", ".join(_named(x) for x in unmatched_new))
        out += [
            "> ↔️ Unmatched libraries (present on one side only; a removal or "
            "addition needs the lacking side's inventory proven complete, and a "
            "failed acquisition is never one -- see the comparison scope, "
            "ADR-065 D2): " + "; ".join(parts),
            "",
        ]
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
    """`compare --used-by`/`--required-symbol(s)` consumer summary (workstream
    D-S1, vision-api-abi-evolution.md "D. Optional prebuilt-consumer
    lifecycle").

    States a supplied consumer's own confirmed/potential/unresolved
    assessment *beside* the full-library breaking/review/safe buckets
    rendered below/above, then lists each app's/contract's own scoped result
    — purely informational: the exit code and headline verdict this comment
    reports always come from the full-library result, never this consumer's
    own.
    """
    if model.scoped_verdict is None:
        return []
    out: list[str] = []
    if model.full_verdict is not None and model.full_verdict != model.scoped_verdict:
        out += [
            f"> ℹ️ **Consumer-scoped verdict: {model.scoped_verdict}** "
            f"(informational only). The full library verdict (all changes "
            f"below, and what this run's exit code/headline are based on) is "
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


def _background_note(model: CommentModel) -> list[str]:
    """One line for the hygiene findings this comparison did not introduce.

    ADR-068 D3 stamps every cross-source hygiene finding with its OLD->NEW
    evolution, and the comparison layer is the only thing that can establish
    it. This states the two counts that matter to a reviewer -- how much
    standing debt both sides carry, and how much the candidate cleared --
    without listing any of it: a library with 336 template-instantiation
    guard variables has 336 of these on every single run, and itemizing them
    beside a real finding is how the real finding gets lost.

    Deliberately a *note*, not a section with a row budget: there is nothing
    here for a reviewer to act on in this pull request, which is the whole
    reason these findings are not in the compatibility buckets.
    """
    persistent, resolved = model.background_counts
    if not persistent and not resolved:
        return []
    parts: list[str] = []
    if persistent:
        parts.append(
            f"{persistent} pre-existing cross-source hygiene finding"
            f"{'s' if persistent != 1 else ''} present on both sides"
        )
    if resolved:
        parts.append(f"{resolved} resolved since the baseline")
    return [
        "> ♻️ " + " · ".join(parts) + " — not introduced by this change; "
        "see the full report for the itemized list.",
        "",
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
