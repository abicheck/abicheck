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

"""Entity-by-operation rollup of an already-serialized report's findings.

Report-owned (ADR-061: "add a report field, report schema, or output format"
-> ``report/``) so every renderer -- the PR comment today, Markdown/HTML if
they want it tomorrow -- reads one answer instead of each growing its own.

Two properties this module exists to guarantee, both of which the PR
comment's own ad-hoc grouping could not:

* **Canonical classification only.** The entity and operation of a finding
  are read from the report's own per-finding ``entity``/``operation`` fields
  (:mod:`abicheck.report.change_operation`, which reads
  ``ChangeKindMeta``). When a report predates those fields, the same
  canonical registry is consulted by kind -- never a kind-*name* prefix or
  suffix rule, which is exactly the classification defect
  ``change_operation.py``'s own docstring records.
* **Counting units are stated, not implied.** A row counts *findings*, not
  declarations and not unique symbols: two findings about one function are
  two findings. :attr:`ChangeSummary.unit` carries that word so a renderer
  cannot silently relabel it, and :attr:`ChangeSummary.exact` records
  whether the list this was computed from was complete.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..model.change_catalog.registry import ChangeEntity, ChangeOperation
from .change_operation import (
    entity_for_change,
    entity_for_kind,
    operation_for_change,
    operation_for_kind,
)

#: Display label per canonical :class:`ChangeEntity`. Plural, because a row
#: counts findings across many entities of that kind.
ENTITY_LABELS: dict[str, str] = {
    ChangeEntity.FUNCTION.value: "Functions",
    ChangeEntity.VARIABLE.value: "Variables",
    ChangeEntity.TYPE.value: "Types",
    ChangeEntity.ENUM.value: "Enums",
    ChangeEntity.BINARY.value: "Binary/container",
    ChangeEntity.BUILD.value: "Build evidence",
    ChangeEntity.SOURCE.value: "Source evidence",
    ChangeEntity.ANALYSIS.value: "Analysis coverage",
}

#: Row order, so two reports' tables are comparable at a glance.
_ENTITY_ORDER = list(ENTITY_LABELS)

#: Label for a finding whose kind is in no registry at all (a hand-built
#: ``Change``, or a report from a newer build). Deliberately *not* folded
#: into one of the real entity rows -- "we could not classify this" is a
#: different fact from "this was a type change".
UNCLASSIFIED_LABEL = "Unclassified"

_OPERATIONS = (
    ChangeOperation.REMOVED.value,
    ChangeOperation.MODIFIED.value,
    ChangeOperation.ADDED.value,
)


#: ``Change.cross_source_evolution`` states that mean *this comparison did
#: not introduce the finding* (ADR-068 D3, stamped by
#: ``workflows/cross_source_hygiene.py``). A one-sided hygiene check is re-run
#: against the baseline and its findings paired, so the comparison layer
#: already knows which are new; a renderer reads that answer and never
#: re-derives it (ADR-072 D1).
#:
#: - ``persistent`` -- flagged identically on both sides: standing library
#:   debt (a template-instantiation guard variable, a function-local static),
#:   not something the change did.
#: - ``resolved`` -- flagged on the baseline only: good news, and still not a
#:   change to review in the candidate.
#:
#: ``not_evaluated`` is not background: one side's evidence could not confirm
#: or deny the finding, an analysis limitation. ``introduced`` is the
#: comparison's own finding.
BACKGROUND_EVOLUTIONS = frozenset({"persistent", "resolved"})

#: Every state an entity-by-operation rollup leaves out: the two background
#: states, plus ``not_evaluated`` -- there the comparison declined to say
#: whether the finding is new at all, so counting it would assert as a
#: modification what the comparison refused to assert. Two of the three mean
#: "present before this change", the third "we could not tell"; a rollup of
#: asserted modifications may claim none of them.
NOT_THIS_CHANGES_EVOLUTIONS = BACKGROUND_EVOLUTIONS | {"not_evaluated"}


def is_this_changes_own(evolution: object) -> bool:
    """Whether a finding stamped *evolution* (a ``cross_source_evolution``
    value, its enum, or absent) belongs in a "What changed" rollup.

    The one predicate every rollup applies -- the scalar PR comment's, and
    the release report's ``change_counts`` -- so a release member counts
    exactly what the same pair's scalar comment counts. Unstamped and
    unrecognised values are the comparison's own.
    """
    value = getattr(evolution, "value", evolution)
    return str(value or "") not in NOT_THIS_CHANGES_EVOLUTIONS


@dataclass(frozen=True)
class EntityRow:
    """One entity's finding counts, split by canonical operation."""

    entity: str
    label: str
    removed: int = 0
    modified: int = 0
    added: int = 0

    @property
    def total(self) -> int:
        return self.removed + self.modified + self.added


