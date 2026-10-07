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


"""``scripts/complexity_bench.py``: the empirical exponent gate.

The fit is checked against generated power laws with known exponents (an
oracle independent of the code under test); the gate's ``--strict`` decision
and JSON receipt are checked through the script's own CLI on its cheapest
real case.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import complexity_bench as cb  # noqa: E402


@pytest.mark.parametrize("k", [0.0, 0.5, 1.0, 1.3, 2.0, 3.0])
@pytest.mark.parametrize("scale", [1e-4, 0.02, 3.0])
def test_fit_recovers_a_generated_power_law(k: float, scale: float) -> None:
    points = {n: scale * n**k for n in (50, 100, 200, 400, 800)}
    assert cb.fit_exponent(points) == pytest.approx(k, abs=1e-9)


def test_fit_needs_two_sizes() -> None:
    with pytest.raises(ValueError):
        cb.fit_exponent({10: 1.0})


def test_every_case_has_a_preparer_doubling_sizes_and_a_subquadratic_ceiling() -> None:
    assert set(cb.CASES) == set(cb.PREPARERS)
    for case in cb.CASES.values():
        assert len(case.sizes) >= 3, case.name
        assert all(b == 2 * a for a, b in zip(case.sizes, case.sizes[1:])), case.name
        assert 1.0 < case.max_exponent < 2.0, case.name


@pytest.mark.slow
def test_cli_reports_json_and_strict_fails_on_an_exceeded_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "bench.json"
    assert (
        cb._main(["--case", "policy_classification", "--strict", "--json", str(out)])
        == 0
    )
    (row,) = json.loads(out.read_text(encoding="utf-8"))
    assert row["case"] == "policy_classification" and row["ok"] is True
    assert len(row["seconds"]) == len(cb.CASES["policy_classification"].sizes)

    tight = dataclasses.replace(cb.CASES["policy_classification"], max_exponent=0.05)
    monkeypatch.setitem(cb.CASES, "policy_classification", tight)
    assert cb._main(["--case", "policy_classification"]) == 0  # report-only
    assert cb._main(["--case", "policy_classification", "--strict"]) == 1


def test_the_bench_sees_an_injected_quadratic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity: a case whose body is genuinely quadratic fits k ~ 2 and
    fails its ceiling, measured in-process the same way a worker does."""

    def quadratic(n: int, tag: str):
        items = list(range(n))
        return lambda: [x for x in items if x in items]

    # Sizes large enough that per-size CPU time dwarfs scheduler/timer
    # noise on fast runners: at (500, 1000, 2000) macOS arm64 measured
    # k = 1.50 under xdist contention (main run 37645089922).
    case = cb.Case("quad", "injected", (1000, 2000, 4000), 1.5)
    monkeypatch.setitem(cb.PREPARERS, "quad", quadratic)
    k = cb.fit_exponent(cb.measure_in_process(case))
    assert k > 1.6
