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

"""Per-run cost accounting for the skill evaluation (`graders/efficiency.py`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills-src"
    / "evaluation"
    / "agents"
    / "skills"
)
sys.path.insert(0, str(EVAL_DIR))

from graders.efficiency import FIELDS, run_efficiency, token_usage  # noqa: E402


@pytest.mark.parametrize(
    ("fresh", "read", "write"),
    [(14, 272_144, 19_360), (0, 0, 0), (5, 0, 7), (1, 2, 3)],
)
def test_total_input_counts_the_prompt_cache(fresh, read, write):
    counts = {
        "input_tokens": fresh,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "output_tokens": 9,
    }
    usage = token_usage(counts)
    assert usage["tokens_in_total"] == fresh + read + write
    assert usage["tokens_in"] == fresh
    assert usage["tokens_out"] == 9


def test_missing_counts_are_unknown_not_zero():
    assert token_usage({})["tokens_in_total"] is None
    assert token_usage({"input_tokens": 3})["tokens_in_total"] == 3


def _result(**usage):
    return json.dumps({"type": "result", "usage": usage, "total_cost_usd": 0.5})


def test_run_efficiency_rereads_tokens_from_the_recorded_events(tmp_path):
    # A run recorded before cache counts were kept: usage.json has only the
    # uncached input, the events still carry the full breakdown.
    (tmp_path / "usage.json").write_text(
        json.dumps(
            {"wall_clock_seconds": 12.5, "turns": 4, "tokens_in": 14, "cost_usd": 0.5}
        ),
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        "not json\n"
        + json.dumps({"type": "assistant"})
        + "\n"
        + _result(
            input_tokens=14,
            cache_read_input_tokens=1000,
            cache_creation_input_tokens=86,
            output_tokens=40,
        )
        + "\n",
        encoding="utf-8",
    )
    got = run_efficiency(tmp_path)
    assert set(got) == set(FIELDS)
    assert got["tokens_in_total"] == 1100
    assert got["tokens_out"] == 40
    assert got["wall_clock_seconds"] == 12.5
    assert got["tool_calls"] is None


def test_run_efficiency_of_an_empty_run_is_all_unknown(tmp_path):
    assert run_efficiency(tmp_path) == dict.fromkeys(FIELDS)
