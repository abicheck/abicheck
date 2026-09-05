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

"""ADR-065 S2's report section: ``comparison_scope``.

The ``compute_*``/``render_*`` split this package's ``AGENTS.md`` asks for:
:func:`build_comparison_scope_section` turns the typed acquisition record
plus the two already-resolved exit contributions into one JSON-shaped
mapping (the *fact* -- what every format carries, the release JSON verbatim
as its ``comparison_scope`` key), and :func:`render_comparison_scope_markdown`
/ :func:`comparison_scope_notice` format that mapping and decide nothing.
The PR-comment renderer reads the same mapping back off the JSON, so the
three views cannot disagree about which member went unchecked or why.

Every view states the incompleteness (ADR-065's reporting rule): the
top-level wording never says "compatible" for the whole scope, only for the
compared members, and a run with zero completed comparisons is named as
such in the one-line notice too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..model.scope_acquisition import (
    SCOPE_ACQUISITION_SCHEMA_VERSION,
    AcquisitionState,
    ScopeAcquisitionRecord,
)
from ..policy.outcome import ScopeCompleteness
from ..policy.scope_completeness import (
    incomplete_scope_exit_contribution,
    no_comparison_completed_exit_contribution,
    scope_completeness_for_record,
    validate_incomplete_scope_policy,
)

__all__ = [
    "ComparisonScopeTerms",
    "build_comparison_scope_section",
    "comparison_scope_notice",
    "comparison_scope_terms",
    "render_comparison_scope_markdown",
]


@dataclass(frozen=True)
class ComparisonScopeTerms:
    """Everything a release writer needs from one acquisition record,
    derived exactly once so the exit fold, ``run_outcome.scope``, the JSON
    section, and the Markdown section cannot disagree.

    ``record``/``section`` are ``None`` for a caller with no record (a
    direct unit-test call, a legacy path); every contribution is then
    ``0`` and ``completeness`` is ``COMPLETE`` -- the scalar reading.
    """

    record: ScopeAcquisitionRecord | None
    policy: str
    completeness: ScopeCompleteness
    incomplete_scope_exit_contribution: int
    no_comparison_completed_exit_contribution: int
    section: dict[str, Any] | None


def comparison_scope_terms(
    record: ScopeAcquisitionRecord | None, policy: str | None
) -> ComparisonScopeTerms:
    """Resolve :class:`ComparisonScopeTerms` for *record* under *policy*."""
    effective = validate_incomplete_scope_policy(policy)
    isc = incomplete_scope_exit_contribution(record, effective)
    ncc = no_comparison_completed_exit_contribution(record)
    return ComparisonScopeTerms(
        record=record,
        policy=effective,
        completeness=scope_completeness_for_record(record),
        incomplete_scope_exit_contribution=isc,
        no_comparison_completed_exit_contribution=ncc,
        section=(
            None
            if record is None
            else build_comparison_scope_section(
                record,
                policy=effective,
                incomplete_scope_exit_contribution=isc,
                no_comparison_completed_exit_contribution=ncc,
            )
        ),
    )


_STATE_LABEL = {
    AcquisitionState.AVAILABLE.value: "compared",
    AcquisitionState.EXPECTED_NOT_PRODUCED.value: "expected, not produced",
    AcquisitionState.FAILED.value: "failed",
    AcquisitionState.NOT_SUPPLIED.value: "not supplied",
    AcquisitionState.UNSUPPORTED.value: "unsupported",
    AcquisitionState.OUT_OF_SCOPE.value: "out of scope",
    AcquisitionState.AMBIGUOUS.value: "ambiguous",
}


def build_comparison_scope_section(
    record: ScopeAcquisitionRecord,
    *,
    policy: str,
    incomplete_scope_exit_contribution: int,
    no_comparison_completed_exit_contribution: int,
) -> dict[str, Any]:
    """The ``comparison_scope`` mapping: the record, the policy it was
    judged under, and the two contributions the exit decision actually
    folded (read from the caller, never recomputed here)."""
    completeness = scope_completeness_for_record(record)
    return {
        "schema_version": SCOPE_ACQUISITION_SCHEMA_VERSION,
        "completeness": completeness.value,
        "policy": policy,
        "incomplete_scope_exit_contribution": incomplete_scope_exit_contribution,
        "no_comparison_completed": record.no_comparison_completed,
        "no_comparison_completed_exit_contribution": (
            no_comparison_completed_exit_contribution
        ),
        "selection": record.selection,
        "selection_reason": record.selection_reason,
        "old_inventory": record.old_inventory.to_dict(),
        "new_inventory": record.new_inventory.to_dict(),
        "counts": record.counts(),
        "members": [m.to_dict() for m in record.members],
        "unchecked": [m.name for m in record.unchecked_members],
        "out_of_scope": [m.name for m in record.out_of_scope_members],
        "proven_removed": [m.name for m in record.proven_removed_members],
        "proven_added": [m.name for m in record.proven_added_members],
    }


def _unchecked_rows(section: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    members = section.get("members")
    if not isinstance(members, list):
        return []
    return [
        m
        for m in members
        if isinstance(m, Mapping)
        and m.get("state")
        not in (AcquisitionState.AVAILABLE.value, AcquisitionState.OUT_OF_SCOPE.value)
    ]


def comparison_scope_notice(section: Mapping[str, Any]) -> str | None:
    """One line for compact views (PR comment, ``--stat``-style summaries),
    or ``None`` when the scope was fully checked."""
    if section.get("completeness") != "incomplete":
        return None
    rows = _unchecked_rows(section)
    names = ", ".join(
        f"{m.get('name')} ({_STATE_LABEL.get(str(m.get('state')), m.get('state'))})"
        for m in rows
    )
    if section.get("no_comparison_completed"):
        lead = "No comparison completed"
        if names:
            lead += f"; unchecked: {names}"
    else:
        lead = f"Comparison scope incompletely checked; unchecked: {names}"
    blocking = int(section.get("incomplete_scope_exit_contribution") or 0) or int(
        section.get("no_comparison_completed_exit_contribution") or 0
    )
    policy = section.get("policy", "warn")
    tail = (
        " -- fails the run (--on-incomplete-scope block)"
        if blocking
        else f" -- accepted as a warning (--on-incomplete-scope {policy})"
    )
    if section.get("no_comparison_completed") and not blocking:
        tail = " -- never a clean pass"
    return lead + tail


def render_comparison_scope_markdown(section: Mapping[str, Any]) -> list[str]:
    """The Markdown ``## Comparison Scope`` section (release report)."""
    completeness = str(section.get("completeness", "complete"))
    counts = section.get("counts") if isinstance(section.get("counts"), Mapping) else {}
    compared = int(counts.get("available", 0)) if counts else 0
    out_of_scope = int(counts.get("out_of_scope", 0)) if counts else 0
    icon = "✅" if completeness == "complete" else "⚠️"
    lines = ["", "## 🧭 Comparison Scope", ""]
    if section.get("no_comparison_completed"):
        lines.append(
            "**No comparison completed** — the selected scope produced no valid "
            "comparison, which is never a clean pass (ADR-065 D7)."
        )
        lines.append("")
    lines.append(
        f"| **Scope** | {icon} `{completeness}` — {compared} member(s) compared"
        + (f", {out_of_scope} out of scope" if out_of_scope else "")
        + " |"
    )
    lines.insert(len(lines) - 1, "| | |")
    lines.insert(len(lines) - 1, "|---|---|")
    lines.append(
        f"| **Selection** | {section.get('selection_reason') or section.get('selection')} |"
    )
    lines.append(
        f"| **Policy** | `--on-incomplete-scope {section.get('policy', 'warn')}` "
        f"(contributes {section.get('incomplete_scope_exit_contribution', 0)} to the exit code) |"
    )
    old_inv = section.get("old_inventory") or {}
    new_inv = section.get("new_inventory") or {}
    lines.append(
        f"| **Inventory** | OLD `{old_inv.get('completeness', '?')}`, "
        f"NEW `{new_inv.get('completeness', '?')}` |"
    )
    rows = _unchecked_rows(section)
    if rows:
        lines += [
            "",
            "Members that did not reach a completed comparison (the verdict above "
            "covers the compared members only):",
            "",
            "| Member | State | OLD | NEW | Reason |",
            "|---|---|---|---|---|",
        ]
        for m in rows:
            lines.append(
                f"| `{m.get('name')}` | {_STATE_LABEL.get(str(m.get('state')), m.get('state'))} "
                f"| {'✓' if m.get('old_present') else '—'} | {'✓' if m.get('new_present') else '—'} "
                f"| {m.get('reason', '')} |"
            )
    for key, title in (
        ("proven_removed", "Removed libraries (inventory-proven)"),
        ("proven_added", "Added libraries (inventory-proven)"),
    ):
        names = section.get(key)
        if isinstance(names, list) and names:
            lines += ["", f"**{title}:** " + ", ".join(f"`{n}`" for n in names)]
    return lines
