# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Lexical/preprocessor pre-scan report sections (plan §3 rows 6/8, §6 Phase
2b; schema 3.13).

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


#: Rendered text for each :func:`~abicheck.workflows.lexical_prescan.
#: _pattern_scan_scope_reason` value (Codex review, fresh evidence) -- three
#: materially different "nothing was scanned" situations that a single
#: "not evaluated (no headers/`--sources` in scope)" message used to
#: collapse onto one another, so a valid empty ``--since`` seed (b) read
#: identically to no inputs being supplied at all (a), and both read
#: identically to every supplied input being unreadable (c).
_SCOPE_REASON_MESSAGES: Mapping[str, str] = {
    "no_inputs": "not evaluated (no headers/`--sources` in scope)",
    "empty_seed": (
        "not evaluated (`--since`/`--changed-path` resolved to a real, "
        "empty scope -- 0 files by design, not a missing/unreadable input)"
    ),
    "unreadable_inputs": (
        "not evaluated (headers/`--sources` were supplied, but every "
        "candidate file was unreadable or matched no scannable extension)"
    ),
}


def _pattern_side_markdown_line(label: str, side: Mapping[str, Any]) -> str:
    reason = side.get("scope_reason")
    if reason is not None:
        message = _SCOPE_REASON_MESSAGES.get(
            reason, "not evaluated (no headers/`--sources` in scope)"
        )
        return f"- **{label}**: {message}"
    coverage = side.get("coverage") or {}
    if coverage.get("status") == "not_collected" or not side.get("files_scanned"):
        # `scope_reason` absent (e.g. a report built before this field
        # existed) -- fall back to the old, coarser message rather than
        # fail to render anything.
        return f"- **{label}**: not evaluated (no headers/`--sources` in scope)"
    facts = side.get("facts") or []
    triggers = side.get("escalation_triggers") or []
    return (
        f"- **{label}**: {side.get('files_scanned', 0)} file(s) scanned, "
        f"{len(facts)} construct(s) found, {len(triggers)} escalation "
        "trigger(s)"
    )


def render_pattern_prescan_markdown(
    summary: PatternPrescanSummary | None,
) -> list[str]:
    """``scan``'s own text output surfaced this pre-scan's coverage status
    (``pr_comment_scan._scan_coverage_lines``'s ```pattern_scan`: present```
    line); this is ``compare``'s Markdown counterpart -- a short, per-side
    status line, not a full one-line-per-fact dump (``scan`` never rendered
    one either; see :mod:`abicheck.workflows.lexical_prescan`'s own
    docstring for why the full advisory detail stays JSON-only). ``[]`` when
    the fold never ran, matching every other optional section's own
    "``None``-shaped ``compute_*`` renders nothing" convention.
    """
    if summary is None:
        return []
    return [
        "",
        "### Pattern Pre-Scan (lexical ABI-risk constructs)",
        "",
        _pattern_side_markdown_line("OLD", summary.old),
        _pattern_side_markdown_line("NEW", summary.new),
    ]


def _preprocessor_side_markdown_line(label: str, side: Mapping[str, Any]) -> str:
    coverage = side.get("coverage") or {}
    status = coverage.get("status")
    if not side.get("ran") or status == "not_collected":
        reason = side.get("skipped_reason") or coverage.get("detail") or "not evaluated"
        return f"- **{label}**: skipped -- {reason}"
    divergences = side.get("divergences") or []
    leaks = side.get("leaks") or []
    line = (
        f"- **{label}**: {len(divergences)} macro divergence(s), "
        f"{len(leaks)} header leak(s)"
    )
    if status == "partial":
        # Codex review, fresh evidence: only some `clang -E` probes
        # succeeded, or the probe count hit `ABICHECK_PREPROCESSOR_SCAN_
        # MAX_PROBES`'s cap -- without this, a partially-inspected build
        # can report zero divergences/leaks and read exactly like a clean,
        # fully-scanned one.
        detail = coverage.get("detail") or "incomplete coverage"
        line += f" -- ⚠️ **partial coverage** ({detail})"
    return line


def render_preprocessor_prescan_markdown(
    summary: PreprocessorPrescanSummary | None,
) -> list[str]:
    """Markdown counterpart to :func:`render_pattern_prescan_markdown`, same
    scope rationale."""
    if summary is None:
        return []
    return [
        "",
        "### Preprocessor Pre-Scan (macro divergence / header leaks)",
        "",
        _preprocessor_side_markdown_line("OLD", summary.old),
        _preprocessor_side_markdown_line("NEW", summary.new),
    ]


def pattern_prescan_review_warnings(summary: PatternPrescanSummary | None) -> list[str]:
    """Coverage-warning strings for the ``--format review`` digest (Codex
    review, fresh evidence): ``build_review_digest_document`` never called
    :func:`render_pattern_prescan_markdown` at all, so an ``unreadable_
    inputs`` side -- headers/``--sources`` were supplied but nothing could
    actually be scanned -- silently vanished from the one GitHub-facing
    summary a reviewer approves a merge from, which could still read as an
    unqualified "safe to merge". Only ``unreadable_inputs`` is surfaced
    here: ``no_inputs``/``empty_seed`` are the ordinary, by-design "nothing
    to scan" cases every other report view already covers via each side's
    own ``coverage`` block, and repeating them in the digest would just be
    noise for the common case of a headerless/sourceless comparison.
    """
    if summary is None:
        return []
    warnings = []
    for label, side in (("OLD", summary.old), ("NEW", summary.new)):
        if side.get("scope_reason") == "unreadable_inputs":
            warnings.append(
                f"{label} pattern pre-scan: headers/--sources were supplied "
                "but every candidate file was unreadable or matched no "
                "scannable extension -- lexical ABI-risk coverage is 0 "
                "files, not a by-design empty scope"
            )
    return warnings


def preprocessor_prescan_review_warnings(
    summary: PreprocessorPrescanSummary | None,
) -> list[str]:
    """Same rationale as :func:`pattern_prescan_review_warnings`, for the S2
    preprocessor pre-scan's own ``partial`` coverage case (some ``clang -E``
    probes failed, or the scan's own probe cap truncated it) -- without this
    a partially-inspected build reports its (possibly incomplete) divergence/
    leak counts in the review digest exactly like a clean, fully-scanned
    one.
    """
    if summary is None:
        return []
    warnings = []
    for label, side in (("OLD", summary.old), ("NEW", summary.new)):
        coverage = side.get("coverage") or {}
        if coverage.get("status") == "partial":
            detail = coverage.get("detail") or "incomplete coverage"
            warnings.append(
                f"{label} preprocessor pre-scan: partial coverage ({detail})"
            )
    return warnings


__all__ = [
    "PatternPrescanSummary",
    "PreprocessorPrescanSummary",
    "compute_pattern_prescan_summary",
    "compute_preprocessor_prescan_summary",
    "render_pattern_prescan_json",
    "render_preprocessor_prescan_json",
    "render_pattern_prescan_markdown",
    "render_preprocessor_prescan_markdown",
    "pattern_prescan_review_warnings",
    "preprocessor_prescan_review_warnings",
]
