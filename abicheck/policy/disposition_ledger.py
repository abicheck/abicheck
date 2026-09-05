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

"""ADR-067 D2/D3: the one policy-disposition ledger for a scalar comparison.

Suppression has one selector grammar (:mod:`abicheck.policy.selectors`) but
**five application points** that each move a change out of the visible set:

* ``post_processing.ApplySuppression.apply()`` (the main change list);
* ``post_processing._merge_findings_respecting_suppression()`` (findings the
  late pattern/template/namespace steps build after ``ApplySuppression``
  already ran);
* ``checker._filter_suppressed_changes()`` (separately produced changes);
* ``checker._filter_pattern_synthetic()`` (ADR-027 A4 synthetics);
* ``appcompat.scope_diff_to_app()``'s consumer-overlay pass over
  ``missing_symbols``.

ADR-067's C-S1 slice requires all of them to route through **one recording
primitive**, because a ledger fed from one helper alone would omit most
ordinary suppressions and break the raw-versus-effective reconciliation D3
makes an executable invariant. That primitive is
:meth:`DispositionLedger.record_suppression` — the first four call it with a
:class:`~abicheck.checker_types.Change` plus the ``Suppression`` rule
:meth:`abicheck.suppression.SuppressionList.evaluate` returned, and the fifth
calls the identical method with its synthesized
``CONSUMER_REQUIRED_SYMBOL_REMOVED`` overlay change (its *shape* differs — it
is built from a raw ``missing_symbols`` string rather than detected by a
detector — but by the time suppression sees it, it is a real ``Change``, so
it needs no second record type and no second query surface).

**Counting contract (D2/D3).** Each atomically detected change carries
exactly *one* terminal disposition (:class:`Disposition`), so the
per-disposition counts sum to the detected total by construction —
:func:`DispositionLedger.counts` and :attr:`DispositionLedger.detected_total`
cannot disagree. Transformations that are *not* alternatives to a
disposition (``reclassified`` today; ``acknowledged`` once ADR-067 D5 lands
in S3) are recorded as independent overlay attributes on the same record and
are deliberately **not** added into the disposition counts.

This module is a leaf: it imports only ``model``-layer vocabulary and reads
every finding-shaped input through duck typing, so the four flat-root
application points above can route through it without any of them growing a
new dependency on the report or gate layers.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

from ..model.change_catalog.registry import Verdict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult
    from ..suppression import Suppression


class Disposition(str, Enum):
    """ADR-067 D2's terminal effective-gate disposition of one change.

    Exactly one of these is recorded per atomically detected change. The
    ``str`` mixin is deliberate: every value is emitted verbatim into the
    JSON report, so the enum member and its wire spelling cannot drift.
    """

    #: Evaluated by policy and contributing to the compatibility gate.
    GATING = "gating"
    #: Evaluated by policy, contributing nothing to the gate.
    NON_GATING = "non_gating"
    #: Removed from the visible set by a ``--suppress`` rule.
    SUPPRESSED = "suppressed"
    #: Proven outside the selected contract/public surface (ADR-024/049).
    OUT_OF_CONTRACT = "out_of_contract"
    #: Relevance could not be resolved from the available evidence.
    UNRESOLVED_RELEVANCE = "unresolved_relevance"
    #: Collapsed into another finding by redundancy/root-cause grouping.
    DEDUPLICATED = "deduplicated"


#: The one disposition that counts towards the *effective* (gating) total.
_EFFECTIVE_DISPOSITIONS = frozenset({Disposition.GATING})

#: Verdict classes that drive the compatibility gate today. Read, never
#: re-derived: these are exactly the two verdicts ``checker._compute_verdict_for``
#: already lets decide a comparison, so labelling a kept finding ``gating``
#: describes what the run did rather than inventing a second gate algorithm
#: (ADR-067's "no second gate algorithm" constraint).
_GATING_VERDICTS = frozenset({Verdict.BREAKING, Verdict.API_BREAK})


@dataclass(frozen=True)
class RuleProvenance:
    """ADR-067 D3's "rule id, source file, reason, expiry" for one match.

    Built from a :class:`~abicheck.suppression.Suppression`'s already-existing
    fields — this adds no field to the suppression grammar. ``intent`` is
    always ``"unspecified"`` today: ADR-067 D5's explicit ``intent:`` key is
    S3 work, and its own migration default for every rule that predates it is
    exactly this value, so recording it now costs nothing and keeps the
    consumer (``semver.recommend_release``'s "suppressed (intent:
    unspecified), not compatible" wording) honest rather than silent.
    """

    rule_id: str | None = None
    source_file: str | None = None
    reason: str | None = None
    label: str | None = None
    expires: str | None = None
    intent: str = "unspecified"
    allow_public_break: bool = False

    def to_dict(self) -> dict[str, object]:
        """JSON-safe mapping; ``None`` fields are emitted so the ledger's rows
        keep one stable shape for machine consumers."""
        return {
            "rule_id": self.rule_id,
            "source_file": self.source_file,
            "reason": self.reason,
            "label": self.label,
            "expires": self.expires,
            "intent": self.intent,
            "allow_public_break": self.allow_public_break,
        }


def rule_provenance(
    rule: Suppression | None, *, source_file: str | None = None
) -> RuleProvenance | None:
    """Project one suppression *rule* onto :class:`RuleProvenance`.

    Duck-typed (``getattr``) rather than importing ``Suppression``, keeping
    this module a leaf; ``None`` in, ``None`` out, which is the honest answer
    for a change whose matching rule was not recorded (a ``DiffResult``
    reconstructed from JSON, for instance).
    """
    if rule is None:
        return None
    expires = getattr(rule, "expires", None)
    label = getattr(rule, "label", None)
    reason = getattr(rule, "reason", None)
    rule_id = _rule_identity(rule) or label or reason
    return RuleProvenance(
        rule_id=rule_id,
        source_file=source_file,
        reason=reason,
        label=label,
        expires=expires.isoformat() if expires is not None else None,
        allow_public_break=bool(getattr(rule, "allow_public_break", False)),
    )


def _rule_identity(rule: object) -> str | None:
    """The rule's canonical selector identity, when it can be derived.

    Deliberately the *selector* spelling, not the free-form ``label``/
    ``reason`` prose: two rules sharing a label must still be distinguishable
    in the audit. Mirrors ``SuppressionList.rule_identities``' field set for
    the handful of selectors a reader needs to recognize the rule, without
    importing it (that method operates on a whole list, not one rule).
    """
    parts = [
        f"{name}={value!r}"
        for name, value in (
            ("finding_id", getattr(rule, "finding_id", None)),
            ("symbol", getattr(rule, "symbol", None)),
            ("symbol_pattern", getattr(rule, "symbol_pattern", None)),
            ("type_pattern", getattr(rule, "type_pattern", None)),
            ("member_name", getattr(rule, "member_name", None)),
            ("namespace", getattr(rule, "namespace", None)),
            ("entity_namespace", getattr(rule, "entity_namespace", None)),
            ("cause_namespace", getattr(rule, "cause_namespace", None)),
            ("source_location", getattr(rule, "source_location", None)),
            ("change_kind", getattr(rule, "change_kind", None)),
            ("binding", getattr(rule, "binding", None)),
        )
        if value is not None
    ]
    return "|".join(parts) if parts else None


@dataclass(frozen=True)
class DispositionRecord:
    """One atomically detected change and the single disposition it received."""

    kind: str
    symbol: str | None
    disposition: Disposition
    application_point: str
    #: The change's effective verdict class at the moment it was disposed of,
    #: e.g. ``"breaking"``. Read off the existing per-change verdict, never
    #: recomputed — it is what lets a consumer ask "was anything major-class
    #: suppressed?" without re-running policy over a set policy never scored.
    verdict_class: str | None = None
    rule: RuleProvenance | None = None
    #: D2 overlay attribute, independent of ``disposition``: the policy
    #: reclassification rule that moved this finding's verdict, if any.
    reclassified_by: str | None = None

    def to_dict(self) -> dict[str, object]:
        entry: dict[str, object] = {
            "kind": self.kind,
            "symbol": self.symbol,
            "disposition": self.disposition.value,
            "application_point": self.application_point,
        }
        if self.verdict_class is not None:
            entry["verdict_class"] = self.verdict_class
        if self.rule is not None:
            entry["rule"] = self.rule.to_dict()
        if self.reclassified_by is not None:
            entry["reclassified_by"] = self.reclassified_by
        return entry


class DispositionLedger:
    """The conserved record of every detected change and its disposition.

    Built up *during* a comparison (each suppression application point calls
    :meth:`record_suppression` as it moves a change out of the visible set)
    and closed by :func:`finalize_ledger`, which labels every change that
    survived to a bucket on the ``DiffResult``. A change is recorded exactly
    once: :meth:`record` is identity-keyed, so a finalization pass over a
    result whose suppressed findings were already recorded cannot double-count
    them.
    """

    def __init__(self) -> None:
        self._records: list[DispositionRecord] = []
        #: ``id(change) -> index into _records``. A dict rather than a set so a
        #: consumer can ask "which rule disposed of *this* change" without a
        #: second, lossy (kind, symbol) join.
        self._seen_ids: dict[int, int] = {}
        # Keeps every recorded change alive for the ledger's lifetime, so the
        # identity keys above cannot be recycled by the allocator while the
        # ledger is still being appended to.
        self._anchors: list[object] = []

    # -- recording -----------------------------------------------------

    def record(
        self,
        change: Change,
        disposition: Disposition,
        *,
        application_point: str,
        rule: RuleProvenance | None = None,
    ) -> None:
        """Record *change*'s single terminal *disposition*.

        A no-op when *change* was already recorded — the disposition it
        received first is the one that actually applied to it.
        """
        key = id(change)
        if key in self._seen_ids:
            return
        self._seen_ids[key] = len(self._records)
        self._anchors.append(change)
        kind = getattr(change, "kind", None)
        self._records.append(
            DispositionRecord(
                kind=getattr(kind, "value", str(kind)),
                symbol=getattr(change, "symbol", None),
                disposition=disposition,
                application_point=application_point,
                verdict_class=_verdict_class_of(change),
                rule=rule,
                reclassified_by=getattr(change, "reclassified_by", None),
            )
        )

    def record_suppression(
        self,
        change: Change,
        *,
        rule: Suppression | None,
        application_point: str,
        source_file: str | None = None,
    ) -> None:
        """The one primitive every suppression application point routes through.

        *rule* is the ``SuppressionOutcome.matched_rule`` that fired;
        *source_file* is the ``--suppress`` document it was loaded from
        (``SuppressionList.source_path``), so D3's "which rule hid this, from
        which file, why, until when" is recorded at the only point where it
        is still known.
        """
        self.record(
            change,
            Disposition.SUPPRESSED,
            application_point=application_point,
            rule=rule_provenance(rule, source_file=source_file),
        )

    # -- querying ------------------------------------------------------

    @property
    def records(self) -> tuple[DispositionRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[DispositionRecord]:
        return iter(self._records)

    @property
    def detected_total(self) -> int:
        """Every atomically detected change, before any disposition applied."""
        return len(self._records)

    @property
    def effective_total(self) -> int:
        """The gating subset — D3's "effective" total."""
        return sum(1 for r in self._records if r.disposition in _EFFECTIVE_DISPOSITIONS)

    def counts(self) -> dict[str, int]:
        """Per-disposition counts. Always carries every key (``0`` when unused)
        so a consumer never has to distinguish "absent" from "none"; sums to
        :attr:`detected_total` by construction (ADR-067 D3)."""
        counts = {d.value: 0 for d in Disposition}
        for record in self._records:
            counts[record.disposition.value] += 1
        return counts

    def apply_gate(self, result: DiffResult, severity_config: object) -> None:
        """Re-label the evaluated records against the *resolved* gate.

        Touches only ``gating``/``non_gating`` records: a suppressed,
        out-of-contract, unresolved or deduplicated finding never reached the
        gate at all, so no severity configuration can change its disposition
        (D2 -- one change, one terminal disposition). Idempotent, so a second
        projection asking for the same ledger cannot double-apply it.
        """
        for index, (record, change) in enumerate(zip(self._records, self._anchors)):
            if record.disposition not in (Disposition.GATING, Disposition.NON_GATING):
                continue
            self._records[index] = replace(
                record,
                disposition=_kept_disposition(
                    change,  # type: ignore[arg-type]
                    result,
                    severity_config,
                ),
            )

    def resolve_verdict_classes(self, result: DiffResult) -> None:
        """Fill in the verdict class of every record that had none.

        Most findings carry no *stamped* per-change verdict at all (the
        contract pipeline's ``compatibility_decision`` and ADR-027's
        ``effective_verdict`` are both opt-in), so the class of a suppressed
        change is only knowable once a ``DiffResult`` exists to answer it —
        ``DiffResult._effective_verdict_for_change`` is that answer, policy
        overrides and frozen-namespace guards included. Reading it here rather
        than re-deriving one keeps the ledger free of a second gate algorithm,
        and it is what makes ``suppressed_gating_records`` (hence
        ``semver.recommend_release``'s conserved delta) answerable at all.
        """
        verdict_of = getattr(result, "_effective_verdict_for_change", None)
        if not callable(verdict_of):
            return  # a duck-typed stand-in with no verdict to read
        for index, (record, change) in enumerate(zip(self._records, self._anchors)):
            if record.verdict_class is not None:
                continue
            self._records[index] = replace(
                record, verdict_class=verdict_of(change).value
            )

    def record_for(self, change: object) -> DispositionRecord | None:
        """The record for *change*, or ``None`` if it was never recorded."""
        index = self._seen_ids.get(id(change))
        return None if index is None else self._records[index]

    def rule_for(self, change: object) -> RuleProvenance | None:
        """The provenance of the rule that disposed of *change*, if any.

        The report's suppression ledger joins on this: it holds the very
        ``Change`` objects the application points recorded, so the answer is
        the exact rule that fired rather than a re-evaluation of the rule set
        against a finding whose relevant fields may since have been enriched.
        """
        record = self.record_for(change)
        return None if record is None else record.rule

    def suppressed_gating_records(self) -> tuple[DispositionRecord, ...]:
        """Suppressed findings whose own verdict class would have gated.

        ``semver.recommend_release``'s conserved-delta input (ADR-067's
        consequences section): a major-class break that a rule hid is still a
        major-class break.
        """
        return tuple(
            r
            for r in self._records
            if r.disposition is Disposition.SUPPRESSED
            and r.verdict_class in {v.value for v in _GATING_VERDICTS}
        )

    def rules(self) -> tuple[tuple[RuleProvenance, int], ...]:
        """Distinct rule provenances with the number of changes each disposed
        of, ordered by first appearance so the audit reads deterministically."""
        ordered: list[RuleProvenance] = []
        tally: dict[RuleProvenance, int] = {}
        for record in self._records:
            if record.rule is None:
                continue
            if record.rule not in tally:
                ordered.append(record.rule)
                tally[record.rule] = 0
            tally[record.rule] += 1
        return tuple((rule, tally[rule]) for rule in ordered)

    def to_dict(self) -> dict[str, object]:
        """The JSON ``disposition_audit`` block (report schema 2.50)."""
        return {
            "detected_total": self.detected_total,
            "effective_total": self.effective_total,
            "counts": self.counts(),
            "rules": [
                {**rule.to_dict(), "matched_count": count}
                for rule, count in self.rules()
            ],
        }


