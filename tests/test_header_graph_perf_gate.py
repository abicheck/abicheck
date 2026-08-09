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
    def _point(self, size: int, attach_ms: float, backend: str = "clang") -> dict:
        return {
            "size": size,
            "backend": backend,
            "baseline_ms": 100.0,
            "attach_ms": attach_ms,
        }

    def test_no_regression_within_tolerance(self):
        points = [self._point(10, 15.0)]
        baseline = {(10, "clang"): 14.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_regression_beyond_tolerance_reported(self):
        points = [self._point(10, 30.0)]
        baseline = {(10, "clang"): 10.0}
        failures = hg_gate.check_regressions(points, baseline, 0.5)
        assert len(failures) == 1
        assert "size=10" in failures[0]
        assert "backend=clang" in failures[0]

    def test_missing_baseline_entry_is_not_a_failure(self):
        points = [self._point(999, 30.0)]
        baseline = {(10, "clang"): 10.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_exactly_at_tolerance_boundary_passes(self):
        points = [self._point(10, 15.0)]
        baseline = {(10, "clang"): 10.0}  # 15.0 == 10.0 * 1.5, not strictly greater
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_zero_baseline_is_skipped_not_a_false_regression(self):
        points = [self._point(10, 5.0)]
        baseline = {(10, "clang"): 0.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []

    def test_backends_are_distinct_baseline_keys(self):
        # A castxml-backend point must never be gated against a clang-backend
        # baseline entry for the same size (their costs are structurally
        # different — see the module docstring).
        points = [self._point(10, 30.0, backend="castxml")]
        baseline = {(10, "clang"): 10.0}
        assert hg_gate.check_regressions(points, baseline, 0.5) == []


class TestLoadBaseline:
    def test_round_trips_points_shape(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps(
                {"points": [{"size": 10, "backend": "clang", "attach_ms": 12.3}]}
            )
        )
        assert hg_gate._load_baseline(report) == {(10, "clang"): 12.3}

    def test_accepts_bare_list_shape(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps([{"size": 10, "backend": "clang", "attach_ms": 12.3}])
        )
        assert hg_gate._load_baseline(report) == {(10, "clang"): 12.3}

    def test_missing_backend_field_defaults_to_clang(self, tmp_path):
        # Back-compat with the earlier single-backend report shape.
        report = tmp_path / "report.json"
        report.write_text(json.dumps([{"size": 10, "attach_ms": 12.3}]))
        assert hg_gate._load_baseline(report) == {(10, "clang"): 12.3}


class TestMatchedPoints:
    def _point(self, size: int, backend: str = "clang") -> dict:
        return {
            "size": size,
            "backend": backend,
            "baseline_ms": 100.0,
            "attach_ms": 5.0,
        }

    def test_matches_when_key_present_in_baseline(self):
        points = [self._point(10)]
        baseline = {(10, "clang"): 4.0}
        assert hg_gate.matched_points(points, baseline) == points

    def test_no_match_when_baseline_covers_different_sizes(self):
        # Regression guard: a baseline generated for size 999 must not
        # silently "pass" a run measuring the default 25/100/400 sweep.
        points = [self._point(10), self._point(20)]
        baseline = {(999, "clang"): 4.0}
        assert hg_gate.matched_points(points, baseline) == []

    def test_partial_match_returns_only_matched_subset(self):
        points = [self._point(10), self._point(20)]
        baseline = {(10, "clang"): 4.0}
        assert hg_gate.matched_points(points, baseline) == [points[0]]


class TestMainSkipsWithoutToolchain:
    def test_skip_message_when_toolchain_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(hg_gate, "_have", lambda tool: False)
        rc = hg_gate.main(["--sizes", "5"])
        assert rc == 0
        assert "SKIP" in capsys.readouterr().out

    def test_baseline_with_no_matching_points_fails_loudly(
        self, monkeypatch, tmp_path, capsys
    ):
        # Same regression guard as TestMatchedPoints, through the real main()
        # entry point: a stale/mistargeted --baseline must never print OK.
        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hg_gate,
            "measure",
            lambda sizes, repeat, backends=hg_gate.BACKENDS: [
                {"size": s, "backend": "clang", "baseline_ms": 10.0, "attach_ms": 5.0}
                for s in sizes
            ],
        )
        baseline_file = tmp_path / "baseline.json"
        baseline_file.write_text(
            json.dumps(
                {"points": [{"size": 999, "backend": "clang", "attach_ms": 1.0}]}
            )
        )
        rc = hg_gate.main(["--sizes", "10", "--baseline", str(baseline_file)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL" in out
        assert "no entry matching" in out


@pytest.mark.integration
@pytest.mark.skipif(
    not (_have("clang") and _have("clang++") and _have("g++"))
    or not sys.platform.startswith("linux"),
    reason="header-graph perf gate needs clang/clang++/g++ on a Linux/ELF host",
)
class TestLiveMeasurement:
    """Compiles real fixtures and invokes clang/g++ — excluded from the fast
    lane by the ``integration`` marker (in addition to its own tool-based
    skipif, since ``integration``'s Linux gate checks castxml/gcc/g++, not
    clang specifically — see ``tests/conftest.py``'s
    ``_integration_skip_reason``)."""

    def test_measure_size_returns_positive_timings(self):
        results = hg_gate._measure_size(5, repeat=1, backends=("clang",))
        assert len(results) == 1
        result = results[0]
        assert result["size"] == 5
        assert result["backend"] == "clang"
        assert result["baseline_ms"] > 0
        assert result["attach_ms"] > 0

    def test_resolve_includes_infers_the_headers_own_directory(self, tmp_path):
        header = tmp_path / "api.h"
        header.write_text("#pragma once\n")
        inc_extra, deferred_tokens, extra_hash_dirs = hg_gate._resolve_includes(header)
        # No build context (default CompileContext) -> the plain -I bucket,
        # not the deferred/-isystem one (see resolve_inferred_header_roots).
        assert inc_extra == [tmp_path]
        assert deferred_tokens == ()
        assert extra_hash_dirs == ()

    def test_repeats_never_share_a_fixture_directory(self, monkeypatch):
        # Regression guard for the cross-repeat/cross-backend disk-cache
        # contamination finding: every _build_fixture call during one
        # _measure_one run must see a distinct temp directory.
        seen_dirs = []
        real_build_fixture = hg_gate._build_fixture

        def _spy(tmp_dir, n):
            seen_dirs.append(tmp_dir)
            return real_build_fixture(tmp_dir, n)

        monkeypatch.setattr(hg_gate, "_build_fixture", _spy)
        hg_gate._measure_one(3, "clang", repeat=3)
        assert len(seen_dirs) == 3
        assert len(set(seen_dirs)) == 3

    def test_main_report_only_run_exits_zero(self, capsys):
        rc = hg_gate.main(["--sizes", "5", "--repeat", "1"])
        assert rc == 0
        assert "report-only" in capsys.readouterr().out
