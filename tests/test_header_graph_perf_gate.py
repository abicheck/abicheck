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
import os
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

# Re-exported by the gate module's own sys.path bootstrap; imported here under
# their real names so these tests construct the same objects production does
# rather than a lookalike.
GateThreshold = hg_gate.GateThreshold
is_gateable = hg_gate.is_gateable


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


_ALL = hg_gate.METRICS


def _th(tolerance: float = 0.5, min_delta: float = 0.0) -> dict:
    """A uniform ``GateThreshold`` for every metric, for the logic tests below."""
    return {m: GateThreshold(tolerance, min_delta) for m in _ALL}


def _base(
    size: int = 10,
    backend: str = "clang",
    *,
    dump_ms: float = 100.0,
    attach_ms: float = 20.0,
    total_ms: float = 121.0,
) -> dict:
    return {
        (size, backend): {
            "dump_ms": dump_ms,
            "attach_ms": attach_ms,
            "total_ms": total_ms,
        }
    }


def _pt(
    size: int = 10,
    backend: str = "clang",
    *,
    dump_ms: float = 100.0,
    attach_ms: float = 20.0,
    total_ms: float = 121.0,
) -> dict:
    return {
        "size": size,
        "backend": backend,
        "dump_ms": dump_ms,
        "attach_ms": attach_ms,
        "total_ms": total_ms,
    }


def _metrics_in(failures: list[str]) -> set[str]:
    """Which metric each failure message names.

    Asserted on rather than a bare failure count, because "one metric
    regressed" and "the *right* metric regressed" are different claims, and
    only the second one is what gating three phases separately buys.
    """
    return {m for m in _ALL for f in failures if f" {m} " in f}


