"""Unit tests for the scaling benchmark harness (``scripts/benchmark_scaling.py``).

These are fast and stdlib-only: they cover the baseline-regression comparison
logic and assert that *every* registered scenario builds and runs without error
at a tiny size (so a mis-wired scenario fails here rather than silently in CI).
The script is loaded by path because ``scripts/`` is not an installed package.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_scaling.py"
_spec = importlib.util.spec_from_file_location("benchmark_scaling", _PATH)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve ``from __future__`` annotations
# via ``sys.modules[cls.__module__]`` during class creation.
sys.modules["benchmark_scaling"] = bench
_spec.loader.exec_module(bench)

# The baseline-regression comparison functions live in the sibling
# scripts/perf_baseline.py (split out to keep benchmark_scaling.py under the
# file-size cap) — importing benchmark_scaling.py above already put
# scripts/ on sys.path as a side effect, so a plain import resolves it.
import perf_baseline  # noqa: E402


# ── Baseline regression comparison ────────────────────────────────────────────
def test_baseline_points_parses_scenarios() -> None:
    base = {"scenarios": {"add_remove": {"points": [{"size": 500, "seconds": 0.1}]}}}
    assert perf_baseline.baseline_points_from_report(base) == {("add_remove", 500): 0.1}


def test_baseline_points_tolerates_garbage() -> None:
    assert perf_baseline.baseline_points_from_report({}) == {}
    assert perf_baseline.baseline_points_from_report({"scenarios": "nope"}) == {}
    assert perf_baseline.baseline_points_from_report({"scenarios": {"x": "bad"}}) == {}
    assert (
        perf_baseline.baseline_points_from_report(
            {"scenarios": {"x": {"points": "nope"}}}
        )
        == {}
    )


def test_baseline_points_tolerates_non_dict_top_level() -> None:
    # CodeRabbit review: a syntactically valid JSON document whose top level
    # isn't an object (a bare list, a string, a number, ...) has no .get()
    # and previously raised an unhandled AttributeError instead of degrading
    # like every other malformed-shape case above.
    assert perf_baseline.baseline_points_from_report([]) == {}
    assert perf_baseline.baseline_points_from_report("nope") == {}
    assert perf_baseline.baseline_points_from_report(1) == {}
    assert perf_baseline.baseline_points_from_report(None) == {}


def test_check_regressions_flags_slowdown() -> None:
    bp = {("s", 1000): 0.2}
    msgs = bench.check_regressions([bench.Point(1000, 0.4, 1000)], "s", bp, 0.5)
    assert len(msgs) == 1
    assert "+100%" in msgs[0]


def test_check_regressions_within_tolerance_ok() -> None:
    bp = {("s", 1000): 0.2}
    # +25% is under the 50% tolerance.
    assert bench.check_regressions([bench.Point(1000, 0.25, 1000)], "s", bp, 0.5) == []


def test_check_regressions_skips_below_floor() -> None:
    # Baseline below the 0.05s noise floor → not compared even on a huge slowdown.
    bp = {("s", 1000): 0.01}
    assert bench.check_regressions([bench.Point(1000, 1.0, 1000)], "s", bp, 0.5) == []


def test_check_regressions_skips_unknown_size() -> None:
    # Size absent from the baseline (e.g. a scenario new in this PR) is skipped.
    bp = {("s", 500): 0.2}
    assert bench.check_regressions([bench.Point(1000, 5.0, 1000)], "s", bp, 0.5) == []


def test_check_regressions_combined_threshold_absolute_floor_protects_tiny_baseline() -> (
    None
):
    # A tiny baseline (well under regress-min-delta-seconds) must not flag on
    # a relatively huge but absolutely tiny slowdown.
    bp = {("s", 1000): 0.10}
    msgs = bench.check_regressions(
        [bench.Point(1000, 0.15, 1000)],  # +50% but only +0.05s
        "s",
        bp,
        0.15,
        min_delta_seconds=0.1,
    )
    assert msgs == []


def test_check_regressions_combined_threshold_still_catches_a_real_regression() -> None:
    bp = {("s", 1000): 0.10}
    msgs = bench.check_regressions(
        [bench.Point(1000, 0.30, 1000)],  # +200%, well past both floors
        "s",
        bp,
        0.15,
        min_delta_seconds=0.1,
    )
    assert len(msgs) == 1


def test_matched_baseline_points_empty_when_no_overlap() -> None:
    points = [bench.Point(1000, 0.4, 1000)]
    bp = {("s", 999): 0.2}  # different size entirely
    assert bench.matched_baseline_points(points, "s", bp) == []


def test_matched_baseline_points_returns_overlapping_subset() -> None:
    points = [bench.Point(1000, 0.4, 1000), bench.Point(2000, 0.8, 2000)]
    bp = {("s", 1000): 0.2}
    assert bench.matched_baseline_points(points, "s", bp) == [points[0]]


# ── Every scenario is wired correctly ─────────────────────────────────────────
@pytest.mark.parametrize("scenario", list(bench.SCENARIOS))
def test_every_scenario_builds_and_runs(scenario: str) -> None:
    spec = bench.SCENARIOS[scenario]
    if spec.needs_demangler and not bench._has_demangler():
        pytest.skip(f"{scenario} needs a demangler")
    size = min(spec.sizes[0], 80)
    count = spec.run(spec.build(size))
    assert isinstance(count, int)
    assert count >= 0


def test_versioned_rename_churn_exercises_scheme_collapse() -> None:
    """The ICU/OpenSSL scenario must actually hit the versioned-scheme path.

    A scenario that merely "builds and runs" is a weak guard — assert the shape
    it is meant to stress: ``~2 × n`` removed/added churn findings *and* the
    single ``versioned_symbol_scheme_detected`` collapse finding that no other
    scenario produces. If a refactor stops the scheme recogniser firing on this
    input, this fails rather than the benchmark silently measuring a cheaper
    path.
    """
    from abicheck.checker import ChangeKind, compare

    old, new = bench._build_versioned_rename_churn(200)
    # Detection (default): 2×n churn + the advisory.
    result = compare(old, new)
    kinds = [c.kind for c in result.changes]
    assert kinds.count(ChangeKind.FUNC_REMOVED) == 200
    assert kinds.count(ChangeKind.FUNC_ADDED) == 200
    assert ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED in kinds

    # The scenario's registered run enables collapse, so the suppression /
    # reclassification branch must actually fire (churn folded away). Guard it
    # here so the benchmark can't silently revert to measuring detection only.
    assert bench.SCENARIOS["versioned_rename_churn"].run is bench._run_compare_collapse
    collapsed = compare(old, new, collapse_versioned_symbols=True)
    assert len(collapsed.changes) < len(result.changes)


def test_segments_fast_path_matches_full_scan() -> None:
    """The ``_segments`` plain-name fast path must equal the char-scan result."""
    from abicheck.diff_namespaces import _segments

    # Plain names (fast path) and names that need the scan (``::`` / templates).
    assert _segments("u_strlen_75") == ["u_strlen_75"]
    assert _segments("") == []
    assert _segments("ns::experimental::sort<int>") == ["ns", "experimental", "sort"]
    assert _segments("sort<int>") == ["sort"]
    assert _segments("foo<bar>::baz") == ["foo", "baz"]


def test_measure_records_peak_memory() -> None:
    pts = bench.measure("add_remove", [50], repeat=1, track_memory=True)
    assert len(pts) == 1
    assert pts[0].peak_mb is not None
    assert pts[0].peak_mb >= 0.0


def test_measure_can_skip_memory() -> None:
    pts = bench.measure("add_remove", [50], repeat=1, track_memory=False)
    assert pts[0].peak_mb is None


def test_fuzzy_rename_churn_exercises_accept_path() -> None:
    """The fuzzy-rename scenario must emit *genuine* renames, not just reject.

    ``rename_churn`` exercises only the reject path (disjoint names → no
    ``FUNC_LIKELY_RENAMED``). This scenario is its complement: every old symbol
    has exactly one plausible partner, so the size-only matcher must emit one
    rename per pair. If a refactor breaks the accept path (or the matcher stops
    pairing them), this fails rather than the benchmark measuring a cheaper,
    no-match run. Guard the contrast with ``rename_churn`` too.
    """
    from abicheck.checker import ChangeKind, compare

    old, new = bench._build_fuzzy_rename_churn(200)
    kinds = [c.kind for c in compare(old, new).changes]
    assert kinds.count(ChangeKind.FUNC_LIKELY_RENAMED) == 200

    # rename_churn (disjoint names) emits no rename — the reject path.
    r_old, r_new = bench._build_rename_churn(200)
    r_kinds = [c.kind for c in compare(r_old, r_new).changes]
    assert ChangeKind.FUNC_LIKELY_RENAMED not in r_kinds


def test_version_node_churn_exercises_moved_nodes() -> None:
    """The LLVM-bump scenario must emit one move per symbol.

    Every export migrates ``LIB_1.0 → LIB_2.0``, so the version-node diff must
    emit ``n`` ``SYMBOL_MOVED_VERSION_NODE`` findings (the 36 991-finding LLVM
    shape, scaled down) plus the single removed-node finding — no add/remove
    churn, since the symbol names are unchanged.
    """
    from abicheck.checker import ChangeKind, compare

    old, new = bench._build_version_node_churn(200)
    kinds = [c.kind for c in compare(old, new).changes]
    assert kinds.count(ChangeKind.SYMBOL_MOVED_VERSION_NODE) == 200
    assert ChangeKind.SYMBOL_VERSION_NODE_REMOVED in kinds
    # No symbol was added or removed — only the version node moved.
    assert ChangeKind.FUNC_ADDED not in kinds
    assert ChangeKind.FUNC_REMOVED not in kinds


def test_measure_records_rss() -> None:
    pts = bench.measure("add_remove", [50], repeat=1, track_memory=True)
    if bench.resource is None:
        pytest.skip("resource module unavailable (Windows)")
    assert pts[0].rss_mb is not None
    assert pts[0].rss_mb > 0.0


def test_exponent_gate_exempts_inherently_superlinear() -> None:
    """``nested_types`` is exempt from the exponent gate; everything else is not."""
    assert bench.SCENARIOS["nested_types"].gate_exponent is False
    assert bench.SCENARIOS["add_remove"].gate_exponent is True
    assert bench.SCENARIOS["fuzzy_rename_churn"].gate_exponent is True


def test_check_exponent_gate_flags_quadratic() -> None:
    # Over budget with a meaningful (above-floor) timing → flagged.
    assert bench._check_exponent_gate("s", 2.0, 1.4, peak_seconds=1.0)
    assert bench._check_exponent_gate("s", 1.1, 1.4, peak_seconds=1.0) == []  # linear
    assert bench._check_exponent_gate("s", None, 1.4, peak_seconds=1.0) == []  # no data


def test_check_exponent_gate_skips_sub_floor_noise() -> None:
    """A sub-50 ms scenario's exponent is noise — never gated, even if huge."""
    assert bench._check_exponent_gate("s", 5.0, 1.4, peak_seconds=0.001) == []
    # Default peak_seconds is inf, so omitting it keeps the old (always-gate)
    # behaviour for callers that don't pass timings.
    assert bench._check_exponent_gate("s", 5.0, 1.4)


