# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Canonical, presentation-only correlation of retained report findings."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..finding_identity import report_finding_id, resolve_change_identity
from .change_operation import entity_for_change, operation_for_kind

if TYPE_CHECKING:
    from .finding import ReportFinding

_EXPORT_KINDS = frozenset({"func_removed_elf_only", "func_visibility_changed"})
_VTABLE_KINDS = frozenset(
    {"type_vtable_changed", "vtable_slot_count_changed", "rtti_inheritance_changed"}
)


@dataclass(frozen=True, slots=True)
class ReviewGroup:
    group_id: str
    library: str | None
    entity: str
    operation: str
    display_name: str
    transition: str
    member_finding_ids: tuple[str, ...]
    member_kinds: tuple[str, ...]
    exact_symbols: tuple[str, ...]
    evidence: tuple[str, ...]
    gating_findings: int
    consequence: str
    action: str

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _family(kind: str) -> str:
    if kind in _EXPORT_KINDS:
        return "export"
    if kind in _VTABLE_KINDS:
        return "vtable"
    return kind


def review_operation(kind: str) -> str:
    """Operation of a correlated review event, independent of severity."""
    family = _family(kind)
    if family == "export":
        return "removed"
    if family == "vtable":
        return "modified"
    return operation_for_kind(kind)


def _identity(finding: ReportFinding) -> str:
    change = finding.change
    if change.entity_id is not None:
        return str(change.entity_id)
    if _family(change.kind.value) in {"export", "vtable"}:
        return change.qualified_name or change.demangled_symbol or change.symbol
    return resolve_change_identity(change).primary_id


def _vtable_transition(members: list[ReportFinding]) -> tuple[str, str]:
    header = next(
        (m.change for m in members if m.change.kind.value == "type_vtable_changed"),
        None,
    )
    size = next(
        (
            m.change
            for m in members
            if m.change.kind.value == "vtable_slot_count_changed"
        ),
        None,
    )
    inheritance = next(
        (
            m.change
            for m in members
            if m.change.kind.value == "rtti_inheritance_changed"
        ),
        None,
    )
    parts: list[str] = []
    consequence = (
        "The virtual interface changed; fixed dispatch offsets may no longer match."
    )
    header_evidence = header.review_evidence if header is not None else None
    if (
        isinstance(header_evidence, dict)
        and header_evidence.get("kind") == "declared_vtable_sequence"
    ):
        old_value = header_evidence.get("old_entries", ())
        new_value = header_evidence.get("new_entries", ())
        old = (
            tuple(str(item) for item in old_value)
            if isinstance(old_value, Sequence)
            else ()
        )
        new = (
            tuple(str(item) for item in new_value)
            if isinstance(new_value, Sequence)
            else ()
        )
    elif (
        header is not None
        and header.old_value is not None
        and header.new_value is not None
    ):
        # Migration fallback for pre-5.1 stored/public Change objects. Current
        # producers always take the structured branch above.
        old = tuple(
            item.strip() for item in header.old_value.split(",") if item.strip()
        )
        new = tuple(
            item.strip() for item in header.new_value.split(",") if item.strip()
        )
    else:
        old = new = ()
    if old or new:
        if new[: len(old)] == old and len(new) > len(old):
            parts.append(
                f"declared sequence appended {len(new) - len(old)} entr{'y' if len(new) - len(old) == 1 else 'ies'}; {len(old)} prior entries retain order"
            )
            consequence = "Append-only header evidence does not prove every emitted layout or existing subclass is safe."
        elif old[: len(new)] == new and len(old) > len(new):
            parts.append(
                f"declared sequence removed {len(old) - len(new)} trailing entr{'y' if len(old) - len(new) == 1 else 'ies'}"
            )
        elif len(old) == len(new) and sorted(old) == sorted(new):
            parts.append("declared entries reordered")
        elif len(old) == len(new):
            parts.append("declared entries replaced")
        else:
            parts.append(
                f"declared sequence changed ({len(old)} to {len(new)} entries)"
            )
    size_evidence = size.review_evidence if size is not None else None
    if (
        isinstance(size_evidence, dict)
        and size_evidence.get("kind") == "elf_vtable_group_size"
    ):
        parts.append(
            f"emitted ELF vtable group {size_evidence.get('old_bytes', '?')} "
            f"to {size_evidence.get('new_bytes', '?')} bytes; size alone cannot "
            "identify slots or inheritance changes"
        )
    elif size is not None:
        parts.append(
            f"emitted ELF vtable group {size.old_value or '?'} to {size.new_value or '?'} bytes; size alone cannot identify slots or inheritance changes"
        )
    if inheritance is not None:
        parts.append(
            f"ELF RTTI inheritance shape changed ({inheritance.old_value or '?'} to "
            f"{inheritance.new_value or '?'} bytes); RTTI size identifies a shape "
            "change, not the exact base transition"
        )
    if not parts:
        parts.append("virtual interface changed with incomplete layout evidence")
    return "; ".join(parts), consequence


