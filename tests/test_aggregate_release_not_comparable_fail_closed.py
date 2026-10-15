from __future__ import annotations

import json
from pathlib import Path

from abicheck.workflows.aggregate.load import _load_report_file


def _release_not_comparable_report(path: Path, run_outcome: dict) -> Path:
    report = path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "not_comparable",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "exit": {"code": 16, "not_comparable_contribution": 1},
                "run_outcome": run_outcome,
            }
        ),
        encoding="utf-8",
    )
    return report


def test_release_not_comparable_contradicted_by_run_outcome_fails_closed(
    tmp_path: Path,
) -> None:
    """A lowercase ``"not_comparable"`` release refusal whose own
    ``run_outcome`` claims ``operational: none`` contradicts the refusal.
    Trusting that block would let a real refusal read as a clean pass, so
    the report must load as malformed (fail closed), not as a gate."""
    report = _release_not_comparable_report(
        tmp_path,
        {
            "schema_version": "1",
            "compatibility": None,
            "assurance": None,
            "gate": "none",
            "operational": "none",
            "lifecycle": "existing",
        },
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is None
    assert loaded.reason is not None and "malformed" in loaded.reason
    assert "contradicts" in loaded.reason


def test_release_not_comparable_with_schema_invalid_run_outcome_fails_closed(
    tmp_path: Path,
) -> None:
    """A lowercase ``"not_comparable"`` release refusal with a ``run_outcome``
    that fails the schema (unknown ``gate`` value) cannot be read as a
    trusted gate decision. It must fail closed as malformed, never as a
    clean or merely unavailable target."""
    report = _release_not_comparable_report(
        tmp_path,
        {
            "schema_version": "1",
            "compatibility": None,
            "assurance": None,
            "gate": "not-a-real-gate",
            "operational": "not_comparable",
            "lifecycle": "existing",
        },
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is None
    assert loaded.reason is not None and "malformed" in loaded.reason
