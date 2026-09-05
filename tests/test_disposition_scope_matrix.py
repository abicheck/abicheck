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
from abicheck.contract_scoped_promotion import (
    stamp_explicit_scope_contract_evaluation,
)
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

#: Every bucket :func:`finalize_ledger` reads, with whether each of the two
#: gate schemes scores it. The oracle is the two gates' own *inputs*, stated
#: here as a table rather than recomputed:
#:
#: * the legacy verdict is computed over ``kept + verdict_redundant``
#:   (`checker.compare`), so `changes` and a policy-scored redundant finding
#:   are both in it;
#: * the severity gate is computed over ``result.changes`` alone
#:   (`policy.gate_decision.gate_decision_for_result`), so the redundant one
#:   is *not*.
#:
#: The two columns differ in exactly one row, and that row is round 13's
#: finding: one boolean cannot describe a finding one gate scores and the
#: other does not.
#:
#: This table is the point of the tests below: rounds 10-13's findings were
#: all "a bucket produced a disposition and a later pass made a wrong
#: assumption about it", and a test covering only the buckets a previous bug
#: happened to touch cannot be the backstop for that family. A bucket added
#: to `finalize_ledger` later and not added here fails
#: `test_the_bucket_table_covers_every_bucket_the_finalizer_reads`.
_BUCKET_SCORED = (
    # bucket, scored by the legacy verdict, scored by the severity gate
    ("changes", True, True),
    ("redundant_scored", True, False),
    ("redundant_unscored", False, False),
    ("opaque_filtered", False, False),
    ("reconciled_changes", False, False),
    ("out_of_surface_changes", False, False),
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


@pytest.mark.parametrize(("bucket", "legacy_scored", "severity_scored"), _BUCKET_SCORED)
@pytest.mark.parametrize("severity", [False, True])
def test_only_the_scored_buckets_are_effective(
    bucket: str, legacy_scored: bool, severity_scored: bool, severity: bool
) -> None:
    """The invariant rounds 10, 11 and 13 each broke one instance of.

    A finding outside the *acting* gate's input stays out of
    `effective_total`, and which gate is acting depends on whether a severity
    configuration was resolved — the two read different inputs, which is the
    whole of round 13's finding. The severity configuration used here rates
    every category `error`, so a bucket that is outside the severity gate's
    input but forgot to say so is promoted back to `gating` and fails here
    rather than being caught a round later.
    """
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    result, change, verdict_scored = _result_with_one_breaking_finding_in(bucket)
    ledger = finalize_ledger(DispositionLedger(), result, verdict_scored=verdict_scored)
    assert ledger.detected_total == 1
    assert ledger.effective_total == int(legacy_scored), bucket

    everything_is_an_error = SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.ERROR,
        addition=SeverityLevel.ERROR,
    )
    expected = severity_scored if severity else legacy_scored
    gated = ledger.with_gate(result, everything_is_an_error if severity else None)
    assert gated.effective_total == int(expected), (
        f"{bucket}: the audit disagrees with the "
        f"{'severity' if severity else 'legacy'} gate about whether this "
        "finding reached it"
    )
    assert gated.detected_total == 1
    assert conservation_holds(gated)


def test_a_severity_gating_record_is_in_the_severity_gates_own_input() -> None:
    """The generic form, so a future bucket is covered without a new row.

    `gate_decision_for_result` scores `result.changes` and nothing else, so a
    record the audit calls `gating` under a severity configuration must be a
    finding in that list. Stated over every bucket the table above knows,
    against the gate function's own input rather than against the table.
    """
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    strict = SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.ERROR,
        addition=SeverityLevel.ERROR,
    )
    for bucket, _, _ in _BUCKET_SCORED:
        result, change, verdict_scored = _result_with_one_breaking_finding_in(bucket)
        ledger = finalize_ledger(
            DispositionLedger(), result, verdict_scored=verdict_scored
        )
        gated = ledger.with_gate(result, strict)
        record = gated.record_for(change)
        if record.disposition is Disposition.GATING:
            assert any(c is change for c in result.changes), (
                f"{bucket}: the audit reports this finding as gating under a "
                "severity configuration, but the severity gate scores "
                "`result.changes` and this finding is not in it"
            )


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
        name for name, _, _ in _BUCKET_SCORED
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

    # Through the real promoter, not a hand-set attribute: what makes this a
    # promotion (rather than an ordinary finding that happens to read as
    # evaluated) is the reason code that function stamps, and a test that set
    # the relevance by hand would pass against the over-broad condition this
    # replaced.
    stamp_explicit_scope_contract_evaluation(promoted)

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
    stamp_explicit_scope_contract_evaluation(promoted)

    close_consumer_scope(ledger, result, gating=[], also_detected=[])
    record = ledger.record_for(promoted)
    assert record.disposition is Disposition.NON_GATING
    assert record.gate_excluded is True
    assert ledger.effective_total == 0


