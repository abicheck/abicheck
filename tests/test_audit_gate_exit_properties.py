# SPDX-License-Identifier: Apache-2.0
"""Orthogonality of the audit-gate axis against the other ``max``-folded
exit axes, as a generated property rather than a handful of fixed
combinations (``AGENTS.md``'s bug-class regression-testing rule).

The stated oracle is written out literally here -- ``max`` over the
generated contributions -- rather than derived from
``report/no_baseline._no_baseline_exit_axes``/``no_baseline_exit_code``,
which is the implementation under test. Modeled after
``tests/test_no_baseline_d3_properties.py``'s shape: a Hypothesis strategy
over the *shape* of the problem (every axis's set of legal contributions),
checked against three properties every orthogonal ``max``-folded axis in
this codebase must satisfy (``contract_coverage_exit.py``'s and
``depth_evidence_contract.py``'s own module docstrings state the same
three facts in prose; this is the audit-gate axis's own instance, made
executable):

1. **Never lowers.** Adding the audit-gate axis's own contribution to a
   fold can only raise the result, never lower it below what the other
   axes alone would already produce.
2. **Never fabricates a compatibility code.** When every contributing axis
   is a *non-compatibility* axis (contract coverage, evidence contract,
   analysis assurance, and the audit-gate axis itself -- never the
   compatibility gate, which this module does not model because an audit
   never computes one), the folded result is never ``2``/``4`` -- ADR-068
   D2's own invariant, restated over every combination the real axes can
   produce rather than only the fixtures the corpus happens to cover.
3. **Associative / order-independent.** Folding the axes in any order, or
   two at a time in any grouping, produces the same result -- ``max`` is
   commutative and associative, and a caller that folds the audit-gate
   axis in a different place than ``report/no_baseline.py`` does today
   must still agree.
"""

from __future__ import annotations

import itertools
from functools import reduce

from hypothesis import given, strategies as st

from abicheck.policy.audit_gate_exit import AUDIT_GATE_EXIT_CODE, fold_audit_gate_exit

#: Every legal contribution the audit-gate axis can produce, per its own
#: module contract: `0` (disabled, or enabled with no gating finding) or
#: exactly `AUDIT_GATE_EXIT_CODE`.
_AUDIT_GATE_CONTRIBUTIONS = st.sampled_from([0, AUDIT_GATE_EXIT_CODE])

#: The other orthogonal, `max`-folded, non-compatibility axes this codebase
#: already has (contract coverage: `0`/`1`; evidence contract: `0`/`7`;
#: analysis assurance: `0`/`1`; ADR-065 completeness: `0`/`1`) -- their own
#: legal value sets, per `contract_coverage_exit.py`/
#: `depth_evidence_contract.py`/`analysis_assurance.py`/
#: `scope_completeness.py`. None of these is `2`/`4` -- those two integers
#: are reserved for the compatibility family alone, which is exactly what
#: property 2 below checks holds even after folding every one of these in
#: alongside the audit-gate axis.
_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS = st.sampled_from([0, 1, 5, 7, 8])


@given(
    audit_gate=_AUDIT_GATE_CONTRIBUTIONS,
    others=st.lists(_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS, min_size=0, max_size=5),
)
def test_audit_gate_never_lowers_the_fold(
    audit_gate: int, others: list[int]
) -> None:
    without = max([0, *others])
    with_gate = reduce(fold_audit_gate_exit, others, audit_gate)
    # `reduce` above folds every "other" axis through the audit-gate axis's
    # own `fold_audit_gate_exit` (identical `max`), then the two totals are
    # compared -- folding audit_gate in can only ever raise `without`.
    assert with_gate >= without


@given(
    audit_gate=_AUDIT_GATE_CONTRIBUTIONS,
    others=st.lists(_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS, min_size=0, max_size=5),
)
def test_folded_result_never_fabricates_a_compatibility_code(
    audit_gate: int, others: list[int]
) -> None:
    """ADR-068 D2: with no compatibility axis contributing at all (an audit
    never computes one), the fold must never land on `2` or `4` -- those
    codes are reserved for a real source-break/ABI-break verdict."""
    folded = reduce(fold_audit_gate_exit, others, audit_gate)
    assert folded not in (2, 4)


@given(
    audit_gate=_AUDIT_GATE_CONTRIBUTIONS,
    a=_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS,
    b=_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS,
    c=_OTHER_NON_COMPATIBILITY_CONTRIBUTIONS,
)
def test_fold_is_order_and_grouping_independent(
    audit_gate: int, a: int, b: int, c: int
) -> None:
    """`max`-folding is commutative and associative -- every permutation of
    the four contributions, folded left to right, agrees, and it agrees
    with folding two subgroups and then combining those."""
    values = [audit_gate, a, b, c]
    results = {
        reduce(fold_audit_gate_exit, perm[1:], perm[0])
        for perm in itertools.permutations(values)
    }
    assert len(results) == 1
    only_result = next(iter(results))

    # Grouped: fold {audit_gate, a} and {b, c} separately, then combine.
    left_group = fold_audit_gate_exit(audit_gate, a)
    right_group = fold_audit_gate_exit(b, c)
    grouped = fold_audit_gate_exit(left_group, right_group)
    assert grouped == only_result == max(values)


def test_two_gating_findings_are_no_worse_than_one() -> None:
    """A monotonicity sanity check in the same spirit as the generated
    properties above: the axis is a floor, not a count -- two independent
    gating signals must not compound into a code beyond
    `AUDIT_GATE_EXIT_CODE`."""
    once = fold_audit_gate_exit(0, AUDIT_GATE_EXIT_CODE)
    twice = fold_audit_gate_exit(once, AUDIT_GATE_EXIT_CODE)
    assert once == twice == AUDIT_GATE_EXIT_CODE