def record_suppressed_change(
    ledger: DispositionLedger | None,
    change: Change,
    *,
    rule: Suppression | None,
    application_point: str,
    suppression: object | None = None,
) -> None:
    """The single call every suppression application point makes.

    A module-level function rather than only a method so the five call sites
    — three of which hold no ``PipelineContext`` and one of which lives in
    ``appcompat`` with a differently-shaped input — share *one* recording
    path, ledger-absence handling included: a ``None`` *ledger* is a caller
    that never opted into the audit, which is skipped, never faked.

    *suppression* is the ``SuppressionList`` the rule came from; its
    ``source_path`` is what puts D3's suppression-document path in the audit.
    """
    if ledger is None:
        return
    ledger.record_suppression(
        change,
        rule=rule,
        application_point=application_point,
        source_file=getattr(suppression, "source_path", None),
    )


def _verdict_class_of(change: object) -> str | None:
    """*change*'s already-decided verdict class, as a wire string.

    Prefers the per-finding compatibility decision the contract pipeline
    stamped (ADR-049 D1), then the pattern/frozen-namespace override, and
    finally nothing — this reads decisions, it never makes one.
    """
    for attribute in ("compatibility_decision", "effective_verdict"):
        value = getattr(change, attribute, None)
        if isinstance(value, Verdict):
            return value.value
    return None


