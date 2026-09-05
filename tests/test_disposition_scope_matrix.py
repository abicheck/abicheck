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

"""ADR-067 C-S1: the consumer-scoping/close mechanism, exhaustively.

The sibling files state the ledger's conservation contract and its
detector/release halves. This one exists because the *scoping* half was
fixed six times in six review rounds, each time for one more corner of the
same state space, and each fix's test pinned only the corner that had just
been reported. A hand-written test only forecloses the input it names (root
``AGENTS.md``, "Primitive-level property tests"), so this file enumerates
the whole space instead:

    (initial disposition: one of D2's six terminal values)
      x (in scope | excluded by the consumer scope)
      x (already recorded before the close | recorded during it)
      x (a severity configuration is in effect | none is)

= 48 cases, each checked against :func:`_oracle` — a hand-written statement
of what ADR-067 D2/D3 and the scope-versus-severity authority rule *say*
should happen. The oracle calls no ledger, no
``gate_contribution_for_change``, and no policy helper; its one gate-shaped
input is a two-row table (``_ORACLE_GATES``) whose entries were established
against the documented semantics of the two fixtures and which *inverts*
between the two severity settings, so it cannot silently agree with the
implementation by sharing its formula.

Registered as a seed test of the ``policy.disposition_conservation`` bug
class (``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.model import AbiSnapshot
from abicheck.policy.disposition_close import (
    close_consumer_scope,
    conservation_holds,
    ledger_for,
    record_kept_change,
)
from abicheck.policy.disposition_ledger import (
    Disposition,
    DispositionLedger,
    record_suppressed_change,
)
from abicheck.policy.severity import SeverityConfig, SeverityLevel

# ---------------------------------------------------------------------------
# The independent oracle
# ---------------------------------------------------------------------------

#: The two fixture kinds' gate contribution under each of the two severity
#: settings, written down rather than computed. Legacy (no severity config)
#: scores a removed function as an ABI break and an added one as harmless;
#: the strict configuration below deliberately inverts *both*, so an oracle
#: that had accidentally reproduced the implementation's own reasoning would
#: disagree with it on every row rather than on none.
_ORACLE_GATES = {
    (ChangeKind.FUNC_REMOVED, False): True,
    (ChangeKind.FUNC_ADDED, False): False,
    (ChangeKind.FUNC_REMOVED, True): False,
    (ChangeKind.FUNC_ADDED, True): True,
}

#: ``abi_breaking`` demoted and ``addition`` promoted — the inversion above.
_INVERTING_SEVERITY = SeverityConfig(
    abi_breaking=SeverityLevel.INFO, addition=SeverityLevel.ERROR
)

_EVALUATED = (Disposition.GATING, Disposition.NON_GATING)


def _oracle(
    initial: Disposition, kind: ChangeKind, *, in_scope: bool, severity: bool
) -> tuple[Disposition, bool]:
    """What D2/D3 say the record should look like after the close and gate.

    Returns ``(disposition, gate_excluded)``. Three rules, in order:

    1. **Scoping only ever narrows, and only what was evaluated.** A
       suppressed, deduplicated, out-of-contract or unresolved-relevance
       finding never reached the gate, so no scope decision can move it (D2:
       one change, one terminal disposition) and it is never marked excluded.
    2. **An evaluated finding outside the scope is demoted and marked.** It
       becomes ``non_gating`` with ``gate_excluded`` set.
    3. **Severity re-scores only what scoping left in.** A scope-excluded or
       non-evaluated record is untouched — severity says how severe a finding
       is, never whether the consumer this run gates on uses it at all.
    """
    if initial not in _EVALUATED:
        return initial, False
    if not in_scope:
        return Disposition.NON_GATING, True
    gates = _ORACLE_GATES[(kind, severity)]
    return (Disposition.GATING if gates else Disposition.NON_GATING), False


# ---------------------------------------------------------------------------
# Fixtures for each of the six initial dispositions
# ---------------------------------------------------------------------------

#: The kind each initial disposition's fixture change carries. The four that
#: `_kept_disposition` can itself produce use a kind whose gate answer the
#: oracle table above knows; the two the gate never sees (a suppression and a
#: deduplication are recorded by their application point, not derived) reuse
#: the breaking kind so a mistaken re-derivation would be visible as a
#: ``gating`` record rather than silently agreeing.
_FIXTURE_KIND = {
    Disposition.GATING: ChangeKind.FUNC_REMOVED,
    Disposition.NON_GATING: ChangeKind.FUNC_ADDED,
    Disposition.SUPPRESSED: ChangeKind.FUNC_REMOVED,
    Disposition.DEDUPLICATED: ChangeKind.FUNC_REMOVED,
    Disposition.OUT_OF_CONTRACT: ChangeKind.FUNC_REMOVED,
    Disposition.UNRESOLVED_RELEVANCE: ChangeKind.FUNC_REMOVED,
}

#: A non-evaluated disposition is reached by stamping the finding's contract
#: relevance, exactly the way `contract_pipeline` does — ADR-049 D1 splits
#: "not evaluated" into a positive determination and evidence running out.
_FIXTURE_RELEVANCE = {
    Disposition.OUT_OF_CONTRACT: ContractRelevance.PROVEN_OUT_OF_CONTRACT,
    Disposition.UNRESOLVED_RELEVANCE: ContractRelevance.UNKNOWN_UNPROVEN,
}


def _empty_result():
    """A real `DiffResult` with no findings, used only for its gate context.

    The matrix drives the ledger with hand-built changes rather than a diff,
    since several of the six dispositions cannot be produced by any single
    snapshot pair — but the policy, kind sets and policy file a real
    comparison resolves are what the gate reads, so they come from one.
    """
    return compare(
        AbiSnapshot(library="libmatrix", version="1.0"),
        AbiSnapshot(library="libmatrix", version="2.0"),
    )


def _fixture_change(initial: Disposition, index: int) -> Change:
    change = Change(
        kind=_FIXTURE_KIND[initial],
        symbol=f"matrix::sym{index}",
        description=f"{initial.value} fixture {index}",
    )
    relevance = _FIXTURE_RELEVANCE.get(initial)
    if relevance is not None:
        change.contract_relevance = relevance
    return change


def _record_initially(
    ledger: DispositionLedger, change: Change, initial: Disposition, result
) -> None:
    """Put *change* into *ledger* with its intended starting disposition.

    Each branch is the production call site that really produces that
    disposition, never a hand-set enum value where a real one exists — and
    the caller asserts the precondition landed, so a fixture that stopped
    producing the disposition it claims fails here rather than making the
    outcome assertion vacuous.
    """
    if initial is Disposition.SUPPRESSED:
        record_suppressed_change(
            ledger, change, rule=None, application_point="matrix_suppression"
        )
    elif initial is Disposition.DEDUPLICATED:
        ledger.record(
            change, Disposition.DEDUPLICATED, application_point="matrix_dedup"
        )
    else:
        record_kept_change(ledger, change, result, application_point="matrix_kept")


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

#: A disposition the gate itself cannot derive has to exist in the ledger
#: before the close; its "recorded during the close" row is therefore the
#: real-world shape of that case — the overlay recorded it, and the
#: orchestrator then offers the same object in `also_detected` — which is
#: what makes `record`'s identity no-op load-bearing rather than incidental.
_PRE_RECORDED_ONLY = (Disposition.SUPPRESSED, Disposition.DEDUPLICATED)

_MATRIX = tuple(
    itertools.product(
        tuple(Disposition),
        (True, False),  # in scope
        (True, False),  # already recorded before the close
        (True, False),  # a severity configuration is in effect
    )
)


def test_the_matrix_covers_the_whole_state_space() -> None:
    """The enumeration is the test's own claim to exhaustiveness, so it is
    asserted rather than assumed: every one of D2's terminal dispositions,
    both scope answers, both timings, both severity settings."""
    assert len(_MATRIX) == 6 * 2 * 2 * 2 == 48
    assert {case[0] for case in _MATRIX} == set(Disposition)


@pytest.mark.parametrize(("initial", "in_scope", "pre_recorded", "severity"), _MATRIX)
def test_scope_and_gate_agree_with_the_oracle(
    initial: Disposition, in_scope: bool, pre_recorded: bool, severity: bool
) -> None:
    """Every cell of the state space, against `_oracle`.

    Checks all four questions the mechanism has been wrong about at least
    once: whether `gate_excluded` is set, what the disposition is after the
    close, what it is after `with_gate` re-scores it, and whether the
    detected total conserves across both.
    """
    result = _empty_result()
    ledger = DispositionLedger()
    change = _fixture_change(initial, 0)
    # A second, always-in-scope finding, so the ledger is never a population
    # of one: a bug that dropped or double-counted a record would otherwise
    # be invisible in the totals.
    companion = _fixture_change(Disposition.GATING, 1)

    must_pre_record = pre_recorded or initial in _PRE_RECORDED_ONLY
    if must_pre_record:
        _record_initially(ledger, change, initial, result)
        assert ledger.record_for(change).disposition is initial, (
            "fixture no longer produces the disposition this case is about"
        )
    _record_initially(ledger, companion, Disposition.GATING, result)

    close_consumer_scope(
        ledger,
        result,
        gating=[companion, *([change] if in_scope else [])],
        also_detected=[change] if not pre_recorded else [],
    )

    expected_disposition, expected_excluded = _oracle(
        initial, _FIXTURE_KIND[initial], in_scope=in_scope, severity=False
    )
    record = ledger.record_for(change)
    assert record is not None, "the close must record a finding it was handed"
    assert record.gate_excluded is expected_excluded
    assert record.disposition is expected_disposition

    # …and again after the resolved gate re-scores what scoping left in.
    config = _INVERTING_SEVERITY if severity else None
    gated = ledger.with_gate(result, config)
    expected_gated, expected_gated_excluded = _oracle(
        initial, _FIXTURE_KIND[initial], in_scope=in_scope, severity=severity
    )
    gated_record = gated.record_for(change)
    assert gated_record.disposition is expected_gated
    assert gated_record.gate_excluded is expected_gated_excluded

    # D3 conservation, on both the closed and the re-scored ledger: two
    # findings were detected, and every one of them holds exactly one
    # terminal disposition.
    for candidate in (ledger, gated):
        assert candidate.detected_total == 2
        assert sum(candidate.counts().values()) == 2
        assert conservation_holds(candidate)


@pytest.mark.parametrize(("initial", "in_scope", "pre_recorded", "severity"), _MATRIX)
def test_the_effective_total_is_the_gating_count(
    initial: Disposition, in_scope: bool, pre_recorded: bool, severity: bool
) -> None:
    """`effective_total` is what every projection prints beside the exit
    code, so it is checked against the oracle's own count of gating findings
    rather than against the ledger's records — a mechanism that agreed with
    itself while both were wrong is exactly the failure this file exists
    for."""
    result = _empty_result()
    ledger = DispositionLedger()
    change = _fixture_change(initial, 0)
    companion = _fixture_change(Disposition.GATING, 1)

    if pre_recorded or initial in _PRE_RECORDED_ONLY:
        _record_initially(ledger, change, initial, result)
    _record_initially(ledger, companion, Disposition.GATING, result)
    close_consumer_scope(
        ledger,
        result,
        gating=[companion, *([change] if in_scope else [])],
        also_detected=[change] if not pre_recorded else [],
    )

    config = _INVERTING_SEVERITY if severity else None
    gated = ledger.with_gate(result, config)

    expected = 0
    # The companion is in scope by construction, so the oracle answers it the
    # same way it answers any in-scope evaluated finding.
    for candidate, candidate_in_scope in ((change, in_scope), (companion, True)):
        disposition, _ = _oracle(
            _initial_of(candidate, initial),
            candidate.kind,
            in_scope=candidate_in_scope,
            severity=severity,
        )
        expected += disposition is Disposition.GATING
    assert gated.effective_total == expected


def _initial_of(change: Change, initial: Disposition) -> Disposition:
    """The companion is always a plain gating finding; the case's own change
    carries the case's initial disposition."""
    return initial if change.symbol.endswith("sym0") else Disposition.GATING


