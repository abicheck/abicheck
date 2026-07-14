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

_REPO = Path(__file__).resolve().parent.parent
_GBR_PATH = _REPO / "scripts" / "generate_benchmark_report.py"
_GT_PATH = _REPO / "examples" / "ground_truth.json"

_spec = importlib.util.spec_from_file_location("generate_benchmark_report", _GBR_PATH)
assert _spec and _spec.loader
gbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gbr)

_GT_CASE_COUNT = len(json.loads(_GT_PATH.read_text())["verdicts"])


def test_parse_doc_table_finds_heading_in_committed_doc() -> None:
    text = gbr.DOC_PATH.read_text()
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
    text = gbr.DOC_PATH.read_text()
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


def test_diff_against_doc_partial_run_skips_numeric_rows() -> None:
    """A --cases smoke run must never be diffed against full-catalog doc
    numbers — that would always spuriously "drift" and defeat the check."""
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": {"abicheck": {"correct": 999, "pct": 12.3, "false_positives": 9, "false_negatives": 9}},
    }
    report = {
        "full_catalog_run": False,
        "coverage_accuracy": {
            "abicheck": {"label": "abicheck", "correct": 1, "total": 1, "pct": 100.0,
                         "false_positives": 0, "false_negatives": 0},
        },
    }
    assert gbr.diff_against_doc(report, doc_table) == []


def test_diff_against_doc_matches_when_numbers_agree() -> None:
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": {
            "abicheck": {"correct": 3, "pct": 100.0, "false_positives": 0, "false_negatives": 0},
        },
    }
    report = {
        "full_catalog_run": True,
        "coverage_accuracy": {
            "abicheck": {"label": "abicheck", "correct": 3, "total": 3, "pct": 100.0,
                         "false_positives": 0, "false_negatives": 0},
        },
    }
    assert gbr.diff_against_doc(report, doc_table) == []


def test_diff_against_doc_flags_numeric_mismatch() -> None:
    doc_table = {
        "date": "2099-01-01",
        "case_count": _GT_CASE_COUNT,
        "rows": {
            "abicheck": {"correct": 3, "pct": 100.0, "false_positives": 0, "false_negatives": 0},
        },
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
