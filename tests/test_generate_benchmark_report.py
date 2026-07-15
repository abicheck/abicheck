# SPDX-License-Identifier: Apache-2.0
"""Benchmark-report generator: pure-logic checks for
``scripts/generate_benchmark_report.py``.

Parses the *real* committed ``docs/reference/tool-comparison.md`` so a
wording change that breaks ``DOC_HEADING_RE``/``parse_doc_table`` is caught
here, not only when someone happens to run ``--check`` locally. The drift-diff
and Markdown-rendering logic are otherwise exercised against synthetic data.
No compiler/castxml/abidiff — the actual benchmark run is exercised
separately by ``scripts/benchmark_comparison.py``'s own external-tool-gated
lanes (``integration``/``libabigail``/``abicc`` markers).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_GBR_PATH = _REPO / "scripts" / "generate_benchmark_report.py"
_GT_PATH = _REPO / "examples" / "ground_truth.json"

_spec = importlib.util.spec_from_file_location("generate_benchmark_report", _GBR_PATH)
assert _spec and _spec.loader
gbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gbr)

_GT_VERDICTS = json.loads(_GT_PATH.read_text(encoding="utf-8"))["verdicts"]
_GT_CASE_COUNT = len(_GT_VERDICTS)
_GT_CASE_NAMES = sorted(_GT_VERDICTS)


def test_repo_dir_is_on_sys_path_for_uninstalled_checkouts() -> None:
    """bc._collect_metadata()'s `from abicheck import __version__` must resolve
    the in-tree package even when abicheck hasn't been pip-installed yet —
    otherwise abicheck_version silently degrades to "unknown", losing one of
    the reproducibility pins this report exists to guarantee."""
    import sys

    assert str(gbr.REPO_DIR) in sys.path


def test_timeout_override_propagates_to_every_lane() -> None:
    """A single --timeout must bound every lane's per-call timeout, not just
    abicheck/abidiff — benchmark_comparison.py tracks abicheck_full and ABICC
    on separate Namespace fields with their own (larger) defaults."""
    args = gbr.parse_args(["--timeout", "5"])
    bc_args = gbr._build_bc_args(args)
    assert bc_args.timeout == 5
    assert bc_args.abicheck_full_timeout == 5
    assert bc_args.abicc_timeout == 5


def test_no_timeout_override_keeps_lane_defaults() -> None:
    args = gbr.parse_args([])
    bc_args = gbr._build_bc_args(args)
    default_bc_args = gbr.bc.parse_args([])
    assert bc_args.timeout == default_bc_args.timeout
    assert bc_args.abicheck_full_timeout == default_bc_args.abicheck_full_timeout
    assert bc_args.abicc_timeout == default_bc_args.abicc_timeout


def test_parse_doc_table_finds_heading_in_committed_doc() -> None:
    text = gbr.DOC_PATH.read_text(encoding="utf-8")
    table = gbr.parse_doc_table(text)
    assert table is not None, (
        "DOC_HEADING_RE no longer matches docs/reference/tool-comparison.md's "
        "'Full-catalog benchmark' heading — update the regex in "
        "generate_benchmark_report.py"
    )
    assert table["case_count"] > 0
    assert set(table["rows"]) == set(gbr.LANE_DOC_LABELS)
    for row in table["rows"].values():
        assert row["correct"] >= 0
        assert 0.0 <= row["pct"] <= 100.0


def test_parse_doc_table_missing_heading_returns_none() -> None:
    assert gbr.parse_doc_table("no benchmark section in this text") is None


def test_diff_against_doc_reports_missing_heading() -> None:
    report = {"full_catalog_run": True, "coverage_accuracy": {}}
    drift = gbr.diff_against_doc(report, None)
    assert len(drift) == 1
    assert "could not find" in drift[0]


def test_diff_against_doc_flags_case_count_drift_against_real_doc() -> None:
    """Regression guard for the exact bug this tool exists to catch: the doc's
    heading case-count silently falling behind examples/ground_truth.json."""
    text = gbr.DOC_PATH.read_text(encoding="utf-8")
    table = gbr.parse_doc_table(text)
    assert table is not None
    report = {"full_catalog_run": False, "coverage_accuracy": {}}
    drift = gbr.diff_against_doc(report, table)
    if table["case_count"] != _GT_CASE_COUNT:
        assert any(
            str(table["case_count"]) in line and str(_GT_CASE_COUNT) in line
            for line in drift
        )
    else:
        assert drift == []


def _all_lanes_rows(**overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A doc_table['rows'] dict with every known lane present (no missing-row
    drift), so tests can focus on one lane's numeric behavior."""
    rows = {
        name: {"correct": 0, "pct": 0.0, "false_positives": 0, "false_negatives": 0}
        for name in gbr.LANE_DOC_LABELS
    }
    rows.update(overrides)
    return rows


