# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""Split the unit suite across CI jobs (``pytest --shard=K/N``).

The canonical coverage-collecting unit lane ran as one ~34-minute job on a
4-core runner, which made it the critical path of every CI run. Running it
as N concurrent jobs and combining their coverage data afterwards keeps the
exact same test selection and the same 95% floor (enforced once, over the
combined data, by ``ci.yml``'s ``unit-tests-coverage`` job) while dividing
the wall time.

Assignment is by **test file**, never by individual test: module- and
class-scoped fixtures stay within one job, so no fixture is built twice.
Files are placed by the longest-processing-time rule over their collected
test counts -- heaviest first, each onto the currently lightest shard, ties
to the lowest index -- sorted by ``(-weight, path)`` beforehand so the result
is a pure function of the collected set and never of collection order.
Every xdist worker computes the same answer independently, which is what
lets this run under ``-n auto`` with no coordination.

Test count is a proxy for cost, not a measurement; it needs no committed
timing file that would go stale. Its known imbalance bound is stated and
tested: the heaviest and lightest shard differ by at most the largest single
file's weight.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def parse_shard(spec: str) -> tuple[int, int]:
    """Parse ``"K/N"`` (1-based) into ``(K, N)``; raise ``ValueError`` if invalid."""
    index_text, sep, total_text = spec.partition("/")
    if not sep:
        raise ValueError(f"--shard must be K/N, got {spec!r}")
    index, total = int(index_text), int(total_text)
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"--shard {spec!r}: need 1 <= K <= N")
    return index, total


def file_of(nodeid: str) -> str:
    """The test file a pytest node id belongs to."""
    return nodeid.split("::", 1)[0]


def assign_files(weights: Mapping[str, int], total: int) -> dict[str, int]:
    """Map each file to a 1-based shard index (longest-processing-time rule)."""
    if total < 1:
        raise ValueError("total must be >= 1")
    loads = [0] * total
    assignment: dict[str, int] = {}
    for path, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        target = min(range(total), key=lambda i: (loads[i], i))
        loads[target] += weight
        assignment[path] = target + 1
    return assignment


def select(nodeids: Iterable[str], index: int, total: int) -> set[str]:
    """The node ids shard ``index`` of ``total`` runs."""
    ids = list(nodeids)
    weights: dict[str, int] = {}
    for nodeid in ids:
        path = file_of(nodeid)
        weights[path] = weights.get(path, 0) + 1
    assignment = assign_files(weights, total)
    return {nodeid for nodeid in ids if assignment[file_of(nodeid)] == index}
