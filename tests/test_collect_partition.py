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

"""``--collect-partition`` (tests/pytest_collect_partition.py) splits the
collection into disjoint slices that together are the full collection.

``scripts/gen_repo_facts.py`` counts the fast lane by collecting N slices at
once and summing them, because one full collection had grown to 107 s of its
120 s budget on a GitHub runner. That count is only right if the slices are
pairwise disjoint and cover every test. ``test_slices_partition_the_real_fast_lane``
checks that over the real tree against pytest's own unpartitioned collection
(the oracle), and checks the script's summed count against it too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pytest_collect_partition import parse_partition, should_ignore, slice_of

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_repo_facts  # noqa: E402

_FAST = gen_repo_facts._FAST_MARKER


def _collect(*extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-m",
            _FAST,
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            *extra,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _ids(proc: subprocess.Popen[str]) -> set[str]:
    out, _ = proc.communicate(timeout=900)
    assert proc.returncode in (0, 5), out[-3000:]
    return {line for line in out.splitlines() if "::" in line}


@pytest.mark.slow
def test_slices_partition_the_real_fast_lane() -> None:
    n = 3
    full_proc = _collect()
    slice_procs = [_collect(f"--collect-partition={k}/{n}") for k in range(1, n + 1)]
    full = _ids(full_proc)
    slices = [_ids(proc) for proc in slice_procs]
    assert len(full) > 10_000, len(full)  # vacuity guard: the lane is real
    for i in range(n):
        for j in range(i + 1, n):
            assert not slices[i] & slices[j], (i + 1, j + 1)
    union = set().union(*slices)
    assert union == full, {"missing": sorted(full - union)[:20]}
    assert gen_repo_facts._fast_test_cases_collected(partitions=n) == len(full)


def _test_modules() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")
    )


@pytest.mark.parametrize("n", range(1, 9))
def test_every_test_module_lands_in_exactly_one_slice(n: int) -> None:
    modules = _test_modules()
    owners = {
        module: [
            k
            for k in range(1, n + 1)
            if not should_ignore(ROOT / module, ROOT, f"{k}/{n}")
        ]
        for module in modules
    }
    assert all(len(ks) == 1 for ks in owners.values())
    assert {ks[0] for ks in owners.values()} <= set(range(1, n + 1))
    # The slice is a property of the path alone, so every process agrees.
    assert all(owners[m] == [slice_of(m, n)] for m in modules)


def test_only_test_modules_are_ever_filtered(tmp_path: Path) -> None:
    for name in ("conftest.py", "_helper.py", "data.txt", "pytest_x.py"):
        (tmp_path / name).write_text("")
        assert not any(
            should_ignore(tmp_path / name, tmp_path, f"{k}/4") for k in range(1, 5)
        )
    assert not should_ignore(tmp_path, tmp_path, "1/4")
    (tmp_path / "test_a.py").write_text("")
    assert not should_ignore(tmp_path / "test_a.py", tmp_path, None)


@pytest.mark.parametrize("spec", ["", "3", "0/2", "3/2", "a/b", "1/0"])
def test_a_malformed_slice_is_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_partition(spec)


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("21646/22923 tests collected (1277 deselected) in 9.1s", 21646),
        ("12 tests collected in 0.4s", 12),
        ("1 test collected in 0.1s", 1),
        ("no tests collected (40 deselected) in 0.3s", 0),
        ("ERROR collecting tests/test_x.py", None),
        # A node id quoting a summary precedes the real one; the last wins.
        ("t.py::c[5/9 tests collected]\n7/10 tests collected in 1s", 7),
    ],
)
def test_the_collection_summary_is_read_as_a_count(
    text: str, count: int | None
) -> None:
    assert gen_repo_facts._collected_in(text) == count