def test_check_rss_gate_flags_over_budget() -> None:
    over = [bench.Point(1000, 0.1, 1000, rss_mb=900.0)]
    under = [bench.Point(1000, 0.1, 1000, rss_mb=100.0)]
    assert len(bench._check_rss_gate("s", over, 512.0)) == 1
    assert bench._check_rss_gate("s", under, 512.0) == []
    # No RSS data (e.g. Windows) → never gates.
    assert bench._check_rss_gate("s", [bench.Point(1000, 0.1, 1000)], 1.0) == []


def test_sizes_and_repeat_reject_non_positive_values() -> None:
    # CodeRabbit review: a plain `type=int` let --repeat 0 through, leaving
    # measure() an empty samples list and crashing with an unhandled
    # ValueError from summarize_samples() instead of a clean argparse usage
    # error. --sizes shares the same positive_int_arg validator for the
    # identical reason check_header_graph_perf.py's own --sizes already had.
    for bad_args in (
        ["--sizes", "0"],
        ["--repeat", "0"],
        ["--sizes", "-5"],
        ["--repeat", "-1"],
    ):
        with pytest.raises(SystemExit):
            bench.parse_args(bad_args)


def test_sizes_and_repeat_accept_positive_values() -> None:
    args = bench.parse_args(["--sizes", "10", "20", "--repeat", "3"])
    assert args.sizes == [10, 20]
    assert args.repeat == 3