def build_review_groups(findings: Sequence[ReportFinding]) -> tuple[ReviewGroup, ...]:
    """Group only structurally identical, explicitly-correlatable findings."""
    buckets: dict[tuple[str, str, str], list[ReportFinding]] = {}
    for finding in findings:
        change = finding.change
        kind = change.kind.value
        family = _family(kind)
        if family == "export" and kind == "func_visibility_changed":
            facts = change.surface_facts or {}
            if (
                facts.get("declared_in_headers") != "true"
                or facts.get("binary_exported") != "false"
            ):
                family = kind
        key = (change.library or "", _identity(finding), family)
        buckets.setdefault(key, []).append(finding)

    expanded: list[tuple[tuple[str, str, str], list[ReportFinding]]] = []
    for key, members in buckets.items():
        if key[2] == "vtable" and _vtable_evidence_conflicts(members):
            expanded.extend(
                ((key[0], _identity(member), member.change.kind.value), [member])
                for member in members
            )
        else:
            expanded.append((key, members))

    groups: list[ReviewGroup] = []
    for (library, identity, family), members in expanded:
        members = sorted(
            members,
            key=lambda member: (
                member.change.kind.value,
                report_finding_id(member.change),
            ),
        )
        kinds = tuple(m.change.kind.value for m in members)
        operations = tuple(dict.fromkeys(operation_for_kind(k) for k in kinds))
        entity = entity_for_change(members[0].change, kinds[0]) or "unknown"
        # A group a reader cannot identify is not a group. All three name
        # sources can legitimately be the empty string -- `Change.symbol` is
        # typed `str` and "" satisfies it -- and `a or b or c` then resolves
        # to "", so the group renders under an empty heading. Falling back to
        # the kind is stable, sorts, and still says what was observed.
        #
        # It also keeps the sort key below total against a `None` symbol,
        # which the annotation forbids and no producer in `abicheck/` emits
        # (`make_change(symbol: str)`, mypy-clean across every call site).
        # That is belt-and-braces, not the reachable case: the reachable one
        # is "" (see `docs/contribute/known-gaps.md`).
        display = (
            members[0].change.qualified_name
            or members[0].change.demangled_symbol
            or members[0].change.symbol
            or kinds[0]
        )
        evidence = tuple(
            dict.fromkeys(
                e for m in members for e in (m.change.evidence_provenance or ())
            )
        )
        gating = sum(m.verdict.name in {"BREAKING", "API_BREAK"} for m in members)
        if family == "export":
            group_operation = review_operation(kinds[0])
            transition = "public declaration retained; dynamic export removed"
            consequence = (
                "Existing binaries referencing this export may fail to link or load."
            )
            action = "Restore the export or explicitly review the ABI break."
        elif family == "vtable":
            group_operation = review_operation(kinds[0])
            transition, consequence = _vtable_transition(members)
            action = "Review consumer and subclass compatibility."
        else:
            group_operation = review_operation(kinds[0])
            transition = "/".join(operations)
            consequence = members[0].change.description
            action = "Review the detailed finding and its evidence."
        raw_key = "\0".join((library, identity, family, *kinds))
        groups.append(
            ReviewGroup(
                group_id=hashlib.sha256(raw_key.encode()).hexdigest()[:16],
                library=library or None,
                entity=entity,
                operation=group_operation,
                display_name=display,
                transition=transition,
                member_finding_ids=tuple(report_finding_id(m.change) for m in members),
                member_kinds=kinds,
                exact_symbols=tuple(dict.fromkeys(m.change.symbol for m in members)),
                evidence=evidence,
                gating_findings=gating,
                consequence=consequence,
                action=action,
            )
        )
    return tuple(
        sorted(
            groups,
            key=lambda g: (
                -g.gating_findings,
                g.library or "",
                g.display_name,
                g.group_id,
            ),
        )
    )


def _vtable_evidence_conflicts(members: list[ReportFinding]) -> bool:
    sequence = next(
        (
            m.change.review_evidence
            for m in members
            if m.change.kind.value == "type_vtable_changed"
        ),
        None,
    )
    size = next(
        (
            m.change.review_evidence
            for m in members
            if m.change.kind.value == "vtable_slot_count_changed"
        ),
        None,
    )
    if not isinstance(sequence, dict) or not isinstance(size, dict):
        return False
    old_entries = sequence.get("old_entries")
    new_entries = sequence.get("new_entries")
    old_bytes, new_bytes = size.get("old_bytes"), size.get("new_bytes")
    if not isinstance(old_entries, list) or not isinstance(new_entries, list):
        return False
    if not isinstance(old_bytes, int) or not isinstance(new_bytes, int):
        return False
    sequence_direction = (len(new_entries) > len(old_entries)) - (
        len(new_entries) < len(old_entries)
    )
    size_direction = (new_bytes > old_bytes) - (new_bytes < old_bytes)
    return bool(
        sequence_direction and size_direction and sequence_direction != size_direction
    )
