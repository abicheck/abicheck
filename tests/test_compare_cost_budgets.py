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

"""Ratchet budgets on what one ``compare()`` costs, beyond time.

Two figures per compare mode, pinned exactly in ``perf_call_budgets.json``:

* **repeat calls** of a reviewed list of expensive whole-snapshot functions
  (``scripts/audit_repeated_calls.py``'s ``BUDGETED_FUNCTIONS``) with the
  *same* arguments -- the "rebuilt the same graph twice" waste a call count
  cannot see;
* **child processes** started (today: one batched ``c++filt``), which must
  also not depend on input size -- a spawn per entity is the classic
  sequential-N+1 shape.

Like a bundle-size budget, the check is exact in both directions: a figure
above its budget is a regression to fix (or justify, by re-recording); a
figure *below* it fails too, so an improvement is locked in by lowering the
number rather than left as slack the next regression can spend. Re-record
with ``python scripts/audit_repeated_calls.py --write-budgets``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _compare_workloads import WORKLOADS  # noqa: E402
from audit_repeated_calls import (  # noqa: E402
    BUDGET_FILE,
    BUDGETED_FUNCTIONS,
    MODES,
    PER_DECL_FUNCTIONS,
    count_subprocess_spawns,
    measure_budgets,
)

from abicheck.checker import compare  # noqa: E402

_RECORDED = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))["budgets"]
_HINT = "re-record with `python scripts/audit_repeated_calls.py --write-budgets` and say why in the PR"


def test_budget_file_covers_every_mode_and_function() -> None:
    expected_keys = (
        {f"repeats:{n}" for n in BUDGETED_FUNCTIONS}
        | {"subprocess_spawns"}
        | {f"per_decl_x10:{n}" for n in PER_DECL_FUNCTIONS}
    )
    assert set(_RECORDED) == set(MODES)
    for mode, figures in _RECORDED.items():
        assert set(figures) == expected_keys, (
            f"{mode}: budget keys out of date -- {_HINT}"
        )


@pytest.mark.parametrize("mode", sorted(MODES))
def test_compare_costs_match_their_budget(mode: str) -> None:
    measured = measure_budgets(tag=f"t_{mode}", modes=(mode,))[mode]
    recorded = _RECORDED[mode]
    over = {
        k: (recorded.get(k), v) for k, v in measured.items() if v > recorded.get(k, 0)
    }
    under = {
        k: (recorded.get(k), v) for k, v in measured.items() if v < recorded.get(k, 0)
    }
    assert not over, (
        f"{mode}: cost above budget (budget, measured): {over} -- fix the regression, or {_HINT}"
    )
    assert not under, (
        f"{mode}: cost below budget (budget, measured): {under} -- an improvement; lock it in: {_HINT}"
    )


@pytest.mark.parametrize("mode", sorted(MODES))
@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_spawn_count_ignores_a_demangler_latch_left_by_earlier_callers(
    workload: str, mode: str
) -> None:
    """A spawn count must not depend on what ran earlier in the process.

    `demangle` latches "c++filt is missing" process-wide. A test that fakes a
    missing binary and leaves the latch set used to make the budget test
    measure 0 spawns on whichever xdist worker ran after it. Oracle: the
    count with the latch forced on equals the count from a clean state, for
    every workload and compare mode.
    """
    import abicheck.demangle as demangle

    def count(prefix: str) -> int:
        old, new = WORKLOADS[workload](50, f"{prefix}_{mode}_{workload}_")
        return count_subprocess_spawns(lambda: compare(old, new, **MODES[mode]))

    demangle._reset_demangle_batch_cache()
    clean = count("latch_clean")
    demangle._cppfilt_binary_confirmed_missing = True
    try:
        latched = count("latch_set")
    finally:
        demangle._reset_demangle_batch_cache()
    assert latched == clean


@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_subprocess_spawns_do_not_grow_with_input(workload: str) -> None:
    def spawns(n: int) -> int:
        old, new = WORKLOADS[workload](n, f"sp_{workload}_{n}_")
        return count_subprocess_spawns(lambda: compare(old, new))

    small, large = spawns(40), spawns(320)
    assert small == large, (
        f"{workload}: {small} child processes at n=40 but {large} at n=320 -- a spawn per entity"
    )


def test_budget_measurement_sees_an_injected_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity: build each side's surface graph twice and the measured
    repeat figure for ``build_surface_graph`` must rise above zero."""
    import abicheck.pattern_verdicts as pv

    real = pv.build_surface_graph

    def twice(snap, **kw):
        real(snap, **kw)
        return real(snap, **kw)

    monkeypatch.setattr(pv, "build_surface_graph", twice)
    measured = measure_budgets(tag="inj", modes=("patterns_and_metrics",))[
        "patterns_and_metrics"
    ]
    assert (
        measured["repeats:build_surface_graph"]
        > _RECORDED["patterns_and_metrics"]["repeats:build_surface_graph"]
    )


def test_spawn_counter_counts_real_processes() -> None:
    import subprocess

    assert (
        count_subprocess_spawns(
            lambda: [
                subprocess.run([sys.executable, "-c", "pass"], check=True)
                for _ in range(3)
            ]
        )
        == 3
    )
