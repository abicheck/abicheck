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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .disposition_types import Disposition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult


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