def test_regress_flags_reject_non_finite_and_negative_values() -> None:
    # Codex review, fresh evidence: a plain `type=float` let
    # --regress-tolerance/--regress-min-delta-seconds through as nan/inf (or
    # an overflowing literal like "1e309", which float() also parses as
    # inf) -- combined_regression_threshold()'s allowed delta then becomes
    # infinite (or the comparison against it always False), silently
    # reporting every regression as a pass. Both flags now share
    # check_header_graph_perf.py's own finite/non-negative validator via
    # perf_measurement.finite_nonnegative_float_arg.
    for flag in ("--regress-tolerance", "--regress-min-delta-seconds"):
        for bad_value in ("nan", "inf", "-inf", "1e309", "-0.1"):
            with pytest.raises(SystemExit):
                bench.parse_args([flag, bad_value])


def test_regress_flags_accept_finite_nonnegative_values() -> None:
    args = bench.parse_args(
        ["--regress-tolerance", "0.15", "--regress-min-delta-seconds", "0.1"]
    )
    assert args.regress_tolerance == pytest.approx(0.15)
    assert args.regress_min_delta_seconds == pytest.approx(0.1)


# ── Threshold precedence (the indefinite-exception fix) ───────────────────────
class TestScenarioThresholdPrecedence:
    """A built-in per-scenario allowance may never outrank an explicit request.

    The defect: ``Scenario.regress_tolerance`` was consulted *first*, so a
    scenario carrying a built-in ``1.3`` ran at 1.3 even when the caller
    passed ``--regress-tolerance 0.1``, and the run still printed ``OK`` with
    nothing in its output revealing which number had actually judged it.

    Driven by constructed numbers rather than real timings on purpose: what
    needs pinning is the precedence algebra and its reported provenance, not
    how long serialization takes.
    """

    def _resolve(self, **kw):
        kw.setdefault("scenario", "serialize")
        kw.setdefault("default_tolerance", 0.5)
        kw.setdefault("default_min_delta", 0.0)
        kw.setdefault("cli_tolerance", None)
        kw.setdefault("cli_min_delta", None)
        return perf_baseline.resolve_scenario_threshold(**kw)

    def test_explicit_cli_tolerance_beats_a_builtin_exception(self):
        resolved = self._resolve(cli_tolerance=0.1, spec_tolerance=1.3)
        assert resolved.tolerance == 0.1
        assert resolved.source == "explicit"

    def test_explicit_cli_min_delta_beats_a_builtin_exception(self):
        resolved = self._resolve(cli_min_delta=0.2, spec_min_delta=5.0)
        assert resolved.min_delta == 0.2
        assert resolved.source == "explicit"

    def test_stating_only_a_tolerance_still_marks_the_whole_threshold_explicit(self):
        # The provenance label describes the decision, not one field: a reader
        # must not have to guess whether the min_delta half was also overridden.
        resolved = self._resolve(cli_tolerance=0.1, spec_min_delta=5.0)
        assert resolved.source == "explicit"
        assert resolved.min_delta == 5.0  # the spec value, since none was stated

    def test_a_builtin_exception_applies_when_nothing_was_stated(self):
        resolved = self._resolve(spec_tolerance=1.3)
        assert resolved.tolerance == 1.3
        assert resolved.source == "scenario_default:serialize"

    def test_the_source_label_names_the_scenario_it_came_from(self):
        # So a receipt reader can tell which scenario's exception is in play.
        assert self._resolve(
            scenario="report_html", spec_tolerance=2.0
        ).source.endswith("report_html")

    def test_the_plain_default_is_labelled_as_such(self):
        resolved = self._resolve()
        assert (resolved.tolerance, resolved.min_delta, resolved.source) == (
            0.5,
            0.0,
            "default",
        )

    def test_a_zero_explicit_tolerance_is_honored_not_treated_as_absent(self):
        # `0.0` is falsy; an `or`-based fallback would silently discard the
        # strictest possible request. This is the exact shape of bug the
        # None-sentinel design exists to prevent.
        resolved = self._resolve(cli_tolerance=0.0, spec_tolerance=1.3)
        assert resolved.tolerance == 0.0
        assert resolved.source == "explicit"

    def test_no_scenario_ships_an_indefinite_tolerance_exception(self):
        # The registry-level half: the `serialize` scenario's permanent 130%
        # allowance for an already-completed one-time data-model cost is gone.
        # An exception added later is not forbidden -- but it must be a
        # deliberate, reviewed edit that trips this test, not something that
        # can drift in unnoticed.
        assert {
            name: spec.regress_tolerance
            for name, spec in bench.SCENARIOS.items()
            if spec.regress_tolerance is not None
        } == {}


