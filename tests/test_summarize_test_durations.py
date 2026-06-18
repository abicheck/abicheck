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

"""Unit tests for scripts/summarize_test_durations.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "summarize_test_durations.py"


def _load():
    spec = importlib.util.spec_from_file_location("summarize_test_durations", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_aggregate_sums_phases_per_nodeid():
    mod = _load()
    rows = [
        {"nodeid": "t::a", "when": "setup", "duration": 0.5},
        {"nodeid": "t::a", "when": "call", "duration": 1.0},
        {"nodeid": "t::a", "when": "teardown", "duration": 0.25},
        {"nodeid": "t::b", "when": "call", "duration": 0.1},
    ]
    totals = mod.aggregate(rows)
    assert totals["t::a"] == 1.75
    assert totals["t::b"] == 0.1


def test_render_orders_slowest_first_and_respects_top():
    mod = _load()
    totals = {"slow": 3.0, "mid": 2.0, "fast": 1.0}
    out = mod.render(totals, top=2)
    # Only the two slowest appear, slowest first; the fastest is excluded.
    assert "`slow`" in out and "`mid`" in out
    assert "`fast`" not in out
    assert out.index("slow") < out.index("mid")
    assert "3.00s" in out


def test_main_prints_when_no_github_summary(tmp_path, capsys, monkeypatch):
    mod = _load()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"nodeid": "t::x", "when": "call", "duration": 2.0}]))
    rc = mod.main([str(p), "--top", "5"])
    assert rc == 0
    assert "`t::x`" in capsys.readouterr().out


def test_main_appends_to_github_summary(tmp_path, monkeypatch):
    mod = _load()
    summary = tmp_path / "summary.md"
    summary.write_text("pre-existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"nodeid": "t::y", "when": "call", "duration": 1.0}]))
    rc = mod.main([str(p)])
    assert rc == 0
    text = summary.read_text()
    assert text.startswith("pre-existing")  # appended, not overwritten
    assert "`t::y`" in text


def test_main_missing_file_is_noop(tmp_path, capsys):
    mod = _load()
    rc = mod.main([str(tmp_path / "nope.json")])
    assert rc == 0  # absence is tolerated (e.g. the test step was skipped)
