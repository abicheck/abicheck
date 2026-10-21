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

"""ADR-067 D3 (P1): the configured-rule inventory and one audit row per rule.

The disposition ledger records *findings*. Before this module, every audit
aggregate was a projection of those records, so a rule that disposed of
nothing had no record to project and disappeared from the audit -- "record
before disposing" held for findings but not for the rules that were
configured to act on them.

This leaf closes that gap by enumerating the *configured* rules first and
joining the records to them:

* :class:`ConfiguredRule` -- one per rule a document declares (suppression,
  reclassify, acknowledgment), keyed by :class:`RuleRef` (document, position);
* :func:`count_rule_records` -- per-rule counts read off the ledger records;
* :func:`build_rule_rows` -- exactly one :class:`RuleAuditRow` per configured
  rule, zero-count rows included, each with a :data:`RULE_STATUSES` status.

Counting contract (ADR-067 D2/D3, maintainer decision): ``disposed`` is the
number of findings the rule *disposed of* -- first-match credit for a
suppression, the verdict-changing decider for a reclassification, the
covered findings for an acknowledgment. ``shadowed`` is every other finding
the rule's selectors matched (overlap with an earlier rule, or a matching
no-op reclassification). ``disposed + shadowed`` is the selector-hit count.

A leaf: imports nothing but :mod:`rule_identity`, so the ledger, the closer,
the report projection and the exit fold can all depend on it without a cycle.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from .rule_identity import rule_id_of

KIND_SUPPRESSION = "suppression"
KIND_RECLASSIFY = "reclassify"
KIND_ACKNOWLEDGMENT = "acknowledgment"
STATUS_ACTIVE = "active"
STATUS_SHADOWED_ONLY = "shadowed_only"
STATUS_NOT_EVALUATED = "not_evaluated"
STATUS_UNUSED = "unused"
STATUS_EXPIRED = "expired"
#: Every status a configured rule can carry, in the order a reader sees them.
RULE_STATUSES: tuple[str, ...] = (
    STATUS_ACTIVE,
    STATUS_SHADOWED_ONLY,
    STATUS_NOT_EVALUATED,
    STATUS_UNUSED,
    STATUS_EXPIRED,
)
#: The one status the ``unused_rules`` gate reacts to (see
#: :mod:`abicheck.policy.unused_rules_gate`).
UNUSED_TRIGGER_STATUS = STATUS_UNUSED


@dataclass(frozen=True, slots=True)
class RuleRef:
    """A configured rule's join key: which document, which entry in it.

    ``entry_index`` is the zero-based position in the document's own list, so
    two byte-identical entries in one file are two rules (P1's duplicate
    case). ``entry_index`` is ``None`` only for a synthetic provenance that
    carries no position (a release-level policy decision with no document
    entry behind it).
    """

    kind: str
    source_file: str | None
    entry_index: int | None

    def key(self) -> str:
        """A stable, human-independent string for this ref (JSON-safe)."""
        index = "" if self.entry_index is None else str(self.entry_index)
        return f"{self.kind}|{self.source_file or ''}|{index}"


@dataclass(frozen=True, slots=True)
class ConfiguredRule:
    """One rule a policy document declares, before any finding is disposed."""

    ref: RuleRef
    rule_id: str
    label: str | None = None
    reason: str | None = None
    expires: date | None = None
    intent: str = "unspecified"
    allow_public_break: bool = False


@dataclass(frozen=True, slots=True)
class RuleCounts:
    """Per-rule record counts. ``hits`` is ``disposed + shadowed``."""

    disposed: int = 0
    shadowed: int = 0

    @property
    def hits(self) -> int:
        return self.disposed + self.shadowed


@dataclass(frozen=True, slots=True)
class RuleAuditRow:
    """One configured rule's audit row -- the one shape every view projects."""

    ref: RuleRef
    rule_id: str
    label: str | None
    reason: str | None
    expires: date | None
    status: str
    matched_count: int
    shadowed_count: int
    selector_matched_count: int
    intent: str = "unspecified"
    allow_public_break: bool = False

    @property
    def kind(self) -> str:
        return self.ref.kind

    @property
    def source_file(self) -> str | None:
        return self.ref.source_file

    @property
    def entry_index(self) -> int | None:
        return self.ref.entry_index