class TestApplyRegressionGateRecordsItsThreshold:
    """The resolved threshold must reach the report, not just the comparison."""

    def test_the_effective_threshold_is_recorded(self):
        record: dict = {}
        perf_baseline.apply_regression_gate(
            [bench.Point(1000, 0.1, 1000)],
            "serialize",
            {("serialize", 1000): 0.1},
            cli_tolerance=0.25,
            cli_min_delta=None,
            record_into=record,
        )
        assert record["effective_threshold"] == {
            "tolerance": 0.25,
            "min_delta": 0.0,
            "source": "explicit",
        }

    def test_it_gates_on_the_threshold_it_recorded(self):
        # The invariant that makes recording worth anything: the number in the
        # receipt is the number that judged the run. A 0.1 tolerance must fail
        # a 0.4s-vs-0.2s point, and the receipt must say 0.1.
        record: dict = {}
        failures = perf_baseline.apply_regression_gate(
            [bench.Point(1000, 0.4, 1000)],
            "serialize",
            {("serialize", 1000): 0.2},
            cli_tolerance=0.1,
            cli_min_delta=None,
            spec_tolerance=1.3,  # the old exception, which must not apply
            record_into=record,
        )
        assert failures, "an explicit strict tolerance must still gate"
        assert record["effective_threshold"]["tolerance"] == 0.1

    def test_recording_is_optional(self):
        assert (
            perf_baseline.apply_regression_gate(
                [bench.Point(1000, 0.1, 1000)],
                "serialize",
                {("serialize", 1000): 0.1},
                cli_tolerance=None,
                cli_min_delta=None,
            )
            == []
        )


