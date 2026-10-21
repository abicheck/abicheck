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

"""ADR-067: how the gate answers *one* finding, for the disposition audit.

The two questions this module owns -- what a comparison's gate inputs are
(:class:`_GateContext`) and what disposition one surviving finding therefore
receives (:func:`_kept_disposition`) -- are the audit's only contact with
gating, and deliberately its narrowest: both delegate to
``policy.severity``'s own per-finding functions rather than deciding
anything, so the ledger cannot become a second gate algorithm.

Split out of :mod:`abicheck.policy.disposition_ledger` when that module
passed the architecture gate's 800-line production ceiling. The seam is real:
the ledger stores records and the closer labels buckets, while this answers a
policy question about a change and knows nothing about either.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .disposition_types import Disposition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import date

    from ..checker_types import DiffResult
    from ..model.change import Change
    from .disposition_types import DispositionRecord


@dataclass(frozen=True, slots=True)
class _GateContext:
    """The per-*result* inputs every per-change gate question needs.

    Resolved once per pass rather than per finding: ``DiffResult.
    effective_kind_sets()`` re-derives the policy's four kind sets (and
    re-applies every policy-file override) on each call, and both
    ``gate_contribution_for_change`` and ``effective_verdict_for_change``
    want them. Hoisting cut the closing pass from ~19% of a 2000-symbol
    ``compare()`` to a fraction of that, with no change to any answer -- the
    values are constant for the whole pass by construction. ``today``
    (Codex review, fresh evidence) is carried the same way: a caller closing
    an already-frozen ``ReportEnvelope``'s ledger passes its own captured
    date once here, rather than every per-change call below re-resolving a
    fresh ``date.today()``.
    """

    policy: str | None
    kind_sets: object | None
    policy_file: object | None
    today: date | None = None

    @classmethod
    def of(cls, result: DiffResult, *, today: date | None = None) -> _GateContext:
        from ..checker_types import DiffResult
        from .evaluate import effective_kind_sets

        return cls(
            policy=getattr(result, "policy", None),
            kind_sets=effective_kind_sets(result)
            if isinstance(result, DiffResult)
            else None,
            policy_file=getattr(result, "policy_file", None),
            today=today,
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
    from ..contract_relevance_types import ContractRelevance
    from ..model.contract_finding_relevance import contract_relevance_of, is_evaluated

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
        today=gate.today,
    )
    return Disposition.GATING if contribution > 0 else Disposition.NON_GATING


def _reset_dated_overlays(records: list[DispositionRecord]) -> list[DispositionRecord]:
    """Clear ``reclassified_by``/``verdict_class`` so a stale value resolved
    at an earlier ``today`` isn't mistaken for already-answered (Codex
    review, fresh evidence): :meth:`DispositionLedger.with_gate` calls this
    before re-running ``resolve_reclassifications``/``resolve_verdict_classes``
    on a ``checker.compare()``-built ledger, whose overlays were resolved at
    comparison time and go stale once a dated ``reclassify:`` rule's match
    crosses this render's own ``today``.
    """
    return [
        replace(
            r,
            reclassified_by=None,
            verdict_class=None
            if r.disposition is Disposition.SUPPRESSED
            else r.verdict_class,
        )
        for r in records
    ]


#: The four D2 dispositions no gate can move: policy already disposed of the
#: finding, and neither gate scheme ever scored it.
_POLICY_TERMINAL_DISPOSITIONS = frozenset(
    {
        Disposition.SUPPRESSED,
        Disposition.OUT_OF_CONTRACT,
        Disposition.UNRESOLVED_RELEVANCE,
    }
)


def regate_record(
    record: DispositionRecord,
    change: object,
    result: DiffResult,
    severity_config: object,
    gate: _GateContext,
    severity_input: set[int],
) -> DispositionRecord:
    """One record's label under the resolved gate. See :meth:`with_gate`."""
    if record.disposition in _POLICY_TERMINAL_DISPOSITIONS:
        # D2: policy already disposed of these, and no gate scores them.
        return record
    if record.scope_decided:
        # A scoped run gates on the consumer's own relevant set, so
        # `result.changes` membership does not *exclude* anything here --
        # but the scoped gate still applies the severity configuration to
        # what it scores, so an included record is re-answered by kind.
        # Only the exclusion itself is untouchable: `gate_excluded` means
        # the consumer does not use this finding, which no severity
        # setting overrides.
        if record.gate_excluded:
            return record
        restored = severity_config is not None and id(change) in severity_input
        if (
            record.disposition not in (Disposition.GATING, Disposition.NON_GATING)
            and not restored
        ):
            # A deduplicated row the scoped gate does not score keeps
            # saying why it is out. One `show_redundant` restored into
            # `result.changes` *is* scored, so it is re-answered like any
            # other included row -- `scope_decided` marks which gate
            # decides, never that a row is frozen.
            return record
        return replace(
            record,
            disposition=_kept_disposition(
                change,  # type: ignore[arg-type]
                result,
                severity_config,
                gate,
            ),
        )
    if severity_config is None:
        # Legacy scheme: `checker.compare` scored `kept + verdict_scored`,
        # which is what the recorded label already answers. A
        # `deduplicated` record was never in that input either, so only
        # the two evaluated labels are re-answered.
        if record.gate_excluded or record.disposition not in (
            Disposition.GATING,
            Disposition.NON_GATING,
        ):
            return record
        return replace(
            record,
            disposition=_kept_disposition(
                change,  # type: ignore[arg-type]
                result,
                None,
                gate,
            ),
        )
    if id(change) not in severity_input:
        if record.disposition not in (
            Disposition.GATING,
            Disposition.NON_GATING,
        ):
            # A deduplicated row the severity gate does not score keeps
            # saying *why* it is out -- it was folded into another
            # finding, which `non_gating` would not convey.
            return record
        return replace(record, disposition=Disposition.NON_GATING, gate_excluded=True)
    # In the severity gate's own input, whatever bucket recorded it: the
    # gate scores it, so the audit counts it. The mechanism that had held
    # it out stays readable in `application_point`.
    return replace(
        record,
        disposition=_kept_disposition(
            change,  # type: ignore[arg-type]
            result,
            severity_config,
            gate,
        ),
        gate_excluded=False,
    )
