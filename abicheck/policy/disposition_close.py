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

"""ADR-067 D2/D3: closing the disposition ledger over a finished comparison.

The sibling :mod:`abicheck.policy.disposition_ledger` owns *recording* — the
ledger, its record type, and the primitives the suppression application
points call while a comparison is still running, none of which has a
``DiffResult`` to consult yet. This module owns what happens once there is
one:

* labelling every finding that survived to a bucket on the result
  (:func:`finalize_ledger`);
* narrowing the gating set to a consumer scope after the fact
  (:func:`close_consumer_scope`, the one call a late producer makes);
* the single accessor every report projection uses (:func:`ledger_for`), and
  D3's conservation invariant as an executable check
  (:func:`conservation_holds`).

Split when the combined module passed the architecture check's 800-line
production ceiling; the seam is a real one rather than a line count, and it
points one way only — closing reads recording, never the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .disposition_ledger import (
    Disposition,
    DispositionLedger,
    _GateContext,
    _kept_disposition,
    record_suppressed_change,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult


def record_kept_change(
    ledger: DispositionLedger | None,
    change: Change,
    result: DiffResult,
    *,
    application_point: str,
    dedupe_key: str | None = None,
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
        from_gate=True,
        dedupe_key=dedupe_key,
    )


#: Findings *policy generated* about another finding, rather than changes any
#: detector observed. They are real -- they carry their own gate contribution
#: and appear in every report -- but they are not detections, so D1's raw
#: total must not move when one appears (adding a rule redistributes
#: dispositions; it never changes what was observed).
_POLICY_OVERLAY_KINDS = frozenset({"suppression_would_hide_public_break"})


def _is_policy_overlay(change: object) -> bool:
    """Is *change* a policy-generated diagnostic rather than an observation?"""
    kind = getattr(change, "kind", None)
    return getattr(kind, "value", kind) in _POLICY_OVERLAY_KINDS


def record_consumer_overlay(
    ledger: DispositionLedger | None,
    change: Change,
    result: DiffResult,
    *,
    rule: object | None = None,
    suppression: object | None = None,
) -> None:
    """Record one ``--used-by`` consumer overlay, deduped across consumers.

    ``appcompat.scope_diff_to_app`` runs once per consumer, and two consumers
    needing the same missing export each synthesize their own
    equal-but-not-identical overlay for the *one* observation. The
    orchestrator dedupes its scoped view by finding id; the ledger is
    identity-keyed, so without the same key it recorded two detections for
    what the report shows as one (ADR-067 D1 -- the raw total counts
    observations, and a second consumer asking about the same export is not a
    second observation).

    Both branches record, since adding a rule must move a finding between
    dispositions rather than change how many were detected; *rule* is the
    ``SuppressionOutcome.matched_rule`` that fired, or ``None`` for a kept
    overlay.
    """
    from ..finding_identity import report_finding_id

    key = report_finding_id(change)
    if rule is None and suppression is None:
        record_kept_change(
            ledger,
            change,
            result,
            application_point="consumer_overlay",
            dedupe_key=key,
        )
        return
    record_suppressed_change(
        ledger,
        change,
        rule=rule,  # type: ignore[arg-type]
        application_point="consumer_overlay",
        suppression=suppression,
        dedupe_key=key,
    )


def _record_bucket(
    ledger: DispositionLedger,
    changes: Iterable[Change],
    disposition: Disposition,
    application_point: str,
) -> None:
    """Record a whole bucket under one disposition.

    Never passes ``from_gate``: a bucket is by definition a population the
    verdict was *not* scored over, so a ``non_gating`` bucket is gate-excluded
    (see :meth:`DispositionLedger.record`).
    """
    for change in changes:
        ledger.record(change, disposition, application_point=application_point)


def finalize_ledger(
    ledger: DispositionLedger,
    result: DiffResult,
    severity_config: object | None = None,
    *,
    verdict_scored: Iterable[Change] = (),
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

    gate = _GateContext.of(result)
    for change in _bucket("changes"):
        if _is_policy_overlay(change):
            # D1 counts *observed* changes. A policy-generated diagnostic is
            # not one: `ApplySuppression` emits
            # SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK alongside the finding it is
            # about when a broad selector matched but the reachability gate
            # withheld it, so recording it here made merely *adding* a rule
            # that changes nothing move `detected_total` from 1 to 2 -- the
            # conservation this audit exists to make checkable, broken by the
            # audit itself. It is an overlay on a record that already exists,
            # not a second detection (Codex review).
            continue
        ledger.record(
            change,
            _kept_disposition(change, result, severity_config, gate),
            application_point="verdict",
            from_gate=True,
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
    # ...and the first half is itself two populations. `checker.compare`
    # scores the verdict on `kept + verdict_scored`, where `verdict_scored` is
    # the redundant findings policy did *not* exclude from the gate (the
    # rename-collapsed halves are excluded; everything else is not). One of
    # those can still drive a breaking exit code -- downgrade a kept
    # `TYPE_SIZE_CHANGED` to compatible while its derived, redundant
    # `FUNC_PARAMS_CHANGED` stays breaking and the run exits 4 -- so labelling
    # the whole prefix `deduplicated` made the audit report `effective_total:
    # 0` for a run that gated. A finding the gate scored is answered by the
    # gate (`_kept_disposition`), like every other scored finding in this
    # module; the mechanism that hid it from the *display* is recorded in the
    # application point, which is what that field is for.
    #
    # `checker.compare` hands the set in rather than this module re-deriving
    # it from `caused_by_type`: that rule belongs to the caller that owns the
    # verdict input, and a second copy of it here would be exactly the kind of
    # parallel policy this ledger exists to avoid. A caller that passes
    # nothing (`ledger_for`'s reconciliation fallback for a hand-built
    # `DiffResult`) keeps the conservative labelling, since such a result
    # carries no scored set to honour.
    scored_ids = {id(c) for c in verdict_scored}
    for change in redundant[:redundant_count]:
        if id(change) in scored_ids:
            ledger.record(
                change,
                _kept_disposition(change, result, severity_config, gate),
                application_point="redundancy_filter_scored",
                from_gate=True,
            )
        else:
            ledger.record(
                change,
                Disposition.DEDUPLICATED,
                application_point="redundancy_filter",
            )
    # Both these buckets are `non_gating` because they were taken *out of the
    # verdict input* before any severity setting existed -- `checker.compare`
    # scores `kept + verdict_scored`, and neither is in it. Neither passes
    # `from_gate`, so `record` marks them gate-excluded and `with_gate` leaves
    # them alone; without that, `abi_breaking: error` would promote a
    # reconciled `TYPE_SIZE_CHANGED` back to `gating` and report the run as
    # gating on a finding `gate_decision_for_result` never scored.
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


def close_consumer_scope(
    ledger: DispositionLedger | None,
    result: DiffResult,
    *,
    gating: Iterable[object],
    also_detected: Iterable[Change] = (),
) -> None:
    """Close the ledger again after a consumer-scoping pass added to it.

    Call this **once per run**, from whatever resolves the whole scoped gate
    -- not once per consumer: ``apply_scope`` only demotes, so a per-consumer
    call intersects the consumers' relevant sets rather than unioning them.
    *also_detected* carries the synthesized scoped findings that never reach
    ``result.changes`` (a missing entrypoint, a PE ordinal retarget): they are
    real detected changes, and without them every scoped view would count a
    population the audit does not (D1).

    ``appcompat.scope_diff_to_app`` runs *after* ``checker.compare()``
    finalized the ledger, so anything it records misses both closing passes:
    the scoped findings it appends keep ``verdict_class=None`` (which hides a
    suppressed consumer-breaking removal from
    ``semver.recommend_release``), and the gating labels still describe the
    whole-library gate rather than the scoped one that actually decides the
    run. Both are the same root cause — a second producer joining after the
    close — so both are fixed by one closing call rather than a patch per
    symptom, and any future late producer has the same call available.
    """
    if ledger is None:
        return
    gating_ids = {id(c) for c in gating}
    gate = _GateContext.of(result)
    for change in also_detected:
        # Through the same per-finding gate resolution every other kept
        # finding goes through -- membership in the scoped set says the
        # finding is *relevant*, not that it gates. A scoped set legitimately
        # contains findings whose own gate contribution is zero (the
        # ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` advisory `scope_diff_to_app`
        # appends is a RISK diagnostic, not a break), and counting those as
        # effective would overstate the audit against the exit code beside it.
        # Out-of-scope late findings are marked excluded for the same reason
        # ``apply_scope`` marks the rest: severity must not pull them in
        # later. The scope demotion applies under the same condition
        # ``apply_scope`` applies it -- only to an *evaluated* disposition. A
        # late finding that is proven out of contract (or whose relevance
        # evidence ran out) is not made ``non_gating`` by being out of scope:
        # that would replace one terminal disposition with another, which D2
        # forbids, and would report a contract exclusion as an ordinary
        # evaluated-and-harmless finding.
        disposition = _kept_disposition(change, result, None, gate)
        evaluated = disposition in (Disposition.GATING, Disposition.NON_GATING)
        excluded = evaluated and id(change) not in gating_ids
        ledger.record(
            change,
            Disposition.NON_GATING if excluded else disposition,
            application_point="consumer_scope",
            # Explicit rather than derived: an in-scope finding the gate
            # scored `non_gating` is *not* gate-excluded, and an out-of-scope
            # one is -- whatever the gate said. Only this call site knows
            # both halves, so it states the answer instead of `from_gate`.
            gate_excluded=excluded,
            # True whichever way the scope ruled: it marks *which gate*
            # decided this record, not what it decided.
            scope_decided=True,
        )
    # Promotion before narrowing: `_promote_scoped_contract` may have moved a
    # finding the `--contract` evaluator excluded into the contract, and
    # `apply_scope` only ever demotes (it skips exactly those dispositions).
    # The order is what makes the result well-defined -- a finding promoted
    # into the contract but not used by this consumer is still excluded.
    ledger.refresh_promoted(result)
    ledger.apply_scope(result, gating)
    ledger.resolve_verdict_classes(result)


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

    Never mutates *result*: a report projection that attached (or relabelled)
    a ledger on the object it renders would break the "rendering changes
    nothing" invariant the HTML renderer already tests for. A caller that
    wants the ledger to *persist* on the result assigns it explicitly —
    ``checker.compare()`` does, which is what gives every projection the
    per-rule provenance recorded during the run.

    *severity_config* is the run's resolved severity configuration, which
    ``checker.compare()`` never sees — the gate is resolved by the front end
    (ADR-064), strictly later. Passing it here is the audit *learning* the
    gate the run was actually scored on; it is not a renderer changing a
    gate, and it can only ever move a finding between ``gating`` and
    ``non_gating`` (:meth:`DispositionLedger.with_gate`).
    """
    existing = getattr(result, "disposition_ledger", None)
    if isinstance(existing, DispositionLedger):
        return (
            existing
            if severity_config is None
            else existing.with_gate(result, severity_config)
        )
    return finalize_ledger(DispositionLedger(), result, severity_config)


def conservation_holds(ledger: DispositionLedger) -> bool:
    """D3's executable invariant: the per-disposition counts sum to the
    detected total. Exposed as a function (rather than only asserted in a
    test) so any consumer can check it against a ledger it did not build."""
    return sum(ledger.counts().values()) == ledger.detected_total
