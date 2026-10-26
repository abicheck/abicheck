# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Named, non-overlapping populations used by every concise report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .change_operation import entity_for_change

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .disposition_audit import DispositionAudit
    from .finding import ReportFinding
    from .review_groups import ReviewGroup


@dataclass(frozen=True, slots=True)
class ResultCounts:
    raw_detected: int
    retained: int
    gating: int
    non_gating: int
    #: The retained findings that gate (the review groups' sum); ``gating``
    #: may exceed it by redundant findings folded out of the findings but
    #: still scored by the legacy verdict (see :func:`held_out_note`).
    gating_retained: int
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
    gating_retained = sum(group.gating_findings for group in groups)
    return ResultCounts(
        raw_detected=audit.detected_total,
        retained=retained,
        gating=audit.effective_total,
        non_gating=retained - gating_retained,
        gating_retained=gating_retained,
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


def held_out_non_gating(counts: Mapping[str, int], audit: DispositionAudit) -> int:
    """Non-gating changes the audit records but the retained findings omit.

    ``result_counts.non_gating`` counts the *retained* findings; the audit's
    ``non_gating`` also counts changes held out of them before display (a
    redundant finding folded into another, an opaque-handle downgrade, an
    out-of-surface or reconciled change). oneCCL: 49 retained vs 59 audited,
    the 10 being folded ``exported_not_public`` findings -- both right, but a
    reader saw two disagreeing numbers with nothing linking them.
    """
    audited = dict(audit.counts).get("non_gating", 0)
    return max(0, audited - int(counts.get("non_gating", 0)))


def held_out_gating(counts: Mapping[str, int]) -> int:
    """Gating findings folded out of the retained findings: a redundant
    finding the legacy verdict still scores (ledger ``application_point:
    redundancy_filter_scored``). Without this the line read "4 retained
    (5 gating, 0 non-gating)", a gating count larger than its population."""
    retained_gating = int(counts.get("gating_retained", counts.get("gating", 0)))
    return max(0, int(counts.get("gating", 0)) - retained_gating)


def held_out_note(counts: Mapping[str, int], audit: DispositionAudit | None) -> str:
    """``" + 1 gating folded into another finding (still scored by the
    verdict) + 10 non-gating held out of the findings (see the disposition
    audit)"`` for whichever held-out population is nonzero, else ``""``."""
    note = ""
    folded = held_out_gating(counts)
    if folded:
        note += (
            f" + {folded} gating folded into another finding "
            "(still scored by the verdict)"
        )
    held = held_out_non_gating(counts, audit) if audit is not None else 0
    if held:
        note += (
            f" + {held} non-gating held out of the findings (see the disposition audit)"
        )
    return note
