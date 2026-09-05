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


def _sweep_result():
    """An empty real `DiffResult`, for the gate context alone."""
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot

    return compare(
        AbiSnapshot(library="libmatrix", version="1.0"),
        AbiSnapshot(library="libmatrix", version="2.0"),
    )


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


def test_one_export_named_twice_is_one_detection() -> None:
    """The overlay path runs once per `--used-by` consumer and builds a fresh
    object each time, so the ledger's identity key alone can disagree with
    the scoped view's finding-id key.

    Two *different* consumers needing the same export are genuinely two
    observations — each overlay names its own consumer, so both the report
    and the audit show two, and they agree. The case that did not agree is
    the same consumer reaching the path twice (a repeated `--used-by`
    operand, or one app resolved through two library aliases): the
    orchestrator's `relevant_changes_by_id` collapses those to one entry
    while the identity-keyed ledger counted two, making the raw total move
    with *how the run was invoked* rather than with what was observed.
    """
    from abicheck.policy.disposition_close import record_consumer_overlay

    result = _sweep_result()
    ledger = DispositionLedger()
    # The very shape the overlay path produces, built three times over -- as
    # a repeated consumer operand does.
    consumers = [
        Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="shared_export",
            description="Consumer 'app' requires symbol 'shared_export'",
        )
        for _ in range(3)
    ]
    for overlay in consumers:
        record_consumer_overlay(ledger, overlay, result)

    assert ledger.detected_total == 1, "one missing export, one observation"
    assert conservation_holds(ledger)
    # …and every one of the three objects still resolves to that record, so a
    # consumer-scoped report can join on whichever it holds.
    assert {id(ledger.record_for(c)) for c in consumers} == {
        id(ledger.record_for(consumers[0]))
    }

    # A different export is a second detection -- and so is the *same* export
    # required by a genuinely different consumer, whose overlay names it and
    # which the scoped view therefore also shows separately. The key is the
    # report's own finding id, so the audit and the report cannot disagree
    # about which of these is one finding and which is two.
    for other in (
        Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="other_export",
            description="Consumer 'app' requires symbol 'other_export'",
        ),
        Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="shared_export",
            description="Consumer 'other_app' requires symbol 'shared_export'",
        ),
    ):
        record_consumer_overlay(ledger, other, result)
    assert ledger.detected_total == 3


def test_the_first_consumers_rule_provenance_is_the_one_kept() -> None:
    """Deduping must not silently re-record: the disposition and rule the
    *first* producer resolved are the ones that applied to the run."""
    from abicheck.policy.disposition_close import record_consumer_overlay
    from abicheck.suppression import Suppression, SuppressionList

    result = _sweep_result()
    ledger = DispositionLedger()
    rule = Suppression(symbol_pattern=".*", reason="first consumer's rule")
    first, second = (
        Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="shared_export",
            description="Consumer 'app' requires symbol 'shared_export'",
        )
        for _ in range(2)
    )
    record_consumer_overlay(
        ledger, first, result, rule=rule, suppression=SuppressionList([rule])
    )
    record_consumer_overlay(ledger, second, result)

    assert ledger.detected_total == 1
    record = ledger.record_for(second)
    assert record.disposition is Disposition.SUPPRESSED
    assert record.rule is not None
    assert record.rule.reason == "first consumer's rule"


def test_a_restored_row_inside_the_consumer_scope_rejoins_the_gate() -> None:
    """The scoped-gate sibling of the restored-row rule.

    `scope.show_redundant` restores a redundant finding into `result.changes`
    *before* `--used-by`/`--required-symbol` scoping runs, so the scoped
    severity gate does score it — but a guard that refused to re-answer
    anything a scope had touched left it `deduplicated`, reporting
    `effective_total: 0` beside a scoped exit 4. `scope_decided` marks *which
    gate* decides a record, never that the record is frozen: only a genuinely
    scope-*excluded* row is untouchable.
    """
    from abicheck.checker_types import DiffResult
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    strict = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
    for in_scope in (True, False):
        result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
        restored = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="f", description="redundant"
        )
        result.redundant_changes = [restored]
        result.redundant_count = 1
        ledger = finalize_ledger(DispositionLedger(), result)
        assert ledger.record_for(restored).disposition is Disposition.DEDUPLICATED

        # …then `show_redundant` restores it, and the scope rules on it.
        result.changes = [restored]
        close_consumer_scope(ledger, result, gating=[restored] if in_scope else [])
        gated = ledger.with_gate(result, strict)
        assert gated.effective_total == int(in_scope), (
            "in scope: the scoped severity gate scores the restored row, so "
            "the audit must count it; out of scope: it must not, whatever "
            "severity says about its kind"
        )
        assert conservation_holds(gated)


def test_a_scoped_alias_resolves_to_its_canonical_record() -> None:
    """Membership must be resolved the same way on both sides of the ledger.

    The overlay path's `dedupe_key` collapses several equal-but-not-identical
    objects of one observation onto a single record, keeping the *first* as
    the anchor. The orchestrator's `relevant_changes_by_id` keeps whichever
    alias it saw *last* for its gating union. Comparing anchor identity
    against that union read the record as out of scope and demoted it to
    `non_gating` — while the scoped gate could still fail on the alias the
    union actually holds, so the audit said `effective_total: 0` beside a
    real scoped failure.

    Stated for every alias position, not just the reported last-wins one: any
    of them naming the finding must put the record in scope.
    """
    from abicheck.policy.disposition_close import record_consumer_overlay

    for union_position in (0, 1, 2):
        result = _sweep_result()
        ledger = DispositionLedger()
        aliases = [
            Change(
                kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
                symbol="shared_export",
                description="Consumer 'app' requires symbol 'shared_export'",
            )
            for _ in range(3)
        ]
        for alias in aliases:
            record_consumer_overlay(ledger, alias, result)
        assert ledger.detected_total == 1

        # The union holds exactly one of them — the orchestrator keeps the
        # last it saw, but the rule cannot depend on which.
        close_consumer_scope(ledger, result, gating=[aliases[union_position]])
        record = ledger.record_for(aliases[0])
        assert record.disposition is Disposition.GATING, (
            f"alias {union_position} names this finding in the scoped gating "
            "union, so its record is in scope"
        )
        assert record.gate_excluded is False
        assert ledger.effective_total == 1
        assert conservation_holds(ledger)


def test_an_alias_absent_from_the_union_is_still_excluded() -> None:
    """The negative control: resolving aliases must not make everything
    in-scope. A finding no alias of which appears in the union is excluded,
    exactly as before."""
    from abicheck.policy.disposition_close import record_consumer_overlay

    result = _sweep_result()
    ledger = DispositionLedger()
    used = Change(
        kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
        symbol="used",
        description="Consumer 'app' requires symbol 'used'",
    )
    unused_aliases = [
        Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="unused",
            description="Consumer 'app' requires symbol 'unused'",
        )
        for _ in range(2)
    ]
    for overlay in (used, *unused_aliases):
        record_consumer_overlay(ledger, overlay, result)
    assert ledger.detected_total == 2

    close_consumer_scope(ledger, result, gating=[used])
    assert ledger.record_for(used).disposition is Disposition.GATING
    excluded = ledger.record_for(unused_aliases[1])
    assert excluded.disposition is Disposition.NON_GATING
    assert excluded.gate_excluded is True
    assert ledger.effective_total == 1
