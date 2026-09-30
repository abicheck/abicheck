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

"""Contract of the unit-suite shard splitter (tests/pytest_shards.py).

Stated as properties over generated node-id sets, not fixed examples: a
sharded coverage lane that silently drops or double-runs a file still goes
green, so the partition itself is the thing to pin.
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from tests.pytest_shards import (
    assign_files,
    file_of,
    file_weights,
    load_weights,
    parse_shard,
    select,
    weights_from_durations,
)

ROOT = Path(__file__).resolve().parent.parent

_files = st.lists(
    st.from_regex(r"tests/test_[a-z]{1,6}\.py", fullmatch=True),
    min_size=0,
    max_size=25,
    unique=True,
)


@st.composite
def _nodeids(draw: st.DrawFn) -> list[str]:
    files = draw(_files)
    ids: list[str] = []
    for path in files:
        count = draw(st.integers(min_value=1, max_value=12))
        ids.extend(f"{path}::test_{i}" for i in range(count))
    return ids


@settings(deadline=None)
@given(_nodeids(), st.integers(min_value=1, max_value=6))
def test_shards_partition_the_selection(ids: list[str], total: int) -> None:
    shards = [select(ids, k, total) for k in range(1, total + 1)]
    assert set().union(*shards) == set(ids)
    assert sum(len(s) for s in shards) == len(set(ids))


@settings(deadline=None)
@given(_nodeids(), st.integers(min_value=1, max_value=6))
def test_a_file_never_straddles_two_shards(ids: list[str], total: int) -> None:
    owner: dict[str, int] = {}
    for k in range(1, total + 1):
        for nodeid in select(ids, k, total):
            assert owner.setdefault(file_of(nodeid), k) == k


@settings(deadline=None)
@given(
    _nodeids(), st.integers(min_value=1, max_value=6), st.randoms(use_true_random=False)
)
def test_assignment_ignores_collection_order(
    ids: list[str], total: int, rnd: random.Random
) -> None:
    shuffled = list(ids)
    rnd.shuffle(shuffled)
    for k in range(1, total + 1):
        assert select(ids, k, total) == select(shuffled, k, total)


@settings(deadline=None)
@given(
    st.dictionaries(st.text(min_size=1, max_size=5), st.integers(1, 50), max_size=30),
    st.integers(1, 6),
)
def test_imbalance_is_bounded_by_the_heaviest_file(
    weights: dict[str, int], total: int
) -> None:
    assignment = assign_files(weights, total)
    loads = [0] * total
    for path, shard in assignment.items():
        loads[shard - 1] += weights[path]
    if weights:
        assert max(loads) - min(loads) <= max(weights.values())


_recorded = st.dictionaries(
    st.from_regex(r"tests/test_[a-z]{1,6}\.py", fullmatch=True),
    st.floats(min_value=0, max_value=500, allow_nan=False),
    max_size=30,
)


@settings(deadline=None)
@given(_nodeids(), st.integers(min_value=1, max_value=6), _recorded)
def test_recorded_weights_never_change_what_runs(
    ids: list[str], total: int, recorded: dict[str, float]
) -> None:
    """Weights steer balance only. Stale entries (files no longer collected),
    missing entries (new files) and zero weights must still yield an exact
    partition of the collected set."""
    shards = [select(ids, k, total, recorded) for k in range(1, total + 1)]
    assert set().union(*shards) == set(ids)
    assert sum(len(x) for x in shards) == len(set(ids))


@settings(deadline=None)
@given(_nodeids(), _recorded)
def test_unrecorded_files_fall_back_to_a_count_based_estimate(
    ids: list[str], recorded: dict[str, float]
) -> None:
    weights = file_weights(ids, recorded)
    assert set(weights) == {file_of(i) for i in ids}
    for path, weight in weights.items():
        if path in recorded:
            assert weight == recorded[path]
        else:
            assert weight > 0


def test_heavy_files_are_spread_rather_than_stacked() -> None:
    """The reason weights exist: two slow files and many fast ones must not
    land the two slow files on one shard just because they hold few tests."""
    ids = [f"tests/test_slow{i}.py::t{j}" for i in range(2) for j in range(3)]
    ids += [f"tests/test_fast{i}.py::t{j}" for i in range(20) for j in range(10)]
    recorded = {"tests/test_slow0.py": 300.0, "tests/test_slow1.py": 300.0}
    recorded |= {f"tests/test_fast{i}.py": 2.0 for i in range(20)}
    owner = {k: select(ids, k, 2, recorded) for k in (1, 2)}
    slow_shards = {k for k, sel in owner.items() for n in sel if "slow" in n}
    assert slow_shards == {1, 2}


def test_durations_fold_into_per_file_seconds() -> None:
    records = [
        {"nodeid": "tests/test_a.py::x", "when": "setup", "duration": 1.0},
        {"nodeid": "tests/test_a.py::x", "when": "call", "duration": 2.5},
        {"nodeid": "tests/test_b.py::C::y[1]", "when": "call", "duration": 0.25},
    ]
    assert weights_from_durations(records) == {
        "tests/test_a.py": 3.5,
        "tests/test_b.py": 0.25,
    }


def test_an_unreadable_weights_file_means_count_only_balancing(tmp_path: Path) -> None:
    bad = tmp_path / "w.json"
    bad.write_text("not json", encoding="utf-8")
    assert load_weights(bad) == {}
    assert load_weights(tmp_path / "missing.json") == {}


@pytest.mark.parametrize("spec", ["", "1", "0/3", "4/3", "1/0", "a/b", "-1/2"])
def test_invalid_specs_are_rejected(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_shard(spec)


def test_the_option_really_deselects_through_pytest(tmp_path: Path) -> None:
    """End to end through the real conftest hook: the shards' collected sets
    partition the unsharded one."""
    targets = ["tests/test_pytest_shards.py", "tests/test_example_shards.py"]

    def collected(*extra: str) -> set[str]:
        out = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *targets,
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                *extra,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return {line for line in out.splitlines() if "::" in line}

    whole = collected()
    parts = [collected(f"--shard={k}/2") for k in (1, 2)]
    assert whole and parts[0] and parts[1]
    assert parts[0] | parts[1] == whole and not parts[0] & parts[1]
