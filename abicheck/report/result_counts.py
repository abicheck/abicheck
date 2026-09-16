# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Named, non-overlapping populations used by every concise report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .change_operation import entity_for_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .disposition_audit import DispositionAudit
    from .finding import ReportFinding
    from .review_groups import ReviewGroup


@dataclass(frozen=True, slots=True)
class ResultCounts:
    raw_detected: int
    retained: int
    gating: int
    non_gating: int
    review_groups: int
    gating_review_groups: int
    public_additions: int
    public_removals: int
    public_modifications: int
    detected_public_additions: int
    detected_public_removals: int
    detected_public_modifications: int
    runtime_dependency: int
    hygiene_introduced: int
    hygiene_resolved: int
    hygiene_persistent: int
    hygiene_not_evaluated: int

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


def compute_result_counts(
    findings: Sequence[ReportFinding],
    groups: Sequence[ReviewGroup],
    audit: DispositionAudit,
    observed_changes: Sequence[Any] = (),
) -> ResultCounts:
    public = {"function", "variable", "type", "enum"}
    operations = {"added": 0, "removed": 0, "modified": 0}
    runtime = 0
    hygiene = {"introduced": 0, "resolved": 0, "persistent": 0, "not_evaluated": 0}
    for finding in findings:
        change = finding.change
        kind = change.kind.value
        evolution = getattr(change.cross_source_evolution, "value", None)
        if evolution is not None:
            hygiene[evolution] += 1
            continue
        entity = entity_for_change(change, kind)
        if entity not in public and entity in {"binary", "build"}:
            runtime += 1
    for group in groups:
        if group.entity in public:
            operations[group.operation] += 1
    # Policy overlays already occur in ``findings``; they are excluded only
    # from the raw observation ledger. Adding ``policy_overlays`` again would
    # double-count the diagnostic in the retained population.
    retained = len(findings)
    from .review_groups import review_operation

    detected_operations = {"added": 0, "removed": 0, "modified": 0}
    detected_keys: set[tuple[str, str, str]] = set()
    for change in observed_changes:
        kind_obj = getattr(change, "kind", "")
        kind = getattr(kind_obj, "value", str(kind_obj))
        if entity_for_change(change, kind) in public:
            identity = str(
                getattr(change, "entity_id", None)
                or getattr(change, "qualified_name", None)
                or getattr(change, "demangled_symbol", None)
                or getattr(change, "symbol", "?")
            )
            operation = review_operation(kind)
            key = (str(getattr(change, "library", None) or ""), identity, operation)
            if key not in detected_keys:
                detected_keys.add(key)
                detected_operations[operation] += 1
    gating = audit.effective_total
    return ResultCounts(
        raw_detected=audit.detected_total,
        retained=retained,
        gating=gating,
        non_gating=max(0, retained - gating),
        review_groups=len(groups),
        gating_review_groups=sum(group.gating_findings > 0 for group in groups),
        public_additions=operations["added"],
        public_removals=operations["removed"],
        public_modifications=operations["modified"],
        detected_public_additions=detected_operations["added"],
        detected_public_removals=detected_operations["removed"],
        detected_public_modifications=detected_operations["modified"],
        runtime_dependency=runtime,
        hygiene_introduced=hygiene["introduced"],
        hygiene_resolved=hygiene["resolved"],
        hygiene_persistent=hygiene["persistent"],
        hygiene_not_evaluated=hygiene["not_evaluated"],
    )