# ---------------------------------------------------------------------------
# The two properties the matrix above states cell by cell, stated globally
# ---------------------------------------------------------------------------


def test_scoping_never_relabels_a_non_evaluated_finding() -> None:
    """D2's sharpest edge, and the last bug this sweep found: an out-of-scope
    finding recorded *during* the close was being written down as
    `non_gating` whatever it really was, so a proven-out-of-contract
    consumer finding lost its exclusion and read as an ordinary
    evaluated-and-harmless change. `apply_scope` had always skipped these;
    the late-recording path had not."""
    result = _empty_result()
    for relevance, expected in (
        (ContractRelevance.PROVEN_OUT_OF_CONTRACT, Disposition.OUT_OF_CONTRACT),
        (ContractRelevance.UNKNOWN_UNPROVEN, Disposition.UNRESOLVED_RELEVANCE),
        (ContractRelevance.UNKNOWN_UNRESOLVED, Disposition.UNRESOLVED_RELEVANCE),
    ):
        late = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="late::sym", description="late"
        )
        late.contract_relevance = relevance
        ledger = DispositionLedger()
        close_consumer_scope(ledger, result, gating=[], also_detected=[late])
        record = ledger.record_for(late)
        assert record.disposition is expected
        assert record.gate_excluded is False, (
            "a finding the gate never scored cannot be excluded from it"
        )