class TestPerPhaseGating:
    """The three phases are gated independently — the core Phase-2 fix.

    Deliberately driven by *constructed numeric inputs* rather than by real
    slow code: a ``time.sleep``-based "regression" would make these tests both
    slow and flaky, and would test the clock rather than the threshold
    algebra. The live end-to-end path has its own coverage in
    ``TestLiveMeasurement``; what needs to be exact here is which gate fires
    for which shape of slowdown.
    """

    def test_clean_run_fails_nothing(self):
        assert (
            hg_gate.check_regressions(
                [_pt(dump_ms=105.0, attach_ms=21.0, total_ms=127.0)], _base(), _th()
            )
            == []
        )

    def test_dump_only_slowdown_is_caught(self):
        # THE regression the original attach-only gate could not see: the
        # primary header-AST extraction pass doubles, attach is untouched.
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=200.0, attach_ms=20.0, total_ms=221.0)], _base(), _th()
        )
        assert "dump_ms" in _metrics_in(failures)

    def test_dump_only_slowdown_does_not_implicate_attach(self):
        # The diagnostic half of the same claim: a dump regression must not be
        # reported as an attach regression, or the metric split buys nothing.
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=200.0, attach_ms=20.0, total_ms=221.0)], _base(), _th()
        )
        assert "attach_ms" not in _metrics_in(failures)

    def test_attach_only_slowdown_is_caught_and_scoped(self):
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=100.0, attach_ms=40.0, total_ms=141.0)], _base(), _th()
        )
        assert _metrics_in(failures) == {"attach_ms"}

    def test_total_only_slowdown_is_caught(self):
        # Neither phase's own median moved beyond tolerance, yet the whole
        # window did. Physically this is time spent between the phases (or a
        # harness that stopped accounting for part of its own work); either
        # way, a gate on the two phases alone would report a clean pass.
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=100.0, attach_ms=20.0, total_ms=300.0)], _base(), _th()
        )
        assert _metrics_in(failures) == {"total_ms"}

    def test_every_metric_can_fail_at_once(self):
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=500.0, attach_ms=500.0, total_ms=1000.0)], _base(), _th()
        )
        assert _metrics_in(failures) == set(_ALL)

    def test_a_metric_excluded_from_thresholds_is_not_gated(self):
        # --metrics must really narrow the gate, and must narrow only what it
        # names: dump regresses hugely but is not selected.
        only_attach = {"attach_ms": GateThreshold(0.5, 0.0)}
        assert (
            hg_gate.check_regressions(
                [_pt(dump_ms=900.0, attach_ms=20.0, total_ms=921.0)],
                _base(),
                only_attach,
            )
            == []
        )

    def test_exactly_at_tolerance_boundary_passes(self):
        # 150.0 == 100.0 * 1.5 exactly, not strictly greater.
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=150.0, attach_ms=20.0, total_ms=121.0)], _base(), _th()
        )
        assert "dump_ms" not in _metrics_in(failures)

    def test_absolute_floor_protects_a_tiny_baseline(self):
        # attach doubles 2.0 -> 4.0: a 100% relative regression, but 2ms of it.
        base = _base(attach_ms=2.0)
        assert (
            hg_gate.check_regressions(
                [_pt(attach_ms=4.0)],
                base,
                {m: GateThreshold(0.5, 10.0) for m in _ALL},
            )
            == []
        )

    def test_per_metric_thresholds_are_applied_independently(self):
        # dump gets a loose threshold, attach a strict one; both grow 60%.
        mixed = {
            "dump_ms": GateThreshold(1.0, 0.0),
            "attach_ms": GateThreshold(0.1, 0.0),
        }
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=160.0, attach_ms=32.0)], _base(), mixed
        )
        assert _metrics_in(failures) == {"attach_ms"}

    def test_failure_message_states_the_threshold_that_judged_it(self):
        # A gate failure a reader cannot trace back to its own threshold is the
        # other half of the invisible-threshold defect this phase fixes.
        failures = hg_gate.check_regressions(
            [_pt(dump_ms=400.0)],
            _base(),
            {"dump_ms": GateThreshold(0.25, 3.0, source="metric_override")},
        )
        assert len(failures) == 1
        assert "tolerance=0.25" in failures[0]
        assert "min_delta_ms=3.0" in failures[0]
        assert "source=metric_override" in failures[0]

    def test_missing_baseline_entry_is_not_a_failure(self):
        assert hg_gate.check_regressions([_pt(size=999)], _base(10), _th()) == []

    def test_backends_are_distinct_baseline_keys(self):
        # A castxml-backend point must never be gated against a clang-backend
        # baseline entry for the same size (their costs are structurally
        # different — see the module docstring).
        assert (
            hg_gate.check_regressions(
                [_pt(backend="castxml", dump_ms=900.0)], _base(10, "clang"), _th()
            )
            == []
        )


class TestNonGateableValuesCannotSilentlyPass:
    """A poisoned number must read as *ungated*, never as a pass.

    ``current > base + allowed`` is ``False`` whenever either side is ``NaN``,
    and ``base=inf`` makes the allowance infinite. Both are values
    ``json.load`` accepts and round-trips, so a hand-edited or
    partially-written report could previously neuter this gate while it still
    printed ``OK``. These tests pin the *direction* of the failure: the metric
    drops out of the gated set and is reported, rather than passing.
    """

    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_a_bad_baseline_value_is_not_gated(self, bad):
        base = {(10, "clang"): {"dump_ms": bad, "attach_ms": 20.0, "total_ms": 121.0}}
        assert hg_gate.gateable_metrics(_pt(), base) == ["attach_ms", "total_ms"]

    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_a_bad_measured_value_is_not_gated(self, bad):
        assert hg_gate.gateable_metrics(_pt(dump_ms=bad), _base()) == [
            "attach_ms",
            "total_ms",
        ]

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_a_bad_value_never_produces_a_pass_verdict(self, bad):
        # The real hazard, stated directly: an enormous measured value against
        # a NaN baseline must not come back as "no failures, all good".
        base = {(10, "clang"): {"dump_ms": bad, "attach_ms": 20.0, "total_ms": 121.0}}
        assert hg_gate.check_regressions([_pt(dump_ms=1e9)], base, _th()) == []
        assert any(
            "dump_ms" in u for u in hg_gate.ungated_metrics([_pt(dump_ms=1e9)], base)
        )

    def test_a_missing_measured_metric_is_reported_not_gated(self):
        point = {"size": 10, "backend": "clang", "attach_ms": 20.0, "total_ms": 121.0}
        assert hg_gate.gateable_metrics(point, _base()) == ["attach_ms", "total_ms"]
        reported = hg_gate.ungated_metrics([point], _base())
        assert any("metric=dump_ms" in r for r in reported)

    def test_a_non_numeric_value_is_rejected(self):
        # A string where a float belongs (a hand-edited report) must not raise
        # a TypeError mid-gate either -- it is simply not gateable.
        base = {
            (10, "clang"): {"dump_ms": "fast", "attach_ms": 20.0, "total_ms": 121.0}
        }
        assert "dump_ms" not in hg_gate.gateable_metrics(_pt(), base)

    def test_a_bool_is_not_a_measurement(self):
        # `True == 1` in Python, so a bare numeric check would accept it.
        assert not is_gateable(True)