# ---------------------------------------------------------------------------
# The structural backstop: a pre-gate `non_gating` cannot forget its marker
# ---------------------------------------------------------------------------
#
# Three review rounds each found one more call site that produced a
# `non_gating` label before the gate ever ran and left `gate_excluded` unset,
# letting a severity configuration promote the finding back into a gate it was
# never scored by. Fixing them one at a time is what produced three rounds; the
# rule now lives in `DispositionLedger.record`, which derives the marker rather
# than trusting each caller, and these tests are what keep it underivable-by-
# accident: they inspect the real call sites and the real step metadata, not a
# hand-listed set of fixtures.


def _record_call_sites():
    """Every ``…record(<disposition>, …)`` call under ``abicheck/``.

    Yields ``(path, lineno, source)`` for each call to a method named
    ``record`` — the ledger's own recording entry point. An AST walk rather
    than a text scan, so a call split across lines is one site and a mention
    in a comment or docstring is none.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "abicheck"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
            ):
                yield path, node


def test_only_a_gate_resolved_call_may_declare_from_gate() -> None:
    """`from_gate=True` is the one way to opt a `non_gating` record out of
    the marker, so it may only be used where the disposition really did come
    from the gate — i.e. where `_kept_disposition` produced it.

    This is the mechanical half. Without it, the derivation in `record` is a
    convention a future call site can defeat by passing `from_gate=True` for
    a label the gate never produced, which is the same bug in a new spelling.
    """
    import ast

    offenders = []
    for path, call in _record_call_sites():
        declares = any(
            kw.arg == "from_gate"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in call.keywords
        )
        if not declares:
            continue
        rendered = ast.unparse(call)
        if "_kept_disposition" not in rendered:
            offenders.append(f"{path.name}:{call.lineno}: {rendered.splitlines()[0]}")
    assert not offenders, (
        "these call sites claim their disposition came from the gate without "
        "computing it with `_kept_disposition`:\n" + "\n".join(offenders)
    )


def test_no_call_site_passes_a_literal_false_gate_exclusion() -> None:
    """The other way to defeat the derivation: state the old default aloud.

    `gate_excluded=False` on an explicitly-labelled `non_gating` record is
    exactly the bug the last three rounds fixed, spelled as an assertion. The
    marker is derived; a caller that needs the record open to severity says
    `from_gate=True`, which the test above then holds to its word.
    """
    import ast

    offenders = [
        f"{path.name}:{call.lineno}"
        for path, call in _record_call_sites()
        for kw in call.keywords
        if kw.arg == "gate_excluded"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is False
    ]
    assert not offenders, (
        "pass `from_gate=True` (and mean it) rather than re-stating the "
        f"pre-derivation default at: {offenders}"
    )


def _pipeline_context(ledger: DispositionLedger):
    """A real `PipelineContext` carrying *ledger*, with empty operands."""
    from abicheck.post_processing import PipelineContext

    ctx = PipelineContext(
        old=AbiSnapshot(library="libmatrix", version="1.0"),
        new=AbiSnapshot(library="libmatrix", version="2.0"),
    )
    ctx.disposition_ledger = ledger
    return ctx


def test_every_compatibility_dropping_step_records_a_gate_excluded_finding() -> None:
    """The behavioural half, over the real pipeline rather than a fixture list.

    Any step declaring `dropped_finding_disposition = NON_GATING` drops a
    finding as compatible noise — `checker.compare` never scores it, so no
    severity setting may put it back. Enumerated from `DEFAULT_PIPELINE`
    itself, so a step added later is covered without editing this test.
    """
    from abicheck.post_processing import (
        _DEFAULT_DROPPED_DISPOSITION,
        DEFAULT_PIPELINE,
        _record_dropped_duplicates,
    )

    declaring = [
        step
        for step in DEFAULT_PIPELINE.steps
        if getattr(step, "dropped_finding_disposition", _DEFAULT_DROPPED_DISPOSITION)
        is Disposition.NON_GATING
    ]
    assert declaring, "the pipeline must still contain such a step"
    for step in declaring:
        ledger = DispositionLedger()
        ctx = _pipeline_context(ledger)
        dropped = _fixture_change(Disposition.GATING, 0)
        _record_dropped_duplicates(
            [dropped], [], 0, ctx, step.name, Disposition.NON_GATING
        )
        record = ledger.record_for(dropped)
        assert record is not None, f"{step.name} dropped a finding unrecorded"
        assert record.disposition is Disposition.NON_GATING
        assert record.gate_excluded is True, (
            f"{step.name}'s compatibility drop can be promoted back into the "
            "gate by a severity configuration"
        )


def test_a_dropped_compatibility_finding_survives_a_strict_severity_config() -> None:
    """…and the same statement end to end, through `with_gate`."""
    from abicheck.policy.severity import SeverityConfig, SeverityLevel
    from abicheck.post_processing import (
        _DEFAULT_DROPPED_DISPOSITION,
        DEFAULT_PIPELINE,
        _record_dropped_duplicates,
    )

    result = _empty_result()
    ledger = DispositionLedger()
    ctx = _pipeline_context(ledger)
    for index, step in enumerate(DEFAULT_PIPELINE.steps):
        if (
            getattr(step, "dropped_finding_disposition", _DEFAULT_DROPPED_DISPOSITION)
            is not Disposition.NON_GATING
        ):
            continue
        dropped = _fixture_change(Disposition.GATING, index)
        _record_dropped_duplicates(
            [dropped], [], 0, ctx, step.name, Disposition.NON_GATING
        )

    strict = SeverityConfig(
        abi_breaking=SeverityLevel.ERROR,
        potential_breaking=SeverityLevel.ERROR,
        quality_issues=SeverityLevel.ERROR,
        addition=SeverityLevel.ERROR,
    )
    assert ledger.effective_total == 0
    assert ledger.with_gate(result, strict).effective_total == 0


def test_an_unstamped_out_of_surface_finding_is_not_a_promotion() -> None:
    """Round 12's regression from round 11's own fix, as a standing control.

    `contract_gating.is_evaluated` answers `True` for an *unstamped* finding
    by design — that is what keeps every run without `--contract` bit-for-bit
    unchanged — so "reads as evaluated now" cannot distinguish *became*
    evaluated from *always was*. Keying the refresh on it relabelled every
    ordinary public-header scope exclusion in a `--used-by` run as a contract
    promotion, corrupting both the disposition and the reason it names.

    The finding here is exactly that shape: recorded `out_of_contract` by the
    out-of-surface bucket, never stamped by anything, in a run with no
    `--contract` at all.
    """
    from abicheck.contract_gating import is_evaluated
    from abicheck.policy.disposition_close import finalize_ledger

    result, change, _ = _result_with_one_breaking_finding_in("out_of_surface_changes")
    assert is_evaluated(change), (
        "the precondition this test exists for: an unstamped finding reads "
        "as evaluated, which is why the old condition misfired"
    )
    ledger = finalize_ledger(DispositionLedger(), result)
    assert ledger.record_for(change).disposition is Disposition.OUT_OF_CONTRACT

    close_consumer_scope(ledger, result, gating=[], also_detected=[])
    record = ledger.record_for(change)
    assert record.disposition is Disposition.OUT_OF_CONTRACT, (
        "an out-of-surface exclusion is not a contract promotion"
    )
    assert record.application_point == "surface_scope"
    assert record.gate_excluded is False, (
        "a finding the gate never scored is not *excluded from* the gate by "
        "a consumer scope; its own disposition already says so"
    )


# ---------------------------------------------------------------------------
# Feature combinations: a later pass can change what an earlier one recorded
# ---------------------------------------------------------------------------


def test_a_redundant_finding_folded_into_changes_is_no_longer_legacy_only() -> None:
    """`legacy_gate_only` is where the record *came from*, not a standing
    claim about the severity gate's input.

    `scope.show_redundant` folds redundant findings into `result.changes`
    before a `--used-by` scoped gate selects them, so a record that was
    legacy-only when the ledger closed can be squarely inside the severity
    gate's final input by the time a report renders. Demoting it on the
    recorded flag alone reported `effective_total: 0` beside a scoped exit
    of 4.
    """
    from abicheck.checker_types import DiffResult
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
    derived = Change(
        kind=ChangeKind.FUNC_REMOVED, symbol="f", description="derived but relevant"
    )
    result.changes = []
    result.redundant_changes = [derived]
    result.redundant_count = 1
    ledger = finalize_ledger(DispositionLedger(), result, verdict_scored=[derived])
    assert ledger.record_for(derived).legacy_gate_only is True

    strict = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
    assert ledger.with_gate(result, strict).effective_total == 0, (
        "while it is outside `result.changes`, the severity gate does not score it"
    )

    # …and what `_finalize_compare_result` does under `show_redundant`.
    result.changes = [derived]
    assert ledger.with_gate(result, strict).effective_total == 1, (
        "once the finding is in the severity gate's own input, the audit "
        "must agree with the gate that scores it"
    )


def test_a_stale_pre_stamped_verdict_does_not_survive_a_contract_exclusion() -> None:
    """A suppressed, proven-out-of-contract finding must not reach
    `recommend_release` as a waived major break.

    The earlier fix declined to *set* a verdict class for a non-evaluated
    finding, which is only half the rule: a detector-produced or
    runtime-modulated finding is stamped with an `effective_verdict` before
    `ApplySuppression` runs, so the record arrives already carrying one and
    the guard was never reached. The stale stamp then routed the exclusion
    straight back into the release recommendation.
    """
    from abicheck.checker import Verdict
    from abicheck.contract_relevance_types import ContractRelevance
    from abicheck.policy.disposition_ledger import record_suppressed_change

    result = _empty_result()
    for relevance in (
        ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        ContractRelevance.UNKNOWN_UNPROVEN,
        ContractRelevance.UNKNOWN_UNRESOLVED,
    ):
        excluded = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="gone", description="excluded"
        )
        # Stamped before suppression ran, which is what put a class on the
        # record in the first place.
        excluded.effective_verdict = Verdict.BREAKING
        excluded.contract_relevance = relevance

        ledger = DispositionLedger()
        record_suppressed_change(
            ledger, excluded, rule=None, application_point="matrix_suppression"
        )
        assert ledger.record_for(excluded).verdict_class == Verdict.BREAKING.value, (
            "the precondition: the record really does arrive pre-stamped"
        )
        ledger.resolve_verdict_classes(result)
        assert ledger.record_for(excluded).verdict_class is None, relevance
        assert ledger.suppressed_gating_records() == (), (
            "a suppressed contract exclusion is not a waived major break"
        )


def test_an_evaluated_pre_stamped_verdict_is_left_alone() -> None:
    """The negative control for the clear above: an ordinary suppressed
    break that *was* evaluated must keep its class, or the conserved delta
    stops seeing real waived breaks at all."""
    from abicheck.checker import Verdict
    from abicheck.policy.disposition_ledger import record_suppressed_change

    result = _empty_result()
    waived = Change(kind=ChangeKind.FUNC_REMOVED, symbol="gone", description="waived")
    waived.effective_verdict = Verdict.BREAKING
    ledger = DispositionLedger()
    record_suppressed_change(
        ledger, waived, rule=None, application_point="matrix_suppression"
    )
    ledger.resolve_verdict_classes(result)
    assert ledger.record_for(waived).verdict_class == Verdict.BREAKING.value
    assert len(ledger.suppressed_gating_records()) == 1


def test_a_scoped_run_gates_on_the_scoped_set_not_on_result_changes() -> None:
    """The one place `gating` is correct for a finding outside
    `result.changes`, recorded so it is not later "fixed" into a bug.

    `test_a_severity_gating_record_is_in_the_severity_gates_own_input`
    asserts that a severity-gating record is in `result.changes` — true for
    an ordinary run, because `gate_decision_for_result` scores exactly that
    list. A `--used-by`/`--required-symbol` run has a *different* gate:
    `cli_helpers_compare._scoped_exit_code` computes the severity exit over
    the consumer's own relevant set, which legitimately contains findings
    that never reach `result.changes` (a synthesized missing entrypoint, a PE
    ordinal retarget, a promoted out-of-surface finding).

    Found by an exhaustive read-through of the module's state combinations
    rather than by a report: it is exactly the shape that reads as a bug
    against the unscoped invariant, so the distinction is stated here.
    """
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    result, change, _ = _result_with_one_breaking_finding_in("out_of_surface_changes")
    ledger = finalize_ledger(DispositionLedger(), result)
    assert ledger.record_for(change).disposition is Disposition.OUT_OF_CONTRACT

    # An explicit consumer contract promotes it and gates on it.
    stamp_explicit_scope_contract_evaluation(change)
    close_consumer_scope(ledger, result, gating=[change], also_detected=[change])

    strict = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
    for config in (None, strict):
        record = ledger.with_gate(result, config).record_for(change)
        assert record.disposition is Disposition.GATING, (
            "the scoped gate scores this finding, so the audit must count it "
            "even though `result.changes` does not contain it"
        )
    assert not any(c is change for c in result.changes), (
        "the precondition that makes this case distinct from the unscoped "
        "invariant next door"
    )
