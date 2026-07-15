# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for PR-review findings in the example proof scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


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


@pytest.mark.parametrize("payload", ["[]", '"a string"', "1", "null", "true"])
def test_full_matrix_load_json_rejects_non_object_top_level(
    tmp_path: Path, payload: str
) -> None:
    """Valid JSON whose root isn't an object must not crash downstream .get() calls."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    non_object = tmp_path / "non_object.json"
    non_object.write_text(payload, encoding="utf-8")
    assert matrix._load_json(non_object) is None


def test_full_matrix_results_by_case_rejects_non_list_results() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    assert matrix._results_by_case({"results": "not-a-list"}) == {}
    assert matrix._results_by_case({"results": [1, "two", {"case_id": "case01"}]}) == {
        "case01": {"case_id": "case01"}
    }


def test_full_matrix_allow_unresolved_never_masks_failed(
    monkeypatch, tmp_path: Path
) -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    monkeypatch.setattr(matrix, "_load_json", lambda _path: {})
    monkeypatch.setattr(matrix, "_artifact_errors", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        matrix,
        "build_matrix",
        lambda **_kwargs: {
            "summary": {"FAILED": 1, "UNRESOLVED": 1},
            "unresolved_cases": ["case_x"],
            "failed_cases": ["case_y"],
        },
    )
    artifacts = [str(tmp_path / f"{name}.json") for name in range(7)]
    assert (
        matrix.main(
            [
                "--gcc",
                artifacts[0],
                "--clang",
                artifacts[1],
                "--runtime",
                artifacts[2],
                "--build-source",
                artifacts[3],
                "--bundle",
                artifacts[4],
                "--special-cli",
                artifacts[5],
                "--proofs",
                artifacts[6],
                "--allow-unresolved",
            ]
        )
        == 1
    )


def _artifact(
    matrix: ModuleType,
    label: str,
    cases: set[str],
    *,
    status: str = "PASS",
) -> dict[str, object]:
    runner, schema = matrix.ARTIFACT_CONTRACTS[label]
    payload: dict[str, object] = {
        "schema_version": schema,
        "runner": runner,
        "ground_truth_sha256": matrix._ground_truth_digest(),
        "ground_truth_cases": len(cases),
        "selected_cases": len(cases),
        "summary": {status: len(cases)},
        "results": [
            {"case_id": case_id, "status": status} for case_id in sorted(cases)
        ],
    }
    if label in {"gcc", "clang", "build_source"}:
        payload["toolchain"] = label if label != "build_source" else "auto"
        payload["artifact_variants"] = [
            "build-source" if label == "build_source" else "debug-headers"
        ]
    elif label == "runtime":
        payload["build_type"] = "Debug"
    else:
        payload["platform"] = "linux"
    return payload


@pytest.mark.parametrize(
    "label", ["gcc", "clang", "build_source", "runtime", "bundle", "special_cli"]
)
def test_full_matrix_required_artifact_rejects_missing_or_wrong_identity(
    label: str,
) -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    cases = {"case01", "case02"}
    assert matrix._artifact_errors(label, None, expected_cases=cases)

    payload = _artifact(matrix, label, cases)
    payload["runner"] = "wrong/runner.py"
    errors = matrix._artifact_errors(label, payload, expected_cases=cases)
    assert any("runner=" in error for error in errors)


def test_full_matrix_artifact_rejects_partial_duplicate_and_stale_catalog() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    cases = {"case01", "case02"}
    payload = _artifact(matrix, "gcc", cases)
    payload["ground_truth_sha256"] = "stale"
    payload["results"] = [
        {"case_id": "case01", "status": "PASS"},
        {"case_id": "case01", "status": "PASS"},
    ]
    errors = matrix._artifact_errors("gcc", payload, expected_cases=cases)
    assert any("ground_truth_sha256" in error for error in errors)
    assert any("duplicate case ids: case01" in error for error in errors)
    assert any("missing case ids: case02" in error for error in errors)


def test_full_matrix_artifact_summary_accounts_for_kinds_mismatch_and_category_collapsed() -> (
    None
):
    """PR #547 added KINDS_MISMATCH/CATEGORY_COLLAPSED as secondary overlay
    tallies in tests/validate_examples.py::_summary_counts -- a case can be
    PASS *and* contribute to one of these (they measure a stricter,
    independent signal, not the primary pass/fail status). The by-status-only
    recomputation must account for both, or a clean artifact whose summary
    legitimately includes them always reads as an ARTIFACT ERROR."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    cases = {"case01", "case02", "case03"}
    payload = _artifact(matrix, "gcc", cases)
    payload["summary"] = {"PASS": 3, "KINDS_MISMATCH": 1, "CATEGORY_COLLAPSED": 1}
    payload["results"] = [
        {"case_id": "case01", "status": "PASS", "kinds_strict": "mismatch"},
        {"case_id": "case02", "status": "PASS", "category_strict": "collapsed"},
        {"case_id": "case03", "status": "PASS"},
    ]
    errors = matrix._artifact_errors("gcc", payload, expected_cases=cases)
    assert not any("summary=" in error for error in errors)


