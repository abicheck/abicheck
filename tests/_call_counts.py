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

"""Deterministic call-count complexity measurement.

Wall-clock scaling exponents (``_perf_scaling.py``) are the right tool when
the claim is about time, but they need several sizes, repeats and the
``slow`` lane to stay quiet on a shared runner. Most real O(n^2)
regressions have a cheaper, exact symptom: some function's *call count*
grows quadratically -- a nested ``in some_list`` scan, a per-entity rebuild
of an index that should be built once, a detector re-walking every other
entity for each entity. Call counts are deterministic, so they can gate in
the ordinary unit lane with no tolerance for scheduler noise.

:func:`profile_call_counts` runs a thunk under :mod:`cProfile` and returns
how many times every first-party (``abicheck/``) function was entered.
:func:`superlinear_call_sites` compares the counts of the same workload at
two sizes and names every function whose count grew faster than the size
ratio allows. Sites are keyed by ``path:line(name)``, so the report points
at the exact definition, and a site that only appears at the larger size
is judged against zero rather than ignored.

The oracle is deliberately independent of what is being measured: it is
the size ratio of the *input*, not a baseline recorded from the code under
test, so there is nothing to re-record when a detector is added and no way
for an accepted regression to become the new normal.
"""

from __future__ import annotations

import cProfile
import pstats
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "abicheck"


def profile_call_counts(thunk: Callable[[], object]) -> dict[str, int]:
    """Return ``{"<path>:<line>(<func>)": call_count}`` for ``abicheck/`` code
    entered while running *thunk*.

    The count is cProfile's *total* call count (recursive entries included),
    which is the quantity that blows up when a function is invoked once per
    pair of entities.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        thunk()
    finally:
        profiler.disable()
    counts: dict[str, int] = {}
    root = str(_PACKAGE_ROOT)
    for (filename, line, func), stat in pstats.Stats(profiler).stats.items():
        if not filename.startswith(root):
            continue
        rel = Path(filename).relative_to(_PACKAGE_ROOT.parent).as_posix()
        counts[f"{rel}:{line}({func})"] = stat[1]
    return counts


@dataclass(frozen=True)
class SuperlinearSite:
    site: str
    small_calls: int
    large_calls: int
    allowed_calls: float

    def describe(self) -> str:
        return f"{self.site}: {self.small_calls} -> {self.large_calls} calls (allowed <= {self.allowed_calls:.0f})"


def superlinear_call_sites(
    small: dict[str, int],
    large: dict[str, int],
    size_ratio: float,
    *,
    exponent_ceiling: float = 1.5,
    min_large_calls: int = 50,
    additive_slack: int = 20,
) -> list[SuperlinearSite]:
    """Name every site whose count grew faster than ``size_ratio**exponent_ceiling``.

    With the default ceiling of 1.5 and a 4x size step, a linear site grows
    4x, an ``n log n`` site about 5x, and a quadratic one 16x against an
    allowance of 8x -- a factor of two of margin on either side, which is
    what lets the check stay exact without being brittle. ``min_large_calls``
    ignores sites too rare at the larger size to say anything about shape;
    ``additive_slack`` absorbs a constant number of extra calls (one more
    warm-up, one more branch) at sites whose small-size count is tiny.
    """
    if size_ratio <= 1:
        raise ValueError(
            "size_ratio must be > 1: compare a smaller run against a larger one"
        )
    growth = size_ratio**exponent_ceiling
    out = []
    for site, large_calls in large.items():
        if large_calls < min_large_calls:
            continue
        small_calls = small.get(site, 0)
        allowed = growth * small_calls + additive_slack
        if large_calls > allowed:
            out.append(SuperlinearSite(site, small_calls, large_calls, allowed))
    return sorted(out, key=lambda s: s.large_calls, reverse=True)