def _kept_disposition(
    change: Change, result: DiffResult, severity_config: object | None = None
) -> Disposition:
    """The terminal disposition of a change that survived into ``changes``.

    ``gating`` means *contributes to this run's gate*, and the answer to that
    is ``severity.gate_contribution_for_change`` — the identical per-finding
    function ``compute_exit_code``/``compute_gate_decision`` fold, so this
    cannot become a second gate algorithm. With no severity configuration in
    effect (*severity_config* ``None``) it returns the finding's own legacy
    verdict exit code, which is what an ordinary ``compare`` is scored on;
    with one, a category configured ``error`` gates and one configured
    ``warning``/``info`` does not — so ``severity.addition: error`` correctly
    reads ``gating`` for a lone addition, and ``abi_breaking: info`` correctly
    reads ``non_gating`` for a break the run lets through.
    """
    from ..contract_gating import contract_relevance_of, is_evaluated
    from ..contract_relevance_types import ContractRelevance

    if not is_evaluated(change):
        # Compared against the enum members themselves, never a spelling of
        # them: ADR-049 splits "not evaluated" into a positive determination
        # (PROVEN_OUT_OF_CONTRACT -- the finding really is outside the
        # promised contract) and evidence running out (the two UNKNOWN_*
        # values), and those are different dispositions with different
        # consequences downstream.
        return (
            Disposition.UNRESOLVED_RELEVANCE
            if contract_relevance_of(change)
            in (
                ContractRelevance.UNKNOWN_UNPROVEN,
                ContractRelevance.UNKNOWN_UNRESOLVED,
            )
            else Disposition.OUT_OF_CONTRACT
        )
    from .severity import gate_contribution_for_change

    # Read through ``getattr``, like every other finding-shaped input this
    # module takes: several report paths (the HTML characterization goldens'
    # own builders, for one) hand the renderers a duck-typed stand-in rather
    # than a real ``DiffResult``, and an audit that raised on one of those
    # would be a projection deciding whether a report can be produced at all.
    kind_sets_of = getattr(result, "_effective_kind_sets", None)
    contribution = gate_contribution_for_change(
        change,
        severity_config,  # type: ignore[arg-type]
        policy=getattr(result, "policy", None),
        kind_sets=kind_sets_of() if callable(kind_sets_of) else None,
        policy_file=getattr(result, "policy_file", None),
    )
    return Disposition.GATING if contribution > 0 else Disposition.NON_GATING


