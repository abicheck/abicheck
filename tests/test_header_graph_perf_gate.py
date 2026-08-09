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

"""Tests for the G31 Phase D header-graph attach-cost perf gate.

The pure logic (synthetic-header generation, regression comparison, baseline
parsing) is exercised unconditionally; the live measurement path
(``_measure_size``/``measure``, which needs a real ``clang``/``g++`` install
to compile a fixture and run the header-graph attach step) is a separate,
self-skipping test — mirroring
``tests/test_clang_header_backend_integration.py``'s own gating.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_header_graph_perf.py"
)
_spec = importlib.util.spec_from_file_location("check_header_graph_perf", _GATE_PATH)
assert _spec and _spec.loader
hg_gate = importlib.util.module_from_spec(_spec)
sys.modules["check_header_graph_perf"] = hg_gate
_spec.loader.exec_module(hg_gate)


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


class TestSyntheticFixtureGeneration:
    def test_header_declares_n_structs_and_functions(self):
        header = hg_gate._synthesize_header(3)
        assert header.count("struct S") == 3
        assert "int fn0(" in header
        assert "int fn1(" in header
        assert "int fn2(" in header
        assert "namespace hgperf" in header

    def test_source_defines_every_declared_function(self):
        source = hg_gate._synthesize_source(4)
        for i in range(4):
            assert f"int fn{i}(" in source

    def test_zero_size_still_valid_shape(self):
        header = hg_gate._synthesize_header(0)
        assert "struct S" not in header
        assert "namespace hgperf" in header


class TestCheckRegressions:
    def _point(self, size: int, attach_ms: float) -> dict:
        return {"size": size, "baseline_ms": 100.0, "attach_ms": attach_ms}

    def test_no_regression_within_tolerance(self):
        points = [self._point(10, 15.0)]
        baseline = {10: 14.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_regression_beyond_tolerance_reported(self):
        points = [self._point(10, 30.0)]
        baseline = {10: 10.0}
        failures = hg_gate.check_regressions(points, baseline, 0.5)
        assert len(failures) == 1
        assert "size=10" in failures[0]

    def test_missing_baseline_entry_is_not_a_failure(self):
        points = [self._point(999, 30.0)]
        baseline = {10: 10.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_exactly_at_tolerance_boundary_passes(self):
        points = [self._point(10, 15.0)]
        baseline = {10: 10.0}  # 15.0 == 10.0 * 1.5, not strictly greater
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_zero_baseline_is_skipped_not_a_false_regression(self):
        points = [self._point(10, 5.0)]
        baseline = {10: 0.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []


class TestLoadBaseline:
    def test_round_trips_points_shape(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"points": [{"size": 10, "attach_ms": 12.3}]}))
        assert hg_gate._load_baseline(report) == {10: 12.3}

    def test_accepts_bare_list_shape(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(json.dumps([{"size": 10, "attach_ms": 12.3}]))
        assert hg_gate._load_baseline(report) == {10: 12.3}


class TestMainSkipsWithoutToolchain:
    def test_skip_message_when_toolchain_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(hg_gate, "_have", lambda tool: False)
        rc = hg_gate.main(["--sizes", "5"])
        assert rc == 0
        assert "SKIP" in capsys.readouterr().out


@pytest.mark.skipif(
    not (_have("clang") and _have("clang++") and _have("g++"))
    or not sys.platform.startswith("linux"),
    reason="header-graph perf gate needs clang/clang++/g++ on a Linux/ELF host",
)
class TestLiveMeasurement:
    def test_measure_size_returns_positive_timings(self):
        result = hg_gate._measure_size(5, repeat=1)
        assert result["size"] == 5
        assert result["baseline_ms"] > 0
        assert result["attach_ms"] > 0

    def test_main_report_only_run_exits_zero(self, capsys):
        rc = hg_gate.main(["--sizes", "5", "--repeat", "1"])
        assert rc == 0
        assert "report-only" in capsys.readouterr().out
