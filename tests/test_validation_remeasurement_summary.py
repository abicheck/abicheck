"""Tests for the combined remeasurement summary generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path("validation/scripts/summarize_remeasurement.py")


def _load_summary():
    spec = importlib.util.spec_from_file_location("summarize_remeasurement", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summarizes_example_artifact(tmp_path: Path) -> None:
    summary = _load_summary()
    artifact = tmp_path / "validate_examples.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "validate_examples.v2",
                "runner": "tests/validate_examples.py",
                "platform": "linux",
                "command": ["python", "tests/validate_examples.py", "--json"],
                "summary": {"PASS": 1, "ERROR": 1},
                "results": [
                    {
                        "status": "PASS",
                        "mode": "debug-headers",
                        "source_layers": ["L0", "L1", "L2"],
                    },
                    {
                        "status": "ERROR",
                        "mode": "build-source",
                        "source_layers": ["L0", "L1", "L2", "L3", "L4", "L5"],
                    },
                ],
            }
        )
    )

    section = summary.summarize_examples(artifact)

    assert section["schema_version"] == "validate_examples.v2"
    assert section["component"] == "synthetic-example"
    assert section["total"] == 2
    assert section["status_counts"] == {"ERROR": 1, "PASS": 1}
    assert section["mode_counts"] == {"build-source": 1, "debug-headers": 1}
    assert section["source_layer_counts"]["L5"] == 1
    assert section["blocking_failures"] == 1


def test_summarizes_component_artifact(tmp_path: Path) -> None:
    summary = _load_summary()
    artifact = tmp_path / "component_suites.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "component_suites.v1",
                "runner": "validation/scripts/run_component_suites.py",
                "platform": "linux",
                "status_counts": {"blocked": 1, "planned": 1},
                "records": [
                    {
                        "suite": "report-policy",
                        "status": "planned",
                        "source_layers": ["L0", "L1"],
                    },
                    {
                        "suite": "debug-metadata",
                        "status": "blocked",
                        "source_layers": ["L1"],
                        "blocked_reasons": ["missing Python dependency: pefile"],
                    },
                ],
            }
        )
    )

    section = summary.summarize_components(artifact)

    assert section["component"] == "component-suite"
    assert section["status_counts"] == {"blocked": 1, "planned": 1}
    assert section["suite_counts"] == {"debug-metadata": 1, "report-policy": 1}
    assert section["blocked"] == [
        {
            "suite": "debug-metadata",
            "blocked_reasons": ["missing Python dependency: pefile"],
        }
    ]
    assert section["blocking_failures"] == 1


def test_summarizes_real_world_artifact(tmp_path: Path) -> None:
    summary = _load_summary()
    artifact = tmp_path / "results.json"
    meta = tmp_path / "results.meta.json"
    artifact.write_text(
        json.dumps(
            [
                {
                    "schema_version": "run_matrix.v2",
                    "mode": "sym->sym",
                    "got": "COMPATIBLE",
                    "expected": "COMPATIBLE",
                    "comparison_status": "MATCH",
                    "source_layers": ["L0"],
                    "exit_code": 0,
                },
                {
                    "schema_version": "run_matrix.v2",
                    "mode": "dwarf->sym",
                    "got": "API_BREAK",
                    "expected": "COMPATIBLE",
                    "comparison_status": "abicheck_stricter",
                    "source_layers": ["L0", "L1"],
                    "exit_code": 2,
                },
            ]
        )
    )
    meta.write_text(
        json.dumps(
            {
                "schema_version": "run_matrix.v2",
                "runner": "validation/scripts/run_matrix.py",
                "platform": "linux",
            }
        )
    )

    section = summary.summarize_real_world(artifact, meta)

    assert section["component"] == "real-world-matrix"
    assert section["schema_version"] == "run_matrix.v2"
    assert section["mode_counts"] == {"dwarf->sym": 1, "sym->sym": 1}
    assert section["verdict_counts"] == {"API_BREAK": 1, "COMPATIBLE": 1}
    assert section["expected_counts"] == {"COMPATIBLE": 2}
    assert section["comparison_status_counts"] == {
        "ABICHECK_STRICTER": 1,
        "MATCH": 1,
    }
    assert section["source_layer_counts"] == {"L0": 2, "L1": 1}
    assert section["run_errors"] == 0
    assert section["expectation_mismatches"] == 1
    assert section["blocking_failures"] == 1


def test_real_world_summary_infers_status_for_older_artifacts(tmp_path: Path) -> None:
    summary = _load_summary()
    artifact = tmp_path / "results.json"
    artifact.write_text(
        json.dumps(
            [
                {
                    "mode": "sym->sym",
                    "verdict": "API_BREAK",
                    "expectation": "BREAKING",
                    "source_layers": ["L0"],
                    "exit_code": 2,
                }
            ]
        )
    )

    section = summary.summarize_real_world(artifact)

    assert section["comparison_status_counts"] == {"MATCH": 1}
    assert section["verdict_counts"] == {"API_BREAK": 1}
    assert section["expected_counts"] == {"BREAKING": 1}
    assert section["blocking_failures"] == 0


def test_main_writes_combined_summary(tmp_path: Path) -> None:
    summary = _load_summary()
    examples = tmp_path / "examples.json"
    out = tmp_path / "summary.json"
    examples.write_text(
        json.dumps(
            {
                "schema_version": "validate_examples.v2",
                "summary": {"PASS": 1},
                "results": [
                    {
                        "status": "PASS",
                        "mode": "debug-headers",
                        "source_layers": ["L0"],
                    }
                ],
            }
        )
    )

    rc = summary.main(["--examples", str(examples), "--output", str(out)])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "remeasurement_summary.v1"
    assert data["command"] == [
        summary.sys.executable,
        "--examples",
        str(examples),
        "--output",
        str(out),
    ]
    assert data["section_count"] == 1
    assert data["total_records"] == 1
    assert data["blocking_failures"] == 0