@dataclass(frozen=True)
class ChangeSummary:
    """Entity-by-operation rollup over one report's findings."""

    rows: tuple[EntityRow, ...] = ()
    #: What one unit in :class:`EntityRow` counts. Always ``"findings"``
    #: today; carried explicitly so a renderer states it rather than
    #: guessing "changes"/"symbols".
    unit: str = "findings"
    #: ``False`` when the finding list this was computed from was itself
    #: capped/truncated, so the counts are a floor rather than a total. A
    #: renderer must disclose that instead of presenting partial totals as
    #: complete.
    exact: bool = True
    #: Why the counts are not exact, when ``exact`` is ``False``.
    inexact_reason: str = ""
    #: Findings counted, i.e. the sum of every row's total.
    counted: int = 0
    #: Fields the producing report did not carry, forcing a by-kind
    #: registry lookup instead. Diagnostic only; never rendered as a
    #: limitation, since the fallback is equally canonical.
    fell_back_to_registry: int = field(default=0, compare=False)

    @property
    def is_empty(self) -> bool:
        return not self.rows


def _entity_of(change: Mapping[str, object]) -> tuple[str | None, bool]:
    """(canonical entity value or ``None``, whether a fallback was used)."""
    stated = change.get("entity")
    if isinstance(stated, str) and stated in ENTITY_LABELS:
        return stated, False
    kind = str(change.get("kind", "") or "")
    return entity_for_kind(kind), True


def _operation_of(change: Mapping[str, object]) -> str:
    stated = change.get("operation")
    if isinstance(stated, str) and stated in _OPERATIONS:
        return stated
    return operation_for_kind(str(change.get("kind", "") or ""))


def summarize_changes(
    changes: Sequence[object],
    *,
    exact: bool = True,
    inexact_reason: str = "",
) -> ChangeSummary:
    """Roll *changes* (serialized finding dicts) up by entity and operation.

    *exact* is the caller's assertion that *changes* is the complete finding
    list for the comparison being summarized. A caller holding a capped list
    (a bundle report's per-library ``findings`` sample) must pass
    ``exact=False`` with a *inexact_reason*: reconstructing a total from a
    truncated list is the one failure this dataclass's ``exact`` flag exists
    to make impossible to do silently.
    """
    buckets: dict[str, dict[str, int]] = {}
    fallbacks = 0
    counted = 0
    for raw in changes:
        if not isinstance(raw, Mapping):
            continue
        entity, used_fallback = _entity_of(raw)
        if used_fallback:
            fallbacks += 1
        key = entity if entity in ENTITY_LABELS else UNCLASSIFIED_LABEL
        buckets.setdefault(key, {})
        op = _operation_of(raw)
        buckets[key][op] = buckets[key].get(op, 0) + 1
        counted += 1

    rows: list[EntityRow] = []
    for key in [*_ENTITY_ORDER, UNCLASSIFIED_LABEL]:
        ops = buckets.get(key)
        if not ops:
            continue
        rows.append(
            EntityRow(
                entity=key,
                label=ENTITY_LABELS.get(key, UNCLASSIFIED_LABEL),
                removed=ops.get(ChangeOperation.REMOVED.value, 0),
                modified=ops.get(ChangeOperation.MODIFIED.value, 0),
                added=ops.get(ChangeOperation.ADDED.value, 0),
            )
        )
    return ChangeSummary(
        rows=tuple(rows),
        exact=exact,
        inexact_reason=inexact_reason if not exact else "",
        counted=counted,
        fell_back_to_registry=fallbacks,
    )