class TestStatedOrDefault:
    """``value or default`` is wrong for a threshold, because ``0.0`` is falsy.

    ``--regress-tolerance 0`` took the 0.5 default in the one place that
    *printed* the tolerance while ``apply_regression_gate`` correctly gated at
    ``0.0``, so the printed number contradicted the number that judged the run --
    the same class of defect this branch removed from the per-scenario override.
    """

    @pytest.mark.parametrize("value", [0.0, 0, 0.1, 1.0, 99.0])
    def test_any_stated_value_including_zero_survives(self, value):
        assert perf_baseline.stated_or_default(value, 0.5) == value

    def test_only_none_falls_back(self):
        assert perf_baseline.stated_or_default(None, 0.5) == 0.5

    def test_load_baseline_prints_the_stated_zero_not_the_default(
        self, tmp_path, capsys
    ):
        report = tmp_path / "base.json"
        report.write_text(
            json.dumps(
                {"scenarios": {"s": {"points": [{"size": 10, "seconds": 1.0}]}}}
            ),
            encoding="utf-8",
        )
        perf_baseline.load_baseline(report, 0.0)
        printed = capsys.readouterr().out
        assert "tolerance 0%" in printed, printed
        assert "50%" not in printed

    def test_load_baseline_prints_the_default_when_unstated(self, tmp_path, capsys):
        report = tmp_path / "base.json"
        report.write_text(
            json.dumps(
                {"scenarios": {"s": {"points": [{"size": 10, "seconds": 1.0}]}}}
            ),
            encoding="utf-8",
        )
        perf_baseline.load_baseline(report, None)
        out = capsys.readouterr().out
        assert (
            f"tolerance {perf_baseline.DEFAULT_REGRESS_TOLERANCE * 100:.0f}%" in out
        ), out


# ── Baseline MEMORY regression gate ───────────────────────────────────────────
#
# The memory counterpart of the timing baseline gate above. It exists because
# `benchmark_scaling.py` recorded `peak_mb` and gated it only against absolute
# ceilings (`--max-memory-mb` / `--max-rss-mb`): an absolute ceiling catches a
# regression only once it crosses the ceiling, so a change doubling a
# scenario's allocation from 200 MiB to 400 MiB passed a 2048 MiB ceiling in
# silence. The timing side already had a base-branch comparison for exactly
# that gradual drift; the memory side had none.


class _MemPoint:
    """Minimal stand-in for ``benchmark_scaling.Point``'s measured fields."""

    def __init__(self, size: int, peak_mb: float | None) -> None:
        self.size = size
        self.peak_mb = peak_mb


def _mem_baseline(**by_size: float) -> dict[tuple[str, int], float]:
    return {("serialize", int(size)): mb for size, mb in by_size.items()}


