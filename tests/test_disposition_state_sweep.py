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

"""ADR-067 C-S1: the disposition mechanism's whole state space, swept.

The sibling `test_disposition_scope_matrix.py` states each mechanism's
contract in a readable, named test. This file is the other half: the full
cross-product of every axis the mechanism actually has --

    bucket x contract relevance x explicit-scope promotion
      x (unscoped | in the consumer scope | excluded by it)
      x (a severity configuration is resolved | not)
      x (`show_redundant` restored the row into `result.changes` | not)

= 504 cells, each checked against four invariants that read no ledger
reasoning of their own. It exists because the last several defects in this
mechanism were *combinations* -- each read correctly in every state a named
test covered, and failed in one nobody had written down. An ad-hoc version
of this sweep found two of them before review did; running it as a test is
what makes the next one fail locally instead.

Split from the matrix file at the architecture gate's 1200-line test cap.
Registered as a seed test of the `policy.disposition_conservation` bug class.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.contract_scoped_promotion import (
    stamp_explicit_scope_contract_evaluation,
)
from abicheck.policy.disposition_close import (
    close_consumer_scope,
    conservation_holds,
)
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger

# ---------------------------------------------------------------------------
# The whole state space, in one place
# ---------------------------------------------------------------------------


def _sweep_case(bucket, relevance, promote, scoped, severity, restore):
    """Build one cell of the sweep: a single finding, driven through the real
    recording, closing, scoping and gate-projection path."""
    from abicheck.checker_types import DiffResult
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.disposition_ledger import record_suppressed_change

    result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")
    if relevance is not None:
        change.contract_relevance = relevance
    scored: list[Change] = []
    ledger = DispositionLedger()
    if bucket == "changes":
        result.changes = [change]
    elif bucket == "redundant_scored":
        result.redundant_changes = [change]
        result.redundant_count = 1
        scored = [change]
    elif bucket == "redundant_unscored":
        result.redundant_changes = [change]
        result.redundant_count = 1
    elif bucket == "opaque_filtered":
        result.redundant_changes = [change]
        result.redundant_count = 0
    elif bucket == "suppressed":
        record_suppressed_change(ledger, change, rule=None, application_point="sweep")
        result.suppressed_changes = [change]
    else:
        setattr(result, bucket, [change])

    ledger = finalize_ledger(ledger, result, verdict_scored=scored)
    if promote:
        stamp_explicit_scope_contract_evaluation(change)
    if scoped is not None:
        close_consumer_scope(
            ledger,
            result,
            gating=[change] if scoped == "in" else [],
            also_detected=[change],
        )
    if restore and bucket not in ("changes", "suppressed"):
        # What `scope.show_redundant` does: restore the row into the list the
        # severity gate scores.
        result.changes = [change]
    return result, change, ledger, severity


_SWEEP = tuple(
    itertools.product(
        (
            "changes",
            "redundant_scored",
            "redundant_unscored",
            "opaque_filtered",
            "reconciled_changes",
            "out_of_surface_changes",
            "suppressed",
        ),
        (
            None,
            ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            ContractRelevance.UNKNOWN_UNPROVEN,
        ),
        (False, True),  # explicit-scope promotion
        (None, "in", "out"),  # unscoped / in the consumer scope / excluded
        (False, True),  # severity configuration resolved
        (False, True),  # restored into `result.changes` by show_redundant
    )
)


def test_the_sweep_covers_the_declared_state_space() -> None:
    """504 cells: 7 buckets x 3 relevances x promote x 3 scope answers x
    severity x restore. Asserted so the enumeration cannot silently shrink."""
    assert len(_SWEEP) == 7 * 3 * 2 * 3 * 2 * 2 == 504


@pytest.mark.parametrize(
    ("bucket", "relevance", "promote", "scoped", "severity", "restore"), _SWEEP
)
def test_the_whole_state_space_holds_four_invariants(
    bucket, relevance, promote, scoped, severity, restore
) -> None:
    """Every combination of the six axes this mechanism actually has, against
    the four invariants ten review rounds each broke one instance of.

    This is the standing form of the ad-hoc sweep that found the last two
    bugs in this file (a `deduplicated` row a consumer scope excluded and
    `show_redundant` then restored; a promoted out-of-surface finding read
    against the wrong gate). Running it as a test is what makes the next one
    fail locally instead of in review.

    The four, none of which reads the ledger's own reasoning:

    1. **Conservation.** One finding in, one record out, counts summing to it.
    2. **The severity gate's input.** `gate_decision_for_result` scores
       `result.changes`; a record the audit calls `gating` under a severity
       configuration must be in it — unless a consumer scope decided the
       record, in which case a *different* gate applies.
    3. **Scope authority.** A finding a consumer scope excluded never gates,
       whatever severity says about its kind.
    4. **No resurrected exclusion.** A suppressed finding compatibility
       policy never scored carries no verdict class, so it cannot reach
       `recommend_release` as a waived break.
    """
    from abicheck.contract_gating import is_evaluated
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    result, change, ledger, sev = _sweep_case(
        bucket, relevance, promote, scoped, severity, restore
    )
    strict = SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.ERROR,
        addition=SeverityLevel.ERROR,
    )
    gated = ledger.with_gate(result, strict if sev else None)

    assert gated.detected_total == 1
    assert sum(gated.counts().values()) == 1
    assert conservation_holds(gated)

    record = gated.record_for(change)
    if record.disposition is Disposition.GATING:
        if sev and not record.scope_decided:
            assert any(c is change for c in result.changes), (
                f"{record.application_point}: gating under a severity "
                "configuration, but the severity gate scores "
                "`result.changes` and this finding is not in it"
            )
        assert scoped != "out", (
            f"{record.application_point}: gating while the consumer scope excluded it"
        )
    if record.disposition is Disposition.SUPPRESSED and not is_evaluated(change):
        assert record.verdict_class is None, (
            "a suppressed finding policy never scored must not reach the "
            "release recommendation as a waived break"
        )