def test_a_second_close_is_idempotent() -> None:
    """The orchestrators close once, but `check_appcompat` and a `--used-by`
    run can both reach the same `DiffResult` in one process. Closing twice
    over the same scope must not move anything or double-count."""
    result = _empty_result()
    ledger = DispositionLedger()
    kept = _fixture_change(Disposition.GATING, 0)
    dropped = _fixture_change(Disposition.GATING, 1)
    for change in (kept, dropped):
        _record_initially(ledger, change, Disposition.GATING, result)

    close_consumer_scope(ledger, result, gating=[kept], also_detected=[kept, dropped])
    first = [(r.disposition, r.gate_excluded) for r in ledger._records]
    close_consumer_scope(ledger, result, gating=[kept], also_detected=[kept, dropped])
    assert [(r.disposition, r.gate_excluded) for r in ledger._records] == first
    assert ledger.detected_total == 2
    assert ledger.effective_total == 1


def test_a_gate_excluded_finding_survives_repeated_gate_projections() -> None:
    """`with_gate` returns a copy, so rendering the same run twice under two
    severity settings must give two independent answers and leave the closed
    ledger untouched — the projection-must-not-mutate rule, applied to the
    scope mark specifically."""
    result = _empty_result()
    ledger = DispositionLedger()
    excluded = _fixture_change(Disposition.GATING, 0)
    _record_initially(ledger, excluded, Disposition.GATING, result)
    close_consumer_scope(ledger, result, gating=[], also_detected=[])

    for config in (None, _INVERTING_SEVERITY, None):
        record = ledger.with_gate(result, config).record_for(excluded)
        assert record.disposition is Disposition.NON_GATING
        assert record.gate_excluded is True
    assert ledger.record_for(excluded).gate_excluded is True
    assert ledger.effective_total == 0


