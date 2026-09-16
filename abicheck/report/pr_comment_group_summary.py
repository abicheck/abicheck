# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Bounded canonical review-group summary for PR comments."""

from __future__ import annotations

from ..pr_comment_base import CommentModel, _esc


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
    for group in model.review_groups[:8]:
        scope = f"{_esc(group['library'])}: " if group.get("library") else ""
        lines.append(
            f"- {scope}**{_esc(group.get('display_name', '?'))}** — "
            f"{_esc(group.get('transition', 'changed'))}"
        )
    if len(model.review_groups) > 8:
        lines.append(f"- … {len(model.review_groups) - 8} more groups omitted")
    lines.append("")
    return lines

