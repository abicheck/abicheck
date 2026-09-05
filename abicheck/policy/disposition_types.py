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

"""ADR-067 D2's terminal disposition vocabulary, and the record shape.

One enum, on its own, because three modules need it and two of them need each
other: the ledger stores it, the gate module answers it, and the closer
assigns it. A leaf inward of all three is what lets them share it without a
cycle. Re-exported from :mod:`abicheck.policy.disposition_ledger`, which
remains the name every consumer imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .rule_provenance import RuleProvenance


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
    #: Set when a *consumer scope* (``--used-by``/``--required-symbol``)
    #: ruled on this finding -- **in scope or out**, not only out.
    #:
    #: It marks which *gate* decided the record, which is why it is separate
    #: from :attr:`gate_excluded` (whether that gate excluded it). A scoped
    #: run is gated by ``cli_helpers_compare._scoped_exit_code`` over the
    #: consumer's own relevant set, not by ``gate_decision_for_result`` over
    #: ``result.changes`` -- so :meth:`with_gate`'s re-read of
    #: ``result.changes`` membership, which is what lets a restored redundant
    #: row rejoin the gate, must not touch a scope-decided record at all: it
    #: would demote a scoped-only finding the scoped gate really does score
    #: (a synthesized missing entrypoint is in no ``result.changes``), and
    #: un-exclude one the consumer simply does not use.
    scope_decided: bool = False
    #: A finding *policy generated about another finding* rather than a change
    #: any detector observed -- today, `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`.
    #:
    #: Recorded, but not counted as a detection: D1's raw total must not move
    #: when merely adding a rule produces one, and D3's per-disposition counts
    #: sum to that total, so an overlay appears in neither. It *is* counted in
    #: :attr:`effective_total`, because a severity configuration can gate on
    #: it independently of the finding it describes (`abi_breaking: info`
    #: demoting the break while `potential_breaking: error` promotes the
    #: diagnostic), and an audit that dropped the record entirely could not
    #: represent that real gate contribution.
    policy_overlay: bool = False

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
