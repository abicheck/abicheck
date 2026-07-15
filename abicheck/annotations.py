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

"""GitHub Actions workflow command annotations for ABI changes.

Emits ``::error``, ``::warning``, and ``::notice`` workflow commands so that
ABI breaking changes appear as inline annotations on PR diffs.

See: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .checker import (
    Change,
    DiffResult,
)
from .checker_policy import (
    ChangeKind,
)

if TYPE_CHECKING:
    from .severity import KindSets, SeverityConfig

# GitHub caps visible annotations at ~50 per step.
_MAX_ANNOTATIONS = 50

# GitHub has undocumented limits on annotation message length.
_MAX_MESSAGE_LENGTH = 200

# Severity ordering for sorting (highest first).
_SEVERITY_ORDER = {
    "error": 0,
    "warning": 1,
    "notice": 2,
}


def _escape_annotation_value(value: str) -> str:
    """Escape special characters for GitHub workflow command values.

    GitHub workflow commands use `%` encoding for special characters
    in property values and messages.
    """
    return (
        value
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _escape_annotation_data(data: str) -> str:
    """Escape special characters in the annotation message body.

    The message (data portion after `::`) only needs newline escaping.
    """
    return (
        data
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _truncate_message(message: str, max_length: int = _MAX_MESSAGE_LENGTH) -> str:
    """Truncate message to max_length, appending ellipsis if truncated."""
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."


def _parse_source_location(loc: str | None) -> tuple[str | None, str | None]:
    """Parse a ``file:line[:col]`` source location string.

    Accepts ``path:line`` or ``path:line:col`` (column is discarded).
    Returns (file, line) where either may be ``None``.
    """
    if not loc:
        return None, None
    # Split into segments on ":"
    # Handle Windows drive letters (e.g. C:\foo\bar.h:42) by preserving
    # a single-character first segment followed by a backslash/path.
    parts = loc.split(":")
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        # Windows path: rejoin drive letter with the next segment.
        # e.g. ["C", "\\foo\\bar.h", "42"] -> file="C:\\foo\\bar.h", rest=["42"]
        # or   ["C", "\\foo\\bar.h", "42", "7"] -> file="C:\\foo\\bar.h", rest=["42","7"]
        file_part = parts[0] + ":" + parts[1]
        rest = parts[2:]
    elif len(parts) >= 2:
        # Unix path: everything before the last numeric segments is the file.
        # e.g. ["include/foo.h", "42"] or ["include/foo.h", "42", "7"]
        file_part = parts[0]
        rest = parts[1:]
    else:
        # No colon at all — just a filename.
        return loc, None

    # First element of rest should be the line number.
    if rest and rest[0].isdigit():
        return file_part, rest[0]
    # No valid line number found.
    return loc, None


def _classify_change(
    kind: ChangeKind,
    breaking_set: frozenset[ChangeKind],
    api_break_set: frozenset[ChangeKind],
    risk_set: frozenset[ChangeKind],
    compatible_set: frozenset[ChangeKind],
    annotate_additions: bool,
) -> str | None:
    """Return the annotation level for a change, or None to skip it.

    Mapping:
      BREAKING        → ::error
      API_BREAK       → ::warning  (title: "API Break")
      RISK            → ::warning  (title: "Deployment Risk")
      COMPATIBLE      → ::notice   (only when annotate_additions is True
                                     AND kind is in compatible_set)
    """
    if kind in breaking_set:
        return "error"
    if kind in api_break_set:
        return "warning"
    if kind in risk_set:
        return "warning"
    if annotate_additions and kind in compatible_set:
        return "notice"
    return None


def _classify_change_by_severity(
    change: Change,
    kind_sets: KindSets,
    severity_config: SeverityConfig,
    annotate_additions: bool,
) -> str | None:
    """Return the annotation level driven by *severity_config*, or None to skip.

    Reflects the actual CI gate (ADR "GateDecision" direction): a category
    configured ``error`` always emits ``::error`` regardless of whether it is
    an addition or a breaking kind, so an annotation is never silently absent
    for a finding that will fail the build. ``warning``/``info`` mirror the
    severity level directly; ``info`` only surfaces (as ``::notice``) when
    *annotate_additions* opts into the noisier informational annotations —
    matching the pre-existing opt-in behaviour for additions.
    """
    from .severity import SeverityLevel, classify_effective_change

    category = classify_effective_change(change, kind_sets=kind_sets)
    level = severity_config.level_for(category)
    if level == SeverityLevel.ERROR:
        return "error"
    if level == SeverityLevel.WARNING:
        return "warning"
    if level == SeverityLevel.INFO:
        return "notice" if annotate_additions else None
    return None


def _title_for_change(
    kind: ChangeKind,
    breaking_set: frozenset[ChangeKind],
    api_break_set: frozenset[ChangeKind],
    risk_set: frozenset[ChangeKind],
    compatible_set: frozenset[ChangeKind],
) -> str:
    """Return the annotation title prefix, distinguishing API Break from Deployment Risk."""
    kind_label = kind.value
    if kind in breaking_set:
        return f"ABI Break: {kind_label}"
    if kind in api_break_set:
        return f"API Break: {kind_label}"
    if kind in risk_set:
        return f"Deployment Risk: {kind_label}"
    if kind in compatible_set:
        return f"ABI Addition: {kind_label}"
    return f"ABI Change: {kind_label}"


def _format_annotation(
    level: str,
    change: Change,
    title: str,
    message: str,
) -> str:
    """Format a single GitHub workflow command annotation line."""
    file, line = _parse_source_location(change.source_location)

    props: list[str] = []
    if file:
        props.append(f"file={_escape_annotation_value(file)}")
    if line:
        props.append(f"line={_escape_annotation_value(line)}")
    props.append(f"title={_escape_annotation_value(title)}")

    props_str = ",".join(props)
    escaped_message = _escape_annotation_data(_truncate_message(message))
    return f"::{level} {props_str}::{escaped_message}"


def collect_annotations(
    diff_result: DiffResult,
    *,
    annotate_additions: bool = False,
    severity_config: SeverityConfig | None = None,
) -> list[tuple[int, str]]:
    """Collect raw annotation tuples (sort_key, line) for a single DiffResult.

    This is the building block for both single-library and multi-library flows.
    Callers are responsible for sorting, truncating, and emitting.

    Without *severity_config*, annotation levels follow the fixed
    kind-set mapping (BREAKING → error, API_BREAK/RISK → warning, additions →
    notice when opted in) — the legacy, verdict-only behaviour. When
    *severity_config* is supplied, it takes priority: the annotation level
    mirrors each finding's actually-configured severity so an annotation is
    never silently absent (or under/over-stated) for a finding that does (or
    does not) gate CI — see :func:`_classify_change_by_severity`.
    """
    kind_sets = diff_result._effective_kind_sets()
    breaking_set, api_break_set, compatible_set, risk_set = kind_sets

    annotations: list[tuple[int, str]] = []

    for change in diff_result.changes:
        if severity_config is not None:
            level = _classify_change_by_severity(
                change, kind_sets, severity_config, annotate_additions,
            )
        else:
            level = _classify_change(
                change.kind, breaking_set, api_break_set, risk_set,
                compatible_set, annotate_additions,
            )
        if level is None:
            continue

        title = _title_for_change(
            change.kind, breaking_set, api_break_set, risk_set, compatible_set,
        )
        line = _format_annotation(level, change, title, change.description)
        sort_key = _SEVERITY_ORDER.get(level, 99)
        annotations.append((sort_key, line))

    return annotations


def format_annotations(
    annotations: list[tuple[int, str]],
    *,
    max_annotations: int = _MAX_ANNOTATIONS,
) -> str:
    """Sort annotation tuples by severity and format as newline-separated output.

    Args:
        annotations: List of (sort_key, line) tuples from :func:`collect_annotations`.
        max_annotations: Maximum number of annotations to emit.

    Returns:
        A string of newline-separated workflow commands (may be empty).
    """
    sorted_annotations = sorted(annotations, key=lambda x: x[0])
    lines = [line for _, line in sorted_annotations[:max_annotations]]
    return "\n".join(lines)


def emit_github_annotations(
    diff_result: DiffResult,
    *,
    annotate_additions: bool = False,
    max_annotations: int = _MAX_ANNOTATIONS,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Generate GitHub Actions annotation lines for ABI changes.

    Args:
        diff_result: The diff result to annotate.
        annotate_additions: If True, also emit ``::notice`` for additions/compatible changes.
        max_annotations: Maximum number of annotations to emit (default 50).
        severity_config: When given, annotation levels follow the configured
            per-category severity instead of the fixed kind-set mapping (see
            :func:`collect_annotations`).

    Returns:
        A string of newline-separated workflow commands (may be empty).
    """
    annotations = collect_annotations(
        diff_result,
        annotate_additions=annotate_additions,
        severity_config=severity_config,
    )
    return format_annotations(annotations, max_annotations=max_annotations)


def is_github_actions() -> bool:
    """Return True if running inside GitHub Actions."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def emit_github_step_summary(diff_result: DiffResult) -> str | None:
    """Write a Markdown job summary to $GITHUB_STEP_SUMMARY if available.

    Returns the summary path if written, None otherwise.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return None

    from .reporter import to_markdown

    md = to_markdown(diff_result)
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")
    return summary_path