def record_kept_change(
    ledger: DispositionLedger | None,
    change: Change,
    result: DiffResult,
    *,
    application_point: str,
) -> None:
    """Record a change that a caller *kept*, with its real gate disposition.

    The counterpart of :func:`record_suppressed_change` for a call site that
    produces a finding after ``compare()`` has already closed the ledger — the
    consumer overlay is the one such site today. Recording both branches is
    what keeps D1's raw-total conservation true there: adding a suppression
    rule must move a finding between dispositions, never change how many were
    detected.
    """
    if ledger is None:
        return
    ledger.record(
        change,
        _kept_disposition(change, result),
        application_point=application_point,
    )


def _record_bucket(
    ledger: DispositionLedger,
    changes: Iterable[Change],
    disposition: Disposition,
    application_point: str,
) -> None:
    for change in changes:
        ledger.record(change, disposition, application_point=application_point)


def finalize_ledger(
    ledger: DispositionLedger,
    result: DiffResult,
    severity_config: object | None = None,
) -> DispositionLedger:
    """Close *ledger* over *result*, labelling every not-yet-recorded change.

    The suppression application points have already recorded their own
    findings (with rule provenance); this pass covers the four buckets that
    reach the report — kept, redundant/deduplicated, out-of-surface, and
    build-context-reconciled — plus, as a fallback, any suppressed finding
    that reached ``result`` without passing one of the recording call sites
    (a ``DiffResult`` assembled by a caller other than ``checker.compare``).
    After it returns, the per-disposition counts sum to the detected total.
    """

    # Every bucket read defensively, for the same reason ``_kept_disposition``
    # reads its inputs that way: a report path may hand a projection a
    # duck-typed stand-in for a ``DiffResult``, and a missing audit bucket is
    # "this caller has none", not an error worth failing a report over.
    def _bucket(name: str) -> list[Change]:
        return list(getattr(result, name, None) or [])

    for change in _bucket("changes"):
        ledger.record(
            change,
            _kept_disposition(change, result, severity_config),
            application_point="verdict",
        )
    # ``redundant_changes`` is two different populations concatenated, split at
    # ``redundant_count`` (``checker.compare``: ``redundant + opaque_filtered``,
    # with the count covering only the first half). They are not the same
    # disposition: a display-dedup finding really was collapsed into another
    # one, while an opaque-handle downgrade is a compatible change excluded
    # from the verdict on its own merits -- calling that "deduplicated" would
    # claim it was folded into a finding that does not exist.
    redundant = _bucket("redundant_changes")
    redundant_count = getattr(result, "redundant_count", len(redundant))
    _record_bucket(
        ledger,
        redundant[:redundant_count],
        Disposition.DEDUPLICATED,
        "redundancy_filter",
    )
    _record_bucket(
        ledger,
        redundant[redundant_count:],
        Disposition.NON_GATING,
        "opaque_downgrade",
    )
    _record_bucket(
        ledger,
        _bucket("out_of_surface_changes"),
        Disposition.OUT_OF_CONTRACT,
        "surface_scope",
    )
    # ADR-039 reconciliation proves a finding is a context-free header-parse
    # artifact rather than a real change, so it is evaluated-and-not-gating
    # rather than a scope exclusion — D2 has no separate "reconciled" terminal
    # disposition, and the application point below is what names the mechanism.
    _record_bucket(
        ledger,
        _bucket("reconciled_changes"),
        Disposition.NON_GATING,
        "build_context_reconciliation",
    )
    for change in _bucket("suppressed_changes"):
        ledger.record_suppression(
            change, rule=None, application_point="unrecorded_suppression"
        )
    ledger.resolve_verdict_classes(result)
    return ledger


