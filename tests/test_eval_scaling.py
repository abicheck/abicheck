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

"""Pure-logic tests for the C1 parallel-L4 scaling harness (skills-src/evaluation/field/scaling.py).

The live driver shells out to git/cmake/abicheck (covered by the scheduled CI
lane, not here). These tests pin the *pure* halves — the speedup/efficiency
table, the Amdahl serial-fraction estimate, and the markdown renderer — so the
scaling curve in SCALING.md cannot silently misreport. The harness lives in
``skills-src/evaluation/field/``, imported by adding that directory to ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = (
    Path(__file__).resolve().parent.parent / "skills-src" / "evaluation" / "field"
)
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

scaling = pytest.importorskip(
    "scaling", reason="skills-src/evaluation/field/scaling.py importable"
)


def test_speedup_rows_are_relative_to_serial_baseline():
    rows = scaling.speedup_rows({1: 60.0, 2: 40.0, 4: 30.0})
    by_jobs = {r["jobs"]: r for r in rows}
    assert by_jobs[1]["speedup"] == 1.0
    assert by_jobs[1]["efficiency"] == 1.0
    # 60/40 = 1.5× at 2 jobs, efficiency 0.75
    assert by_jobs[2]["speedup"] == 1.5
    assert by_jobs[2]["efficiency"] == 0.75
    # 60/30 = 2.0× at 4 jobs, efficiency 0.5
    assert by_jobs[4]["speedup"] == 2.0
    assert by_jobs[4]["efficiency"] == 0.5


def test_speedup_rows_require_serial_baseline():
    with pytest.raises(ValueError):
        scaling.speedup_rows({2: 40.0, 4: 30.0})


def test_speedup_rows_are_sorted_by_jobs():
    rows = scaling.speedup_rows({4: 30.0, 1: 60.0, 2: 40.0})
    assert [r["jobs"] for r in rows] == [1, 2, 4]


def test_perfect_linear_scaling_has_zero_serial_fraction():
    # 4× speedup at 4 jobs => f = (4/4 - 1)/3 = 0.
    assert scaling.amdahl_serial_fraction({1: 80.0, 4: 20.0}) == 0.0


def test_no_speedup_yields_full_serial_fraction():
    # Whole-dump time unchanged by parallelism => entirely serial.
    assert scaling.amdahl_serial_fraction({1: 50.0, 2: 50.0, 4: 50.0}) == 1.0


def test_serial_fraction_uses_best_speedup_point():
    # Best speedup is at jobs=2 (1.25×); a regression at jobs=4 must not drag the
    # estimate. f = (2/1.25 - 1)/(2-1) = 0.6.
    f = scaling.amdahl_serial_fraction({1: 50.0, 2: 40.0, 4: 43.0})
    assert f == pytest.approx(0.6, abs=0.01)


def test_serial_fraction_none_without_parallel_sample():
    assert scaling.amdahl_serial_fraction({1: 50.0}) is None


def test_render_scaling_includes_tree_table_and_skips():
    payload = {
        "generated_utc": "2026-06-14T00:00:00+00:00",
        "abicheck_version": "abicheck 0.3.0",
        "host": {"platform": "Linux", "python": "3.11.0", "cpus": 4},
        "reps": 1,
        "trees": [
            {
                "label": "zstd",
                "tus": 76,
                "serial_fraction": 0.7,
                "rows": scaling.speedup_rows({1: 63.0, 2: 49.0, 4: 48.0}),
            },
            {"label": "broken", "error": "skipped: missing git"},
        ],
    }
    out = scaling.render_scaling(payload)
    assert "zstd — 76 TUs (~70% serial)" in out
    assert "| 2 | 49.0 | 1.29× | 0.64 |" in out
    # The skipped tree is surfaced, not silently dropped.
    assert "skipped: missing git" in out
    # Header documents the Amdahl framing.
    assert "Amdahl" in out
