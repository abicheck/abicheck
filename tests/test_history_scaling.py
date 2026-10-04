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

"""Growth with accumulated history: ``build_longitudinal_history`` over K releases.

History is where data accumulates across runs (every release ever seen,
every removal, every deprecation window), so it is the place a per-release
step quietly starts re-walking everything before it. Each release in the
chain built here keeps a stable core, adds functions, retires ones added
three releases earlier and deprecates one -- so the ``ever_seen``/
``removed``/``deprecated`` state genuinely grows with K.

* ``test_history_call_counts_grow_linearly_in_releases`` (unit lane,
  deterministic): no first-party function's call count may grow faster than
  ``(K ratio)^1.5`` between K=5 and K=20 releases -- one pairwise compare
  per release is linear, so a step that rescans earlier releases shows up.
* ``test_history_time_scaling_in_releases`` (``slow``): the wall-clock
  exponent over K up to 50 releases stays well below quadratic.
"""

from __future__ import annotations

import time

import pytest
from _call_counts import profile_call_counts, superlinear_call_sites
from _perf_scaling import measure_scaling_exponent

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.history import HistoryEntry, build_longitudinal_history

CORE = 40


def _fn(name: str, deprecated: bool = False) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
        deprecated="superseded" if deprecated else None,
    )


def _chain(releases: int, tag: str) -> list[HistoryEntry]:
    entries = []
    for r in range(releases):
        funcs = [_fn(f"{tag}core_{i}", deprecated=(i == r % CORE)) for i in range(CORE)]
        # Additions from the last three releases survive; older ones are retired.
        funcs += [
            _fn(f"{tag}add_{a}_{j}")
            for a in range(max(0, r - 2), r + 1)
            for j in range(5)
        ]
        snap = AbiSnapshot(library="libhist.so", version=f"1.{r}.0", functions=funcs)
        entries.append(
            HistoryEntry(index=r, version=f"1.{r}.0", path=f"v{r}.json", snapshot=snap)
        )
    return entries


def test_chain_really_accumulates_history() -> None:
    result = build_longitudinal_history(_chain(8, "acc_"))
    assert len(result.pairwise) == 7
    kinds = {
        str(e.event.value if hasattr(e.event, "value") else e.event)
        for e in result.events
    }
    # Additions, retirements and deprecations all occur, so every piece of
    # cross-release state the scaling test leans on is exercised.
    assert {"introduced", "removed", "deprecated"} <= kinds, kinds


def test_history_call_counts_grow_linearly_in_releases() -> None:
    small_k, large_k = 5, 20
    small = profile_call_counts(
        lambda: build_longitudinal_history(_chain(small_k, "cs_"))
    )
    large = profile_call_counts(
        lambda: build_longitudinal_history(_chain(large_k, "cl_"))
    )
    offenders = superlinear_call_sites(small, large, large_k / small_k)
    assert not offenders, (
        "history cost grew faster than (releases)^1.5:\n  "
        + "\n  ".join(o.describe() for o in offenders[:10])
    )


@pytest.mark.slow
def test_history_time_scaling_in_releases() -> None:
    sizes = (12, 20, 33, 50)
    chains = {k: _chain(k, f"t{k}_") for k in sizes}

    def measure(k: int) -> float:
        start = time.perf_counter()
        build_longitudinal_history(chains[k])
        return time.perf_counter() - start

    exponent = measure_scaling_exponent(measure, sizes)
    assert exponent < 1.6, (
        f"history time scaling exponent {exponent:.2f} over {sizes} releases (regressing toward O(K^2))"
    )
