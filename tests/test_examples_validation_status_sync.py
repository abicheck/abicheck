"""Regression tests for the Examples Validation status-table synchronizer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "check_examples_validation_status_sync.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("examples_status_sync", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_checks_renamed_build_source_and_full_matrix_rows(tmp_path: Path) -> None:
    """The two artifact-derived rows stay synchronized with their CI outputs."""
    module = _load_module()
    readme = tmp_path / "README.md"
    ground_truth = tmp_path / "ground_truth.json"
    build_source = tmp_path / "build-source.json"
    full_matrix = tmp_path / "full-matrix.json"

    readme.write_text(
        "\n".join(
            [
                "| Check | Command | Executed where | Scope | Result | Status |",
                "|---|---|---|---:|---|---|",
                "| Full example proof matrix | command | CI | 2 catalog cases | 2/2 COVERED; 1 direct; 0 FAILED / 0 UNRESOLVED | status |",
                "| Default/debug verdicts | command | CI | 2 catalog cases | result | status |",
                "| Runtime smoke | command | CI | 2 catalog cases | result | status |",
                "| Release headers | command | CI | 2 catalog cases | result | status |",
                "| Stripped headers | command | CI | 2 catalog cases | result | status |",
                "| Build/source proof | command | CI | fixed 10-case proof set | 2 PASS | status |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ground_truth.write_text(
        json.dumps({"verdicts": {"case01": {}, "case02": {}}}), encoding="utf-8"
    )
    build_source.write_text(json.dumps({"summary": {"PASS": 2}}), encoding="utf-8")
    full_matrix.write_text(
        json.dumps(
            {
                "ground_truth_cases": 2,
                "summary": {"COVERED": 2},
                "direct_coverage": {"covered": 1, "total": 2},
            }
        ),
        encoding="utf-8",
    )

    module.README = readme
    module.GROUND_TRUTH = ground_truth

    assert (
        module.main(
            [
                "--build-source",
                str(build_source),
                "--full-matrix",
                str(full_matrix),
                "--check",
            ]
        )
        == 0
    )
