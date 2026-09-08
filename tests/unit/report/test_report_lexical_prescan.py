# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for the pattern/preprocessor pre-scan report sections (schema 3.12,
plan §3 rows 6/8, §6 Phase 2b)."""

from __future__ import annotations

import json

from abicheck.checker_types import DiffResult
from abicheck.report.lexical_prescan import (
    compute_pattern_prescan_summary,
    compute_preprocessor_prescan_summary,
    render_pattern_prescan_json,
    render_preprocessor_prescan_json,
)
from abicheck.reporter import to_json


def test_compute_pattern_summary_none_when_never_folded():
    assert compute_pattern_prescan_summary(None) is None
    assert render_pattern_prescan_json(None) is None


def test_compute_preprocessor_summary_none_when_never_folded():
    assert compute_preprocessor_prescan_summary(None) is None
    assert render_preprocessor_prescan_json(None) is None


def test_compute_pattern_summary_round_trips_verbatim():
    old = {"files_scanned": 1, "facts": []}
    new = {"files_scanned": 2, "facts": []}
    summary = compute_pattern_prescan_summary({"old": old, "new": new})
    assert summary is not None
    assert render_pattern_prescan_json(summary) == {"old": old, "new": new}


def test_compute_preprocessor_summary_round_trips_verbatim():
    old = {"ran": False, "skipped_reason": "no L3 build evidence"}
    new = {"ran": True, "skipped_reason": ""}
    summary = compute_preprocessor_prescan_summary({"old": old, "new": new})
    assert summary is not None
    assert render_preprocessor_prescan_json(summary) == {"old": old, "new": new}


def test_json_report_carries_both_blocks_when_folded():
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={"old": {"files_scanned": 0}, "new": {"files_scanned": 1}},
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "x"},
            "new": {"ran": False, "skipped_reason": "x"},
        },
    )
    doc = json.loads(to_json(result))
    assert doc["pattern_prescan"] == {
        "old": {"files_scanned": 0},
        "new": {"files_scanned": 1},
    }
    assert doc["preprocessor_prescan"] == {
        "old": {"ran": False, "skipped_reason": "x"},
        "new": {"ran": False, "skipped_reason": "x"},
    }


def test_json_report_omits_both_blocks_when_never_folded():
    """A `DiffResult` built by a caller that never runs `fold_lexical_prescan`
    (e.g. a direct `checker.compare()` unit test) omits both blocks, rather
    than rendering an empty/null placeholder."""
    result = DiffResult(old_version="1.0", new_version="1.1", library="libfoo.so")
    doc = json.loads(to_json(result))
    assert "pattern_prescan" not in doc
    assert "preprocessor_prescan" not in doc


def test_both_blocks_stable_across_report_modes():
    """ADR-068 D4: presentation never changes analysis -- the same
    pre-scan attachment appears identically across every JSON report_mode."""
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={"old": {"files_scanned": 0}, "new": {"files_scanned": 1}},
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "x"},
            "new": {"ran": False, "skipped_reason": "x"},
        },
    )
    for mode in ("full", "leaf", "root-cause"):
        doc = json.loads(to_json(result, report_mode=mode))
        assert doc["pattern_prescan"]["new"]["files_scanned"] == 1
        assert doc["preprocessor_prescan"]["old"]["ran"] is False
