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

"""ADR-067 D5 acknowledgment resolution over a closed-over ledger.

Fills ``DispositionRecord.acknowledged_by`` for every observed change a loaded
acknowledgment list matches, and -- under ``breaks: accept`` (see
:mod:`abicheck.policy.acknowledgment_breaks`) -- records an acknowledged gating
break as non-gating under the acknowledgment application point.

Split out of ``disposition_close.py`` (the 800-line production cap); the close
pass calls :func:`resolve_acknowledgments` exactly as it did when this lived
there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from ..model.change_catalog.kind_variants import (
    ROLLUP_MEMBER_KINDS,
    rollup_member_kinds,
)
from .acknowledgment_breaks import (
    ACCEPTABLE_BREAK_VERDICTS,
    ACCEPTED_BREAK_APPLICATION_POINT,
)
from .disposition_ledger import Disposition, DispositionLedger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import date

    from ..checker_types import DiffResult

__all__ = ["break_verdict_reader", "resolve_acknowledgments"]


def resolve_acknowledgments(
    ledger: DispositionLedger,
    acknowledgments: object | None,
    *,
    component: str | None = None,
    baseline: str | None = None,
    release_label: str | None = None,
    strict: bool = False,
    breaks: str = "gate",
    verdict_class_of: Callable[[object], str] | None = None,
) -> None:
    """Fill in ``acknowledged_by`` (ADR-067 D5/C-S3) -- the acknowledgment
    counterpart of ``DispositionLedger.resolve_reclassifications``. Reaches
    ``ledger``'s private record/anchor lists directly, the same private access
    the close pass already uses. Duck-typed (only ``.evaluate`` is called); a
    no-op when *acknowledgments* is ``None``/empty (every pre-existing caller).

    *breaks* / *verdict_class_of* are ADR-067 D5's ``breaks: accept``: with
    ``accept``, an acknowledged gating break whose verdict class (read through
    *verdict_class_of*) is a break is recorded as non-gating under the
    acknowledgment application point.

    *strict* controls what happens on an ambiguous match
    (:class:`~abicheck.policy.acknowledgment.AmbiguousAcknowledgmentError`,
    D5: "an ambiguous or unknown identity requires review"). This function
    runs from two different contexts that need two different answers:
    ``checker.compare()`` (via :func:`finalize_ledger`, ``strict=True``) is
    the run that *owns* the comparison, and must let the error propagate
    for every finding kind, not only the additions-review gate's own direct
    ``evaluate()`` call covers. ``ledger_for()``'s reconciliation fallback
    (a report projection over an already-produced, hand-built
    ``DiffResult`` with no persisted ledger, ``strict=False``) must still be
    able to state D3's counts rather than take the whole render down over
    one unresolved overlay fact — the run that could have raised already
    had its chance (CodeRabbit review, PR #1137).
    """
    from .acknowledgment import AmbiguousAcknowledgmentError
    from .rule_inventory import KIND_ACKNOWLEDGMENT, RuleRef

    evaluate = getattr(acknowledgments, "evaluate", None)
    if not callable(evaluate):
        return
    # P1: the record's position in the loaded list is its join key for the
    # disposition audit's acknowledgment row. Identity, not equality.
    listed = list(acknowledgments) if hasattr(acknowledgments, "__iter__") else []
    position = {id(record): i for i, record in enumerate(listed)}
    source = getattr(acknowledgments, "source_path", None)
    scope = {
        "component": component,
        "baseline": baseline,
        "release_label": release_label,
    }
    unresolvable = _changes_matched_by_ambiguous_records(
        ledger, acknowledgments, scope=scope, strict=strict
    )
    for index, (record, change) in enumerate(
        zip(ledger._records, ledger._anchors)  # noqa: SLF001
    ):
        if record.acknowledged_by is not None:
            continue  # already resolved (e.g. a re-closed scoped record)
        if id(change) in unresolvable:
            continue  # non-strict only: see _changes_matched_by_ambiguous_records
        try:
            ack = evaluate(change, **scope)
        except AmbiguousAcknowledgmentError:
            if strict:
                raise
            continue  # unresolved -- see the docstring's non-strict note above
        if ack is None:
            continue
        record_id_fn = getattr(ack, "record_id", None)
        acknowledged_by = record_id_fn() if callable(record_id_fn) else str(ack)
        covered = RuleRef(
            KIND_ACKNOWLEDGMENT,
            None if source is None else str(source),
            position.get(id(ack)),
        )
        # ADR-067 D5 `breaks: accept`: an acknowledged break that gated the run
        # is recorded as its own non-gating disposition, under the
        # acknowledgment application point. Its verdict class is stamped (the
        # break is still a break); nothing else about the change moves.
        verdict = (
            verdict_class_of(change)
            if breaks == "accept"
            and verdict_class_of is not None
            and record.disposition is Disposition.GATING
            else None
        )
        if verdict in ACCEPTABLE_BREAK_VERDICTS:
            ledger._records[index] = replace(  # noqa: SLF001
                record,
                acknowledged_by=acknowledged_by,
                disposition=Disposition.NON_GATING,
                application_point=ACCEPTED_BREAK_APPLICATION_POINT,
                verdict_class=verdict,
                acknowledgment_rule=covered,
            )
        else:
            ledger._records[index] = replace(  # noqa: SLF001
                record, acknowledged_by=acknowledged_by, acknowledgment_rule=covered
            )
    _carry_rollup_acknowledgments(ledger)


def _carry_rollup_acknowledgments(ledger: DispositionLedger) -> None:
    """Acknowledge each roll-up whose covered removals are all acknowledged.

    A roll-up (``public_surface_shrank``) has no symbol a record can name, so
    it cannot be acknowledged directly. It is acknowledged exactly when every
    removal it summarises (the covered members, variants included, that were
    not suppressed) carries an acknowledgment, and then it takes the first
    covered removal's record. Acknowledgment never hides a finding, so the
    roll-up stays visible with its verdict class; only its record changes.
    """
    records = ledger._records  # noqa: SLF001
    for index, record in enumerate(records):
        if record.kind not in ROLLUP_MEMBER_KINDS or record.acknowledged_by is not None:
            continue
        members = rollup_member_kinds(record.kind)
        covered = [
            r
            for r in records
            if r.kind in members and r.disposition is not Disposition.SUPPRESSED
        ]
        if not covered or any(r.acknowledged_by is None for r in covered):
            continue
        first = covered[0]
        records[index] = replace(
            record,
            acknowledged_by=first.acknowledged_by,
            acknowledgment_rule=first.acknowledgment_rule,
        )


def _changes_matched_by_ambiguous_records(
    ledger: DispositionLedger,
    acknowledgments: object,
    *,
    scope: dict[str, str | None],
    strict: bool,
) -> set[int]:
    """ADR-067 D5/G3: a record must match exactly one finding in a run.

    ``evaluate()`` only catches two *records* matching one change. A record
    keyed by a canonical finding id, or by a symbol and kind, can also match
    two *changes* in the same run -- the id is deliberately not unique per
    finding (``report_canonical_finding_id``), so two distinct findings can
    share it -- and accepting both by one line is the silent double-accept D5
    forbids. Strict callers (the run that owns the comparison) raise; a
    projection over an already-built result returns the ids of the changes
    such a record would have covered, so they are left unresolved rather than
    accepted by an ambiguous line.
    """
    from .acknowledgment import AmbiguousAcknowledgmentError

    try:
        records = list(acknowledgments)  # type: ignore[call-overload]
    except TypeError:
        return set()
    observed = [
        change
        for record, change in zip(ledger._records, ledger._anchors)  # noqa: SLF001
        if not record.policy_overlay
    ]
    unresolvable: set[int] = set()
    for ack_record in records:
        matched = {
            id(change) for change in observed if ack_record.matches(change, **scope)
        }
        if len(matched) <= 1:
            continue
        if strict:
            raise AmbiguousAcknowledgmentError(
                f"acknowledgment {ack_record.record_id()} matches {len(matched)} "
                "changes in this run: its finding_id (or symbol) is shared by "
                "more than one finding. A record must match exactly one finding "
                "per run; narrow it with change_kind, component or candidate."
            )
        unresolvable |= matched
    return unresolvable


def break_verdict_reader(
    result: DiffResult, today: date | None
) -> Callable[[object], str]:
    """The per-change verdict class reader ``breaks: accept`` decides on."""
    from .acknowledgment_breaks import break_verdict_of

    def read(change: object) -> str:
        return break_verdict_of(change, result, today=today)

    return read
