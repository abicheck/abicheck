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

"""Workstream G S1's "what changed / review actions" surface-first section.

``docs/contribute/plans/vision-api-abi-evolution.md`` workstream G states the
report invariant this module exists to satisfy: *"Compatible additions are
visible changes: a compatible run still itemizes what was added; '0
breaking' is not 'nothing happened'."* Today's severity-grouped views bury
additions inside a low-severity bucket a reader may never expand; this
module projects the same already-computed findings (``report.finding.
report_findings_for`` -- no new detection, no new policy) into one
surface-shaped grouping: additions, removals, and modifications, each entry
carrying its old/new declaration so a reviewer can act on it directly.

Follows this package's compute/render split (``abicheck/report/AGENTS.md``):
:func:`compute_surface_changes` reads a ``DiffResult`` and returns a frozen
struct of plain values; :func:`render_surface_changes_lines`/
:func:`render_surface_changes_section` format it and decide nothing.
:func:`add_surface_changes` is this section's JSON attachment point, the
counterpart of ``disposition_audit.add_disposition_audit``.

**Grouping, not a new policy decision.** "Addition" is
``policy.severity.IssueCategory.ADDITION`` -- the same category
``ReportFinding.category`` already resolves for every other view, so this
section cannot disagree with the severity-grouped one about which finding is
an addition. "Removal" is any change whose kind spells the ``model``-layer's
own ``*_removed`` naming convention (enforced by the "Adding a new
ChangeKind" procedure in the root ``AGENTS.md`` -- a kind that removes a
declaration is named ``<noun>_removed``); everything else is a
"modification". This is a display grouping, not a severity/gate
classification, so it does not need (and must not read) a fourth kind-set
membership test the way ``ADDITION_KINDS``/``BREAKING_KINDS`` do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .finding import ReportFinding, build_report_findings, report_findings_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult


@dataclass(frozen=True, slots=True)
class SurfaceChangeEntry:
    """One reviewable surface change, with the declarations it touches."""

    kind: str
    symbol: str
    description: str
    verdict: str
    category: str
    old_declaration: str | None
    new_declaration: str | None
    source_location: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "description": self.description,
            "verdict": self.verdict,
            "category": self.category,
            "old_declaration": self.old_declaration,
            "new_declaration": self.new_declaration,
            "source_location": self.source_location,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SurfaceChangeEntry:
        return cls(
            kind=str(d["kind"]),
            symbol=str(d["symbol"]),
            description=str(d.get("description", "")),
            verdict=str(d["verdict"]),
            category=str(d["category"]),
            old_declaration=d.get("old_declaration"),
            new_declaration=d.get("new_declaration"),
            source_location=d.get("source_location"),
        )


@dataclass(frozen=True, slots=True)
class SurfaceChangeSection:
    """The "what changed / review actions" grouping -- additions, removals,
    modifications -- over every detected finding, gating or not."""

    additions: tuple[SurfaceChangeEntry, ...]
    removals: tuple[SurfaceChangeEntry, ...]
    modifications: tuple[SurfaceChangeEntry, ...]

    @property
    def total(self) -> int:
        return len(self.additions) + len(self.removals) + len(self.modifications)

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "additions": [e.to_dict() for e in self.additions],
            "removals": [e.to_dict() for e in self.removals],
            "modifications": [e.to_dict() for e in self.modifications],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SurfaceChangeSection:
        return cls(
            additions=tuple(
                SurfaceChangeEntry.from_dict(e) for e in d.get("additions") or ()
            ),
            removals=tuple(
                SurfaceChangeEntry.from_dict(e) for e in d.get("removals") or ()
            ),
            modifications=tuple(
                SurfaceChangeEntry.from_dict(e) for e in d.get("modifications") or ()
            ),
        )


def _is_removal(finding: ReportFinding) -> bool:
    kind = finding.change.kind
    value = kind.value if hasattr(kind, "value") else str(kind)
    return value.endswith("_removed")


def _entry_for(finding: ReportFinding) -> SurfaceChangeEntry:
    change = finding.change
    return SurfaceChangeEntry(
        kind=change.kind.value if hasattr(change.kind, "value") else str(change.kind),
        symbol=change.symbol,
        description=change.description,
        verdict=finding.verdict.value,
        category=finding.category.value,
        old_declaration=change.old_value,
        new_declaration=change.new_value,
        source_location=change.source_location,
    )


def compute_surface_changes(
    result: DiffResult,
    findings: Sequence[ReportFinding] | None = None,
    *,
    changes: Sequence[Change] | None = None,
) -> SurfaceChangeSection:
    """Group *result*'s findings into additions/removals/modifications.

    *findings*, when given, is reused rather than recomputed -- a caller that
    already ran :func:`~abicheck.report.finding.report_findings_for` for
    another section of the same render should pass its result rather than
    resolving every change's verdict/category twice.

    *changes*, when given (and *findings* is not), is the *displayed* change
    sequence to group instead of ``result.changes`` -- a caller rendering
    under ``--show-only`` must pass its own already-filtered list, or this
    section would show a finding the rest of the same view just filtered
    out (workstream G's "rendering never changes a gate" invariant is about
    verdicts/exit codes, not about which findings a filtered view lists).
    """
    from ..policy.severity import IssueCategory

    if findings is not None:
        resolved = findings
    elif changes is not None:
        resolved = build_report_findings(
            list(changes),
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    else:
        resolved = report_findings_for(result)
    additions: list[SurfaceChangeEntry] = []
    removals: list[SurfaceChangeEntry] = []
    modifications: list[SurfaceChangeEntry] = []
    for finding in resolved:
        entry = _entry_for(finding)
        if finding.category is IssueCategory.ADDITION:
            additions.append(entry)
        elif _is_removal(finding):
            removals.append(entry)
        else:
            modifications.append(entry)
    return SurfaceChangeSection(
        additions=tuple(additions),
        removals=tuple(removals),
        modifications=tuple(modifications),
    )


def add_surface_changes(
    d: dict[str, object],
    result: DiffResult,
    changes: Sequence[Change] | None = None,
) -> None:
    """Attach the ``surface_changes`` block to a JSON report (schema 3.9).

    Unconditional, like ``disposition_audit.add_disposition_audit`` -- a
    compatible run with zero breaking findings still gets an (empty-removal,
    non-empty-addition) block, which is the executable form of "0 breaking
    is not nothing happened".

    *changes*, when given, is the caller's own already-``--show-only``-
    filtered list (see :func:`compute_surface_changes`); omit it only when
    the caller has no filtering of its own to honor.
    """
    d["surface_changes"] = compute_surface_changes(result, changes=changes).to_dict()


def _declaration_line(entry: SurfaceChangeEntry) -> str:
    old = entry.old_declaration
    new = entry.new_declaration
    if old and new and old != new:
        decl = f"`{old}` → `{new}`"
    elif new:
        decl = f"`{new}`"
    elif old:
        decl = f"`{old}`"
    else:
        decl = entry.description
    loc = f" ({entry.source_location})" if entry.source_location else ""
    return f"- **{entry.symbol}** — {decl}{loc}"


def render_surface_changes_lines(section: SurfaceChangeSection) -> list[str]:
    """The Markdown form: one sub-heading per group, each entry itemized
    with its old/new declaration so a reviewer can act on it directly."""
    if section.total == 0:
        return []
    lines: list[str] = []
    groups: tuple[tuple[str, tuple[SurfaceChangeEntry, ...]], ...] = (
        ("Additions", section.additions),
        ("Removals", section.removals),
        ("Modifications", section.modifications),
    )
    for label, entries in groups:
        lines.append(f"**{label}** ({len(entries)})")
        lines.append("")
        if entries:
            lines.extend(_declaration_line(e) for e in entries)
        else:
            lines.append("- none")
        lines.append("")
    return lines


def render_surface_changes_section(section: SurfaceChangeSection | None) -> list[str]:
    """The Markdown *section* every report mode appends, mirroring
    ``disposition_audit.render_disposition_audit_section``. ``None`` renders
    nothing, which is what a document produced before this block existed
    round-trips to. Unlike the disposition audit's table (whose zero counts
    are still a fact worth stating), a section with nothing in any group is
    strictly derived from the same ``changes`` list the surrounding report
    already states as empty ("No ABI changes detected") -- an empty heading
    here would repeat that, not add to it, so it is omitted too.
    """
    if section is None or section.total == 0:
        return []
    return ["", "## Surface changes", "", *render_surface_changes_lines(section)]
