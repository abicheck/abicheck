# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""Policy-level invariants: verdict aggregation is monotonic and severity-stable.

``compute_verdict`` is "worst contributed category wins". Two properties must
hold for *any* set of changes, and a regression in either would silently soften
real breaks:

* **Monotonicity** — adding a change can only keep or raise the overall verdict
  severity, never lower it.
* **Severity precedence** — a change-set containing any BREAKING kind is always
  BREAKING regardless of what else it contains; likewise API_BREAK dominates
  risk/compatible. (Guards against a re-ordering of the precedence ladder.)

The fast tests are deterministic; the Hypothesis-randomized generalization is
marked ``slow`` so it stays out of the default lane.
"""
from __future__ import annotations

import random

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    Verdict,
    compute_verdict,
)
from abicheck.checker_types import Change

# Severity ladder (low → high). compute_verdict must never move a set down it.
_SEVERITY = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 1,
    Verdict.COMPATIBLE_WITH_RISK: 2,
    Verdict.API_BREAK: 3,
    Verdict.BREAKING: 4,
}


def _change(kind: ChangeKind) -> Change:
    return Change(kind=kind, symbol=f"sym_{kind.value}", description="x")


def _verdict(kinds: list[ChangeKind]) -> Verdict:
    return compute_verdict([_change(k) for k in kinds])


def test_adding_breaking_kind_never_lowers_verdict():
    base = [next(iter(COMPATIBLE_KINDS))]
    before = _verdict(base)
    after = _verdict(base + [next(iter(BREAKING_KINDS))])
    assert _SEVERITY[after] >= _SEVERITY[before]
    assert after == Verdict.BREAKING


def test_breaking_dominates_any_mix():
    mix = [
        next(iter(BREAKING_KINDS)),
        next(iter(API_BREAK_KINDS)),
        next(iter(COMPATIBLE_KINDS)),
    ]
    if RISK_KINDS:
        mix.append(next(iter(RISK_KINDS)))
    assert _verdict(mix) == Verdict.BREAKING


def test_api_break_dominates_risk_and_compatible():
    mix = [next(iter(API_BREAK_KINDS)), next(iter(COMPATIBLE_KINDS))]
    if RISK_KINDS:
        mix.append(next(iter(RISK_KINDS)))
    assert _verdict(mix) == Verdict.API_BREAK


def test_empty_is_no_change():
    assert compute_verdict([]) == Verdict.NO_CHANGE


def test_monotonic_under_supersets_deterministic():
    """A superset of changes is never less severe than its subset."""
    rng = random.Random(1234)
    all_kinds = list(ChangeKind)
    for _ in range(200):
        n = rng.randint(0, 6)
        subset = rng.sample(all_kinds, n)
        extra = rng.choice(all_kinds)
        v_sub = _verdict(subset)
        v_super = _verdict(subset + [extra])
        assert _SEVERITY[v_super] >= _SEVERITY[v_sub], (
            f"adding {extra.value} lowered verdict {v_sub} → {v_super} "
            f"(subset={[k.value for k in subset]})"
        )
