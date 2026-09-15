# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for the report-only composite Action's data boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "actions" / "report" / "report.py"
    spec = importlib.util.spec_from_file_location("_action_report", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env(monkeypatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(f"INPUT_{key}", value)


def _report(directory: Path, name: str = "linux") -> None:
    (directory / f"abi-report-{name}.json").write_text(
        json.dumps({"verdict": "COMPATIBLE"}), encoding="utf-8"
    )


def test_renders_canonical_aggregate_summary_without_github_token(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load()
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports)
    monkeypatch.chdir(tmp_path)
    _env(
        monkeypatch,
        REPORTS_DIR=str(reports),
        MANIFEST="",
        DISCOVERED_ONLY="true",
        SUMMARY_FILE="out/summary.md",
        MAX_REPORT_FILES="10",
        MAX_REPORT_BYTES="4096",
        MAX_SUMMARY_BYTES="4096",
        ADD_JOB_SUMMARY="false",
    )

    assert module.main() == 0
    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "ABI aggregate gate: Passed" in summary
    assert "linux" in summary


def test_refuses_a_symlinked_component_report(tmp_path: Path, monkeypatch) -> None:
    module = _load()
    reports = tmp_path / "reports"
    reports.mkdir()
    target = tmp_path / "outside.json"
    target.write_text('{"verdict":"COMPATIBLE"}', encoding="utf-8")
    (reports / "abi-report-linux.json").symlink_to(target)
    monkeypatch.chdir(tmp_path)
    _env(
        monkeypatch,
        REPORTS_DIR=str(reports),
        MANIFEST="",
        DISCOVERED_ONLY="true",
        SUMMARY_FILE="summary.md",
        MAX_REPORT_FILES="10",
        MAX_REPORT_BYTES="4096",
        MAX_SUMMARY_BYTES="4096",
        ADD_JOB_SUMMARY="false",
    )

    assert module.main() == 64
    assert not (tmp_path / "summary.md").exists()


def test_rejection_escapes_workflow_command_newlines(capsys) -> None:
    module = _load()

    assert module._fail("bad\n::warning::injected") == 64
    assert "\n::warning::" not in capsys.readouterr().err


def test_summary_is_bounded_at_a_utf8_boundary(tmp_path: Path, monkeypatch) -> None:
    module = _load()
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(reports)
    monkeypatch.chdir(tmp_path)
    _env(
        monkeypatch,
        REPORTS_DIR=str(reports),
        MANIFEST="",
        DISCOVERED_ONLY="true",
        SUMMARY_FILE="summary.md",
        MAX_REPORT_FILES="10",
        MAX_REPORT_BYTES="4096",
        MAX_SUMMARY_BYTES="1024",
        ADD_JOB_SUMMARY="false",
    )

    assert module.main() == 0
    assert len((tmp_path / "summary.md").read_bytes()) <= 1024
