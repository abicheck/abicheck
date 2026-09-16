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

from ..change_registry import ChangeEntity, ChangeOperation
from .change_operation import entity_for_kind, operation_for_kind

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
