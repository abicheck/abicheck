# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The pure text projection for ``--stat`` output: the one-line
``LABEL: detail (N total) [...]`` summary format.

ADR-061 Phase 2: :func:`render_stat_document` is a real ``ReportDocument ->
str`` projection — it reads only already-resolved primitives (a verdict
label, summary counts, an optional precomputed severity exit code) out of
the document's mapping and formats them. It never classifies a change,
computes an exit code, or otherwise makes a compatibility/gate decision;
that happens once, before the document is built, exactly as D9 (renderers
are pure projections) requires. :func:`format_stat_line` is the lower-level
formatter shared with a second caller that has no ``ReportDocument`` to
build one from: ``cli_compare_fold._ScopedFold.into_oneline`` (CLI cleanup
phase two, PR 1's ``--profile quick`` + ``--used-by``/``--required-symbol``
scoped-gate fix) renders the identical layout from a *scoped* verdict/counts
pair instead of a whole-library comparison result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .document import ReportDocument


def format_stat_line(
    label: str,
    *,
    breaking: int,
    source_breaks: int,
    risk_count: int,
    compatible_additions: int,
    total_changes: int,
    redundant_count: int = 0,
    gate_note: str = "",
) -> str:
    """Render already-resolved counts/label as the one-line summary."""
    parts = []
    if breaking:
        parts.append(f"{breaking} breaking")
    if source_breaks:
        parts.append(f"{source_breaks} source-level breaks")
    if risk_count:
        parts.append(f"{risk_count} risk")
    if compatible_additions:
        parts.append(f"{compatible_additions} compatible")
    detail = ", ".join(parts) if parts else "no changes"
    redundant_note = (
        f" [{redundant_count} redundant hidden]" if redundant_count > 0 else ""
    )
    return f"{label}: {detail} ({total_changes} total){redundant_note}{gate_note}"


def render_stat_document(document: ReportDocument) -> str:
    """Render a stat-mode :class:`ReportDocument` as the one-line summary.

    Expects the document's root to carry ``verdict_label`` (a pre-resolved
    display label, not the raw ``Verdict`` value), a ``summary`` object with
    ``breaking``/``source_breaks``/``risk_changes``/``compatible_additions``/
    ``total_changes``, an optional top-level ``redundant_count``, and an
    optional ``severity`` object with ``exit_code`` — the same shape
    :func:`abicheck.reporter.to_stat_json`'s severity block already carries,
    so a future caller building one document for both the JSON and text
    stat outputs only has to supply these fields once. The ``gate:``
    suffix is pure formatting of that already-resolved ``exit_code``, not a
    new computation of it.
    """
    d = document.to_mapping()
    summary = d["summary"]
    assert isinstance(summary, dict)  # narrows for the keyword calls below
    redundant_count = d.get("redundant_count", 0)
    assert isinstance(redundant_count, int)
    gate_note = ""
    severity = d.get("severity")
    if isinstance(severity, dict) and "exit_code" in severity:
        exit_code = severity["exit_code"]
        gate_note = (
            f" [gate: FAIL (exit {exit_code})]" if exit_code else " [gate: PASS]"
        )
    return format_stat_line(
        str(d["verdict_label"]),
        breaking=summary["breaking"],
        source_breaks=summary["source_breaks"],
        risk_count=summary["risk_changes"],
        compatible_additions=summary["compatible_additions"],
        total_changes=summary["total_changes"],
        redundant_count=redundant_count,
        gate_note=gate_note,
    )
