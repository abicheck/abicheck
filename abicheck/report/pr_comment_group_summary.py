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
    if not model.review_groups:
        return []
    counts = model.result_counts or {}
    gating_groups = sum(
        bool(group.get("gating_findings")) for group in model.review_groups
    )
    lines = [
        f"**Review groups:** {counts.get('gating_review_groups', gating_groups)} gating; "
        f"{counts.get('review_groups', len(model.review_groups))} retained total.",
        "",
    ]
    for group in model.review_groups[:MAX_COMMENT_REVIEW_GROUPS]:
        scope = f"{_esc(group['library'])}: " if group.get("library") else ""
        lines.append(
            f"- {scope}**{_esc(group.get('display_name', '?'))}** — "
            f"{_esc(group.get('transition', 'changed'))}"
        )
    omitted = len(model.review_groups) - MAX_COMMENT_REVIEW_GROUPS
    if omitted > 0:
        lines.append(f"- … {omitted} more groups omitted")
    lines.append("")
    return lines
