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

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from tests.pytest_shards import (
    INTEGRATION_WEIGHTS_FILE,
    WEIGHTS_FILE,
    _main,
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


def test_an_underflowing_recorded_weight_still_gives_new_files_a_positive_one() -> None:
    ids = ["tests/test_a.py::t0", "tests/test_a.py::t1", "tests/test_b.py::t0"]
    weights = file_weights(ids, {"tests/test_a.py": 5e-324})
    assert weights["tests/test_b.py"] > 0


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


@pytest.mark.parametrize("spec", ["a/b", "/3", "1/", "x"])
def test_a_malformed_shard_explains_the_expected_form(spec: str) -> None:
    with pytest.raises(ValueError, match="K/N"):
        parse_shard(spec)


def test_non_numeric_and_boolean_weights_are_ignored(tmp_path: Path) -> None:
    import json

    doc = tmp_path / "w.json"
    doc.write_text(
        json.dumps(
            {
                "files": {
                    "tests/a.py": True,
                    "tests/b.py": False,
                    "tests/c.py": "3",
                    "tests/d.py": -1,
                    "tests/e.py": 2.5,
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_weights(doc) == {"tests/e.py": 2.5}


def test_an_unreadable_weights_file_means_count_only_balancing(tmp_path: Path) -> None:
    bad = tmp_path / "w.json"
    bad.write_text("not json", encoding="utf-8")
    assert load_weights(bad) == {}
    assert load_weights(tmp_path / "missing.json") == {}


@pytest.mark.parametrize(
    "spec", ["", "1", "0/3", "4/3", "1/0", "a/b", "-1/2", "/3", "1/", "1.5/3"]
)
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


def test_the_committed_weights_file_loads_and_is_nonnegative() -> None:
    weights = load_weights()
    assert len(weights) > 100, "tests/shard_weights.json is missing or nearly empty"
    assert all(v >= 0 for v in weights.values())
    assert all(k.startswith("tests/") and k.endswith(".py") for k in weights)


_THREE_FILES = [
    "tests/test_pytest_shards.py",
    "tests/test_example_shards.py",
    "tests/test_collect_partition.py",
]


def _collected_files(*extra: str) -> set[str]:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *_THREE_FILES,
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
    return {file_of(line) for line in out.splitlines() if "::" in line}


@pytest.mark.parametrize("heavy", _THREE_FILES)
def test_shard_weights_option_steers_the_split_through_pytest(
    heavy: str, tmp_path: Path
) -> None:
    """``--shard-weights`` is what the integration lane balances on: the file
    it names as heaviest gets a shard of its own, whichever file that is, and
    the two shards still partition the collected files."""
    weights = tmp_path / "w.json"
    weights.write_text(
        json.dumps(
            {"files": {f: (1000.0 if f == heavy else 1.0) for f in _THREE_FILES}}
        ),
        encoding="utf-8",
    )
    opt = f"--shard-weights={weights}"
    one, two = (_collected_files(f"--shard={k}/2", opt) for k in (1, 2))
    assert one == {heavy}
    assert two == set(_THREE_FILES) - {heavy}


def test_update_weights_writes_only_the_named_file(tmp_path: Path) -> None:
    durations = tmp_path / "d.json"
    durations.write_text(
        json.dumps(
            [
                {"nodeid": "tests/test_a.py::t", "when": "call", "duration": 2.5},
                {"nodeid": "tests/test_a.py::u", "when": "setup", "duration": 0.5},
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "integration.json"
    before = WEIGHTS_FILE.read_bytes()
    assert _main(["update-weights", "--out", str(out), str(durations)]) == 0
    assert load_weights(out) == {"tests/test_a.py": 3.0}
    assert WEIGHTS_FILE.read_bytes() == before
    assert _main(["update-weights", "--out"]) == 64
    assert _main(["update-weights", "--out", str(out)]) == 64


def test_the_committed_integration_weights_measure_integration_cost() -> None:
    """The integration lane's weights are integration seconds, not the unit
    lane's: a catalog-wide integration file weighs minutes there and almost
    nothing in the unit file."""
    integration, unit = load_weights(INTEGRATION_WEIGHTS_FILE), load_weights()
    assert len(integration) > 20
    assert all(v >= 0 for v in integration.values())
    heavy = "tests/test_family_f8_catalog_backend_parity.py"
    assert integration[heavy] > 60 > unit.get(heavy, 0.0)
