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


"""Render ``compare --use-cases``'s attribution block: JSON, text and Markdown.

The report-side half of :mod:`abicheck.policy.use_case_attribution`. Everything
here reads an already-computed :class:`UseCaseImpact` and shapes it for a
reader; nothing here attributes a finding to a use case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.use_case_attribution import UseCaseImpact

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.change import Change
    from ..policy.use_case_attribution import UseCaseChange


def _use_case_change_to_dict(change: UseCaseChange) -> dict[str, str]:
    return {
        "symbol": change.symbol,
        "kind": change.kind,
        "finding_id": change.finding_id,
    }


def use_case_impact_to_dict(impact: UseCaseImpact) -> dict[str, Any]:
    """The ``use_case_impact`` report block, as JSON-ready data."""
    return {
        "manifest": impact.manifest,
        "use_case_count": impact.use_case_count,
        "total_changes": impact.total_changes,
        "unattributed_changes": impact.unattributed_changes,
        "use_cases": [
            {
                "use_case": r.use_case,
                "resolved_entrypoints": list(r.resolved_entrypoints),
                "unresolved_entrypoints": list(r.unresolved_entrypoints),
                "tests": list(r.tests),
            }
            for r in impact.resolutions
        ],
        "by_use_case": {
            name: [_use_case_change_to_dict(c) for c in changes]
            for name, changes in impact.by_use_case.items()
        },
    }


def render_use_case_impact_lines(impact: UseCaseImpact) -> list[str]:
    """The human-facing section for a text/markdown report."""
    lines = ["", f"Use-case impact ({impact.manifest}):"]
    for resolution in impact.resolutions:
        changes = impact.by_use_case.get(resolution.use_case, ())
        lines.append(f"  {resolution.use_case}: {len(changes)} change(s)")
        for change in changes:
            lines.append(f"    - {change.symbol} ({change.kind})")
        if resolution.unresolved_entrypoints:
            lines.append(
                "    unresolved entrypoints: "
                + ", ".join(resolution.unresolved_entrypoints)
            )
    lines.append(
        f"  {impact.unattributed_changes} of {impact.total_changes} change(s) "
        "reached by no declared entrypoint (absence of proof, not proof of "
        "absence)."
    )
    return lines


def add_use_case_impact(
    d: dict[str, Any], result: Any, displayed: Sequence[Change] | None = None
) -> None:
    """Emit ``compare --use-cases``'s block into a JSON report dict.

    Lives here rather than in ``reporter.py`` for the ordinary reason that
    module's helpers keep moving out: it is at the 2000-line hard cap.

    Omitted entirely without the flag rather than emitted empty: an empty
    block would read as "no use case is affected" for a run that never
    resolved a manifest.

    *displayed*, when given, is the change list this report actually shows
    (``--show-only`` already applied); the block is projected onto it so it
    cannot name a finding the reader cannot see. ``None`` means "everything
    is displayed" and emits the block unchanged -- distinct from an empty
    list, which really is a report showing nothing.
    """
    impact = getattr(result, "use_case_impact", None)
    if isinstance(impact, UseCaseImpact):
        if displayed is not None:
            impact = impact.restricted_to(displayed)
        d["use_case_impact"] = use_case_impact_to_dict(impact)
        annotate_affected_use_cases(d.get("changes"), impact)


def annotate_affected_use_cases(entries: object, impact: UseCaseImpact) -> None:
    """Stamp ``affected_use_cases`` on each already-built change entry.

    Joined on the entry's own ``finding_id`` -- the key ``by_use_case``
    rows already carry -- so the per-finding field is the report-level
    block read the other way round, never a second attribution. An entry no
    use case reaches reads ``[]`` ("attribution ran, reached by none"); the
    key is absent only when ``--use-cases`` was not given at all, which is
    what keeps every pre-existing report byte-identical. Root-cause mode's
    ``findings`` lists hold the same dict objects as ``changes``, so they
    pick the field up without a second pass.
    """
    if not isinstance(entries, list):
        return
    inverse = impact.use_cases_by_finding()
    for entry in entries:
        if isinstance(entry, dict) and "finding_id" in entry:
            entry["affected_use_cases"] = list(
                inverse.get(str(entry["finding_id"]), ())
            )


def use_case_impact_block(
    result: Any, displayed: Sequence[Any] | None = None
) -> dict[str, Any] | None:
    """*result*'s attribution as the JSON block, or ``None`` when none ran.

    *displayed* is the ``--show-only`` filtered list, or ``None`` when every
    finding is displayed -- the same projection the JSON block applies.
    """
    impact = getattr(result, "use_case_impact", None)
    if not isinstance(impact, UseCaseImpact):
        return None
    if displayed is not None:
        impact = impact.restricted_to(displayed)
    return use_case_impact_to_dict(impact)


def use_case_impact_text_lines(
    result: Any, displayed: Sequence[Any] | None = None
) -> list[str] | None:
    """*result*'s attribution as text lines, or ``None`` when none ran.

    *displayed* projects the attribution onto the findings the report above
    it actually lists, so the section cannot resurface a filtered-out change.
    """
    impact = getattr(result, "use_case_impact", None)
    if not isinstance(impact, UseCaseImpact):
        return None
    if displayed is not None:
        impact = impact.restricted_to(displayed)
    return render_use_case_impact_lines(impact)


def use_cases_by_finding_for(result: Any) -> dict[str, tuple[str, ...]] | None:
    """*result*'s per-finding attribution, or ``None`` when none ran.

    The renderer-side reader (Markdown rows, review digest): the same
    inverse of ``by_use_case`` the JSON ``affected_use_cases`` field is
    built from. Not projected through ``--show-only`` because it does not
    need to be: projection only drops findings, and a displayed finding's
    own attribution is identical before and after it.
    """
    impact = getattr(result, "use_case_impact", None)
    if not isinstance(impact, UseCaseImpact):
        return None
    return impact.use_cases_by_finding()