def rule_status(
    rule: ConfiguredRule,
    counts: RuleCounts,
    *,
    today: date,
    detectors_refused: bool,
) -> str:
    """The status of one configured rule (ADR-067 D3 / ADR-065 four-state rule).

    Checked in this order, first applicable wins:

    1. ``active`` -- it disposed of at least one finding (a record is a fact,
       so this wins even when the rule has since expired);
    2. ``shadowed_only`` -- its selectors matched findings, but an earlier rule
       (or a no-op reclassification) claimed every one of them;
    3. ``expired`` -- past its ``expires`` date (an expired rule never matches);
    4. ``not_evaluated`` -- it matched nothing, and at least one detector of
       this comparison did not run, so "nothing matched" is not proof the rule
       was checked (ADR-067 D3: never ``unused`` when the evaluation was not
       performed);
    5. ``unused`` -- it matched nothing, and every detector ran.
    """
    if counts.disposed > 0:
        return STATUS_ACTIVE
    if counts.shadowed > 0:
        return STATUS_SHADOWED_ONLY
    if rule.expires is not None and today > rule.expires:
        return STATUS_EXPIRED
    if detectors_refused:
        return STATUS_NOT_EVALUATED
    return STATUS_UNUSED


def build_rule_rows(
    rules: Sequence[ConfiguredRule],
    counts: Mapping[RuleRef, RuleCounts],
    *,
    today: date,
    detectors_refused: bool,
) -> tuple[RuleAuditRow, ...]:
    """Exactly one :class:`RuleAuditRow` per configured rule, in input order.

    Zero-count rules are rows, never omitted (P1): a rule with no record reads
    ``matched_count: 0`` with the status that explains why. A ref that appears
    in *counts* but not in *rules* is the caller's to synthesize (see
    :func:`~abicheck.policy.disposition_rules.configured_rule_rows`) -- this
    function refuses it rather than dropping it.
    """
    configured = {rule.ref for rule in rules}
    if len(configured) != len(rules):
        raise ValueError("duplicate configured rule reference in the inventory")
    stray = [ref for ref in counts if ref not in configured]
    if stray:
        raise ValueError(f"record references an unconfigured rule: {stray[0]}")
    rows: list[RuleAuditRow] = []
    for rule in rules:
        c = counts.get(rule.ref, RuleCounts())
        rows.append(
            RuleAuditRow(
                ref=rule.ref,
                rule_id=rule.rule_id,
                label=rule.label,
                reason=rule.reason,
                expires=rule.expires,
                status=rule_status(
                    rule, c, today=today, detectors_refused=detectors_refused
                ),
                matched_count=c.disposed,
                shadowed_count=c.shadowed,
                selector_matched_count=c.hits,
                intent=rule.intent,
                allow_public_break=rule.allow_public_break,
            )
        )
    return tuple(rows)


