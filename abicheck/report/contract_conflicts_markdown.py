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

import re
from dataclasses import dataclass

from .markdown_budget import NAME_LIMIT, clip, more_line


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


#: Rows itemised in Markdown; the rest are counted. The JSON
#: ``contract_conflicts`` block keeps every row. Without a cap a C++ library
#: exporting libstdc++ template instantiations rendered a 355 KB section on a
#: byte-identical rebuild (SVS field report), past what a GitHub step summary
#: or PR comment can hold.
CONFLICT_ROW_LIMIT = 20
#: Characters of one entity name shown; the JSON keeps the full name.
CONFLICT_NAME_LIMIT = NAME_LIMIT
#: Characters of one source line shown.
CONFLICT_SOURCE_LINE_LIMIT = 300

_RUNTIME_DEMANGLED_PREFIXES = ("std::", "__gnu_cxx::", "__cxxabiv1::")

#: An Itanium name owned by ``std``/``__gnu_cxx``: a nested name (``_ZN``,
#: ``_ZNK``), special name (``_ZT[VIST]``), local entity (``_ZZ``) or guard
#: (``_ZGVZ``) whose first component is ``St`` or one of the standard
#: substitutions (``Sa`` allocator, ``Sb`` basic_string, ``Ss`` string,
#: ``Si``/``So``/``Sd`` istream/ostream/iostream), or ``9__gnu_cxx``.
_RUNTIME_MANGLED = re.compile(r"^_Z(?:GVZ|Z|T[VIST])?N?K?(?:S[tabsiod]|9__gnu_cxx)")


def conflict_origin(entity: str) -> str:
    """``toolchain runtime`` for a C++ runtime entity, else ``library``.

    A display grouping only: the contract's own origin decision is
    ``policy.contract_toolchain_origin``'s and is not re-derived here.
    """
    runtime = entity.startswith(_RUNTIME_DEMANGLED_PREFIXES) or bool(
        _RUNTIME_MANGLED.match(entity)
    )
    return "toolchain runtime" if runtime else "library"


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

    The section is bounded: a count per conflict kind and origin, then at
    most :data:`CONFLICT_ROW_LIMIT` rows (library-owned entities first) with
    clipped names. Nothing is dropped from the JSON report.
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
    counts: dict[tuple[str, str], int] = {}
    for row in section.rows:
        key = (row.conflict_kind, conflict_origin(row.entity))
        counts[key] = counts.get(key, 0) + 1
    if len(section.rows) > CONFLICT_ROW_LIMIT or len(counts) > 1:
        lines += ["| Conflict | Origin | Count |", "|---|---|---|"]
        for (kind, origin), n in sorted(counts.items()):
            lines.append(f"| {kind} | {origin} | {n} |")
        lines.append("")
    ordered = sorted(section.rows, key=lambda r: conflict_origin(r.entity) != "library")
    for row in ordered[:CONFLICT_ROW_LIMIT]:
        side_suffix = f" ({row.side})" if row.side else ""
        entity = clip(row.entity, CONFLICT_NAME_LIMIT)
        lines.append(f"- **{row.conflict_kind}**{side_suffix}: `{entity}`")
        for src_line in row.source_lines:
            lines.append(f"  - {clip(src_line, CONFLICT_SOURCE_LINE_LIMIT)}")
    hidden = len(section.rows) - CONFLICT_ROW_LIMIT
    if (more := more_line(hidden, "contract_conflicts")) is not None:
        lines.append(more)
    lines.append("")
    return lines


__all__ = [
    "ContractConflictRow",
    "CONFLICT_ROW_LIMIT",
    "ContractConflictsSection",
    "conflict_origin",
    "render_contract_conflicts_section",
]