def test_the_ledger_a_projection_reads_is_never_mutated_by_scoping_twice() -> None:
    """`ledger_for` must keep answering the same object once a close has run,
    so the counts a report prints are the counts the gate used."""
    result = _empty_result()
    ledger = DispositionLedger()
    change = _fixture_change(Disposition.GATING, 0)
    _record_initially(ledger, change, Disposition.GATING, result)
    result.disposition_ledger = ledger
    close_consumer_scope(ledger_for(result), result, gating=[change])
    assert ledger_for(result) is ledger
    assert ledger_for(result).effective_total == 1


# ---------------------------------------------------------------------------
# The audit and the exit code are computed from the same findings
# ---------------------------------------------------------------------------


def _gates(verdict) -> bool:
    """Whether *verdict* produces a non-zero legacy exit code.

    Written down here rather than read from `severity.legacy_exit_code`: this
    is the oracle, and an oracle that calls the function under test proves
    nothing (`tests/test_snapshot_compression.py`'s own lesson, generalized).
    """
    from abicheck.checker import Verdict

    return verdict in (Verdict.BREAKING, Verdict.API_BREAK)


@pytest.mark.parametrize(("removed", "added"), [(0, 0), (2, 0), (0, 2), (3, 2), (1, 1)])
@pytest.mark.parametrize("suppress_everything", [False, True])
def test_the_effective_total_agrees_with_the_verdict(
    removed: int, added: int, suppress_everything: bool
) -> None:
    """A gating verdict means at least one gating finding, and vice versa.

    The invariant behind a whole family of this ledger's bugs: the audit is
    supposed to *reconcile with* the number the user is gated on, and every
    time a bucket was trusted to imply a disposition instead of the gate
    being asked, the two silently drifted apart. Most recently, a redundant
    finding that policy still scored (`checker.compare` computes the verdict
    over `kept + verdict_redundant`) was labelled `deduplicated`
    unconditionally, so a run that really exits 4 reported
    `effective_total: 0`.

    The oracle is `DiffResult.verdict` — computed by a different code path
    from anything the ledger touches — not a recount of the ledger's own
    records.
    """
    from abicheck.model import Function, Visibility
    from abicheck.suppression import Suppression, SuppressionList

    old = AbiSnapshot(library="libmatrix", version="1.0")
    new = AbiSnapshot(library="libmatrix", version="2.0")
    for i in range(removed):
        old.functions.append(
            Function(
                name=f"gone{i}",
                mangled=f"_Z4gone{i}v",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        )
    for i in range(added):
        new.functions.append(
            Function(
                name=f"new{i}",
                mangled=f"_Z3new{i}v",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        )
    rules = (
        SuppressionList(
            [Suppression(symbol_pattern=".*", reason="all", allow_public_break=True)]
        )
        if suppress_everything
        else None
    )
    result = compare(old, new, rules)
    effective = ledger_for(result).effective_total
    assert (effective > 0) is _gates(result.verdict), (
        f"verdict {result.verdict.value} and effective_total {effective} "
        "disagree about whether this run gated"
    )


def test_a_policy_scored_redundant_finding_is_not_deduplicated() -> None:
    """The reported shape directly, at the finalizer's own boundary.

    A derived finding can be redundant *for display* and still be scored by
    the gate — `checker.compare` computes the verdict over
    `kept + verdict_redundant`. `deduplicated` claims it was folded into
    another finding and removes it from `effective_total`; the disposition
    has to come from the gate instead, with the display mechanism recorded as
    the application point.
    """
    from abicheck.checker_types import DiffResult
    from abicheck.policy.disposition_close import finalize_ledger

    result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
    root = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="T", description="root")
    derived = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="f", description="derived"
    )
    collapsed = Change(
        kind=ChangeKind.FUNC_REMOVED, symbol="g", description="rename half"
    )
    result.changes = [root]
    result.redundant_changes = [derived, collapsed]
    result.redundant_count = 2

    # Only `derived` was scored; `collapsed` is the rename-collapsed half
    # `checker.compare` deliberately keeps out of the verdict input.
    ledger = finalize_ledger(DispositionLedger(), result, verdict_scored=[derived])
    assert ledger.record_for(derived).disposition is Disposition.GATING
    assert ledger.record_for(derived).application_point == "redundancy_filter_scored"
    assert ledger.record_for(collapsed).disposition is Disposition.DEDUPLICATED
    assert ledger.effective_total == 2, "the root and the scored derived finding"
    assert ledger.detected_total == 3
    assert conservation_holds(ledger)


