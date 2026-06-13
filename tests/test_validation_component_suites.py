"""Tests for the component-suite remeasurement runner."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

_SCRIPT = Path("validation/scripts/run_component_suites.py")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_component_suites", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_pytest_counts_handles_mixed_summary() -> None:
    runner = _load_runner()

    counts = runner.parse_pytest_counts(
        "== 12 failed, 34 passed, 5 skipped, 2 warnings in 1.23s =="
    )

    assert counts == {
        "passed": 34,
        "failed": 12,
        "skipped": 5,
        "warnings": 2,
    }


def test_dry_run_record_has_remeasurement_metadata() -> None:
    runner = _load_runner()

    record = runner.run_suite("report-policy", dry_run=True, pytest_args=[])

    assert record["schema_version"] == "component_suites.v1"
    assert record["component"] == "component-suite"
    assert record["case_id"] == "report-policy"
    assert record["status"] == "planned"
    assert record["platform"]
    assert record["source_layers"] == ["L0", "L1", "L2", "L3", "L4", "L5"]
    assert "tests/test_reporter.py" in record["tests"]
    assert record["command"][:4] == [
        runner.sys.executable,
        "-m",
        "pytest",
        "-q",
    ]
    assert record["blocked_reasons"] == []


def test_unsupported_platform_is_blocked(monkeypatch) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "platform_tag", lambda: "windows")

    record = runner.run_suite("elf-symbol-surface", dry_run=False, pytest_args=[])

    assert record["status"] == "blocked"
    assert record["exit_code"] is None
    assert "unsupported platform: windows" in record["blocked_reasons"][0]


def test_pytest_timeout_is_failed(monkeypatch) -> None:
    runner = _load_runner()

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=600)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    record = runner.run_suite("report-policy", dry_run=False, pytest_args=[])

    assert record["status"] == "failed"
    assert record["exit_code"] == 124
    assert "pytest timed out after 600s" in record["blocked_reasons"]


def test_report_summarizes_suite_statuses() -> None:
    runner = _load_runner()
    records = [
        {"status": "planned"},
        {"status": "planned"},
        {"status": "blocked"},
    ]

    report = runner.make_report(records)

    assert report["schema_version"] == "component_suites.v1"
    assert report["runner"] == "validation/scripts/run_component_suites.py"
    assert report["suite_count"] == 3
    assert report["status_counts"] == {"blocked": 1, "planned": 2}
    assert report["records"] == records


def test_main_writes_json_report(tmp_path: Path) -> None:
    runner = _load_runner()
    out = tmp_path / "component_suites.json"

    rc = runner.main(["--suite", "report-policy", "--dry-run", "--output", str(out)])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "component_suites.v1"
    assert data["suite_count"] == 1
    assert data["records"][0]["suite"] == "report-policy"
    assert data["records"][0]["status"] == "planned"