def fold_change_summaries(
    summaries: Sequence[ChangeSummary],
    *,
    inexact_reason: str = "",
) -> ChangeSummary:
    """Merge several reports' rollups into one, row by row.

    Used when one comment covers several reports (``aggregate``'s fan-in --
    see :mod:`abicheck.report.pr_comment_aggregate`). Folding the already-
    computed :class:`ChangeSummary` objects, rather than re-summarizing a
    concatenation of finding dicts, is what keeps a member whose own rollup
    was already inexact honest: there is no finding list to re-count for it,
    only a floor it already declared.

    Three invariants, stated here because they are the whole contract:

    * **Addition, not recomputation.** Every operation count is the plain
      sum of its inputs', and :attr:`ChangeSummary.counted` is the sum of
      the inputs' ``counted`` -- never re-derived from the folded rows,
      which would let a row-vs-total disagreement pass silently.
    * **Inexactness is absorbing.** The fold is ``exact`` only when *every*
      input was. One member counting from a capped list makes the total a
      floor, and a floor presented as a total is the one thing
      :attr:`ChangeSummary.exact` exists to prevent. When an input was
      inexact and the caller states no *inexact_reason*, the first input's
      own reason is carried.
    * **Order-independent.** Rows come out in :data:`_ENTITY_ORDER`
      regardless of input order, so folding the same set of members in a
      different order produces an equal result.

    An empty *summaries* (or one holding only empty rollups) folds to an
    empty, exact :class:`ChangeSummary` -- "nothing to summarize", which
    :attr:`ChangeSummary.is_empty` already means and every renderer already
    skips.
    """
    totals: dict[str, dict[str, int]] = {}
    counted = 0
    fallbacks = 0
    exact = True
    carried_reason = ""
    for summary in summaries:
        counted += summary.counted
        fallbacks += summary.fell_back_to_registry
        if not summary.exact:
            exact = False
            if not carried_reason:
                carried_reason = summary.inexact_reason
        for row in summary.rows:
            bucket = totals.setdefault(row.entity, {})
            bucket["removed"] = bucket.get("removed", 0) + row.removed
            bucket["modified"] = bucket.get("modified", 0) + row.modified
            bucket["added"] = bucket.get("added", 0) + row.added
    rows = [
        EntityRow(
            entity=key,
            label=ENTITY_LABELS.get(key, UNCLASSIFIED_LABEL),
            removed=totals[key].get("removed", 0),
            modified=totals[key].get("modified", 0),
            added=totals[key].get("added", 0),
        )
        for key in [*_ENTITY_ORDER, UNCLASSIFIED_LABEL]
        if key in totals
    ]
    return ChangeSummary(
        rows=tuple(rows),
        exact=exact,
        inexact_reason="" if exact else (inexact_reason or carried_reason),
        counted=counted,
        fell_back_to_registry=fallbacks,
    )


def summarize_change_objects(changes: Sequence[object]) -> ChangeSummary:
    """:func:`summarize_changes` over live ``Change`` objects this comparison
    established as its own (:func:`is_this_changes_own`), exact.

    Each finding is classified the way ``change_json`` writes a scalar
    report's per-finding ``entity``/``operation`` (:func:`entity_for_change`,
    :func:`operation_for_change`), so a release member's count equals the
    rollup of the same pair's scalar ``compare`` report.
    """
    rows: list[dict[str, object]] = []
    for change in changes:
        if not is_this_changes_own(getattr(change, "cross_source_evolution", None)):
            continue
        kind = getattr(change, "kind", "")
        kind_val = str(getattr(kind, "value", kind) or "")
        rows.append(
            {
                "kind": kind_val,
                "entity": entity_for_change(change, kind_val),
                "operation": operation_for_change(change, kind_val),
            }
        )
    return summarize_changes(rows)