def count_rule_records(
    records: Iterable[Any],
) -> tuple[dict[RuleRef, RuleCounts], dict[RuleRef, ConfiguredRule]]:
    """Per-rule counts read off ledger *records* (duck-typed).

    Returns ``(counts, orphans)``. *orphans* holds a synthesized
    :class:`ConfiguredRule` for every suppression provenance seen, keyed by its
    ref; the caller drops the ones a configured rule already accounts for, and
    keeps the rest (a release-level policy decision, or a ledger whose
    suppression inventory was never registered). Those are real dispositions,
    so they keep a row rather than vanishing.
    """
    disposed: Counter[RuleRef] = Counter()
    shadow: Counter[RuleRef] = Counter()
    reclass_hits: Counter[RuleRef] = Counter()
    orphans: dict[RuleRef, ConfiguredRule] = {}
    for record in records:
        provenance = getattr(record, "rule", None)
        if provenance is not None:
            ref = RuleRef(
                KIND_SUPPRESSION,
                getattr(provenance, "source_file", None),
                getattr(provenance, "entry_index", None),
            )
            disposed[ref] += 1
            if ref not in orphans:
                orphans[ref] = _orphan_from_provenance(ref, provenance)
        for shadowed_ref in getattr(record, "shadowed_refs", ()) or ():
            shadow[shadowed_ref] += 1
        changed = getattr(record, "reclassify_rule", None)
        if changed is not None:
            disposed[changed] += 1
        for hit in getattr(record, "reclassify_hits", ()) or ():
            reclass_hits[hit] += 1
        covered = getattr(record, "acknowledgment_rule", None)
        if covered is not None:
            disposed[covered] += 1
    for ref, hits in reclass_hits.items():
        shadow[ref] += max(0, hits - disposed.get(ref, 0))
    counts: dict[RuleRef, RuleCounts] = {}
    for ref in set(disposed) | set(shadow):
        counts[ref] = RuleCounts(
            disposed=disposed.get(ref, 0), shadowed=shadow.get(ref, 0)
        )
    return counts, orphans


def _orphan_from_provenance(ref: RuleRef, provenance: object) -> ConfiguredRule:
    expires_raw = getattr(provenance, "expires", None)
    expires = date.fromisoformat(expires_raw) if isinstance(expires_raw, str) else None
    return ConfiguredRule(
        ref=ref,
        rule_id=str(getattr(provenance, "rule_id", None) or "rule"),
        label=getattr(provenance, "label", None),
        reason=getattr(provenance, "reason", None),
        expires=expires,
        intent=str(getattr(provenance, "intent", "unspecified") or "unspecified"),
        allow_public_break=bool(getattr(provenance, "allow_public_break", False)),
    )


def suppression_rules(suppression: object) -> tuple[ConfiguredRule, ...]:
    """The configured suppression rules of one loaded ``SuppressionList``."""
    source = getattr(suppression, "source_path", None)
    source_file = None if source is None else str(source)
    return tuple(
        ConfiguredRule(
            ref=RuleRef(KIND_SUPPRESSION, source_file, index),
            rule_id=rule_id_of(rule),
            label=getattr(rule, "label", None),
            reason=getattr(rule, "reason", None),
            expires=getattr(rule, "expires", None),
            allow_public_break=bool(getattr(rule, "allow_public_break", False)),
        )
        for index, rule in enumerate(getattr(suppression, "rules", ()) or ())
    )


def reclassify_rules(policy_file: object | None) -> tuple[ConfiguredRule, ...]:
    """The configured ``reclassify:`` rules of one loaded policy document."""
    if policy_file is None:
        return ()
    source = getattr(policy_file, "source_path", None)
    source_file = None if source is None else str(source)
    return tuple(
        ConfiguredRule(
            ref=RuleRef(KIND_RECLASSIFY, source_file, index),
            rule_id=rule_id_of(rule),
            label=getattr(rule, "label", None),
            reason=getattr(rule, "reason", None),
            expires=getattr(rule, "expires", None),
        )
        for index, rule in enumerate(getattr(policy_file, "reclassify", ()) or ())
    )


def acknowledgment_rules(acknowledgments: object | None) -> tuple[ConfiguredRule, ...]:
    """The configured acknowledgment records of one loaded ``AcknowledgmentList``."""
    if acknowledgments is None:
        return ()
    source = getattr(acknowledgments, "source_path", None)
    source_file = None if source is None else str(source)
    out: list[ConfiguredRule] = []
    listed = cast("Iterable[Any]", acknowledgments)
    for index, record in enumerate(listed):
        record_id = getattr(record, "record_id", None)
        out.append(
            ConfiguredRule(
                ref=RuleRef(KIND_ACKNOWLEDGMENT, source_file, index),
                rule_id=record_id() if callable(record_id) else str(record),
                reason=getattr(record, "reason", None) or None,
                expires=getattr(record, "expires", None),
            )
        )
    return tuple(out)