class TestUngatedMetrics:
    def test_a_fully_covered_point_reports_nothing(self):
        assert hg_gate.ungated_metrics([_pt()], _base()) == []

    def test_an_absent_point_reports_every_metric_with_that_reason(self):
        reported = hg_gate.ungated_metrics([_pt(size=999)], _base(10))
        assert len(reported) == len(_ALL)
        assert all("no baseline entry" in r for r in reported)

    def test_a_legacy_baseline_reports_only_the_metric_it_lacks(self):
        # The cross-version case the CI regression lane really hits.
        legacy = {(10, "clang"): {"dump_ms": 100.0, "attach_ms": 20.0}}
        reported = hg_gate.ungated_metrics([_pt()], legacy)
        assert [r.split("metric=")[1].split(":")[0] for r in reported] == ["total_ms"]


class TestLoadBaseline:
    def test_round_trips_all_three_metrics(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps(
                {
                    "points": [
                        {
                            "size": 10,
                            "backend": "clang",
                            "dump_ms": 100.0,
                            "attach_ms": 12.3,
                            "total_ms": 113.0,
                        }
                    ]
                }
            )
        )
        assert hg_gate._load_baseline(report) == {
            (10, "clang"): {"dump_ms": 100.0, "attach_ms": 12.3, "total_ms": 113.0}
        }

    def test_accepts_bare_list_shape(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps([{"size": 10, "backend": "clang", "attach_ms": 12.3}])
        )
        assert hg_gate._load_baseline(report) == {(10, "clang"): {"attach_ms": 12.3}}

    def test_missing_backend_field_defaults_to_clang(self, tmp_path):
        # Back-compat with the earlier single-backend report shape.
        report = tmp_path / "report.json"
        report.write_text(json.dumps([{"size": 10, "attach_ms": 12.3}]))
        assert hg_gate._load_baseline(report) == {(10, "clang"): {"attach_ms": 12.3}}

    def test_legacy_baseline_ms_is_read_as_dump_ms(self, tmp_path):
        # The schema-1 shape the PR-vs-base CI lane measures the *base* branch
        # with. Renaming the field must not silently stop gating the dump
        # phase for the one commit that does the rename.
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps([{"size": 10, "baseline_ms": 100.0, "attach_ms": 12.3}])
        )
        assert hg_gate._load_baseline(report) == {
            (10, "clang"): {"dump_ms": 100.0, "attach_ms": 12.3}
        }

    def test_total_ms_is_never_reconstructed_from_a_legacy_report(self):
        # The specific wrong arithmetic total_ms exists to avoid: a schema-1
        # report genuinely never measured the joint window, so it must read as
        # absent rather than as dump+attach.
        assert "total_ms" not in hg_gate.LEGACY_METRIC_ALIASES

    def test_a_real_metric_wins_over_its_legacy_alias(self, tmp_path):
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps([{"size": 10, "dump_ms": 7.0, "baseline_ms": 999.0}])
        )
        assert hg_gate._load_baseline(report)[(10, "clang")]["dump_ms"] == 7.0

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_json_literals_are_dropped(self, tmp_path, literal):
        report = tmp_path / "report.json"
        report.write_text(
            '{"points": [{"size": 10, "backend": "clang", "dump_ms": '
            + literal
            + ', "attach_ms": 20.0, "total_ms": 121.0}]}'
        )
        loaded = hg_gate._load_baseline(report)
        assert "dump_ms" not in loaded[(10, "clang")]
        assert loaded[(10, "clang")]["attach_ms"] == 20.0


