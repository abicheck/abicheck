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

    def test_non_positive_baseline_entry_is_not_counted_as_matched(self):
        # matched_points must agree with check_regressions' own "base is
        # None or base <= 0" skip -- otherwise main()'s final "N checked"
        # count would include a point check_regressions never actually
        # gated (CodeRabbit review).
        points = [self._point(10)]
        baseline = {(10, "clang"): 0.0}
        assert hg_gate.matched_points(points, baseline) == []


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
        baseline = {(10, "clang"): 10.0}
        assert hg_gate.check_regressions(points, baseline, float("nan")) == []
        assert hg_gate.check_regressions(points, baseline, float("inf")) == []
        assert hg_gate.check_regressions(points, baseline, 0.5) != []


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