def test_diff_against_doc_flags_missing_row() -> None:
    """A row silently deleted or relabeled in the doc must be caught, even on
    a partial run — this is a doc-authoring bug, independent of what a given
    benchmark invocation happened to cover."""
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(),
    }
    del doc_table["rows"]["abidiff"]
    report = {"full_catalog_run": False, "coverage_accuracy": {}}
    drift = gbr.diff_against_doc(report, doc_table)
    assert any("abidiff" in line and "missing" in line for line in drift)


def test_diff_against_doc_partial_run_skips_numeric_rows() -> None:
    """A --cases smoke run must never be diffed against full-catalog doc
    numbers — that would always spuriously "drift" and defeat the check."""
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(
            abicheck={"correct": 999, "pct": 12.3, "false_positives": 9, "false_negatives": 9}
        ),
    }
    report = {
        "full_catalog_run": False,
        "coverage_accuracy": {
            "abicheck": {"label": "abicheck", "correct": 1, "total": 1, "pct": 100.0,
                         "false_positives": 0, "false_negatives": 0},
        },
    }
    assert gbr.diff_against_doc(report, doc_table) == []


def _full_run_report(**coverage_overrides: dict[str, Any]) -> dict[str, Any]:
    """A full-catalog report with matching data for every documented lane and
    every ground-truth case — the "nothing missing" baseline for drift tests."""
    coverage_accuracy = {
        name: {"label": name, "correct": 0, "total": _GT_CASE_COUNT, "pct": 0.0,
                "false_positives": 0, "false_negatives": 0}
        for name in gbr.LANE_DOC_LABELS
    }
    coverage_accuracy.update(coverage_overrides)
    return {
        "full_catalog_run": True,
        "case_names": list(_GT_CASE_NAMES),
        "coverage_accuracy": coverage_accuracy,
    }


def test_diff_against_doc_matches_when_numbers_agree() -> None:
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(
            abicheck={"correct": 3, "pct": 100.0, "false_positives": 0, "false_negatives": 0}
        ),
    }
    report = _full_run_report(
        abicheck={"label": "abicheck", "correct": 3, "total": 3, "pct": 100.0,
                  "false_positives": 0, "false_negatives": 0},
    )
    assert gbr.diff_against_doc(report, doc_table) == []


def test_diff_against_doc_flags_lane_with_no_data_on_full_run() -> None:
    """A full-catalog run where a documented lane has neither live nor frozen
    data must be drift, not a silent "OK" — see the case where stale/deleted
    frozen-competitor data would otherwise let --check pass unverified."""
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(),
    }
    report = _full_run_report()
    del report["coverage_accuracy"]["abidiff"]
    drift = gbr.diff_against_doc(report, doc_table)
    assert any("abidiff" in line and "no fresh or frozen data" in line for line in drift)


def test_diff_against_doc_flags_incomplete_case_coverage_on_full_run() -> None:
    """A full-catalog run must actually cover every case in ground_truth.json,
    not just the right *count* — a same-count-different-set drift (one case
    swapped for another) must not slip through as "matches"."""
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(),
    }
    report = _full_run_report()
    report["case_names"] = report["case_names"][1:] + ["case_not_in_ground_truth"]
    drift = gbr.diff_against_doc(report, doc_table)
    assert any("missing" in line and "ground_truth.json" in line for line in drift)
    assert any("not in ground_truth.json" in line for line in drift)


def test_diff_against_doc_flags_numeric_mismatch() -> None:
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": _all_lanes_rows(
            abicheck={"correct": 3, "pct": 100.0, "false_positives": 0, "false_negatives": 0}
        ),
    }
    report = {
        "full_catalog_run": True,
        "coverage_accuracy": {
            "abicheck": {"label": "abicheck", "correct": 2, "total": 3, "pct": 66.7,
                         "false_positives": 0, "false_negatives": 1},
        },
    }
    drift = gbr.diff_against_doc(report, doc_table)
    assert any("correct" in line for line in drift)
    assert any("false negatives" in line for line in drift)