def test_an_unscored_redundant_finding_stays_deduplicated() -> None:
    """The negative control: passing no scored set (the reconciliation
    fallback for a `DiffResult` no `compare()` built) must not start
    promoting redundant findings into the gate."""
    from abicheck.checker_types import DiffResult
    from abicheck.policy.disposition_close import finalize_ledger

    result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
    derived = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="f", description="derived"
    )
    result.changes = []
    result.redundant_changes = [derived]
    result.redundant_count = 1
    ledger = finalize_ledger(DispositionLedger(), result)
    assert ledger.record_for(derived).disposition is Disposition.DEDUPLICATED
    assert ledger.effective_total == 0


# ---------------------------------------------------------------------------
# Every place a finding can leave the gate, enumerated
# ---------------------------------------------------------------------------

#: Every bucket :func:`finalize_ledger` reads, with whether `checker.compare`
#: scores the verdict over it. The oracle is that function's own verdict input
#: — `kept + verdict_redundant`, i.e. `result.changes` plus the redundant
#: findings policy did not exclude — stated here as a table rather than
#: recomputed, so it cannot agree with the ledger by sharing its reasoning.
#:
#: This table is the point of the test below: the round-10 and round-11
#: findings were both "a bucket produced a disposition and a later pass made a
#: wrong assumption about it", and a test that covers only the buckets a
#: previous bug happened to touch cannot be the backstop for that family.
#: A bucket added to `finalize_ledger` later and not added here fails
#: `test_the_bucket_table_covers_every_bucket_the_finalizer_reads`.
_BUCKET_SCORED = (
    ("changes", True),
    ("redundant_scored", True),
    ("redundant_unscored", False),
    ("opaque_filtered", False),
    ("reconciled_changes", False),
    ("out_of_surface_changes", False),
)


def _result_with_one_breaking_finding_in(bucket: str):
    """A `DiffResult` whose single breaking finding sits only in *bucket*."""
    from abicheck.checker_types import DiffResult

    result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
    change = Change(
        kind=ChangeKind.FUNC_REMOVED, symbol="only", description="the one finding"
    )
    scored: list[Change] = []
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
    else:
        setattr(result, bucket, [change])
    return result, change, scored


