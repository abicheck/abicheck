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

"""Unit tests for the ADR-033 Phase 7 evidence benchmark report.

Compiler-free: the report times the pure-Python inline collection path and
reuses the FP-rate gate metrics, so it runs in the default fast lane.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "evidence_benchmark.py"
_spec = importlib.util.spec_from_file_location("evidence_benchmark", _SCRIPT)
assert _spec and _spec.loader
eb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eb)


def test_collection_timings_cover_all_report_modes():
    rows = eb.collection_timings(n_units=3)
    modes = {r["mode"] for r in rows}
    assert modes == set(eb._REPORT_MODES)
    for r in rows:
        assert r["duration_seconds"] >= 0
        # 'build' mode collects L3 only; source/graph modes engage L4/L5.
        if r["mode"] == "build":
            assert r["layers_collected"] == ["L3"]
        else:
            assert "L3" in r["layers_collected"]


def test_build_report_has_both_sections():
    report = eb.build_report(n_units=2)
    assert "collection_performance" in report
    fp = report["false_positive"]
    assert "false_positive_delta_vs_baseline" in fp
    assert "false_negative_delta_vs_baseline" in fp


def test_json_main_emits_report(capsys):
    rc = eb.main(["--json", "--units", "2"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["collection_performance"]
    assert "false_positive" in out
