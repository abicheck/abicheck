# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ADR-068 D3 cross-source evolution report section (schema 3.3)."""

from __future__ import annotations

import json

from abicheck.checker_policy import ChangeKind, FindingEvolution
from abicheck.checker_types import Change, DiffResult
from abicheck.report.cross_source_evolution import (
    compute_cross_source_evolution_summary,
    render_cross_source_evolution_json,
)
from abicheck.reporter import to_json


def _evolved_change(evolution: FindingEvolution, symbol: str) -> Change:
    return Change(
        kind=ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        symbol=symbol,
        description="desc",
        finding_evolution=evolution,
    )


def test_compute_summary_none_when_no_change_carries_evolution():
    changes = [Change(kind=ChangeKind.FUNC_ADDED, symbol="f", description="d")]
    assert compute_cross_source_evolution_summary(changes) is None
    assert render_cross_source_evolution_json(None) is None


def test_compute_summary_counts_each_state():
    changes = [
        _evolved_change(FindingEvolution.INTRODUCED, "a"),
        _evolved_change(FindingEvolution.INTRODUCED, "b"),
        _evolved_change(FindingEvolution.RESOLVED, "c"),
        _evolved_change(FindingEvolution.PERSISTENT, "d"),
        _evolved_change(FindingEvolution.NOT_EVALUATED, "e"),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="f", description="d"),  # not counted
    ]
    summary = compute_cross_source_evolution_summary(changes)
    assert summary is not None
    assert render_cross_source_evolution_json(summary) == {
        "introduced": 2,
        "resolved": 1,
        "persistent": 1,
        "not_evaluated": 1,
    }


def test_json_report_carries_summary_and_per_change_field():
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        changes=[_evolved_change(FindingEvolution.INTRODUCED, "_Z6leakyv")],
    )
    doc = json.loads(to_json(result))
    assert doc["cross_source_evolution"] == {
        "introduced": 1,
        "resolved": 0,
        "persistent": 0,
        "not_evaluated": 0,
    }
    change_entries = [c for c in doc["changes"] if c["symbol"] == "_Z6leakyv"]
    assert len(change_entries) == 1
    assert change_entries[0]["finding_evolution"] == "introduced"


def test_json_report_omits_block_when_nothing_evolved():
    result = DiffResult(old_version="1.0", new_version="1.1", library="libfoo.so")
    doc = json.loads(to_json(result))
    assert "cross_source_evolution" not in doc


def test_finding_evolution_stable_across_report_modes():
    """ADR-068 D4 (plan F-19): presentation never changes analysis -- the
    same evolution-stated finding appears identically across every JSON
    report_mode, not just the default 'full' one."""
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        changes=[_evolved_change(FindingEvolution.PERSISTENT, "_Z6leakyv")],
    )
    for mode in ("full", "leaf", "root-cause"):
        doc = json.loads(to_json(result, report_mode=mode))
        assert doc["cross_source_evolution"] == {
            "introduced": 0,
            "resolved": 0,
            "persistent": 1,
            "not_evaluated": 0,
        }
