# SPDX-License-Identifier: Apache-2.0
"""Orthogonality of the audit-gate axis against the other ``max``-folded
exit axes, as a generated property rather than a handful of fixed
combinations (``AGENTS.md``'s bug-class regression-testing rule).

The stated oracle is written out literally here -- ``max`` over the
generated contributions -- and the implementation under test is the real
fold, ``report/no_baseline.no_baseline_exit_code``, fed the generated
per-axis contributions in place of ``_no_baseline_exit_axes``. Modeled after
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
3. **Order-independent.** Reporting the axes in any order produces the
   same result -- ``max`` is
   commutative and associative, so the order the axes are reported in
   must not matter.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given, strategies as st

from abicheck.model import AbiSnapshot
from abicheck.policy.audit_gate_exit import AUDIT_GATE_EXIT_CODE
from abicheck.report import no_baseline as nb

#: Every legal contribution the audit-gate axis can produce, per its own
#: module contract: `0` (disabled, or enabled with no gating finding) or
#: exactly `AUDIT_GATE_EXIT_CODE`.
_AUDIT_GATE_CONTRIBUTIONS = st.sampled_from([0, AUDIT_GATE_EXIT_CODE])

#: The other orthogonal, `max`-folded, non-compatibility axes an audit folds
#: (contract coverage: `0`/`1`; evidence contract: `0`/`7`; analysis
#: assurance: `0`/`1`; completeness: `0`/`1`) -- their legal value sets, per
#: `contract_coverage_exit.py`/`depth_evidence_contract.py`/
#: `analysis_assurance.py`/`scope_completeness.py`. None is `2`/`4`: those
#: two integers belong to the compatibility family alone.
_OTHER_AXES = (
    "contract_coverage",
    "analysis_assurance",
    "evidence_contract",
    "incomplete_scope",
    "no_comparison_completed",
)
_OTHER_CONTRIBUTION = st.sampled_from([0, 1, 5, 7, 8])


@pytest.fixture(scope="module")
def audit_result():
    from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

    return run_no_baseline_compare(AbiSnapshot(library="libfoo.so", version="1.0"))


def _exit_code(result, axes: dict[str, int]) -> int:
    """`no_baseline_exit_code` -- the real fold -- over *axes* as the per-axis
    contributions `_no_baseline_exit_axes` reported."""
    real = nb._no_baseline_exit_axes
    nb._no_baseline_exit_axes = lambda *_a, **_k: dict(axes)
    try:
        return nb.no_baseline_exit_code(result)
    finally:
        nb._no_baseline_exit_axes = real


def _axes(audit_gate: int, others: list[int]) -> dict[str, int]:
    return {"audit_gate": audit_gate, **dict(zip(_OTHER_AXES, others, strict=True))}


_OTHERS = st.lists(_OTHER_CONTRIBUTION, min_size=5, max_size=5)


@given(audit_gate=_AUDIT_GATE_CONTRIBUTIONS, others=_OTHERS)
def test_audit_gate_never_lowers_the_fold(audit_result, audit_gate, others) -> None:
    without = _exit_code(audit_result, _axes(0, others))
    assert _exit_code(audit_result, _axes(audit_gate, others)) >= without
    assert _exit_code(audit_result, _axes(audit_gate, others)) >= audit_gate


@given(audit_gate=_AUDIT_GATE_CONTRIBUTIONS, others=_OTHERS)
def test_folded_result_never_fabricates_a_compatibility_code(
    audit_result, audit_gate, others
) -> None:
    """ADR-068 D2: with no compatibility axis contributing at all (an audit
    never computes one), the fold must never land on `2` or `4` -- those
    codes are reserved for a real source-break/ABI-break verdict."""
    assert _exit_code(audit_result, _axes(audit_gate, others)) not in (2, 4)


@given(
    audit_gate=_AUDIT_GATE_CONTRIBUTIONS,
    others=_OTHERS,
    seed=st.integers(min_value=0, max_value=2**16),
)
def test_fold_is_independent_of_axis_order(
    audit_result, audit_gate, others, seed
) -> None:
    """The literal oracle: the exit code is the largest contribution,
    whatever order the axes are reported in."""
    axes = list(_axes(audit_gate, others).items())
    random.Random(seed).shuffle(axes)
    assert _exit_code(audit_result, dict(axes)) == max([audit_gate, *others])


def test_two_gating_findings_are_no_worse_than_one(audit_result) -> None:
    """The axis is a floor, not a count: a gating audit with every other axis
    clean exits exactly `AUDIT_GATE_EXIT_CODE`."""
    assert _exit_code(audit_result, _axes(AUDIT_GATE_EXIT_CODE, [0] * 5)) == (
        AUDIT_GATE_EXIT_CODE
    )
