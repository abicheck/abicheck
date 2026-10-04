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

"""Wall-clock scaling exponents for the ``compare()`` workload shapes.

``test_performance.py`` already guards two shapes in time (add/remove and
type churn). This extends the same least-squares exponent guard
(``_perf_scaling.py``) to the other shapes in ``_compare_workloads.py``,
each of which reaches a different detector family. Its deterministic
sibling, ``test_compare_call_complexity.py``, gates call-count growth for
the same shapes in the unit lane; this file catches what call counts
cannot -- a site whose call count is linear but whose *per-call* cost grows
with input size (e.g. a helper that copies or sorts a whole collection on
every call).

Measured exponents on an idle machine (2026-10): 0.8-1.1 for every shape.
The 1.6 ceiling sits between that and quadratic, and
``test_sizes_catch_a_synthetic_quadratic_regression`` pins that the size
sweep (fixed per-call overhead included) still fits a real quadratic above
it.
"""

from __future__ import annotations

import itertools
import time

import pytest
from _compare_workloads import WORKLOADS
from _perf_scaling import measure_scaling_exponent

from abicheck.checker import compare

pytestmark = pytest.mark.slow

# Irregular log-spacing (see _perf_scaling.py); covered by type_churn and
# add_remove in test_performance.py already, so they are not repeated here.
SIZES = (600, 850, 1200, 1600)
CEILING = 1.6
SHAPES = sorted(set(WORKLOADS) - {"type_churn", "add_remove"})

_tags = itertools.count()


@pytest.mark.parametrize("workload", SHAPES)
def test_compare_scaling_exponent(workload: str) -> None:
    pairs = {n: WORKLOADS[workload](n, f"ts{next(_tags)}_") for n in SIZES}
    compare(
        *WORKLOADS[workload](50, f"warm{next(_tags)}_")
    )  # first-call imports out of the timing

    def measure(n: int) -> float:
        old, new = pairs[n]
        start = time.perf_counter()
        result = compare(old, new)
        elapsed = time.perf_counter() - start
        assert result.changes, (
            f"{workload}: n={n} produced no findings -- the workload stopped exercising detectors"
        )
        return elapsed

    exponent = measure_scaling_exponent(measure, SIZES)
    assert exponent < CEILING, (
        f"{workload}: compare() time scaling exponent {exponent:.2f} >= {CEILING} (regressing toward O(n^2))"
    )


def test_sizes_catch_a_synthetic_quadratic_regression() -> None:
    # Fixed overhead equal to ~5% of the smallest size's quadratic cost, as
    # test_performance.py models it.
    a = 1.0
    c = 0.05 * a * SIZES[0] ** 2
    exponent = measure_scaling_exponent(lambda n: c + a * n * n, SIZES, repeats=1)
    assert exponent >= CEILING, (
        f"size sweep {SIZES} fits C + a*n^2 at {exponent:.3f}, under the {CEILING} ceiling"
    )
