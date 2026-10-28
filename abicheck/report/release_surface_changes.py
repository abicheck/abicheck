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

"""The release (directory/package) ``surface_changes`` block.

Each compared member's additions/removals/modifications come from
:func:`~abicheck.report.surface_changes.compute_surface_changes` -- the same
owner the scalar report uses, so a member's entries equal what a standalone
``compare`` of that pair reports, plus a ``library`` attribution. The member's
scalar block is attached to its own ``libraries[]`` entry before the release
renderers strip the working ``_diff_result`` (see
:func:`attach_member_surface_changes`), the same way ``change_inventory`` is
carried per member; the release block is the fold of those members.

Libraries present on one side only are *not* surface changes: each is listed
in ``library_states`` with the ADR-065 state the acquisition record already
decided. A library is ``removed`` or ``added`` only when the record's proven
inventory says so (``proven_removed_members``/``proven_added_members``); every
other one-sided library keeps its own state -- ``unselected``,
``expected_not_produced``, ``failed``, ``unproven``, or a support-promise
``retired``/``introduced`` -- so an absent member is never reported as a
removal without inventory evidence.

This module decides nothing about verdicts or exit codes; it projects the
completed comparisons it is handed. The live and the stored release operands
both build the block here, which keeps their renderings in agreement.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from ..model.scope_acquisition import AcquisitionState
from .markdown_text import md_text
from .surface_changes import (
    SurfaceChangeEntry,
    SurfaceChangeSection,
    compute_surface_changes,
    render_surface_changes_lines,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.scope_acquisition import MemberAcquisition, ScopeAcquisitionRecord

#: ADR-065's named states for a one-sided library that is *not* a proven
#: removal or addition, keyed by the acquisition state the record holds for
#: it. ``NOT_SUPPLIED`` without proof is ``unproven``: the library is
#: unmatched, and its absence on the other side is not established.
_UNPROVEN_STATE_LABELS: dict[AcquisitionState, str] = {
    AcquisitionState.OUT_OF_SCOPE: "unselected",
    AcquisitionState.EXPECTED_NOT_PRODUCED: "expected_not_produced",
    AcquisitionState.FAILED: "failed",
    AcquisitionState.NOT_SUPPLIED: "unproven",
    AcquisitionState.UNSUPPORTED: "unsupported",
    AcquisitionState.AMBIGUOUS: "ambiguous",
    AcquisitionState.DECLARED_ABSENT: "declared_absent",
}


def attach_member_surface_changes(
    library_results: Sequence[MutableMapping[str, object]],
) -> None:
    """Attach each compared member's scalar ``surface_changes`` block.

    Must run before the release renderers strip ``_diff_result``: the block
    is the one value a member keeps. A member already carrying the key is left
    alone, so the call is idempotent.
    """
    from ..checker_types import DiffResult

    for entry in library_results:
        diff = entry.get("_diff_result")
        if "surface_changes" in entry or not isinstance(diff, DiffResult):
            continue
        entry["surface_changes"] = compute_surface_changes(diff).to_dict()


def member_surface_section(
    entry: Mapping[str, object],
) -> SurfaceChangeSection | None:
    """A member's surface section, from its attached block, else computed from
    its still-present ``DiffResult``; ``None`` when it has neither (an
    operational outcome that never reached a comparison)."""
    from ..checker_types import DiffResult

    block = entry.get("surface_changes")
    if isinstance(block, Mapping):
        return SurfaceChangeSection.from_dict(block)
    diff = entry.get("_diff_result")
    if isinstance(diff, DiffResult):
        return compute_surface_changes(diff)
    return None


def _row_for(
    member: MemberAcquisition,
    proven_removed: set[str],
    proven_added: set[str],
) -> dict[str, object]:
    if member.member in proven_removed:
        state = "removed"
    elif member.member in proven_added:
        state = "added"
    else:
        state = _UNPROVEN_STATE_LABELS.get(member.state, member.state.value)
    return {"library": member.name, "state": state, "reason": member.reason}


def _library_state_rows(
    record: ScopeAcquisitionRecord | None,
    promise_states: Mapping[str, str],
) -> list[dict[str, object]]:
    """One row per library present on one side only, plus each support-promise
    component (``retired``/``introduced``). Sorted by library name."""
    rows: list[dict[str, object]] = []
    if record is not None:
        proven_removed = {m.member for m in record.proven_removed_members}
        proven_added = {m.member for m in record.proven_added_members}
        for member in record.expected_members:
            # Exactly one side present: a member absent on both sides was never
            # produced at all, which the comparison-scope section reports.
            if member.old_present == member.new_present:
                continue
            rows.append(_row_for(member, proven_removed, proven_added))
    rows.extend(
        {"library": name, "state": state, "reason": ""}
        for name, state in promise_states.items()
    )
    return sorted(rows, key=lambda row: str(row["library"]))


def release_surface_changes(
    sections: Sequence[tuple[str, SurfaceChangeSection]],
    record: ScopeAcquisitionRecord | None,
    promise_states: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """The release ``surface_changes`` block over the completed members.

    *sections* are ``(library, section)`` pairs for compared members.
    *promise_states* maps a support-promise component to ``retired`` or
    ``introduced``; such a component's own comparison is not a member, so it
    is excluded from *sections* and listed once in ``library_states``.

    The block is the scalar ``surface_changes`` shape (``total``,
    ``additions``, ``removals``, ``modifications``) with every entry carrying
    its ``library``, plus ``counts`` (the aggregate), ``members`` (per-member
    counts, sorted by name) and ``library_states``.
    """
    promise = dict(promise_states or {})
    additions: list[SurfaceChangeEntry] = []
    removals: list[SurfaceChangeEntry] = []
    modifications: list[SurfaceChangeEntry] = []
    per_member: list[dict[str, object]] = []
    for name, section in sorted(sections, key=lambda pair: pair[0]):
        if name in promise:
            continue
        additions.extend(replace(e, library=name) for e in section.additions)
        removals.extend(replace(e, library=name) for e in section.removals)
        modifications.extend(replace(e, library=name) for e in section.modifications)
        per_member.append(
            {
                "library": name,
                "total": section.total,
                "additions": len(section.additions),
                "removals": len(section.removals),
                "modifications": len(section.modifications),
            }
        )
    block: dict[str, object] = SurfaceChangeSection(
        additions=tuple(additions),
        removals=tuple(removals),
        modifications=tuple(modifications),
    ).to_dict()
    block["counts"] = {
        "additions": len(additions),
        "removals": len(removals),
        "modifications": len(modifications),
        "members_compared": len(per_member),
    }
    block["members"] = per_member
    block["library_states"] = _library_state_rows(record, promise)
    return block


def live_surface_changes(
    library_results: Sequence[Mapping[str, object]],
    record: ScopeAcquisitionRecord | None,
) -> dict[str, object]:
    """The block for a live fan-out, folded from its ``libraries[]`` entries."""
    sections: list[tuple[str, SurfaceChangeSection]] = []
    promise: dict[str, str] = {}
    for entry in library_results:
        name = str(entry.get("library", ""))
        if "support_promise" in entry:
            promise[name] = str(entry["support_promise"])
        section = member_surface_section(entry)
        if section is not None:
            sections.append((name, section))
    return release_surface_changes(sections, record, promise)


def render_release_surface_lines(block: Mapping[str, object]) -> list[str]:
    """The Markdown ``## Surface changes`` section of a release document.

    Empty when the block has no entry and no one-sided library, mirroring the
    scalar section. The library states are a table, so the records the JSON
    carries are readable in place.
    """
    section = SurfaceChangeSection.from_dict(block)
    raw_states = block.get("library_states")
    states = [
        row
        for row in (raw_states if isinstance(raw_states, list) else [])
        if isinstance(row, Mapping)
    ]
    if section.total == 0 and not states:
        return []
    members = block.get("members")
    compared = len(members) if isinstance(members, list) else 0
    lines = [
        "",
        "## Surface changes",
        "",
        f"{section.total} surface change(s) across {compared} compared member(s).",
        "",
    ]
    lines += render_surface_changes_lines(section)
    if states:
        lines += ["**Libraries present on one side only**", ""]
        lines += ["| Library | State | Reason |", "|---|---|---|"]
        lines += [
            f"| {md_text(row['library'], code=True, table=True)} "
            f"| {md_text(row['state'], table=True)} | {md_text(row.get('reason') or '', table=True)} |"
            for row in states
        ]
        lines.append("")
    return lines
