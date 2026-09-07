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

"""Workstream E slice S3's Markdown section: multi-source contract conflicts.

Split into its own module (rather than added to ``render_markdown.py``, at
its own file-size ceiling) following this package's own ``compute_*``/
``render_*`` split (``abicheck/report/AGENTS.md``): ``reporter_markdown.py``
owns the ``compute_*`` half that reads a ``DiffResult``, this module owns the
``render_*`` half that formats already-structured values and decides
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContractConflictRow:
    conflict_kind: str
    entity: str
    side: str | None
    reason_code: str
    #: One short line per source, e.g. ``"export_table: binary exports
    #: symbol 'foo'"`` — every disagreeing source's own claim, kept
    #: side-by-side rather than resolved to one of them (ADR-067).
    source_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContractConflictsSection:
    rows: tuple[ContractConflictRow, ...]


def render_contract_conflicts_section(
    section: ContractConflictsSection | None,
) -> list[str]:
    """Render Workstream E slice S3's multi-source contract conflicts.

    ``None`` (contract evaluation did not run) renders nothing; an empty
    ``rows`` tuple (it ran and found none) also renders nothing — same
    "omit when there is nothing to show" convention as every other optional
    Markdown section, distinct from the JSON report's own `[]`-means-"ran
    clean" convention (Markdown has no analogous "field was present but
    empty" signal worth spending a heading on).
    """
    if not section or not section.rows:
        return []
    lines = [
        "## Contract Source Conflicts",
        "",
        (
            "_Two or more evidence sources disagree about the same entity. "
            "Both claims are recorded below — neither is resolved for you._"
        ),
        "",
    ]
    for row in section.rows:
        side_suffix = f" ({row.side})" if row.side else ""
        lines.append(f"- **{row.conflict_kind}**{side_suffix}: `{row.entity}`")
        for src_line in row.source_lines:
            lines.append(f"  - {src_line}")
    lines.append("")
    return lines


__all__ = [
    "ContractConflictRow",
    "ContractConflictsSection",
    "render_contract_conflicts_section",
]