#: The JSON key a release document carries its authoritative rollup under:
#: ``libraries[].change_counts`` per member and the top-level fold (release
#: schema 2.7).
CHANGE_COUNTS_KEY = "change_counts"


def change_counts_to_dict(summary: ChangeSummary) -> dict[str, object]:
    """The serialized ``change_counts`` block of an exact *summary*.

    ``{"unit", "counted", "rows": [{"entity", "removed", "modified",
    "added"}, ...]}`` -- the entity is the canonical :class:`ChangeEntity`
    value (or :data:`UNCLASSIFIED_LABEL`), never the display label, so a
    reader re-labels it the one way :data:`ENTITY_LABELS` does. Only an
    exact summary is serialized: a floor written as a count would be read
    back as a total.
    """
    if not summary.exact:
        raise ValueError("change_counts records complete counts only")
    return {
        "unit": summary.unit,
        "counted": summary.counted,
        "rows": [
            {
                "entity": row.entity,
                "removed": row.removed,
                "modified": row.modified,
                "added": row.added,
            }
            for row in summary.rows
        ],
    }


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def change_summary_from_counts(block: object) -> ChangeSummary | None:
    """Read a ``change_counts`` block back into an exact :class:`ChangeSummary`.

    ``None`` for anything that is not a well-formed block -- an absent key
    (a document older than release schema 2.7), a foreign unit, a negative
    or non-integer count, or rows that do not sum to ``counted``. A caller
    falls back to its own inexact path then; it never trusts half a block.
    """
    if not isinstance(block, Mapping) or block.get("unit") != "findings":
        return None
    counted = _count(block.get("counted"))
    raw_rows = block.get("rows")
    if counted is None or not isinstance(raw_rows, list):
        return None
    by_entity: dict[str, EntityRow] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            return None
        entity = raw.get("entity")
        removed, modified, added = (_count(raw.get(op)) for op in _OPERATIONS)
        if removed is None or modified is None or added is None:
            return None
        if not isinstance(entity, str) or entity in by_entity:
            return None
        if entity not in ENTITY_LABELS and entity != UNCLASSIFIED_LABEL:
            return None
        by_entity[entity] = EntityRow(
            entity=entity,
            label=ENTITY_LABELS.get(entity, UNCLASSIFIED_LABEL),
            removed=removed,
            modified=modified,
            added=added,
        )
    rows = tuple(
        by_entity[key]
        for key in [*_ENTITY_ORDER, UNCLASSIFIED_LABEL]
        if key in by_entity and by_entity[key].total
    )
    if sum(row.total for row in rows) != counted:
        return None
    return ChangeSummary(rows=rows, counted=counted)


def _finding_dicts(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def release_change_summary(report: Mapping[str, object]) -> ChangeSummary | None:
    """The entity-by-operation rollup of a release (directory/package) report.

    Exact when the document carries the top-level ``change_counts`` block
    (release schema 2.7): the producer's own fold over every member's
    complete, pre-view finding pool plus the bundle and matrix findings.
    A document without it (older than 2.7) is summarized from its finding
    lists and marked ``exact=False``: those lists may be view-filtered, and
    an older producer's per-library list may have been capped, so the
    counts are only a floor.
    """
    authoritative = change_summary_from_counts(report.get(CHANGE_COUNTS_KEY))
    if authoritative is not None:
        return authoritative if authoritative.rows else None
    sampled: list[Mapping[str, object]] = []
    for lib in _finding_dicts(report.get("libraries")):
        sampled.extend(_finding_dicts(lib.get("findings")))
    for key in ("bundle_findings", "matrix_findings"):
        sampled.extend(_finding_dicts(report.get(key)))
    sampled = [
        f for f in sampled if is_this_changes_own(f.get("cross_source_evolution"))
    ]
    if not sampled:
        return None
    return summarize_changes(
        sampled,
        exact=False,
        inexact_reason=(
            "this report predates release schema 2.7's change_counts, so "
            "these are counted from its finding lists and are lower bounds"
        ),
    )
