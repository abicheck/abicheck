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

"""ADR-067 D3 (P1): per-rule disposition rows, from the configured inventory.

:mod:`abicheck.policy.rule_inventory` holds the pure pieces (refs, statuses,
the row builder). This module wires them to a finished comparison:

* :func:`stamp_reclassifications` -- records, for every finding, which
  ``reclassify:`` rules matched it and which one decided its verdict. Called
  from :meth:`DispositionLedger.resolve_reclassifications`, so every closing
  pass stamps the same way.
* :func:`configured_rule_rows` -- one audit row per configured rule across all
  three rule kinds, zero-count rows included. The report projection and the
  ``unused_rules`` gate both read this, so they cannot disagree.

Kept out of ``disposition_close.py`` and ``disposition_ledger.py``: both are at
the architecture gate's line ceiling, and the seam is a real one -- this module
joins a finished ledger to a finished document's rule set, which neither of
those two does.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

from .rule_inventory import (
    KIND_RECLASSIFY,
    ConfiguredRule,
    RuleAuditRow,
    RuleRef,
    acknowledgment_rules,
    build_rule_rows,
    count_rule_records,
    reclassify_rules,
    suppression_rules,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import DiffResult


class _LedgerLike(Protocol):
    """The closed ledger's surface this module reads.

    A structural type rather than an import of ``DispositionLedger``: the ledger
    module imports this one (at call time), so importing it back would be a
    cycle the architecture gate rejects.
    """

    _records: list[Any]
    _anchors: list[Any]

    @property
    def records(self) -> Any: ...

    @property
    def suppression_inventories(self) -> Any: ...


def _reclassify_ref(policy_file: object, index: int) -> RuleRef:
    source = getattr(policy_file, "source_path", None)
    return RuleRef(KIND_RECLASSIFY, None if source is None else str(source), index)


def stamp_reclassifications(
    ledger: _LedgerLike, result: DiffResult, *, today: date | None = None
) -> None:
    """Stamp every record with the ``reclassify:`` rules that touched it (P1).

    One :func:`~abicheck.policy.reclassify_outcome.reclassify_outcome_for_change` call
    per record decides three things at once: the rules whose selectors matched
    (``reclassify_hits``), the rule that moved the verdict (``reclassify_rule``),
    and the display label (``reclassified_by``, unchanged from before P1: the
    rule's ``label``, else its ``reason``, else its target verdict). A record
    already stamped is left alone, so re-closing a scoped ledger is a no-op.
    """
    from .disposition_gate import _GateContext
    from .reclassify_outcome import reclassify_outcome_for_change

    gate = _GateContext.of(result, today=today)
    policy_file = gate.policy_file
    rules = (
        getattr(policy_file, "reclassify", None) if policy_file is not None else None
    )
    if not rules:
        return
    for index, (record, change) in enumerate(
        zip(ledger._records, ledger._anchors)  # noqa: SLF001
    ):
        if record.reclassify_hits or record.reclassify_rule is not None:
            continue  # already stamped (e.g. a re-closed scoped record)
        outcome = reclassify_outcome_for_change(
            change,  # type: ignore[arg-type]
            policy_file,
            gate.today,
        )
        if not outcome.hits:
            continue
        hits = tuple(_reclassify_ref(policy_file, i) for i in outcome.hits)
        decider = None
        reclassified_by = record.reclassified_by
        if outcome.changed and outcome.decider is not None:
            decider = _reclassify_ref(policy_file, outcome.decider)
            rule = rules[outcome.decider]
            reclassified_by = rule.label or rule.reason or rule.to_verdict.value
        ledger._records[index] = replace(  # noqa: SLF001
            record,
            reclassified_by=reclassified_by,
            reclassify_rule=decider,
            reclassify_hits=hits,
        )


def configured_rule_inventory(
    ledger: _LedgerLike, result: object
) -> tuple[ConfiguredRule, ...]:
    """Every rule this comparison was configured with, across all three kinds.

    Suppression rules come from the lists the ledger registered at dispose time
    (see :meth:`DispositionLedger.register_suppression`); reclassify rules from
    the result's policy document; acknowledgment records from its loaded list.
    Each ref appears once: a second inventory naming the same document entry is
    dropped, not double-counted.
    """
    found: dict[RuleRef, ConfiguredRule] = {}
    sources: list[ConfiguredRule] = []
    for suppression in ledger.suppression_inventories:
        sources.extend(suppression_rules(suppression))
    sources.extend(reclassify_rules(getattr(result, "policy_file", None)))
    sources.extend(acknowledgment_rules(getattr(result, "acknowledgments", None)))
    for rule in sources:
        found.setdefault(rule.ref, rule)
    return tuple(found.values())


def configured_rule_rows(
    result: DiffResult,
    *,
    ledger: _LedgerLike,
    today: date | None = None,
) -> tuple[RuleAuditRow, ...]:
    """One :class:`RuleAuditRow` per configured rule of *result* (ADR-067 D3).

    *ledger* is the run's closed ledger (``disposition_close.ledger_for``); it is
    required rather than looked up here, because finding the ledger is the
    closer's job and this module must not import it. Zero-count rules are rows.
    A record whose rule no configured document accounts for (a release-level
    policy decision, or a ledger whose inventory was never registered) keeps a
    row too, built from its own provenance, so a real disposition never vanishes.
    """
    configured = list(configured_rule_inventory(ledger, result))
    counts, orphans = count_rule_records(ledger.records)
    known = {rule.ref for rule in configured}
    for ref, orphan in orphans.items():
        if ref not in known:
            configured.append(orphan)
            known.add(ref)
    for ref in counts:
        if ref not in known:  # counted but unconfigured: keep it visible
            configured.append(
                ConfiguredRule(ref=ref, rule_id=f"{ref.kind}#{ref.entry_index}")
            )
            known.add(ref)
    return build_rule_rows(
        configured,
        counts,
        today=today or date.today(),
        ran_detectors=ran_detectors(result),
    )


def ran_detectors(result: object) -> frozenset[str]:
    """The names of the detectors that actually ran in *result*.

    A detector recorded ``not_evaluated`` (its evidence gate refused it) did not
    run, and a detector with no entry at all did not run either: only an
    enabled, evaluated entry counts. A result with no detector list reports
    nothing ran, so no rule can read ``unused`` from it.
    """
    return frozenset(
        det.name
        for det in getattr(result, "detector_results", None) or ()
        if getattr(det, "enabled", True) and not getattr(det, "not_evaluated", False)
    )


__all__ = [
    "configured_rule_inventory",
    "configured_rule_rows",
    "stamp_reclassifications",
]
