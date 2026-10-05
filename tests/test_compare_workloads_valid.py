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

"""The synthetic workloads are themselves a contract: a gate measured on an
unrealistic input measures the wrong path. Two such slips happened while
these were written -- a workload that produced one compatible finding
instead of exercising its detectors, and a workload whose ~1000-character
manglings c++filt declined, so the budget gate measured the demangler's
fallback. These invariants keep every workload honest at both gate sizes
and with a long tag (the shape the budget measurement uses).
"""

from __future__ import annotations

import pytest
from _compare_workloads import WORKLOADS

from abicheck.checker import Verdict, compare
from abicheck.demangle import demangle_batch

SIZES = (50, 200)
LONG_TAG = "t_contract_spawn_contract_{w}_{n}_"


@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_every_mangled_name_demangles(workload: str) -> None:
    for n in SIZES:
        old, new = WORKLOADS[workload](n, LONG_TAG.format(w=workload, n=n))
        mangled = [
            f.mangled
            for snap in (old, new)
            for f in snap.declarations.functions
            if f.mangled.startswith("_Z")
        ]
        demangled = demangle_batch(mangled)
        undemangled = [m for m in mangled if m not in demangled]
        assert not undemangled, (
            f"{workload}/n={n}: {len(undemangled)} invalid mangling(s), e.g. {undemangled[0][:120]}"
        )


@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_every_workload_exercises_detectors(workload: str) -> None:
    for n in SIZES:
        result = compare(*WORKLOADS[workload](n, f"ex_{workload}_{n}_"))
        assert result.changes, f"{workload}/n={n}: no findings"
        assert result.verdict is not Verdict.COMPATIBLE, (
            f"{workload}/n={n}: only compatible findings -- the workload does not reach the breaking detectors"
        )
