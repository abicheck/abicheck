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

"""Deterministic complexity gate for ``compare()``: call counts, not timers.

For every synthetic workload in ``_compare_workloads.py`` and every
``compare()`` mode that switches on a separate pass (default, ``--contract``
evaluation, pattern verdicts + surface metrics), the same workload is run
at two sizes and every first-party function's call count is compared
(``_call_counts.py``). A function whose count grows faster than
``size_ratio**1.5`` -- e.g. a per-entity linear scan, quadratic overall --
fails the test, and the failure names the definition.

This runs in the ordinary unit lane: counts are exact, so there is no
timing noise to budget for. The wall-clock counterpart for the same shapes
is ``test_compare_scaling_shapes.py`` (``slow``).

Each run salts its symbol names with a fresh tag: compare-path helpers keep
process-wide caches, and a smaller run must not pre-warm a larger one (or
be pre-warmed by an earlier test) -- see ``_compare_workloads.py``.

The gate is proven non-vacuous twice over: the predicate itself is pinned
on synthetic count tables (linear and ``n log n`` pass, quadratic and
appear-only-at-scale fail), and an end-to-end test injects a real
quadratic call pattern into ``diff_symbols`` and asserts the gate names it.
"""

from __future__ import annotations

import itertools
import math

import pytest
from _call_counts import profile_call_counts, superlinear_call_sites
from _compare_workloads import WORKLOADS

from abicheck import diff_symbols
from abicheck.checker import compare
from abicheck.model import surface_facts

SMALL, LARGE = 50, 200

MODES: dict[str, dict[str, object]] = {
    "default": {},
    "contract": {"contract_evaluation": True, "contract_mode": "public"},
    "patterns_and_metrics": {"pattern_verdicts": True, "surface_metrics": True},
}

_tags = itertools.count()


def _counts(workload: str, n: int, mode: dict[str, object]) -> dict[str, int]:
    old, new = WORKLOADS[workload](n, f"cc{next(_tags)}_")
    return profile_call_counts(lambda: compare(old, new, **mode))


@pytest.mark.parametrize("mode", sorted(MODES))
@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_compare_call_counts_grow_subquadratically(workload: str, mode: str) -> None:
    small = _counts(workload, SMALL, MODES[mode])
    large = _counts(workload, LARGE, MODES[mode])
    # Non-vacuity: the run must actually reach the detectors (thousands of
    # first-party calls), not fail fast on an error path whose counts are
    # trivially flat.
    assert sum(large.values()) > 5 * LARGE, (
        f"{workload}/{mode}: compare() made almost no first-party calls"
    )
    offenders = superlinear_call_sites(small, large, LARGE / SMALL)
    assert not offenders, (
        f"{workload}/{mode}: call counts grew faster than (size ratio)^1.5 "
        f"between n={SMALL} and n={LARGE} -- likely an O(n^2) loop:\n  "
        + "\n  ".join(o.describe() for o in offenders[:10])
    )


class TestSuperlinearPredicate:
    """The predicate's contract, on count tables with a known growth law."""

    @staticmethod
    def _table(law, n: int) -> dict[str, int]:
        return {"site": max(1, round(law(n)))}

    @pytest.mark.parametrize("small_n", [50, 100, 250, 1000])
    @pytest.mark.parametrize("ratio", [2, 4, 8])
    def test_linear_and_nlogn_growth_pass(self, small_n: int, ratio: int) -> None:
        for law in (lambda n: 3 * n, lambda n: n * math.log2(n), lambda n: 7 * n + 40):
            small, large = self._table(law, small_n), self._table(law, small_n * ratio)
            assert superlinear_call_sites(small, large, ratio) == []

    @pytest.mark.parametrize("small_n", [50, 100, 250, 1000])
    @pytest.mark.parametrize("ratio", [4, 8])
    def test_quadratic_growth_is_flagged(self, small_n: int, ratio: int) -> None:
        for law in (
            lambda n: n * n,
            lambda n: n * (n - 1) / 2,
            lambda n: (n / 20) ** 2 * 3 + 5,
        ):
            small, large = self._table(law, small_n), self._table(law, small_n * ratio)
            if large["site"] < 50:
                continue  # below min_large_calls: too rare to judge shape
            (hit,) = superlinear_call_sites(small, large, ratio)
            assert hit.site == "site"

    def test_site_absent_at_small_size_is_judged_against_zero(self) -> None:
        (hit,) = superlinear_call_sites({}, {"new_site": 500}, 4)
        assert hit.small_calls == 0

    def test_rare_sites_are_ignored(self) -> None:
        assert superlinear_call_sites({"s": 1}, {"s": 49}, 4) == []

    def test_ratio_must_exceed_one(self) -> None:
        with pytest.raises(ValueError):
            superlinear_call_sites({}, {}, 1)


def test_gate_names_an_injected_quadratic_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: make every signature check consult every old function's
    surface fact (a per-entity linear scan, the textbook regression), and the
    gate must name ``in_source_declaration_index`` as the offending site."""
    real = diff_symbols._check_function_signature
    population: list = []

    def regressed(mangled, f_old, f_new, **kw):
        for f in population:
            surface_facts.in_source_declaration_index(f)
        return real(mangled, f_old, f_new, **kw)

    monkeypatch.setattr(diff_symbols, "_check_function_signature", regressed)

    def counts(n: int) -> dict[str, int]:
        old, new = WORKLOADS["signature_churn"](n, f"inj{next(_tags)}_")
        population[:] = old.declarations.functions
        return profile_call_counts(lambda: compare(old, new))

    offenders = superlinear_call_sites(counts(SMALL), counts(LARGE), LARGE / SMALL)
    assert any("in_source_declaration_index" in o.site for o in offenders), [
        o.describe() for o in offenders
    ]
