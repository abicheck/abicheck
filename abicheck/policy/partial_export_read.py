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

"""A removal resting on a partially read export table is not a proven break
(ADR-079 D4).

A NEW-side platform block that records ``export_read: partial`` holds real
entries, but the part that was not read may hold the symbol a removal finding
names; one that records ``failed`` proves no absence at all. The finding is kept and annotated (ADR-067 record before disposing):
its kind, old value and this rule stay in the report, its effective verdict
reads ``COMPATIBLE_WITH_RISK`` with the reason below, and the analysis
assurance reads ``partial`` (``analysis_assurance/_export_read.py``), so the
run neither reports a break nobody observed nor reads as clean.

A removal the NEW side's *headers* establish stands: when the NEW snapshot
parsed headers and no longer declares the entity, the removal is a header
fact, not an export-table absence.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.declarations import Function, Variable
from ..model.export_index import snapshot_export_names
from ..model.export_read import ExportReadState
from ..model.snapshot import AbiSnapshot
from .outcome import ScopeCompleteness

__all__ = [
    "PARTIAL_EXPORT_READ_REASON",
    "PARTIAL_EXPORT_READ_RULE",
    "demote_unestablished_export_removals",
    "export_read_scope_completeness",
    "partially_read_platforms",
]

PARTIAL_EXPORT_READ_RULE = "partial_export_table_read"
PARTIAL_EXPORT_READ_REASON = (
    "the new side's export table was not completely read (ADR-079); the "
    "symbol may be in the unread part, so its removal is not established"
)

#: Findings whose only evidence can be the symbol's absence from the NEW
#: side's export table.
_REMOVAL_KINDS = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
        ChangeKind.VAR_REMOVED_ELF_ONLY,
        ChangeKind.IFUNC_REMOVED,
    }
)


def partially_read_platforms(snap: AbiSnapshot) -> tuple[str, ...]:
    """The platforms whose export table *snap* records (ADR-079) as not
    completely read: ``partial`` or ``failed``. A block with no record
    (``unrecorded``, a pre-v70 document) is not listed -- the pre-v70 rule
    (``compare.edge_query.export_table_covered``) still governs it."""
    gap = (ExportReadState.PARTIAL, ExportReadState.FAILED)
    return tuple(
        platform
        for platform in ("elf", "pe", "macho")
        if getattr(getattr(snap, platform, None), "export_read", None) in gap
    )


def _new_declares(new: AbiSnapshot, change: Change) -> bool:
    spellings = {
        s for s in (change.symbol, change.old_value) if isinstance(s, str) and s
    }
    decls = new.declarations
    functions: list[Function] = list(decls.functions)
    variables: list[Variable] = list(decls.variables)
    return any(f.mangled in spellings or f.name in spellings for f in functions) or any(
        v.mangled in spellings or v.name in spellings for v in variables
    )


def _header_establishes_removal(new: AbiSnapshot, change: Change) -> bool:
    """The NEW side parsed headers and no longer declares the entity."""
    parsed_headers = bool(getattr(new, "ast_producer", None)) or bool(
        getattr(new, "header_only", False)
    )
    return parsed_headers and not _new_declares(new, change)


def demote_unestablished_export_removals(
    changes: list[Change], new: AbiSnapshot
) -> list[Change]:
    """Annotate each removal the NEW side's partial export table cannot
    establish as ``COMPATIBLE_WITH_RISK``. Mutates and returns *changes*; a
    no-op unless the NEW side records a partial or failed read."""
    platforms = partially_read_platforms(new)
    if not platforms:
        return changes
    exported: frozenset[str] | None = None
    for change in changes:
        if change.effective_verdict is not None or change.kind not in _REMOVAL_KINDS:
            continue
        if exported is None:
            exported = snapshot_export_names(new)
        if change.symbol in exported or _header_establishes_removal(new, change):
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = PARTIAL_EXPORT_READ_RULE
        change.modulation_reason = (
            f"{PARTIAL_EXPORT_READ_REASON} ({', '.join(platforms)})"
        )
    return changes


def export_read_scope_completeness(assurance: object) -> ScopeCompleteness:
    """ADR-079 D4: the scalar comparison's ``run_outcome.scope``.

    ``INCOMPLETE`` when either side's export table is recorded as partially
    read or failed (the assurance's ``export_read`` states), so a comparison
    that relied on it reports incomplete coverage; ``COMPLETE`` otherwise --
    the fixed scalar value ADR-065 D6 gives every other single-pair run. Under
    the default ``scope.on_incomplete: warn`` this adds no exit contribution.
    """
    by_side = getattr(assurance, "export_read", None) or {}
    gap = {ExportReadState.PARTIAL.value, ExportReadState.FAILED.value}
    for states in by_side.values():
        if any(state in gap for state in states.values()):
            return ScopeCompleteness.INCOMPLETE
    return ScopeCompleteness.COMPLETE
