"""Runtime-proof and semantic-review contracts of the catalog validation lanes.

From the 2026-10-08 GCC/Clang catalog re-audit: an invalid baseline stayed
green, any stdout change read as a demonstrated break, and COVERED read as a
semantic approval. Each rule is stated against the runner/collector scripts
themselves (loaded by path -- they are not a package).
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_validation_script(name: str) -> ModuleType:
    path = _ROOT / "skills-src/evaluation/validation/scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_artifact(matrix: ModuleType, statuses: dict[str, str]) -> dict:
    rows = [{"case_id": case, "status": status} for case, status in statuses.items()]
    return {
        "runner": matrix.ARTIFACT_CONTRACTS["runtime"][0],
        "schema_version": matrix.ARTIFACT_CONTRACTS["runtime"][1],
        "ground_truth_sha256": matrix._ground_truth_digest(),
        "ground_truth_cases": len(statuses),
        "selected_cases": len(statuses),
        "build_type": "Debug",
        "summary": dict(Counter(statuses.values())),
        "results": rows,
    }


@pytest.mark.parametrize(
    ("status", "fails"),
    [
        ("DEMONSTRATED", False),
        ("NO_RUNTIME_SIGNAL", False),
        ("SKIP", False),
        ("BUILD_ERROR", True),
        ("BASELINE_SIGNAL", True),  # an invalid baseline voids the proof
        ("CONTRADICTED", True),  # a clean case whose old consumer failed
    ],
)
def test_runtime_proof_failure_statuses(status: str, fails: bool) -> None:
    """Runner exit code and collector agree on which runtime rows void the
    proof lane (2026-10-08 re-audit: BASELINE_SIGNAL stayed green)."""
    matrix = _load_validation_script("collect_full_example_matrix.py")
    smoke = _load_validation_script("run_example_runtime_smoke.py")
    cases = {"case_a": "DEMONSTRATED", "case_b": status}
    errors = matrix._artifact_errors(
        "runtime", _runtime_artifact(matrix, cases), expected_cases=set(cases)
    )
    assert any("failing runner statuses" in e for e in errors) is fails, errors
    assert (status in smoke.PROOF_FAILURE_STATUSES) is fails


@pytest.mark.parametrize(
    ("signal", "strength"),
    [
        ("nonzero", "consumer_assertion"),
        ("timeout", "consumer_assertion"),
        ("stdout_changed", "output_only"),
        ("stderr_changed", "output_only"),
        ("no_runtime_signal", "none"),
    ],
)
def test_runtime_proof_strength(signal: str, strength: str) -> None:
    """Only a changed exit status is evidence a consumer broke; different
    output alone is not (2026-10-08 re-audit: DEMONSTRATED != proven)."""
    smoke = _load_validation_script("run_example_runtime_smoke.py")
    assert smoke.proof_strength(signal) == strength


@pytest.mark.parametrize(
    "expected", ["NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "BREAKING"]
)
@pytest.mark.parametrize(
    "signal", ["nonzero", "timeout", "stdout_changed", "no_runtime_signal"]
)
@pytest.mark.parametrize("behavioral", [False, True])
def test_runtime_contradiction_rule(
    expected: str, signal: str, behavioral: bool
) -> None:
    smoke = _load_validation_script("run_example_runtime_smoke.py")
    entry = {"expected": expected, "behavioral_break": behavioral}
    want = (
        expected in {"NO_CHANGE", "COMPATIBLE"}
        and not behavioral
        and signal in {"nonzero", "timeout"}
    )
    assert smoke.contradicts_ground_truth(entry, signal) is want


def test_semantic_review_manifest_is_well_formed() -> None:
    """Semantic review is recorded per case, apart from detector conformance:
    every listed case exists and cites evidence that is not abicheck's output."""
    matrix = _load_validation_script("collect_full_example_matrix.py")
    truth = json.loads(matrix.GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]
    reviews = matrix.semantic_reviews()
    assert reviews, "catalog/semantic_review.json lists no review"
    for case, review in reviews.items():
        assert case in truth, case
        evidence = review.get("evidence") or []
        assert evidence and set(evidence) <= matrix.SEMANTIC_REVIEW_EVIDENCE, case
        assert review.get("date") and review.get("note"), case


def test_matrix_never_reads_covered_as_reviewed() -> None:
    matrix = _load_validation_script("collect_full_example_matrix.py")
    result = matrix.build_matrix(
        gcc=None, clang=None, bundle=None, special_cli=None, runtime=None
    )
    reviews = matrix.semantic_reviews()
    summary = result["semantic_review"]
    assert summary["reviewed"] == len(reviews)
    assert summary["total"] == result["ground_truth_cases"]