@pytest.mark.parametrize(("bucket", "scored"), _BUCKET_SCORED)
@pytest.mark.parametrize("severity", [False, True])
def test_only_the_scored_buckets_are_effective(
    bucket: str, scored: bool, severity: bool
) -> None:
    """The invariant round 10 and round 11 each broke one instance of.

    A finding excluded from the verdict input stays out of `effective_total`,
    *including* once a severity configuration is resolved — severity says how
    severe a finding is, never whether it reached the gate. The severity
    configuration used here rates every category `error`, so any bucket that
    forgot to mark itself gate-excluded is promoted back to `gating` and
    fails this test rather than being caught a round later.
    """
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    result, change, verdict_scored = _result_with_one_breaking_finding_in(bucket)
    ledger = finalize_ledger(DispositionLedger(), result, verdict_scored=verdict_scored)
    assert ledger.detected_total == 1
    assert ledger.effective_total == int(scored), bucket

    everything_is_an_error = SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.ERROR,
        addition=SeverityLevel.ERROR,
    )
    gated = ledger.with_gate(result, everything_is_an_error if severity else None)
    assert gated.effective_total == int(scored), (
        f"{bucket}: a severity configuration changed whether this finding "
        "reached the gate, which is not severity's decision to make"
    )
    assert gated.detected_total == 1
    assert conservation_holds(gated)


def test_the_bucket_table_covers_every_bucket_the_finalizer_reads() -> None:
    """The table above is only a backstop if it stays complete.

    Read off `finalize_ledger`'s source rather than maintained by hand: a
    bucket added there without a row here is the exact way this test would
    silently stop being the backstop for the family it exists to close.
    """
    import inspect

    from abicheck.policy import disposition_close

    source = inspect.getsource(disposition_close.finalize_ledger)
    read = {
        line.split('_bucket("')[1].split('"')[0]
        for line in source.splitlines()
        if '_bucket("' in line
    }
    # `changes` and `redundant_changes` are covered by four rows between them
    # (kept, scored, unscored, opaque); `suppressed_changes` is the fallback
    # for a finding no application point recorded, which by construction
    # never reaches the gate and is covered by the conservation matrix above.
    covered = {"changes", "redundant_changes", "suppressed_changes"} | {
        name for name, _ in _BUCKET_SCORED
    }
    assert read <= covered, (
        f"finalize_ledger reads buckets with no row in _BUCKET_SCORED: "
        f"{sorted(read - covered)}"
    )


def test_a_contract_promotion_refreshes_a_stale_exclusion() -> None:
    """Round 11's second half: promotion has to update the record.

    ADR-049 §4.3 lets an explicit `--used-by`/`--required-symbol` contract
    promote a finding the `--contract` evaluator excluded, and the scoped
    gate then scores it. `apply_scope` only ever demotes and skips exactly
    the two non-evaluated dispositions, so without a promotion pass the
    record keeps its stale `out_of_contract` label and an evaluated breaking
    removal exits nonzero while the audit reports `effective_total: 0`.
    """
    result = _empty_result()
    ledger = DispositionLedger()
    promoted = _fixture_change(Disposition.OUT_OF_CONTRACT, 0)
    _record_initially(ledger, promoted, Disposition.OUT_OF_CONTRACT, result)
    assert ledger.record_for(promoted).disposition is Disposition.OUT_OF_CONTRACT

    # What `contract_scoped_promotion.stamp_scoped_changes` does to the
    # finding: an explicit consumer outranks the snapshot-derived exclusion.
    promoted.contract_relevance = ContractRelevance.IN_CONTRACT

    close_consumer_scope(ledger, result, gating=[promoted], also_detected=[promoted])
    record = ledger.record_for(promoted)
    assert record.disposition is Disposition.GATING
    assert record.application_point == "contract_promotion"
    assert ledger.effective_total == 1


def test_a_promotion_outside_the_consumer_scope_is_still_excluded() -> None:
    """Promotion widens, scoping narrows, and the order is promote-then-narrow:
    a finding promoted into the contract that *this* consumer does not use is
    still out of the gate, and marked so severity cannot pull it back."""
    result = _empty_result()
    ledger = DispositionLedger()
    promoted = _fixture_change(Disposition.OUT_OF_CONTRACT, 0)
    _record_initially(ledger, promoted, Disposition.OUT_OF_CONTRACT, result)
    promoted.contract_relevance = ContractRelevance.IN_CONTRACT

    close_consumer_scope(ledger, result, gating=[], also_detected=[])
    record = ledger.record_for(promoted)
    assert record.disposition is Disposition.NON_GATING
    assert record.gate_excluded is True
    assert ledger.effective_total == 0
