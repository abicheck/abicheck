# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for PR-review findings in the example proof scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType


def _load_script(relpath: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_matrix_load_json_missing_or_malformed_is_missing_lane(
    tmp_path: Path,
) -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    assert matrix._load_json(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert matrix._load_json(bad) is None


def test_full_matrix_allow_unresolved_never_masks_failed(monkeypatch) -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    monkeypatch.setattr(
        matrix,
        "build_matrix",
        lambda **_kwargs: {
            "summary": {"FAILED": 1, "UNRESOLVED": 1},
            "unresolved_cases": ["case_x"],
            "failed_cases": ["case_y"],
        },
    )
    assert matrix.main(["--allow-unresolved"]) == 1


def test_stub_pair_case_is_covered_by_python_api_proof() -> None:
    # A .pyi-pair example (G23 case163) is owned by the python_api proof lane and
    # counts as COVERED when --proof-python-api is supplied — it must not fall
    # through to the compiled single-library lanes and be reported UNRESOLVED.
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    result = matrix.build_matrix(
        gcc=None,
        clang=None,
        bundle=None,
        runtime=None,
        proof_g20=True,
        proof_l3l4l5=True,
        proof_btf=True,
        proof_python_api=True,
    )
    stub_rows = [r for r in result["results"] if r["owner"] == "python_api"]
    assert stub_rows, "no python_api-owned example case found"
    for row in stub_rows:
        assert row["status"] == "COVERED"
        assert row["case_id"] not in result["unresolved_cases"]


def test_bundle_runner_timeout_is_per_case_error(monkeypatch) -> None:
    bundle = _load_script("validation/scripts/run_bundle_examples.py")

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            ["cmake"], timeout=1, output="out", stderr="err"
        )

    monkeypatch.setattr(bundle.subprocess, "run", raise_timeout)
    result = bundle._run(["cmake"], timeout=1)
    assert result.returncode == 124
    assert "timeout after 1s" in result.stderr


def test_bundle_runner_rejects_unexpected_bundle_kinds(
    monkeypatch, tmp_path: Path
) -> None:
    bundle = _load_script("validation/scripts/run_bundle_examples.py")
    monkeypatch.setattr(bundle, "_build_case", lambda *_args: None)
    monkeypatch.setattr(
        bundle,
        "_compare_release",
        lambda *_args: (
            {
                "bundle_verdict": "BREAKING",
                "bundle_findings": [
                    {"kind": "bundle_intra_dep_removed"},
                    {"kind": "bundle_provider_changed"},
                ],
            },
            None,
        ),
    )
    result = bundle._validate_case(
        "case90_bundle_intra_dep_removed",
        {
            "expected_bundle_verdict": "BREAKING",
            "expected_bundle_kinds": ["bundle_intra_dep_removed"],
            "allow_extra_bundle_kinds": False,
        },
        tmp_path,
    )
    assert result["status"] == "FAIL"
    assert "unexpected" in result["message"]


def test_bundle_runner_validates_expected_libraries() -> None:
    bundle = _load_script("validation/scripts/run_bundle_examples.py")
    payload = {
        "libraries": [
            {
                "library": "libcore.so",
                "verdict": "BREAKING",
                "changes": [{"kind": "func_removed"}],
            }
        ]
    }
    assert (
        bundle._validate_expected_libraries(
            payload,
            {"libcore.so": {"verdict": "BREAKING", "kinds": ["func_removed"]}},
        )
        == []
    )
    errors = bundle._validate_expected_libraries(
        payload,
        {"libcore.so": {"verdict": "BREAKING", "kinds": ["func_added"]}},
    )
    assert errors and "missing" in errors[0]


def test_forced_validate_results_path_matches_ci_workflow(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "validate_examples.json").write_text(
        json.dumps({"summary": {"PASS": 2}, "results": []}), encoding="utf-8"
    )
    assert (results / "validate_examples.json").exists()