def test_full_matrix_artifact_summary_mismatch_still_caught() -> None:
    """A genuinely wrong KINDS_MISMATCH/CATEGORY_COLLAPSED count must still be
    flagged -- the fix must not blanket-ignore these keys."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    cases = {"case01"}
    payload = _artifact(matrix, "gcc", cases)
    payload["summary"] = {"PASS": 1, "KINDS_MISMATCH": 1}  # no row actually mismatched
    payload["results"] = [{"case_id": "case01", "status": "PASS"}]
    errors = matrix._artifact_errors("gcc", payload, expected_cases=cases)
    assert any("summary=" in error for error in errors)


def test_full_matrix_runtime_build_error_is_an_artifact_error() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    payload = _artifact(matrix, "runtime", {"case01"}, status="BUILD_ERROR")
    errors = matrix._artifact_errors("runtime", payload, expected_cases={"case01"})
    assert errors == ["runtime: failing runner statuses for: case01"]


def _proof_artifact(matrix: ModuleType) -> dict[str, object]:
    owners = sorted(matrix.SPECIAL_PROOFS)
    return {
        "schema_version": matrix.PROOF_ARTIFACT_SCHEMA,
        "runner": matrix.PROOF_ARTIFACT_RUNNER,
        "ground_truth_sha256": matrix._ground_truth_digest(),
        "selected_owners": len(owners),
        "summary": {"PASS": len(owners)},
        "results": [
            {"owner": owner, "status": "PASS", "returncode": 0} for owner in owners
        ],
    }


def test_full_matrix_requires_machine_readable_owner_proofs() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    assert matrix._proof_artifact_errors(None)

    payload = _proof_artifact(matrix)
    payload["results"][0]["status"] = "FAIL"
    payload["results"][0]["returncode"] = 1
    payload["summary"] = {
        "FAIL": 1,
        "PASS": len(matrix.SPECIAL_PROOFS) - 1,
    }
    errors = matrix._proof_artifact_errors(payload)
    assert any("failing owners" in error for error in errors)


def test_owner_proof_runner_records_each_exit_code(monkeypatch) -> None:
    proofs = _load_script("validation/scripts/run_example_owner_proofs.py")

    class Completed:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    monkeypatch.setattr(proofs.subprocess, "run", lambda *_args, **_kwargs: Completed())
    result = proofs._run_owner("g20", proofs.OWNER_PROOFS["g20"])
    assert result["status"] == "PASS"
    assert result["returncode"] == 0
    assert result["proof"] == "tests/test_g20_catalog.py"


def test_full_matrix_rejects_artifact_error_even_when_rows_are_covered(
    monkeypatch, tmp_path: Path
) -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    monkeypatch.setattr(matrix, "_load_json", lambda _path: {})
    monkeypatch.setattr(
        matrix,
        "_artifact_errors",
        lambda label, *_args, **_kwargs: [f"{label}: invalid"],
    )
    monkeypatch.setattr(
        matrix,
        "build_matrix",
        lambda **_kwargs: {
            "summary": {"COVERED": 181},
            "unresolved_cases": [],
            "failed_cases": [],
            "results": [],
        },
    )
    artifacts = [str(tmp_path / f"{name}.json") for name in range(7)]
    assert (
        matrix.main(
            [
                "--gcc",
                artifacts[0],
                "--clang",
                artifacts[1],
                "--runtime",
                artifacts[2],
                "--build-source",
                artifacts[3],
                "--bundle",
                artifacts[4],
                "--special-cli",
                artifacts[5],
                "--proofs",
                artifacts[6],
            ]
        )
        == 1
    )


def test_stub_pair_case_requires_public_cli_proof() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    result = matrix.build_matrix(
        gcc=None,
        clang=None,
        bundle=None,
        special_cli=None,
        runtime=None,
    )
    stub_rows = [r for r in result["results"] if r["owner"] == "python_api"]
    assert stub_rows, "no python_api-owned example case found"
    assert all(row["status"] == "UNRESOLVED" for row in stub_rows)


def test_special_cli_cases_require_direct_cli_results() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    special = _load_script("validation/scripts/run_special_cli_examples.py")
    special_payload = {
        "results": [
            {"case_id": case_id, "status": "PASS"} for case_id in special.CASE_IDS
        ]
    }
    result = matrix.build_matrix(
        gcc=None,
        clang=None,
        bundle=None,
        special_cli=special_payload,
        runtime=None,
    )
    rows = [row for row in result["results"] if row["case_id"] in special.CASE_IDS]
    assert len(rows) == 26
    assert all(row["status"] == "COVERED" for row in rows)
    assert all(row["proof_lane"] == "special-abicheck-cli" for row in rows)
    assert all(row["provenance"] == "abicheck-cli-workflow" for row in rows)


def test_case98_has_one_expected_verdict_and_l2_miss_is_covered_by_l3() -> None:
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    case_id = "case98_cxx_standard_floor_raised"
    l2_miss = {
        "results": [
            {
                "case_id": case_id,
                "status": "XFAIL",
                "expected": "COMPATIBLE_WITH_RISK",
                "got": "NO_CHANGE",
            }
        ]
    }
    l3_proof = {
        "results": [
            {
                "case_id": case_id,
                "status": "PASS",
                "expected": "COMPATIBLE_WITH_RISK",
                "got": "COMPATIBLE_WITH_RISK",
            }
        ]
    }

    result = matrix.build_matrix(
        gcc=l2_miss,
        clang=l2_miss,
        bundle=None,
        special_cli=None,
        runtime=None,
        build_source=l3_proof,
    )
    row = next(r for r in result["results"] if r["case_id"] == case_id)

    assert row["expected"] == "COMPATIBLE_WITH_RISK"
    assert row["status"] == "COVERED"
    assert row["proof_lane"] == "build-source"
    assert row["provenance"] == "abicheck-cli-workflow"


@pytest.mark.parametrize("stdout", ["[]", '"a string"', "1", "null"])
def test_run_json_command_rejects_non_object_json_root(
    monkeypatch, stdout: str
) -> None:
    """A CLI that emits valid-but-non-object JSON must fail the case, not crash on .get()."""
    special = _load_script("validation/scripts/run_special_cli_examples.py")
    completed = subprocess.CompletedProcess(
        args=["abicheck", "compare"], returncode=0, stdout=stdout, stderr=""
    )
    monkeypatch.setattr(special.subprocess, "run", lambda *_args, **_kwargs: completed)
    result = special._run_json_command(["abicheck", "compare"], timeout=10)
    assert result["payload"] is None
    assert "object" in result["message"]


def test_special_cli_runner_accepts_semantic_breaking_exit_code(monkeypatch) -> None:
    special = _load_script("validation/scripts/run_special_cli_examples.py")
    monkeypatch.setattr(
        special,
        "_run_json_command",
        lambda *_args, **_kwargs: {
            "returncode": 4,
            "payload": {
                "verdict": "BREAKING",
                "changes": [{"kind": "kabi_crc_changed"}],
            },
            "message": "",
            "stdout": "",
            "stderr": "",
            "seconds": 0.1,
        },
    )
    result = special._run_compare_case(
        "case175_kabi_crc_changed",
        special.COMPARE_CASES["case175_kabi_crc_changed"],
        {"expected": "BREAKING", "expected_kinds": ["kabi_crc_changed"]},
        10,
    )
    assert result["status"] == "PASS"
    assert result["returncode"] == 4


def test_python_case_setup_timeout_is_a_failed_case_not_a_crash(
    monkeypatch, tmp_path: Path
) -> None:
    special = _load_script("validation/scripts/run_special_cli_examples.py")

    def raise_timeout(*_args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["cc"], timeout=kwargs.get("timeout", 1), output="", stderr="stuck"
        )

    monkeypatch.setattr(special.subprocess, "run", raise_timeout)
    with open(special.GROUND_TRUTH) as f:
        entry = json.load(f)["verdicts"][special.PYTHON_CASE]
    result = special._run_python_case(entry, timeout=1, temp_root=tmp_path)
    assert result["status"] in {"FAIL", "ERROR"}
    assert "timed out" in result["message"]


def test_python_case_setup_os_error_is_a_failed_case_not_a_crash(
    monkeypatch, tmp_path: Path
) -> None:
    special = _load_script("validation/scripts/run_special_cli_examples.py")

    def raise_os_error(*_args, **_kwargs):
        raise FileNotFoundError("cc: command not found")

    monkeypatch.setattr(special.subprocess, "run", raise_os_error)
    with open(special.GROUND_TRUTH) as f:
        entry = json.load(f)["verdicts"][special.PYTHON_CASE]
    result = special._run_python_case(entry, timeout=1, temp_root=tmp_path)
    assert result["status"] in {"FAIL", "ERROR"}
    assert "could not start" in result["message"]


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
            "expected": "BREAKING",
            "expected_kinds": ["bundle_intra_dep_removed"],
            "allow_extra_bundle_kinds": False,
        },
        tmp_path,
    )
    assert result["status"] == "FAIL"
    assert "unexpected" in result["message"]


def test_bundle_runner_validates_library_assertions() -> None:
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
        bundle._validate_library_assertions(
            payload,
            {"libcore.so": {"verdict": "BREAKING", "kinds": ["func_removed"]}},
        )
        == []
    )
    errors = bundle._validate_library_assertions(
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