def test_memory_baseline_points_parse_peak_mb() -> None:
    report = {
        "scenarios": {
            "serialize": {"points": [{"size": 2000, "seconds": 2.9, "peak_mb": 85.3}]}
        }
    }
    assert perf_baseline.baseline_points_from_report(report, field="peak_mb") == {
        ("serialize", 2000): 85.3
    }
    # The same parser still reads timings, so the two cannot drift apart.
    assert perf_baseline.baseline_points_from_report(report) == {
        ("serialize", 2000): 2.9
    }


def test_null_peak_mb_is_not_measured_rather_than_zero() -> None:
    """A ``--no-memory`` baseline must not read back as "allocated nothing".

    If a null collapsed to 0.0, every current point would regress against it
    by an infinite ratio -- a gate that fails every run is as useless as one
    that fails none, and it would make the gate impossible to introduce
    against any pre-existing baseline.
    """
    report = {
        "scenarios": {
            "serialize": {
                "points": [
                    {"size": 2000, "seconds": 2.9, "peak_mb": None},
                    {"size": 4000, "seconds": 6.5},
                    {"size": 8000, "seconds": 9.9, "peak_mb": "not-a-number"},
                ]
            }
        }
    }
    assert perf_baseline.baseline_points_from_report(report, field="peak_mb") == {}


def test_memory_regression_is_reported() -> None:
    msgs = perf_baseline.check_memory_regressions(
        [_MemPoint(2000, 170.0)], "serialize", _mem_baseline(**{"2000": 85.0}), 0.20
    )
    assert len(msgs) == 1
    assert "170.0 MiB vs baseline 85.0 MiB" in msgs[0]


def test_memory_within_tolerance_is_not_reported() -> None:
    """The negative control: growth inside the allowance must stay silent.

    Without this, a gate that flagged *every* comparison would pass the
    regression test above and still be worthless.
    """
    assert (
        perf_baseline.check_memory_regressions(
            [_MemPoint(2000, 95.0)], "serialize", _mem_baseline(**{"2000": 85.0}), 0.20
        )
        == []
    )


def test_memory_improvement_is_never_a_regression() -> None:
    assert (
        perf_baseline.check_memory_regressions(
            [_MemPoint(2000, 40.0)], "serialize", _mem_baseline(**{"2000": 85.0}), 0.20
        )
        == []
    )


@pytest.mark.parametrize(
    "base_mb,current_mb,expect_flagged",
    [
        # Relative arm dominates once the baseline is large: 20% of 100 = 20 MiB.
        (100.0, 119.0, False),
        (100.0, 121.0, True),
        # Absolute arm dominates on a small baseline: max(20% of 10, 4) = 4 MiB.
        (10.0, 13.0, False),
        (10.0, 15.0, True),
        # Below the floor, nothing is comparable at all.
        (4.0, 400.0, False),
    ],
)
def test_threshold_is_the_larger_of_the_relative_and_absolute_arms(
    base_mb: float, current_mb: float, expect_flagged: bool
) -> None:
    """The rule is ``max(tolerance x baseline, min_delta_mb)``, both arms live.

    Enumerated on both sides of each arm's crossover rather than asserted on
    one example, so a change that silently drops an arm (using only the
    percentage, or only the absolute floor) is caught: dropping the absolute
    arm passes rows 1-2 and fails row 3; dropping the relative arm does the
    reverse. The expectation is derived from the documented rule here, not
    from the implementation's own helper.
    """
    flagged = perf_baseline.check_memory_regressions(
        [_MemPoint(2000, current_mb)],
        "serialize",
        {("serialize", 2000): base_mb},
        0.20,
        min_delta_mb=4.0,
        floor_mb=8.0,
    )
    assert bool(flagged) is expect_flagged


def test_unmeasured_current_point_is_skipped_not_flagged() -> None:
    assert (
        perf_baseline.check_memory_regressions(
            [_MemPoint(2000, None)], "serialize", _mem_baseline(**{"2000": 85.0}), 0.20
        )
        == []
    )


def test_scenario_absent_from_baseline_is_skipped() -> None:
    assert (
        perf_baseline.check_memory_regressions(
            [_MemPoint(9999, 500.0)], "serialize", _mem_baseline(**{"2000": 85.0}), 0.20
        )
        == []
    )