def test_status_counts_tallies_non_verdict_outcomes() -> None:
    results = [
        {"case": "case01", "abicheck": "BREAKING"},
        {"case": "case02", "abicheck": "ERROR"},
        {"case": "case03", "abicheck": "ERROR"},
        {"case": "case04", "abicheck": "TIMEOUT"},
    ]
    counts = gbr._status_counts(results, "abicheck")
    assert counts == {"ERROR": 2, "TIMEOUT": 1}


def test_cache_state_for_live_tool_is_live() -> None:
    bc_args = gbr.bc.parse_args([])
    bc_args.tools = ["abicheck"]
    state = gbr.cache_state_for(bc_args, ["abicheck"])
    assert state["abicheck"] == "live"


def test_cache_state_for_absent_tool_is_n_a() -> None:
    bc_args = gbr.bc.parse_args([])
    bc_args.tools = ["abicheck"]
    state = gbr.cache_state_for(bc_args, ["a_tool_that_does_not_exist"])
    assert state["a_tool_that_does_not_exist"] == "n/a"


def test_display_path_is_relative_inside_repo() -> None:
    expected = str(Path("docs", "reference", "tool-comparison.md"))
    assert gbr._display_path(gbr.DOC_PATH) == expected


def test_display_path_falls_back_outside_repo() -> None:
    outside = Path("/tmp/somewhere/else/report.md")
    assert gbr._display_path(outside) == str(outside)


def test_render_markdown_includes_key_fields() -> None:
    report = {
        "generated_at": "2099-01-01T00:00:00Z",
        "abicheck_version": "0.0.0-test",
        "git_commit": "deadbeefcafe",
        "ground_truth_sha256": "0123456789ab",
        "case_count": 1,
        "full_catalog_run": False,
        "wall_time_s": 1.2,
        "peak_rss_mb": 42.0,
        "tool_versions": {"gcc": "gcc 13"},
        "accuracy": {"abicheck": {"total_ms": 500}},
        "coverage_accuracy": {
            "abicheck": {"label": "abicheck", "correct": 1, "total": 1, "pct": 100.0,
                         "false_positives": 0, "false_negatives": 0},
        },
        "status_counts": {"abicheck": {}},
    }
    md = gbr.render_markdown(report, {"abicheck": "live"})
    assert "deadbeefcafe" in md
    assert "42.0 MiB" in md
    assert "abicheck" in md
    assert "100.0%" in md


def test_render_markdown_round_trips_through_parse_and_diff() -> None:
    """Regression guard for the exact workflow the doc note recommends: paste
    render_markdown()'s table over the doc's table, then --check it later.
    parse_doc_table() must recognize the pasted table (same label strings,
    same column layout) and diff_against_doc() must find no drift against the
    report that produced it."""
    coverage_accuracy = {
        name: {"label": name, "correct": 5, "total": _GT_CASE_COUNT, "pct": 83.3,
                "false_positives": 1, "false_negatives": 0}
        for name in gbr.LANE_DOC_LABELS
    }
    report = {
        "generated_at": "2099-01-01T00:00:00Z",
        "abicheck_version": "0.0.0-test",
        "git_commit": "cafefeedface",
        "ground_truth_sha256": "abc123",
        "case_count": _GT_CASE_COUNT,
        "full_catalog_run": True,
        "case_names": list(_GT_CASE_NAMES),
        "wall_time_s": 3.0,
        "peak_rss_mb": 10.0,
        "tool_versions": {},
        "accuracy": {name: {"total_ms": 100} for name in gbr.LANE_DOC_LABELS},
        "coverage_accuracy": coverage_accuracy,
        "status_counts": {name: {} for name in gbr.LANE_DOC_LABELS},
    }
    cache_state = {name: "live" for name in gbr.LANE_DOC_LABELS}
    md = gbr.render_markdown(report, cache_state)

    pasted_doc = f"## Full-catalog benchmark (2099-01-01, all {_GT_CASE_COUNT} cases)\n\n{md}"
    table = gbr.parse_doc_table(pasted_doc)
    assert table is not None, "parse_doc_table() could not read render_markdown()'s own output back"
    assert set(table["rows"]) == set(gbr.LANE_DOC_LABELS)
    assert gbr.diff_against_doc(report, table) == []
