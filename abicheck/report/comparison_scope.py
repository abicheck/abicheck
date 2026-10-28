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
the completeness axis is *decided* by ``policy.scope_completeness.
resolve_scope_decision`` (the record, the policy, ``run_outcome.scope``,
and the two exit contributions, as one :class:`~abicheck.policy.
scope_completeness.ScopeDecision`); :func:`build_comparison_scope_section`
projects that already-made decision into one JSON-shaped mapping (the
*fact* -- what every format carries, the release JSON verbatim as its
``comparison_scope`` key), and :func:`render_comparison_scope_markdown` /
:func:`comparison_scope_notice` format that mapping. Nothing here computes
a contribution or a policy: a caller that needs the exit values reads them
off the decision it resolved, never off this module (Codex review,
twenty-eighth round). The PR-comment renderer reads the same mapping back
off the JSON, so the three views cannot disagree about which member went
unchecked or why.

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
from ..policy.scope_completeness import ScopeDecision
from .markdown_text import md_text

__all__ = [
    "ComparisonScopeTerms",
    "build_comparison_scope_section",
    "comparison_scope_notice",
    "comparison_scope_terms",
    "render_comparison_scope_markdown",
]


@dataclass(frozen=True)
class ComparisonScopeTerms:
    """One resolved :class:`ScopeDecision` plus its JSON projection, built
    exactly once so ``run_outcome.scope``, the JSON section, and the
    Markdown section cannot disagree with the decision the exit fold read.

    ``section`` is ``None`` when the decision carries no record (a scalar
    comparison, a direct unit-test call, a legacy path). The exit
    contributions are deliberately *not* mirrored here: read them off
    ``decision``, the policy-owned object.
    """

    decision: ScopeDecision
    section: dict[str, Any] | None

    @property
    def record(self) -> ScopeAcquisitionRecord | None:
        """The decided record (``None`` for a scalar comparison)."""
        return self.decision.record

    @property
    def policy(self) -> str:
        """The effective ``scope.on_incomplete`` policy the decision applied."""
        return self.decision.policy

    @property
    def completeness(self) -> ScopeCompleteness:
        """The decided ``run_outcome.scope`` value."""
        return self.decision.completeness


def comparison_scope_terms(decision: ScopeDecision) -> ComparisonScopeTerms:
    """Project an already-resolved *decision* (never a record + policy: the
    deciding happened in ``policy``) into :class:`ComparisonScopeTerms`."""
    return ComparisonScopeTerms(
        decision=decision,
        section=(
            None
            if decision.record is None
            else build_comparison_scope_section(decision)
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


def build_comparison_scope_section(decision: ScopeDecision) -> dict[str, Any]:
    """The ``comparison_scope`` mapping: the record, the policy it was
    judged under, and the two contributions the exit decision actually
    folded -- every value copied off *decision*, none recomputed here.
    *decision* must carry a record."""
    record = decision.record
    if record is None:
        raise ValueError(
            "build_comparison_scope_section needs a decision with a record"
        )
    section = {
        "schema_version": SCOPE_ACQUISITION_SCHEMA_VERSION,
        "completeness": decision.completeness.value,
        "policy": decision.policy,
        "incomplete_scope_exit_contribution": decision.incomplete_scope_exit_contribution,
        "no_comparison_completed": record.no_comparison_completed,
        "no_comparison_completed_exit_contribution": (
            decision.no_comparison_completed_exit_contribution
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
    if record.variant_pairing is not None:  # ADR-062 A1.6: stored/stored only
        section["variant_pairing"] = record.variant_pairing.to_dict()
    return section


def _unchecked_rows(section: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The member rows the unchecked list renders: exactly the section's own
    resolved ``unchecked`` names, never re-derived from acquisition state.
    A proven removal/addition keeps its ``not_supplied`` state but is a
    finding the record already answered for, not a member that went
    unchecked (the same projection the JUnit scope suite applies)."""
    members = section.get("members")
    names = section.get("unchecked")
    if not isinstance(members, list) or not isinstance(names, list):
        return []
    unchecked_names = set(names)
    return [
        m
        for m in members
        if isinstance(m, Mapping) and m.get("name") in unchecked_names
    ]


def comparison_scope_notice(section: Mapping[str, Any]) -> str | None:
    """One line for compact views (PR comment, ``--stat``-style summaries),
    or ``None`` when the scope was fully checked."""
    if section.get("completeness") != "incomplete":
        return None
    rows = _unchecked_rows(section)
    names = ", ".join(
        f"{md_text(m.get('name'), table=True)} ({_STATE_LABEL.get(str(m.get('state')), m.get('state'))})"
        for m in rows
    )
    # D7 first: a zero-comparison run fails under either policy (its own
    # contribution is always 1), so attributing it to `block` would name a
    # policy the user may not have selected (Codex review). Only a partial
    # scope's failure is the D6 policy's doing.
    if section.get("no_comparison_completed"):
        lead = "No comparison completed"
        if names:
            lead += f"; unchecked: {names}"
        return lead + " -- never a clean pass, under either scope.on_incomplete setting"
    lead = f"Comparison scope incompletely checked; unchecked: {names}"
    blocking = int(section.get("incomplete_scope_exit_contribution") or 0)
    policy = section.get("policy", "warn")
    tail = (
        " -- fails the run (scope.on_incomplete: block)"
        if blocking
        else f" -- accepted as a warning (scope.on_incomplete: {policy})"
    )
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
            "comparison, which is never a clean pass."
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
        f"| **Selection** | {md_text(section.get('selection_reason') or section.get('selection'), table=True)} |"
    )
    lines.append(
        f"| **Policy** | `scope.on_incomplete: {section.get('policy', 'warn')}` "
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
                f"| {md_text(m.get('name'), code=True, table=True)} | {_STATE_LABEL.get(str(m.get('state')), m.get('state'))} "
                f"| {'✓' if m.get('old_present') else '—'} | {'✓' if m.get('new_present') else '—'} "
                f"| {md_text(m.get('reason', ''), table=True)} |"
            )
    for key, title in (
        ("proven_removed", "Removed libraries (inventory-proven)"),
        ("proven_added", "Added libraries (inventory-proven)"),
    ):
        names = section.get(key)
        if isinstance(names, list) and names:
            lines += [
                "",
                f"**{title}:** " + ", ".join(md_text(n, code=True) for n in names),
            ]
    return lines