def test_matched_memory_points_counts_only_comparable_ones() -> None:
    """A baseline sharing zero comparable points is a gate that checked nothing.

    The caller turns a zero count into a hard failure, the same way the timing
    side already does, so this count is what stands between "passed" and
    "passed without comparing anything".
    """
    baseline = {("serialize", 2000): 85.0, ("serialize", 4000): 2.0}
    points = [
        _MemPoint(2000, 90.0),  # comparable
        _MemPoint(4000, 90.0),  # baseline below the floor
        _MemPoint(8000, 90.0),  # absent from the baseline
        _MemPoint(2000, None),  # not measured this run
    ]
    assert (
        perf_baseline.matched_memory_baseline_points(points, "serialize", baseline) == 1
    )


def test_memory_defaults_are_tighter_than_the_timing_defaults() -> None:
    """An allocation count is far less noisy than a wall-clock duration.

    Pinned so the memory tolerance cannot be quietly relaxed to the timing
    one, which would make the gate miss the 20-50% growths it exists to catch.
    """
    assert (
        perf_baseline.DEFAULT_REGRESS_MEMORY_TOLERANCE
        < perf_baseline.DEFAULT_REGRESS_TOLERANCE
    )
    assert perf_baseline.DEFAULT_REGRESS_MIN_DELTA_MB > 0
    assert perf_baseline.DEFAULT_MEMORY_FLOOR_MB > 0


# ── The memory gate is actually wired into CI ─────────────────────────────────
#
# A gate that exists only as a script flag gates nothing. These assert the
# workflow really invokes it, which is the half that a unit test of
# `check_memory_regressions` cannot reach — and the half AGENTS.md's
# "clone with a different name" audit found genuinely untested for an
# adjacent `performance.yml` claim.


def _performance_workflow() -> dict:
    import yaml

    root = pathlib.Path(__file__).resolve().parents[1]
    return yaml.safe_load(
        (root / ".github" / "workflows" / "performance.yml").read_text()
    )