class TestMatchedPoints:
    def test_matches_when_a_metric_is_gateable(self):
        points = [_pt()]
        assert hg_gate.matched_points(points, _base()) == points

    def test_no_match_when_baseline_covers_different_sizes(self):
        # Regression guard: a baseline generated for size 999 must not
        # silently "pass" a run measuring the default 25/100/400 sweep.
        points = [_pt(10), _pt(20)]
        assert hg_gate.matched_points(points, _base(999)) == []

    def test_partial_match_returns_only_matched_subset(self):
        points = [_pt(10), _pt(20)]
        assert hg_gate.matched_points(points, _base(10)) == [points[0]]

    def test_a_point_with_no_gateable_metric_is_not_matched(self):
        # matched_points must agree with check_regressions' own skip
        # conditions -- otherwise main()'s final "N checked" count would
        # include a point check_regressions never actually gated.
        base = {
            (10, "clang"): {"dump_ms": 0.0, "attach_ms": -1.0, "total_ms": float("nan")}
        }
        assert hg_gate.matched_points([_pt()], base) == []

    def test_matching_honors_the_selected_metric_subset(self):
        # Present in the baseline, but not for the one metric being gated.
        legacy = {(10, "clang"): {"dump_ms": 100.0, "attach_ms": 20.0}}
        assert hg_gate.matched_points([_pt()], legacy, ("total_ms",)) == []
        assert hg_gate.matched_points([_pt()], legacy, ("dump_ms",)) == [_pt()]


class TestResolveThresholds:
    """Effective thresholds must be derivable, complete, and reportable."""

    def _args(self, argv: list[str]):
        return hg_gate.parse_args(argv)

    def test_default_applies_to_every_metric(self):
        resolved = hg_gate.resolve_thresholds(self._args([]))
        assert set(resolved) == set(_ALL)
        assert all(
            t.tolerance == hg_gate.DEFAULT_REGRESS_TOLERANCE for t in resolved.values()
        )

    def test_an_explicit_cli_tolerance_is_marked_explicit(self):
        resolved = hg_gate.resolve_thresholds(
            self._args(["--regress-tolerance", "0.1"])
        )
        assert {t.source for t in resolved.values()} == {"explicit"}
        assert all(t.tolerance == 0.1 for t in resolved.values())

    def test_a_per_metric_override_only_touches_that_metric(self):
        resolved = hg_gate.resolve_thresholds(
            self._args(["--regress-tolerance-attach", "0.05"])
        )
        assert resolved["attach_ms"].tolerance == 0.05
        assert resolved["attach_ms"].source == "metric_override"
        assert resolved["dump_ms"].tolerance == hg_gate.DEFAULT_REGRESS_TOLERANCE
        assert resolved["total_ms"].source == "default"

    def test_a_per_metric_min_delta_override_keeps_the_shared_tolerance(self):
        resolved = hg_gate.resolve_thresholds(
            self._args(
                ["--regress-tolerance", "0.2", "--regress-min-delta-ms-total", "50"]
            )
        )
        assert resolved["total_ms"].tolerance == 0.2
        assert resolved["total_ms"].min_delta == 50.0

    def test_metrics_selection_narrows_what_is_resolved(self):
        resolved = hg_gate.resolve_thresholds(self._args(["--metrics", "attach_ms"]))
        assert set(resolved) == {"attach_ms"}

    def test_every_resolved_threshold_is_serializable_for_the_receipt(self):
        for metric, t in hg_gate.resolve_thresholds(self._args([])).items():
            assert set(t.as_dict()) == {"tolerance", "min_delta", "source"}, metric