def ledger_for(
    result: DiffResult, severity_config: object | None = None
) -> DispositionLedger:
    """The conserved ledger for *result* — the one accessor every consumer uses.

    Returns the ledger ``checker.compare`` built (with per-rule provenance
    captured at each suppression application point) when there is one, and
    otherwise finalizes a fresh ledger over the result's own buckets, so a
    ``DiffResult`` produced by any other caller still reconciles. Never
    ``None``: a report projection must be able to state D3's counts
    unconditionally.

    *severity_config* is the run's resolved severity configuration, which
    ``checker.compare()`` never sees — the gate is resolved by the front end
    (ADR-064), strictly later. Passing it here is the audit *learning* the
    gate the run was actually scored on, applied once to the one shared
    ledger so every projection agrees; it is not a renderer changing a gate,
    and it can only ever move a finding between ``gating`` and
    ``non_gating`` (:meth:`DispositionLedger.apply_gate`).
    """
    existing = getattr(result, "disposition_ledger", None)
    if isinstance(existing, DispositionLedger):
        if severity_config is not None:
            existing.apply_gate(result, severity_config)
        return existing
    ledger = finalize_ledger(DispositionLedger(), result, severity_config)
    # Attached, not only returned: a later recording call site (the consumer
    # overlay, whose suppressions happen after ``compare()`` returned) must
    # reach the *same* ledger every projection will read, and two callers
    # asking for "this result's ledger" must not get two divergent objects.
    try:
        result.disposition_ledger = ledger
    except AttributeError:  # pragma: no cover - a slotted/frozen stand-in
        pass
    return ledger


def conservation_holds(ledger: DispositionLedger) -> bool:
    """D3's executable invariant: the per-disposition counts sum to the
    detected total. Exposed as a function (rather than only asserted in a
    test) so any consumer can check it against a ledger it did not build."""
    return sum(ledger.counts().values()) == ledger.detected_total
