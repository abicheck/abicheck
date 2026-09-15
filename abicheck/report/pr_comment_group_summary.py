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


def suppression_note(model: CommentModel) -> list[str]:
    """Disclose raw dispositions even when every finding was suppressed."""
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
        from .disposition_audit import (
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