class TestMainEntryPoint:
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
            lambda sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False: [
                {
                    "size": s,
                    "backend": "clang",
                    "dump_ms": 10.0,
                    "attach_ms": 5.0,
                    "total_ms": 15.0,
                }
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
        assert "no gateable entry" in out

    def test_measure_runs_under_a_redirected_xdg_cache_home(
        self, monkeypatch, tmp_path
    ):
        # Regression guard: every repeat forces a cache miss (see
        # _build_fixture), so a real (non-redirected) XDG_CACHE_HOME would
        # accumulate a never-reused AST cache entry on every run.
        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")
        seen_during_measure = {}

        def _fake_measure(
            sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False
        ):
            seen_during_measure["xdg"] = os.environ.get("XDG_CACHE_HOME")
            return [
                {"size": s, "backend": "clang", "baseline_ms": 1.0, "attach_ms": 1.0}
                for s in sizes
            ]

        monkeypatch.setattr(hg_gate, "measure", _fake_measure)
        monkeypatch.setenv("XDG_CACHE_HOME", "/should/not/be/used")

        rc = hg_gate.main(["--sizes", "5"])

        assert rc == 0
        # measure() ran under a redirected, throwaway cache dir, not the
        # caller's real XDG_CACHE_HOME.
        assert seen_during_measure["xdg"] != "/should/not/be/used"
        assert seen_during_measure["xdg"] is not None
        # And the env var is restored to its original value afterward.
        assert os.environ.get("XDG_CACHE_HOME") == "/should/not/be/used"

    def test_json_out_same_path_as_baseline_still_gates_correctly(
        self, monkeypatch, tmp_path, capsys
    ):
        # Regression guard: --baseline must be read before --json-out
        # (potentially the same file) overwrites it.
        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hg_gate,
            "measure",
            lambda sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False: [
                {"size": s, "backend": "clang", "baseline_ms": 10.0, "attach_ms": 100.0}
                for s in sizes
            ],
        )
        shared = tmp_path / "report.json"
        shared.write_text(
            json.dumps({"points": [{"size": 10, "backend": "clang", "attach_ms": 1.0}]})
        )

        rc = hg_gate.main(
            [
                "--sizes",
                "10",
                "--baseline",
                str(shared),
                "--json-out",
                str(shared),
            ]
        )
        out = capsys.readouterr().out
        # attach_ms 100.0 vs. the pre-existing baseline's 1.0 is a real,
        # detected regression -- not a comparison against the just-written
        # (and therefore self-matching) new report.
        assert rc == 1
        assert "FAIL" in out
        assert "regression" in out
        # The historical baseline on disk must survive completely untouched
        # -- not just correctly read once -- so a later run can still catch
        # the same regression (Codex review, fresh evidence: this is a
        # separate guarantee from the read-before-write ordering alone).
        assert "NOTE" in out
        assert json.loads(shared.read_text()) == {
            "points": [{"size": 10, "backend": "clang", "attach_ms": 1.0}]
        }

    def test_malformed_baseline_fails_cleanly_instead_of_crashing(
        self, monkeypatch, tmp_path, capsys
    ):
        # An unreadable/malformed --baseline previously propagated as an
        # unhandled traceback rather than a clear gate failure (CodeRabbit
        # review).
        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hg_gate,
            "measure",
            lambda sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False: [
                {"size": s, "backend": "clang", "baseline_ms": 10.0, "attach_ms": 5.0}
                for s in sizes
            ],
        )
        bad_baseline = tmp_path / "not_json.json"
        bad_baseline.write_text("{not valid json")

        rc = hg_gate.main(["--sizes", "10", "--baseline", str(bad_baseline)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL" in out
        assert str(bad_baseline) in out

    def test_missing_baseline_file_fails_cleanly(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hg_gate,
            "measure",
            lambda sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False: [
                {"size": s, "backend": "clang", "baseline_ms": 10.0, "attach_ms": 5.0}
                for s in sizes
            ],
        )
        missing = tmp_path / "does_not_exist.json"

        rc = hg_gate.main(["--sizes", "10", "--baseline", str(missing)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL" in out

    def test_require_castxml_version_error_prints_distinct_message(
        self, monkeypatch, capsys
    ):
        # A castxml *version-policy* rejection under --require-castxml must
        # print a message a caller (header-graph-regression's base-branch
        # step) can grep for specifically -- distinct from any other
        # extraction failure below, so the two are never conflated by a
        # shared message prefix (Codex review, fresh evidence).
        from abicheck.errors import UnsupportedCastxmlVersionError

        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")

        def _fake_measure(
            sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False
        ):
            raise UnsupportedCastxmlVersionError("out-of-policy castxml build")

        monkeypatch.setattr(hg_gate, "measure", _fake_measure)

        rc = hg_gate.main(["--sizes", "10", "--require-castxml"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL: castxml version rejected by this build's policy:" in out
        # And must NOT be mistakable for the other-extraction-failure message.
        assert "FAIL: header extraction failed:" not in out

    def test_require_castxml_other_snapshot_error_prints_distinct_message(
        self, monkeypatch, capsys
    ):
        # Any OTHER SnapshotError (a timeout, a crash, malformed output) is a
        # genuine extraction regression, not a version-policy mismatch -- it
        # must print a different message than the version-rejection case
        # above, so a caller string-matching on the version-specific message
        # never mistakes this for the skippable condition (Codex review,
        # fresh evidence).
        from abicheck.errors import SnapshotError

        monkeypatch.setattr(hg_gate, "_have", lambda tool: True)
        monkeypatch.setattr(sys, "platform", "linux")

        def _fake_measure(
            sizes, repeat, backends=hg_gate.BACKENDS, require_castxml=False
        ):
            raise SnapshotError("clang crashed parsing this header")

        monkeypatch.setattr(hg_gate, "measure", _fake_measure)

        rc = hg_gate.main(["--sizes", "10", "--require-castxml"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL: header extraction failed:" in out
        assert "FAIL: castxml version rejected by this build's policy:" not in out


class TestPositiveInt:
    def test_accepts_a_positive_value(self):
        assert hg_gate._positive_int("5") == 5

    def test_rejects_zero(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._positive_int("0")

    def test_rejects_negative(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._positive_int("-1")

    def test_sizes_and_repeat_reject_non_positive_values(self, capsys):
        for bad_args in (["--sizes", "0"], ["--repeat", "0"], ["--sizes", "-5"]):
            with pytest.raises(SystemExit):
                hg_gate.parse_args(bad_args)


class TestFiniteNonnegativeFloat:
    # A `nan`/`inf` --regress-tolerance neuters check_regressions() silently:
    # `current > base * (1 + nan)` is always False, and an infinite allowance
    # accepts everything -- both would print OK despite an arbitrarily
    # regressed measurement (Codex review, fresh evidence).
    def test_accepts_a_finite_nonnegative_value(self):
        assert hg_gate._finite_nonnegative_float("0.5") == 0.5

    def test_accepts_zero(self):
        assert hg_gate._finite_nonnegative_float("0") == 0.0

    def test_rejects_nan(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._finite_nonnegative_float("nan")

    def test_rejects_positive_infinity(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._finite_nonnegative_float("inf")

    def test_rejects_negative_infinity(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._finite_nonnegative_float("-inf")

    def test_rejects_negative(self):
        with pytest.raises(hg_gate.argparse.ArgumentTypeError):
            hg_gate._finite_nonnegative_float("-0.1")

    def test_regress_tolerance_flag_rejects_nan_and_inf(self):
        for bad_value in ("nan", "inf", "-inf", "-1"):
            with pytest.raises(SystemExit):
                hg_gate.parse_args(["--regress-tolerance", bad_value])

    def test_a_nan_tolerance_would_have_masked_a_real_regression(self):
        # Direct proof of the failure mode this type= guard closes: without
        # it, check_regressions() itself silently accepts an arbitrarily
        # regressed measurement under a nan/inf tolerance.
        points = [{"size": 10, "backend": "clang", "attach_ms": 1000.0}]
        baseline = {(10, "clang"): {"attach_ms": 10.0}}

        def gate(tolerance: float) -> list[str]:
            return hg_gate.check_regressions(
                points, baseline, {"attach_ms": GateThreshold(tolerance, 0.0)}
            )

        assert gate(float("nan")) == []
        assert gate(float("inf")) == []
        assert gate(0.5) != []


class TestRequireRealAstAttach:
    class _FakeGraph:
        def __init__(self, passes):
            self.extractor_passes = passes

    class _FakeBuildSource:
        def __init__(self, graph):
            self.source_graph = graph

    class _FakeSnap:
        def __init__(self, build_source):
            self.build_source = build_source

    def test_passes_when_both_passes_stamped(self):
        from abicheck.buildsource.header_graph import (
            HEADER_CALL_GRAPH_PASS,
            HEADER_INCLUDE_GRAPH_PASS,
        )

        snap = self._FakeSnap(
            self._FakeBuildSource(
                self._FakeGraph(
                    {HEADER_CALL_GRAPH_PASS: True, HEADER_INCLUDE_GRAPH_PASS: True}
                )
            )
        )
        assert hg_gate._require_real_ast_attach(snap, 5, "clang") is None

    def test_raises_when_call_graph_pass_missing(self):
        snap = self._FakeSnap(self._FakeBuildSource(self._FakeGraph({})))
        with pytest.raises(RuntimeError, match="degraded"):
            hg_gate._require_real_ast_attach(snap, 5, "castxml")

    def test_raises_when_include_graph_pass_missing(self):
        # Regression guard: the main AST parse can succeed while the
        # separate include-graph (`clang -M`) pass degrades or never runs
        # -- checking HEADER_CALL_GRAPH_PASS alone would miss this.
        from abicheck.buildsource.header_graph import HEADER_CALL_GRAPH_PASS

        snap = self._FakeSnap(
            self._FakeBuildSource(self._FakeGraph({HEADER_CALL_GRAPH_PASS: True}))
        )
        with pytest.raises(RuntimeError, match="include-graph"):
            hg_gate._require_real_ast_attach(snap, 5, "clang")

    def test_raises_when_no_build_source_at_all(self):
        snap = self._FakeSnap(None)
        with pytest.raises(RuntimeError, match="degraded"):
            hg_gate._require_real_ast_attach(snap, 5, "clang")


class TestMeasureSizeErrorHandling:
    """``_measure_size`` must only self-skip the one narrow, genuinely
    optional condition -- an out-of-policy castxml build
    (``UnsupportedCastxmlVersionError``) -- and must propagate every other
    ``SnapshotError``, on either backend: a clang failure mid-sweep is
    always a real regression, and so is a *non-version* castxml failure
    (a timeout, a crash) on an otherwise-supported install. Silently
    dropping either would let main() see only the points that did succeed
    and potentially still report a clean "OK" (Codex review, fresh
    evidence: an earlier version of this fix still caught every castxml
    SnapshotError, not just the version-gate one)."""

    def test_unsupported_castxml_version_is_skipped(self, monkeypatch):
        from abicheck.errors import UnsupportedCastxmlVersionError

        def _fake_measure_one(n, backend, repeat):
            if backend == "castxml":
                raise UnsupportedCastxmlVersionError("out-of-policy castxml build")
            return {"baseline_ms": 1.0, "attach_ms": 1.0}

        monkeypatch.setattr(hg_gate, "_measure_one", _fake_measure_one)
        points = hg_gate._measure_size(10, repeat=1, backends=("clang", "castxml"))
        assert [p["backend"] for p in points] == ["clang"]

    def test_clang_snapshot_error_propagates(self, monkeypatch):
        from abicheck.errors import SnapshotError

        def _fake_measure_one(n, backend, repeat):
            raise SnapshotError("clang crashed parsing this header")

        monkeypatch.setattr(hg_gate, "_measure_one", _fake_measure_one)
        with pytest.raises(SnapshotError, match="clang crashed"):
            hg_gate._measure_size(10, repeat=1, backends=("clang",))

    def test_non_version_castxml_snapshot_error_propagates(self, monkeypatch):
        from abicheck.errors import SnapshotError

        def _fake_measure_one(n, backend, repeat):
            raise SnapshotError("castxml timed out")

        monkeypatch.setattr(hg_gate, "_measure_one", _fake_measure_one)
        with pytest.raises(SnapshotError, match="castxml timed out"):
            hg_gate._measure_size(10, repeat=1, backends=("castxml",))

    def test_require_castxml_propagates_even_a_version_error(self, monkeypatch):
        # --require-castxml (callers that explicitly installed a pinned
        # castxml, e.g. the CI jobs) must not treat even the normally-
        # optional UnsupportedCastxmlVersionError as a skip -- there, a
        # version rejection means the pinned install/policy regressed
        # (Codex review, fresh evidence).
        from abicheck.errors import UnsupportedCastxmlVersionError

        def _fake_measure_one(n, backend, repeat):
            raise UnsupportedCastxmlVersionError("out-of-policy castxml build")

        monkeypatch.setattr(hg_gate, "_measure_one", _fake_measure_one)
        with pytest.raises(UnsupportedCastxmlVersionError):
            hg_gate._measure_size(
                10, repeat=1, backends=("castxml",), require_castxml=True
            )

    def test_require_castxml_bypasses_measures_own_presence_filter(self, monkeypatch):
        # measure()'s `active` filter normally drops "castxml" from the
        # sweep entirely when it's absent from PATH -- with
        # require_castxml=True that filter must NOT silently narrow the
        # sweep; a genuinely-absent castxml should instead surface as a
        # hard failure from _measure_size/_measure_one itself.
        monkeypatch.setattr(hg_gate, "_have", lambda tool: tool != "castxml")
        seen_backends = []

        def _fake_measure_size(n, repeat, backends, *, require_castxml=False):
            seen_backends.append(backends)
            return []

        monkeypatch.setattr(hg_gate, "_measure_size", _fake_measure_size)
        hg_gate.measure((10,), 1, require_castxml=True)
        assert "castxml" in seen_backends[0]


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
        #
        # _measure_one calls _one_pair() repeat + 1 times, not repeat times:
        # one untimed warmup pair first (its own fresh temp dir, same as
        # every timed repeat -- see _measure_one's own docstring), then the
        # `repeat` timed pairs. This was `== repeat` before the warmup pair
        # was added (PR history: 8ba5852), left stale until this regression
        # surfaced it as a real CI failure -- fix the count, not the
        # implementation, since the warmup itself is the deliberate,
        # documented behavior this test's own docstring already assumes.
        seen_dirs = []
        real_build_fixture = hg_gate._build_fixture

        def _spy(tmp_dir, n):
            seen_dirs.append(tmp_dir)
            return real_build_fixture(tmp_dir, n)

        monkeypatch.setattr(hg_gate, "_build_fixture", _spy)
        repeat = 3
        hg_gate._measure_one(3, "clang", repeat=repeat)
        assert len(seen_dirs) == repeat + 1
        assert len(set(seen_dirs)) == repeat + 1

    def test_main_report_only_run_exits_zero(self, capsys):
        rc = hg_gate.main(["--sizes", "5", "--repeat", "1"])
        assert rc == 0
        assert "report-only" in capsys.readouterr().out
