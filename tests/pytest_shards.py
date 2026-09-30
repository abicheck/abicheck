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
Files are placed by the longest-processing-time rule -- heaviest first, each
onto the currently lightest shard, ties to the lowest index -- sorted by
``(-weight, path)`` beforehand so the result is a pure function of the
collected set and never of collection order. Every xdist worker computes the
same answer independently, which is what lets this run under ``-n auto``
with no coordination.

A file's weight is its measured seconds from ``shard_weights.json`` when
recorded there, and otherwise its collected test count times that file's
median per-test cost. Test count alone was measured to be a poor proxy (one
shard took 7.5 minutes and another 11.5 on the same suite: files of
subprocess-driven Action tests cost ~2s per test, most files ~20ms). The
weights only steer *balance*: a stale or missing entry can never drop or
duplicate a test, since the partition is over the collected node ids.
Refresh them from CI's ``test-durations-shard*`` artifacts with::

    python -m tests.pytest_shards update-weights test-durations-shard*.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

WEIGHTS_FILE = Path(__file__).with_name("shard_weights.json")


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


def assign_files(weights: Mapping[str, float], total: int) -> dict[str, int]:
    """Map each file to a 1-based shard index (longest-processing-time rule)."""
    if total < 1:
        raise ValueError("total must be >= 1")
    loads = [0.0] * total
    assignment: dict[str, int] = {}
    for path, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        target = min(range(total), key=lambda i: (loads[i], i))
        loads[target] += weight
        assignment[path] = target + 1
    return assignment


def load_weights(path: Path = WEIGHTS_FILE) -> dict[str, float]:
    """Recorded per-file seconds; empty (count-only balancing) if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files", {}) if isinstance(data, dict) else {}
    return {
        str(k): float(v)
        for k, v in files.items()
        if isinstance(v, int | float) and v >= 0
    }


def file_weights(
    nodeids: Iterable[str], recorded: Mapping[str, float] | None = None
) -> dict[str, float]:
    """Each collected file's balancing weight (see the module docstring)."""
    counts: dict[str, int] = {}
    for nodeid in nodeids:
        path = file_of(nodeid)
        counts[path] = counts.get(path, 0) + 1
    recorded = recorded or {}
    per_test = [
        recorded[p] / counts[p] for p in counts if p in recorded and recorded[p] > 0
    ]
    fallback = statistics.median(per_test) if per_test else 1.0
    return {
        path: recorded.get(path, count * fallback) for path, count in counts.items()
    }


def select(
    nodeids: Iterable[str],
    index: int,
    total: int,
    recorded: Mapping[str, float] | None = None,
) -> set[str]:
    """The node ids shard ``index`` of ``total`` runs."""
    ids = list(nodeids)
    assignment = assign_files(file_weights(ids, recorded), total)
    return {nodeid for nodeid in ids if assignment[file_of(nodeid)] == index}


def weights_from_durations(records: Iterable[Mapping[str, object]]) -> dict[str, float]:
    """Per-file seconds (setup + call + teardown) from conftest's
    ``ABICHECK_DURATIONS_JSON`` records."""
    totals: dict[str, float] = {}
    for record in records:
        path = file_of(str(record["nodeid"]))
        duration = record["duration"]
        assert isinstance(duration, int | float)
        totals[path] = totals.get(path, 0.0) + float(duration)
    return {path: round(seconds, 2) for path, seconds in sorted(totals.items())}


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "update-weights":
        print(
            "usage: python -m tests.pytest_shards update-weights DURATIONS.json...",
            file=sys.stderr,
        )
        return 64
    records: list[Mapping[str, object]] = []
    for name in argv[1:]:
        records.extend(json.loads(Path(name).read_text(encoding="utf-8")))
    document = {
        "comment": "Per-file seconds steering `pytest --shard` balance only; regenerate with `python -m tests.pytest_shards update-weights`.",
        "files": weights_from_durations(records),
    }
    WEIGHTS_FILE.write_text(
        json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(document['files'])} file weights to {WEIGHTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
