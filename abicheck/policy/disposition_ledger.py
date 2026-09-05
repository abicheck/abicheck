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
from .rule_provenance import (
    RuleProvenance as RuleProvenance,
    rule_provenance as rule_provenance,
)

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


@dataclass(frozen=True, slots=True)
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
    #: Set when something *other than severity* already put this finding
    #: outside the gate that decides the run: a consumer scope
    #: (``--used-by``/``--required-symbol``) judged it irrelevant, ADR-039
    #: build-context reconciliation proved it a header-parse artifact, or the
    #: opaque-handle downgrade excluded it from the verdict on its own merits.
    #:
    #: Tracked separately from the disposition because the two are decided by
    #: different authorities and must not overwrite each other: severity says
    #: *how severe* a finding is, never whether it reached the gate at all.
    #: Without it, resolving a severity configuration re-answers
    #: ``_kept_disposition`` for such a record and can promote it back to
    #: ``gating`` -- reporting a run as gating on a finding
    #: ``gate_decision_for_result`` never scored, since that reads
    #: ``result.changes`` and these are not in it.
    #:
    #: Generalized from a scope-only flag once a second source appeared. Any
    #: future exclusion that happens *before* the gate belongs here too; the
    #: rule is "was this finding withheld from the gate by something severity
    #: does not control", not "which mechanism withheld it" (that is the
    #: application point).
    gate_excluded: bool = False
    #: The two gate schemes read *different inputs*, and one disposition
    #: cannot describe both without saying which.
    #:
    #: ``checker.compare`` scores the legacy verdict over
    #: ``kept + verdict_redundant``; ``policy.gate_decision.
    #: gate_decision_for_result`` scores the severity gate over
    #: ``result.changes`` alone. A redundant finding policy still scored is
    #: therefore genuinely ``gating`` under the legacy scheme and genuinely
    #: outside the gate once a severity configuration is in effect -- not a
    #: contradiction, and not something ``gate_excluded`` can express, since
    #: that flag says "no gate scored this" unconditionally.
    #:
    #: So this is the third state, and it is narrow on purpose: set only on a
    #: record the legacy verdict scored and the severity gate's narrower input
    #: does not contain. :meth:`with_gate` demotes it exactly when a severity
    #: configuration is passed.
    legacy_gate_only: bool = False

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
        from_gate: bool = False,
        gate_excluded: bool | None = None,
        legacy_gate_only: bool = False,
    ) -> None:
        """Record *change*'s single terminal *disposition*.

        A no-op when *change* was already recorded — the disposition it
        received first is the one that actually applied to it.

        **``gate_excluded`` is derived, not trusted to each call site.** A
        ``non_gating`` label has exactly two possible origins, and only one of
        them may be re-answered later by a severity configuration:

        * the gate itself said so — :func:`_kept_disposition` scored the
          finding and it contributed nothing. Those callers pass
          ``from_gate=True``, and the record stays open to
          :meth:`with_gate`.
        * *anything else* said so — a pipeline step dropped it as compatible
          noise, a bucket held it out of the verdict input, a consumer scope
          judged it irrelevant. The finding never reached the gate, so no
          severity setting can put it there, and the record is marked.

        The default is therefore the *conservative* one: an explicitly-passed
        ``non_gating`` is gate-excluded unless the caller states it came from
        the gate. Three separate rounds of review found three separate call
        sites that produced a pre-gate ``non_gating`` and forgot the marker
        (build-context reconciliation, the opaque downgrade, and two
        compatibility-drop pipeline steps); a fourth would have been a fourth
        review round, because the rule lived in each caller instead of here.

        *gate_excluded* may still be passed explicitly, for the one case the
        derivation cannot see: a ``gating``-shaped answer that a consumer
        scope is simultaneously excluding (:meth:`apply_scope`'s late sibling
        in ``close_consumer_scope``).
        """
        if gate_excluded is None:
            gate_excluded = disposition is Disposition.NON_GATING and not from_gate
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
                gate_excluded=gate_excluded,
                legacy_gate_only=legacy_gate_only,
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

    def with_gate(
        self, result: DiffResult, severity_config: object
    ) -> DispositionLedger:
        """A copy of this ledger re-labelled against the *resolved* gate.

        A copy, not an in-place relabel: a report projection must not mutate
        the result it renders (``tests/unit/report/test_render_html.py``
        states that as an executable invariant), and the same run can be
        rendered twice under different severity configurations.

        Touches only ``gating``/``non_gating`` records: a suppressed,
        out-of-contract, unresolved or deduplicated finding never reached the
        gate at all, so no severity configuration can change its disposition
        (D2 -- one change, one terminal disposition). A **scope-excluded**
        record is skipped for the same reason under a different authority:
        severity says how severe a finding is, never whether the consumer
        this run gates on uses it at all.

        A :attr:`~DispositionRecord.legacy_gate_only` record is demoted
        outright rather than re-scored, because the question changes with the
        scheme: the severity gate reads ``result.changes``, which does not
        contain it, so under *any* severity configuration it is outside the
        gate that decides the run -- however severe its own kind is.
        """
        gate = _GateContext.of(result)
        gated = DispositionLedger()
        gated._anchors = list(self._anchors)
        gated._seen_ids = dict(self._seen_ids)
        gated._records = [
            self._regated(record, change, result, severity_config, gate)
            for record, change in zip(self._records, self._anchors)
        ]
        return gated

    @staticmethod
    def _regated(
        record: DispositionRecord,
        change: object,
        result: DiffResult,
        severity_config: object,
        gate: _GateContext,
    ) -> DispositionRecord:
        """One record's label under the resolved gate. See :meth:`with_gate`."""
        if record.gate_excluded or record.disposition not in (
            Disposition.GATING,
            Disposition.NON_GATING,
        ):
            return record
        if record.legacy_gate_only and severity_config is not None:
            return replace(
                record, disposition=Disposition.NON_GATING, gate_excluded=True
            )
        return replace(
            record,
            disposition=_kept_disposition(
                change,  # type: ignore[arg-type]
                result,
                severity_config,
                gate,
            ),
        )

    def resolve_verdict_classes(self, result: DiffResult) -> None:
        """Fill in the verdict class of every *suppressed* record that had none.

        Scoped to the suppressed ones because they are the only records whose
        class is read: ``suppressed_gating_records`` (hence
        ``semver.recommend_release``'s conserved delta) is the one consumer,
        and a kept finding's verdict is the report's own subject anyway.
        Resolving it for every record instead cost a policy lookup and a
        record rewrite per finding on a path that runs once per comparison --
        measurable on a wide diff, for an answer nothing asked for.


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
        from ..contract_gating import is_evaluated
        from ..reclassify import effective_verdict_for_change

        if not callable(getattr(result, "_effective_verdict_for_change", None)):
            return  # a duck-typed stand-in with no verdict to read
        # The same function ``DiffResult._effective_verdict_for_change``
        # delegates to, with the kind sets resolved once for the whole pass
        # instead of re-derived per finding (see ``_GateContext``).
        gate = _GateContext.of(result)
        for index, (record, change) in enumerate(zip(self._records, self._anchors)):
            if (
                record.verdict_class is not None
                or record.disposition is not Disposition.SUPPRESSED
            ):
                continue
            if not is_evaluated(change):
                # ADR-049 D1: compatibility policy never scored this finding,
                # and `contract_pipeline.record_compatibility_decisions`
                # deliberately leaves its decision `None` for exactly that
                # reason. Filling in a kind-level verdict here would undo
                # that: a *suppressed* proven-out-of-contract finding would
                # then drive `semver.recommend_release` to MAJOR/REVIEW,
                # while the identical unsuppressed exclusion correctly
                # recommends no bump. Suppression must not resurrect a
                # contract exclusion.
                continue
            self._records[index] = replace(
                record,
                verdict_class=effective_verdict_for_change(
                    change,  # type: ignore[arg-type]
                    policy=gate.policy,
                    kind_sets=gate.kind_sets,  # type: ignore[arg-type]
                    policy_file=gate.policy_file,
                ).value,
            )

    def refresh_promoted(self, result: DiffResult) -> None:
        """Re-answer any record whose finding an explicit scope *promoted*.

        ADR-049 §4.3: a run given ``--used-by``/``--required-symbol`` has been
        *told* what the contract is, and
        ``contract_scoped_promotion.stamp_scoped_changes`` promotes a finding
        the ``--contract`` evaluator had ruled ``PROVEN_OUT_OF_CONTRACT`` (or
        left ``UNKNOWN_*``) to ``IN_CONTRACT`` -- an explicit consumer
        outranks anything two snapshots can show. The scoped gate then scores
        it, so its ledger record's ``out_of_contract`` /
        ``unresolved_relevance`` label is stale, and :meth:`apply_scope`'s
        narrowing-only guard skips exactly those two dispositions rather than
        refreshing them. Left alone, an evaluated breaking removal exits
        nonzero while the audit reports ``effective_total: 0``.

        **Keyed on the promoter's own stamp, not on the finding currently
        reading as evaluated.** ``contract_gating.is_evaluated`` answers
        ``True`` for an *unstamped* finding by design (an unstamped finding is
        evaluated -- that is what keeps every run without ``--contract``
        unchanged), so "is evaluated now" cannot distinguish *became*
        evaluated from *always was*. Reading it here refreshed every
        out-of-surface record in a ``--used-by`` run that never passed
        ``--contract`` at all, relabelling an ordinary public-header scope
        exclusion as a contract promotion. The reason code
        ``stamp_explicit_scope_contract_evaluation`` writes is the transition
        signal, and it is written by exactly the function whose effect this
        pass exists to follow.

        A promotion is the one thing that may *widen* a record, which is why
        it is a separate pass ahead of the narrowing one rather than a branch
        inside it: the two move in opposite directions, and promote-then-
        narrow is what makes the result well-defined -- a finding promoted
        into the contract that this consumer does not use is still excluded.
        """
        from ..contract_relevance_types import EXPLICIT_SCOPE_REASON_CODE

        gate = _GateContext.of(result)
        for index, (record, change) in enumerate(zip(self._records, self._anchors)):
            if record.disposition not in (
                Disposition.OUT_OF_CONTRACT,
                Disposition.UNRESOLVED_RELEVANCE,
            ):
                continue
            if (
                getattr(change, "contract_reason_code", None)
                != EXPLICIT_SCOPE_REASON_CODE
            ):
                continue
            self._records[index] = replace(
                record,
                disposition=_kept_disposition(
                    change,  # type: ignore[arg-type]
                    result,
                    None,
                    gate,
                ),
                application_point="contract_promotion",
            )

    def apply_scope(self, result: DiffResult, in_scope: Iterable[object]) -> None:
        """Narrow the gating set to the findings the *scoped* gate scores.

        ``compare --used-by``/``--required-symbol`` gates on a consumer's own
        subset, and that scoped decision — not the whole-library verdict — is
        what produces the run's exit code. Without this, a breaking removal
        the consumer never calls stays ``gating`` and inflates
        ``effective_total`` while the gate it is supposedly counted in passes
        with ``0``.

        *in_scope* must be the **union** across every consumer the run gates
        on: this only ever demotes, so calling it once per consumer would
        intersect their relevant sets instead, and a finding only the second
        consumer uses would be excluded by the first one's call.

        Only ever *narrows*: a finding already outside the gate cannot be
        pulled into it by scoping, and a suppressed or excluded finding is
        untouched (D2 — scoping is not a second chance at a disposition).
        Mutates in place, unlike :meth:`with_gate`: this is the engine
        recording what the run actually gated on, not a projection rendering
        it.
        """
        scoped = {id(c) for c in in_scope}
        for index, (record, change) in enumerate(zip(self._records, self._anchors)):
            # Both evaluated dispositions, not just ``gating``: a finding that
            # is *already* non-gating (a compatible addition, say) still has
            # to be marked excluded, or a later severity setting that promotes
            # its category (``severity.addition: error``) would pull it into a
            # gate the consumer-scoped run never evaluated it for. The mark is
            # what ``with_gate`` reads; the demotion below is a no-op for one
            # that was not gating to begin with.
            if record.disposition not in (
                Disposition.GATING,
                Disposition.NON_GATING,
            ):
                continue
            if id(change) not in scoped:
                self._records[index] = replace(
                    record,
                    disposition=Disposition.NON_GATING,
                    gate_excluded=True,
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
        source_file=_source_file_for(suppression, rule),
    )


def _source_file_for(
    suppression: object | None, rule: Suppression | None
) -> str | None:
    """The document *rule* came from, preferring its own per-rule origin.

    A merged rule set (the ABICC front end combines a ``--suppress`` file with
    rules synthesized from ``-skip-*`` options) has no single source path, so
    the list-level answer is ``None`` there even for a rule that really did
    come from the file. ``SuppressionList.source_for`` answers per rule; the
    list-level ``source_path`` remains the fallback for any other rule-set
    implementation.
    """
    if rule is not None:
        source_for = getattr(suppression, "source_for", None)
        if callable(source_for):
            resolved = source_for(rule)
            if resolved is not None:
                return str(resolved)
    source_path = getattr(suppression, "source_path", None)
    return str(source_path) if source_path is not None else None


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


@dataclass(frozen=True, slots=True)
class _GateContext:
    """The per-*result* inputs every per-change gate question needs.

    Resolved once per pass rather than per finding: ``DiffResult.
    _effective_kind_sets()`` re-derives the policy's four kind sets (and
    re-applies every policy-file override) on each call, and both
    ``gate_contribution_for_change`` and ``effective_verdict_for_change``
    want them. Hoisting cut the closing pass from ~19% of a 2000-symbol
    ``compare()`` to a fraction of that, with no change to any answer -- the
    values are constant for the whole pass by construction.
    """

    policy: str | None
    kind_sets: object | None
    policy_file: object | None

    @classmethod
    def of(cls, result: DiffResult) -> _GateContext:
        kind_sets_of = getattr(result, "_effective_kind_sets", None)
        return cls(
            policy=getattr(result, "policy", None),
            kind_sets=kind_sets_of() if callable(kind_sets_of) else None,
            policy_file=getattr(result, "policy_file", None),
        )


def _kept_disposition(
    change: Change,
    result: DiffResult,
    severity_config: object | None = None,
    gate: _GateContext | None = None,
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
    gate = gate or _GateContext.of(result)
    contribution = gate_contribution_for_change(
        change,
        severity_config,  # type: ignore[arg-type]
        policy=gate.policy,
        kind_sets=gate.kind_sets,  # type: ignore[arg-type]
        policy_file=gate.policy_file,
    )
    return Disposition.GATING if contribution > 0 else Disposition.NON_GATING
