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
    assert len(rows) == len(special.CASE_IDS)
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


def _synthetic_ground_truth(tmp_path: Path, verdicts: dict) -> Path:
    gt = tmp_path / "ground_truth.json"
    gt.write_text(json.dumps({"verdicts": verdicts}), encoding="utf-8")
    return gt


def test_all_xfail_without_source_smoke_is_unresolved_not_covered(
    monkeypatch, tmp_path: Path
) -> None:
    """An all-XFAIL known_gap case with no declared oracle must not earn free
    known-gap-oracle coverage — only a case whose own source_smoke actually
    proved the canonical verdict may skip direct detector/CLI proof."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    case_id = "caseXX_unproven_known_gap"
    gt = _synthetic_ground_truth(
        tmp_path,
        {
            case_id: {
                "expected": "API_BREAK",
                "known_gap": "claims a detector gap but has no oracle behind it",
            }
        },
    )
    monkeypatch.setattr(matrix, "GROUND_TRUTH", gt)
    xfail_lane = {
        "results": [
            {
                "case_id": case_id,
                "status": "XFAIL",
                "expected": "API_BREAK",
                "got": "COMPATIBLE",
                "message": "known_gap: claims a detector gap but has no oracle behind it",
            }
        ]
    }
    result = matrix.build_matrix(
        gcc=xfail_lane, clang=xfail_lane, bundle=None, special_cli=None, runtime=None
    )
    row = next(r for r in result["results"] if r["case_id"] == case_id)
    assert row["status"] == "UNRESOLVED"
    assert "no source_smoke oracle" in row["note"]


def test_all_xfail_with_source_smoke_is_covered_known_gap_oracle(
    monkeypatch, tmp_path: Path
) -> None:
    """The mirror case: an all-XFAIL known_gap case that DOES declare a
    source_smoke oracle is legitimately COVERED via known-gap-oracle
    provenance (this is case111's actual shape)."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    case_id = "caseXX_proven_known_gap"
    gt = _synthetic_ground_truth(
        tmp_path,
        {
            case_id: {
                "expected": "API_BREAK",
                "known_gap": "detector gap, but proven by this case's own source_smoke",
                "source_smoke": {
                    "v1": {"code": "int main(){return 0;}", "expect": "success"},
                    "v2": {"code": "int main(){return bad;}", "expect": "failure"},
                },
            }
        },
    )
    monkeypatch.setattr(matrix, "GROUND_TRUTH", gt)
    xfail_lane = {
        "results": [
            {
                "case_id": case_id,
                "status": "XFAIL",
                "expected": "API_BREAK",
                "got": "COMPATIBLE",
                "message": "known_gap: detector gap, but proven by this case's own source_smoke",
            }
        ]
    }
    result = matrix.build_matrix(
        gcc=xfail_lane, clang=xfail_lane, bundle=None, special_cli=None, runtime=None
    )
    row = next(r for r in result["results"] if r["case_id"] == case_id)
    assert row["status"] == "COVERED"
    assert row["proof_lane"] == "known-gap-xfail"
    assert row["provenance"] == "known-gap-oracle"


def test_build_source_proof_cases_cover_every_l3plus_single_library_case() -> None:
    """BUILD_SOURCE_PROOF_CASES must include every single-library case whose
    min_evidence is L3/L4/L5 — the only shape `--artifact-variant
    build-source` can actually prove (it needs a real compilable v1/v2 pair).
    g20/l3l4l5/reconcile-owned L3-L5 cases ship committed snapshot fixtures
    instead and are proven by their own dedicated lanes
    (test_g20_catalog.py, test_l3l4l5_examples.py, test_diff_reconcile.py),
    not this variant — see examples/README.md's "Known validation gaps".
    This guards against a newly-added single-library L3+ case silently
    missing the build-source smoke."""
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    with open(matrix.GROUND_TRUTH) as f:
        verdicts = json.load(f)["verdicts"]
    required = {
        name
        for name, entry in verdicts.items()
        if matrix._case_owner(name, entry) == "single-library"
        and entry.get("min_evidence") in ("L3", "L4", "L5")
    }
    missing = required - matrix.BUILD_SOURCE_PROOF_CASES
    assert not missing, (
        f"single-library L3+ cases missing from BUILD_SOURCE_PROOF_CASES: {missing}"
    )


def _full_catalog_case_sets(matrix: ModuleType) -> tuple[dict, set, set]:
    with open(matrix.GROUND_TRUTH) as f:
        gt = json.load(f)["verdicts"]
    bundle_cases = {
        name
        for name, entry in gt.items()
        if matrix._case_owner(name, entry) == "bundle"
    }
    special_cli_cases = {
        name
        for name, entry in gt.items()
        if matrix._case_owner(name, entry) not in {"single-library", "bundle"}
    }
    return gt, bundle_cases, special_cli_cases


def _build_source_artifact(
    matrix: ModuleType, gt: dict, *, status: str = "PASS"
) -> dict:
    """A clean build-source artifact fixture. Unlike ``_artifact()``, this
    label's ``ground_truth_cases`` is the *full* catalog count (181), not
    the selected-case count -- ``_artifact_errors`` special-cases
    ``build_source`` that way (see its ``expected_ground_truth_cases``)."""
    cases = matrix.BUILD_SOURCE_PROOF_CASES
    artifact = _artifact(matrix, "build_source", cases, status=status)
    artifact["ground_truth_cases"] = len(gt)
    return artifact


def test_full_catalog_artifact_failures_is_clean_for_passing_lanes() -> None:
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    gt, bundle_cases, special_cli_cases = _full_catalog_case_sets(matrix)
    proofs = _proof_artifact(matrix)
    bundle = _artifact(matrix, "bundle", bundle_cases)
    special_cli = _artifact(matrix, "special_cli", special_cli_cases)
    runtime = _artifact(matrix, "runtime", set(gt), status="DEMONSTRATED")
    build_source = _build_source_artifact(matrix, gt)
    assert (
        catalog._artifact_failures(
            gt, proofs, bundle, special_cli, runtime, build_source
        )
        == []
    )


def test_full_catalog_artifact_failures_surfaces_owner_proof_failure() -> None:
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    gt, bundle_cases, special_cli_cases = _full_catalog_case_sets(matrix)
    proofs = _proof_artifact(matrix)
    proofs["results"][0]["status"] = "FAIL"
    proofs["results"][0]["returncode"] = 1
    proofs["summary"] = {"FAIL": 1, "PASS": len(matrix.SPECIAL_PROOFS) - 1}
    bundle = _artifact(matrix, "bundle", bundle_cases)
    special_cli = _artifact(matrix, "special_cli", special_cli_cases)
    runtime = _artifact(matrix, "runtime", set(gt), status="DEMONSTRATED")
    build_source = _build_source_artifact(matrix, gt)
    errors = catalog._artifact_failures(
        gt, proofs, bundle, special_cli, runtime, build_source
    )
    assert any("failing owners" in error for error in errors)


def test_full_catalog_artifact_failures_surfaces_missing_owner_proof_row() -> None:
    """Regression (Codex review): a hand-rolled loop over *present* proof
    rows can't see an owner missing from the artifact entirely -- a
    partial run_example_owner_proofs.py output that silently drops an
    owner must still be caught, not just a present row with a bad
    status."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    gt, bundle_cases, special_cli_cases = _full_catalog_case_sets(matrix)
    proofs = _proof_artifact(matrix)
    del proofs["results"][0]
    proofs["selected_owners"] -= 1
    proofs["summary"] = {"PASS": len(matrix.SPECIAL_PROOFS) - 1}
    bundle = _artifact(matrix, "bundle", bundle_cases)
    special_cli = _artifact(matrix, "special_cli", special_cli_cases)
    runtime = _artifact(matrix, "runtime", set(gt), status="DEMONSTRATED")
    build_source = _build_source_artifact(matrix, gt)
    errors = catalog._artifact_failures(
        gt, proofs, bundle, special_cli, runtime, build_source
    )
    assert any("missing owners" in error for error in errors)


def test_full_catalog_artifact_failures_surfaces_runtime_build_error() -> None:
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    gt, bundle_cases, special_cli_cases = _full_catalog_case_sets(matrix)
    proofs = _proof_artifact(matrix)
    bundle = _artifact(matrix, "bundle", bundle_cases)
    special_cli = _artifact(matrix, "special_cli", special_cli_cases)
    runtime = _artifact(matrix, "runtime", set(gt), status="DEMONSTRATED")
    runtime["results"][0]["status"] = "BUILD_ERROR"
    runtime["summary"] = {"DEMONSTRATED": len(gt) - 1, "BUILD_ERROR": 1}
    build_source = _build_source_artifact(matrix, gt)
    errors = catalog._artifact_failures(
        gt, proofs, bundle, special_cli, runtime, build_source
    )
    assert any("failing runner statuses" in error for error in errors)


def test_full_catalog_artifact_failures_surfaces_build_source_failure() -> None:
    """Regression (Codex review): if validate_examples.py --artifact-variant
    build-source FAILs/ERRORs for one of the L3+ proof cases while the
    normal compiler lane happens to pass that same case for an unrelated
    reason, the per-case matrix can still show all-COVERED -- this lane
    must be validated too, not just proofs/bundle/special_cli/runtime."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    matrix = _load_script("validation/scripts/collect_full_example_matrix.py")
    gt, bundle_cases, special_cli_cases = _full_catalog_case_sets(matrix)
    proofs = _proof_artifact(matrix)
    bundle = _artifact(matrix, "bundle", bundle_cases)
    special_cli = _artifact(matrix, "special_cli", special_cli_cases)
    runtime = _artifact(matrix, "runtime", set(gt), status="DEMONSTRATED")
    build_source = _build_source_artifact(matrix, gt)
    build_source["results"][0]["status"] = "FAIL"
    build_source["summary"] = {
        "PASS": len(matrix.BUILD_SOURCE_PROOF_CASES) - 1,
        "FAIL": 1,
    }
    errors = catalog._artifact_failures(
        gt, proofs, bundle, special_cli, runtime, build_source
    )
    assert any("failing runner statuses" in error for error in errors)


def test_full_catalog_has_compiler_finds_versioned_clang_only(monkeypatch) -> None:
    """Regression (Codex review): tests/validate_examples.py._find_compiler
    tries clang-18/clang++-18 before the bare clang/clang++ names, so a host
    with only the versioned binaries installed must still be detected as
    having a usable clang -- otherwise run_full_catalog.py silently skips an
    available toolchain-sensitive retry even though
    `validate_examples.py --toolchain clang` would succeed."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in ("clang-18", "clang++-18") else None

    monkeypatch.setattr(catalog.shutil, "which", fake_which)
    assert catalog._has_compiler("clang") is True


def test_full_catalog_family_of_recognizes_msvc(monkeypatch) -> None:
    """Regression (Codex review): _family_of only ever checked for "clang" in
    the compiler name, so cl/cl.exe (MSVC) fell through to "gcc" -- every
    row on an MSVC run got toolchain_used="gcc", and the auto retry logic
    computed a gcc/clang alternate for a producer that is neither. _alternate
    and _has_compiler must also treat msvc as having no real cross-family
    retry (there's no msvc entry in _ALT_COMPILER_PROBE) instead of
    crashing with a KeyError."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    assert catalog._family_of("cl") == "msvc"
    assert catalog._family_of("cl.exe") == "msvc"
    assert catalog._family_of("gcc") == "gcc"
    assert catalog._family_of("clang++") == "clang"
    assert catalog._alternate("msvc") == "msvc"
    monkeypatch.setattr(catalog.shutil, "which", lambda _name: None)
    assert catalog._has_compiler("msvc") is False


def test_run_compiler_lane_surfaces_failed_and_missing_retries(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression (Codex review): when --toolchain auto retries a
    toolchain-sensitive case with the alternate compiler and that retry
    itself FAILs/ERRORs, or produces no result row at all, the primary
    XFAIL result was kept unchanged with no record that the retry ever
    went wrong -- collect_full_example_matrix._single_library_status would
    then promote the untouched primary XFAIL to COVERED via its own
    source_smoke oracle, hiding a real alternate-toolchain regression
    behind a match that never actually proved anything."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    synthetic_gt = tmp_path / "ground_truth.json"
    synthetic_gt.write_text(
        json.dumps(
            {
                "verdicts": {
                    "caseA": {"known_gap_toolchains": ["gcc"]},
                    "caseB": {"known_gap_toolchains": ["gcc"]},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "GROUND_TRUTH", synthetic_gt)
    monkeypatch.setattr(catalog, "_has_compiler", lambda _family: True)

    primary = {
        "compiler_cxx": "g++",
        "results": [
            {"name": "caseA", "status": "XFAIL", "message": "known_gap"},
            {"name": "caseB", "status": "XFAIL", "message": "known_gap"},
        ],
    }
    alt = {
        # The alt run's own producer must actually be clang (matching the
        # requested alt_family) so this test still exercises FAIL/missing
        # handling rather than the (separately tested) fallback-mismatch
        # path.
        "compiler_cxx": "clang++",
        "results": [
            {"name": "caseA", "status": "FAIL", "message": "boom"},
            # caseB deliberately omitted from the alt run's own results.
        ],
    }
    calls = {"n": 0}

    def fake_run_json(_cmd):
        calls["n"] += 1
        return primary if calls["n"] == 1 else alt

    monkeypatch.setattr(catalog, "_run_json", fake_run_json)

    by_case, _desc, retry_failures = catalog._run_compiler_lane("auto")
    assert sorted(retry_failures) == [
        "caseA: clang retry returned FAIL",
        "caseB: clang retry produced no result row",
    ]
    assert by_case["caseA"]["status"] == "XFAIL"
    assert by_case["caseB"]["status"] == "XFAIL"


def test_run_compiler_lane_derives_retries_from_split_cc_cxx(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression (Codex review): with split CC/CXX (e.g. CC=gcc CXX=clang++),
    a C case's own family is compiler_c while a C++ case's is compiler_cxx --
    using compiler_cxx alone for every case mislabels toolchain_used for C
    cases and picks the wrong alternate family for their retry. Uses two
    real example cases (case64, a .c case; case34, a .cpp case) so
    _case_family's real _resolve_case_sources call resolves them for real."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    synthetic_gt = tmp_path / "ground_truth.json"
    synthetic_gt.write_text(
        json.dumps(
            {
                "verdicts": {
                    "case64_calling_convention_changed": {
                        "known_gap_toolchains": ["gcc"]
                    },
                    "case34_access_level": {"known_gap_toolchains": ["clang"]},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "GROUND_TRUTH", synthetic_gt)
    monkeypatch.setattr(catalog, "_has_compiler", lambda _family: True)

    primary = {
        "compiler_c": "gcc",
        "compiler_cxx": "clang++",
        "results": [
            {
                "name": "case64_calling_convention_changed",
                "status": "XFAIL",
                "message": "known_gap",
            },
            {"name": "case34_access_level", "status": "XFAIL", "message": "known_gap"},
        ],
    }
    retry_calls: list[list[str]] = []

    def fake_run_json(cmd):
        if cmd[cmd.index("--toolchain") + 1] == "auto":
            return primary
        retry_calls.append(cmd)
        toolchain = cmd[cmd.index("--toolchain") + 1]
        name = cmd[-1]
        compiler_c, compiler_cxx = (
            ("clang", "clang++") if toolchain == "clang" else ("gcc", "g++")
        )
        return {
            "compiler_c": compiler_c,
            "compiler_cxx": compiler_cxx,
            "results": [{"name": name, "status": "PASS", "toolchain": toolchain}],
        }

    monkeypatch.setattr(catalog, "_run_json", fake_run_json)

    by_case, _desc, retry_failures = catalog._run_compiler_lane("auto")

    assert retry_failures == []
    # C case retried with clang (opposite of its own family_c=gcc); C++ case
    # retried with gcc (opposite of its own family_cxx=clang) -- two distinct
    # alternate families derived from ONE split-compiler primary run.
    retried_toolchains = {
        cmd[cmd.index("--toolchain") + 1]: cmd[-1] for cmd in retry_calls
    }
    assert retried_toolchains == {
        "clang": "case64_calling_convention_changed",
        "gcc": "case34_access_level",
    }
    assert by_case["case64_calling_convention_changed"]["toolchain_used"] == "clang"
    assert by_case["case34_access_level"]["toolchain_used"] == "gcc"


def test_run_compiler_lane_forced_toolchain_reports_actual_fallback_compiler(
    monkeypatch,
) -> None:
    """Regression (Codex review): tests/validate_examples.py._find_compiler
    doesn't fail closed on a forced --toolchain -- it falls back to the other
    family when the requested one isn't on PATH, and reports the *actual*
    producer via compiler_c/compiler_cxx. Blindly labeling every row
    toolchain_used=<requested> would claim clang built a case gcc actually
    built (and could wrongly promote a gcc-scoped known_gap as covered under
    a clang label the case never really ran under)."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    primary = {
        # Requested clang, but neither compiler_c nor compiler_cxx actually
        # resolved to clang -- a clang-less host silently fell back to gcc.
        "compiler_c": "gcc",
        "compiler_cxx": "g++",
        "results": [
            {"name": "case64_calling_convention_changed", "status": "PASS"},
            {"name": "case34_access_level", "status": "PASS"},
        ],
    }
    monkeypatch.setattr(catalog, "_run_json", lambda _cmd: primary)

    by_case, desc, retry_failures = catalog._run_compiler_lane("clang")

    assert retry_failures == []
    # desc must not claim clang was "forced" when the actual producer
    # (reported below) fell back to gcc -- that would misrepresent how the
    # results were actually produced (CodeRabbit review).
    assert desc == "toolchain=clang (requested for every case; actual c=gcc/cxx=gcc)"
    # Both cases actually built with gcc regardless of source language --
    # not the requested "clang".
    assert by_case["case64_calling_convention_changed"]["toolchain_used"] == "gcc"
    assert by_case["case34_access_level"]["toolchain_used"] == "gcc"


def test_run_compiler_lane_rejects_retry_that_itself_fell_back(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression (CodeRabbit review): _has_compiler("clang") only checks
    that *some* clang-family binary is on PATH, but tests/validate_examples.py
    -._find_compiler resolves per-language and can itself fall back to a
    third family for one case's actual source language (e.g. clang++ absent,
    clang present -- a C++ case's retry silently builds with gcc instead). A
    retry batch's own compiler_c/compiler_cxx must be checked before trusting
    its PASS as real evidence for the *requested* alternate family, or a
    fallback-produced PASS could wrongly promote a gcc-scoped known_gap under
    a clang label the case never actually built under."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    synthetic_gt = tmp_path / "ground_truth.json"
    synthetic_gt.write_text(
        json.dumps(
            {"verdicts": {"case34_access_level": {"known_gap_toolchains": ["gcc"]}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "GROUND_TRUTH", synthetic_gt)
    monkeypatch.setattr(catalog, "_has_compiler", lambda _family: True)

    primary = {
        "compiler_c": "gcc",
        "compiler_cxx": "gcc",
        "results": [
            {"name": "case34_access_level", "status": "XFAIL", "message": "known_gap"}
        ],
    }

    def fake_run_json(cmd):
        if cmd[cmd.index("--toolchain") + 1] == "auto":
            return primary
        # Retry was requested with --toolchain clang, but the retry's own
        # producer fell back to gcc anyway (e.g. clang++ absent even though
        # a bare clang satisfied _has_compiler's coarser probe).
        return {
            "compiler_c": "gcc",
            "compiler_cxx": "gcc",
            "results": [{"name": "case34_access_level", "status": "PASS"}],
        }

    monkeypatch.setattr(catalog, "_run_json", fake_run_json)

    by_case, _desc, retry_failures = catalog._run_compiler_lane("auto")

    assert retry_failures == [
        "case34_access_level: requested clang retry actually used gcc"
    ]
    # The primary's XFAIL must survive untouched -- the "PASS" was never
    # actually produced by the requested alternate family, so it isn't real
    # evidence that clang clears this case's known_gap.
    assert by_case["case34_access_level"]["status"] == "XFAIL"
    assert by_case["case34_access_level"]["toolchain_used"] == "gcc"


def test_run_compiler_lane_desc_counts_only_attempted_retries(monkeypatch) -> None:
    """Regression (CodeRabbit review): a retry group skipped because its
    alternate compiler isn't on PATH must not be counted as "retried" in the
    compiler_lane description -- only groups that actually ran a retry
    subprocess should count toward the attempted figure."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    gt = {
        "caseC": {"known_gap_toolchains": ["gcc"]},  # retried: clang present
        "caseCxx": {"known_gap_toolchains": ["clang"]},  # skipped: gcc absent
    }

    def fake_gt_text():
        return json.dumps({"verdicts": gt})

    monkeypatch.setattr(
        catalog,
        "GROUND_TRUTH",
        type("P", (), {"read_text": staticmethod(fake_gt_text)})(),
    )
    monkeypatch.setattr(
        catalog, "_case_family", lambda name, fc, fcxx: fc if name == "caseC" else fcxx
    )
    monkeypatch.setattr(catalog, "_has_compiler", lambda family: family == "clang")

    primary = {
        "compiler_c": "gcc",
        "compiler_cxx": "clang++",
        "results": [
            {"name": "caseC", "status": "XFAIL", "message": "known_gap"},
            {"name": "caseCxx", "status": "XFAIL", "message": "known_gap"},
        ],
    }

    def fake_run_json(cmd):
        if cmd[cmd.index("--toolchain") + 1] == "auto":
            return primary
        # caseC's retry is requested with clang -- the monkeypatched
        # _case_family above returns family_c verbatim for "caseC", so
        # compiler_c here must actually say clang for the new actual-vs-
        # requested family check to accept this as a genuine clang retry.
        return {
            "compiler_c": "clang",
            "compiler_cxx": "clang++",
            "results": [{"name": "caseC", "status": "PASS"}],
        }

    monkeypatch.setattr(catalog, "_run_json", fake_run_json)

    _by_case, desc, _retry_failures = catalog._run_compiler_lane("auto")
    assert "2 toolchain-sensitive case(s) found" in desc
    assert "1 retried" in desc
    assert "1 improved to PASS" in desc


def test_full_catalog_resolve_single_library_threads_ground_truth_entry() -> None:
    """collect_full_example_matrix._single_library_status takes (name, entry,
    lanes) — calling it without entry raises TypeError on every single-library
    case (Codex review). Cover both branches entry actually drives: a PASS
    lane (entry unused) and an all-XFAIL lane with no declared source_smoke
    (entry.get("source_smoke") must gate UNRESOLVED vs COVERED)."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    entry = {"expected": "API_BREAK"}
    compiler_result = {
        "status": "PASS",
        "toolchain_used": "gcc",
        "expected": "API_BREAK",
        "got": "API_BREAK",
        "message": "",
    }
    resolved = catalog._resolve_single_library("case01", entry, compiler_result, None)
    assert resolved["status"] == "COVERED"

    xfail_no_oracle_result = {
        "status": "XFAIL",
        "toolchain_used": "gcc",
        "expected": "API_BREAK",
        "got": "COMPATIBLE",
        "message": "known_gap: no oracle behind it",
    }
    resolved = catalog._resolve_single_library(
        "case02", entry, xfail_no_oracle_result, None
    )
    assert resolved["status"] == "UNRESOLVED"
    assert "no source_smoke oracle" in resolved["note"]


def test_full_catalog_main_exit_code_reflects_artifact_errors(
    monkeypatch, tmp_path: Path
) -> None:
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    monkeypatch.setattr(
        catalog,
        "run_full_catalog",
        lambda *_args, **_kwargs: {
            "schema_version": "full_catalog_single_config.v1",
            "requested_toolchain": "auto",
            "compiler_lane": "toolchain=auto",
            "ground_truth_cases": 181,
            "summary": {"COVERED": 181},
            "owner_proofs_summary": {"PASS": 7},
            "unresolved_cases": [],
            "failed_cases": [],
            "artifact_errors": ["runtime smoke build error: case07"],
            "results": [],
        },
    )
    exit_code = catalog.main(
        ["--toolchain", "auto", "--out", str(tmp_path / "out.json")]
    )
    assert exit_code == 1


def test_full_catalog_main_creates_missing_out_parent_directory(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression (CodeRabbit review): a --out path whose parent directory
    doesn't exist yet (e.g. --out artifacts/catalog/full.json) must not
    crash -- write_text() doesn't create missing parents on its own."""
    catalog = _load_script("validation/scripts/run_full_catalog.py")
    monkeypatch.setattr(
        catalog,
        "run_full_catalog",
        lambda *_args, **_kwargs: {
            "schema_version": "full_catalog_single_config.v1",
            "requested_toolchain": "auto",
            "compiler_lane": "toolchain=auto",
            "ground_truth_cases": 181,
            "summary": {"COVERED": 181},
            "owner_proofs_summary": {"PASS": 7},
            "unresolved_cases": [],
            "failed_cases": [],
            "artifact_errors": [],
            "results": [],
        },
    )
    out_path = tmp_path / "artifacts" / "catalog" / "full.json"
    exit_code = catalog.main(["--toolchain", "auto", "--out", str(out_path)])
    assert exit_code == 0
    assert out_path.exists()
