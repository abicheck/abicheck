# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Lexical/preprocessor pre-scan report sections (plan §3 rows 6/8, §6 Phase
2b; schema 3.12).

Follows this package's compute/render split (see ``abicheck/report/
AGENTS.md``): each ``compute_*`` reads ``DiffResult.pattern_prescan``/
``.preprocessor_prescan`` (already-serialized per-side dicts --
``workflows.lexical_prescan.fold_lexical_prescan`` is what populates them)
and returns a small frozen fact; each ``render_*`` turns it into its JSON
block. Neither block is folded through ``Change``/``ChangeKind``/the
``CrossSourceEvolution`` axis -- see ``workflows/lexical_prescan.py``'s
module docstring for why these two pre-scans are evidence/coverage
attachments rather than diffed findings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PatternPrescanSummary:
    """Both sides' lexical ABI-risk pre-scan results, verbatim."""

    old: Mapping[str, Any]
    new: Mapping[str, Any]


def compute_pattern_prescan_summary(
    pattern_prescan: Mapping[str, Any] | None,
) -> PatternPrescanSummary | None:
    """Read ``DiffResult.pattern_prescan``, or ``None`` if the fold never ran.

    ``None`` here means "this ``DiffResult`` was never put through
    ``fold_lexical_prescan``" (e.g. a direct ``checker.compare()`` unit test)
    -- every documented front end populates the field, so a real report
    never omits this block. Distinct from a populated-but-``files_scanned:
    0`` side, which is the honest "nothing to scan" case surfaced inside the
    block itself (via each side's own ``coverage`` shape), not an omission.
    """
    if pattern_prescan is None:
        return None
    old = pattern_prescan.get("old")
    new = pattern_prescan.get("new")
    if old is None or new is None:
        return None
    return PatternPrescanSummary(old=old, new=new)


def render_pattern_prescan_json(
    summary: PatternPrescanSummary | None,
) -> dict[str, Any] | None:
    """Project *summary* into the JSON report's ``pattern_prescan`` block."""
    if summary is None:
        return None
    return {"old": dict(summary.old), "new": dict(summary.new)}


@dataclass(frozen=True, slots=True)
class PreprocessorPrescanSummary:
    """Both sides' S2 preprocessor pre-scan results, verbatim."""

    old: Mapping[str, Any]
    new: Mapping[str, Any]


def compute_preprocessor_prescan_summary(
    preprocessor_prescan: Mapping[str, Any] | None,
) -> PreprocessorPrescanSummary | None:
    """Read ``DiffResult.preprocessor_prescan``, or ``None`` if never folded.

    Same "never folded" vs. "folded, both sides read ``ran: false``" split
    as :func:`compute_pattern_prescan_summary` above.
    """
    if preprocessor_prescan is None:
        return None
    old = preprocessor_prescan.get("old")
    new = preprocessor_prescan.get("new")
    if old is None or new is None:
        return None
    return PreprocessorPrescanSummary(old=old, new=new)


def render_preprocessor_prescan_json(
    summary: PreprocessorPrescanSummary | None,
) -> dict[str, Any] | None:
    """Project *summary* into the JSON report's ``preprocessor_prescan`` block."""
    if summary is None:
        return None
    return {"old": dict(summary.old), "new": dict(summary.new)}


__all__ = [
    "PatternPrescanSummary",
    "PreprocessorPrescanSummary",
    "compute_pattern_prescan_summary",
    "compute_preprocessor_prescan_summary",
    "render_pattern_prescan_json",
    "render_preprocessor_prescan_json",
]
