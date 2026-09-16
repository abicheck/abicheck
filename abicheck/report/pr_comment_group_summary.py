# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Bounded canonical review-group summary for PR comments."""

from __future__ import annotations

from ..pr_comment_base import CommentModel, _esc

#: Cap on how many review groups the PR comment itemizes. Named rather than
#: inline so the bound is visible next to the disclosure that reports it --
#: the same reason ``surface_changes.MAX_COMPACT_SURFACE_ITEMS`` is named.
#: This caps one flat list, so it cannot exhibit the cross-group starvation
#: that cap documents; the count line above is read off ``result_counts`` and
#: therefore stays complete however far the itemized list is cut.
MAX_COMMENT_REVIEW_GROUPS = 8


def review_group_note(model: CommentModel) -> list[str]:
    counts = model.result_counts or {}
    # Bail only when there is nothing to report *at all*. `--view show=` can
    # filter every displayed group while `result_counts["review_groups"]`
    # stays positive, and returning early on the displayed list alone then
    # hid both the authoritative retained total and the omission disclosure
    # -- the same "read the authoritative count, not the list you were
    # handed" rule the omission arithmetic below already follows
    # (CodeRabbit review).
    if not model.review_groups and not counts.get("review_groups"):
        return []
    gating_groups = sum(
        bool(group.get("gating_findings")) for group in model.review_groups
    )
    # The authoritative retained total, which is what the headline states.
    retained_total = counts.get("review_groups", len(model.review_groups))
    lines = [
        f"**Review groups:** {counts.get('gating_review_groups', gating_groups)} gating; "
        f"{retained_total} retained total.",
        "",
    ]
    shown = model.review_groups[:MAX_COMMENT_REVIEW_GROUPS]
    for group in shown:
        scope = f"{_esc(group['library'])}: " if group.get("library") else ""
        lines.append(
            f"- {scope}**{_esc(group.get('display_name', '?'))}** — "
            f"{_esc(group.get('transition', 'changed'))}"
        )
    # Measured against that same authoritative total, not against the list
    # this note happens to have been handed. `report/build.py` builds
    # `review_groups` from the *displayed* findings under `--view show=`,
    # while `result_counts` is computed over every retained finding -- so a
    # filtered list can be shorter than the headline with the display cap
    # never reached, and deriving the omission from the list alone then
    # reports none. The headline and the list would disagree with nothing
    # accounting for the difference (CodeRabbit review).
    omitted = max(0, retained_total - len(shown))
    if omitted:
        lines.append(f"- … {omitted} more groups omitted")
    lines.append("")
    return lines