def _step_run_text(job: dict) -> str:
    """Every `run:` body in *job*, with YAML comments stripped.

    Comments are stripped because this file *documents* the flags it rejects
    (`--no-memory` on the timing job, and why memory is not gated there). A
    raw substring search would find those in prose and report the opposite of
    the truth -- the exact mistake a prior review round made asserting against
    this same workflow's text.
    """
    out = []
    for step in job.get("steps", []):
        body = step.get("run")
        if not isinstance(body, str):
            continue
        out.extend(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
    return "\n".join(out)


def test_memory_regression_job_exists_and_gates_on_a_baseline() -> None:
    jobs = _performance_workflow()["jobs"]
    assert "memory-regression" in jobs, (
        "the memory gate has no CI job, so nothing runs it on a PR"
    )
    runs = _step_run_text(jobs["memory-regression"])
    assert "--baseline" in runs, "memory job never compares against a base measurement"
    assert "--regress-memory-tolerance" in runs
    assert "--regress-min-delta-mb" in runs


def test_memory_regression_job_actually_measures_memory() -> None:
    """--no-memory would make the whole job vacuous: every peak would be None,
    the gate would compare nothing, and the job would pass unconditionally."""
    runs = _step_run_text(_performance_workflow()["jobs"]["memory-regression"])
    assert "--no-memory" not in runs


def test_memory_regression_job_does_not_also_gate_timing() -> None:
    """Timings taken under tracemalloc are distorted by construction.

    Gating them here would fail PRs for an artifact of the instrument, so the
    timing tolerance is explicitly neutralised rather than left at its
    default.
    """
    runs = _step_run_text(_performance_workflow()["jobs"]["memory-regression"])
    assert "--regress-tolerance 100" in runs


def test_memory_regression_job_is_not_advisory() -> None:
    """`continue-on-error` would make this report-only, not a gate."""
    job = _performance_workflow()["jobs"]["memory-regression"]
    assert job.get("continue-on-error") in (None, False)
    for step in job.get("steps", []):
        assert step.get("continue-on-error") in (None, False)


def test_timing_regression_job_still_excludes_memory() -> None:
    """The two jobs must stay split.

    If someone 'simplifies' by dropping --no-memory from the timing job, its
    measurements silently acquire the lru_cache-clearing bias that flag exists
    to remove -- which would corrupt the timing gate rather than improve the
    memory one.
    """
    runs = _step_run_text(_performance_workflow()["jobs"]["regression"])
    assert "--no-memory" in runs


# ── Non-finite baseline values, and a memory gate that compared nothing ──────
#
# Both are "the gate ran and could not fail" defects (CodeRabbit, PR #1323),
# which is the failure mode this repo's own guidance treats as worse than a
# gate that is absent: absence is visible, a vacuous pass is not.


def test_non_finite_baseline_measurements_are_dropped() -> None:
    """`json.loads` accepts `Infinity`/`NaN`, and neither can ever gate.

    An infinite baseline makes every finite head value an infinitely large
    *improvement*; every comparison against NaN is False. Both read as "no
    regression" for any input a run could produce, so such a point must be
    absent rather than present-and-unfailable.
    """
    document = json.loads(
        '{"scenarios":{"s":{"points":['
        '{"size":1,"seconds":Infinity,"peak_mb":Infinity},'
        '{"size":2,"seconds":NaN,"peak_mb":NaN},'
        '{"size":3,"seconds":0.5,"peak_mb":50.0}]}}}'
    )
    assert perf_baseline.baseline_points_from_report(document) == {("s", 3): 0.5}
    assert perf_baseline.baseline_points_from_report(document, field="peak_mb") == {
        ("s", 3): 50.0
    }


@pytest.mark.parametrize("bad", ["Infinity", "-Infinity", "NaN"])
def test_a_non_finite_baseline_point_cannot_silently_absorb_a_regression(
    bad: str,
) -> None:
    """The consequence, asserted rather than inferred from the parser.

    Before the fix each of these parsed through and then reported no
    regression against a head value hundreds of times larger.
    """
    document = json.loads(
        '{"scenarios":{"s":{"points":[{"size":1,"seconds":' + bad + "}]}}}"
    )
    points = perf_baseline.baseline_points_from_report(document)

    class _P:
        size = 1
        seconds = 10_000.0

    # The point is gone, so there is nothing to compare -- which the
    # zero-overlap checks then catch, rather than a false "OK".
    assert points == {}
    assert perf_baseline.check_regressions([_P()], "s", points, 0.15) == []
    assert perf_baseline.matched_baseline_points([_P()], "s", points) == []


def test_finite_negative_baselines_are_kept_for_the_floors_to_handle() -> None:
    """Dropping non-finite values must not also drop merely odd ones."""
    document = json.loads('{"scenarios":{"s":{"points":[{"size":1,"seconds":-2.0}]}}}')
    assert perf_baseline.baseline_points_from_report(document) == {("s", 1): -2.0}


def test_total_memory_points_compared_sums_across_scenarios() -> None:
    assert (
        perf_baseline.total_memory_points_compared(
            {
                "scenarios": {
                    "a": {"memory_regression": {"compared_points": 3}},
                    "b": {"memory_regression": {"compared_points": 2}},
                }
            }
        )
        == 5
    )


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"scenarios": {}},
        {"scenarios": "not-a-mapping"},
        {"scenarios": {"a": "not-a-mapping"}},
        {"scenarios": {"a": {}}},
        {"scenarios": {"a": {"memory_regression": {}}}},
        {"scenarios": {"a": {"memory_regression": {"compared_points": True}}}},
        {"scenarios": {"a": {"memory_regression": {"compared_points": "3"}}}},
    ],
)
def test_total_memory_points_compared_reads_zero_for_anything_unusable(
    report: dict,
) -> None:
    """Zero is what the caller turns into a hard failure, so every shape that
    does not carry a real count must read as zero rather than as a pass.

    ``True`` is listed because ``bool`` is an ``int`` subclass: counting it
    would let a malformed report claim one comparison it never made.
    """
    assert perf_baseline.total_memory_points_compared(report) == 0


def test_memory_gate_failure_on_zero_overlap_is_wired_into_main() -> None:
    """A gate that compared nothing must fail, not report OK.

    `matched_memory_baseline_points` existed from the start, but nothing
    aggregated it: `_run_scenario` returned only the timing overlap and
    `main()` checked only that, so a memory baseline whose scenarios or sizes
    stopped lining up with the run produced zero comparisons and passed. This
    asserts the wiring, which is the half a unit test of the counter cannot
    reach.
    """
    source = pathlib.Path(bench.__file__).read_text()
    assert "total_memory_points_compared(report) == 0" in source, (
        "main() no longer fails on a memory gate that compared nothing"
    )
