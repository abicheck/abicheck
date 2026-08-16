# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the fixed build/source release-proof artifact gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_build_source_release_proof.py"
)
_spec = importlib.util.spec_from_file_location("check_build_source_release_proof", _GATE_PATH)
assert _spec and _spec.loader
proof_gate = importlib.util.module_from_spec(_spec)
sys.modules["check_build_source_release_proof"] = proof_gate
_spec.loader.exec_module(proof_gate)


def _passing_artifact() -> dict[str, object]:
    return {
        "selected_cases": len(proof_gate.EXPECTED_CASE_IDS),
        "results": [
            {"case_id": case_id, "status": "PASS"}
            for case_id in sorted(proof_gate.EXPECTED_CASE_IDS)
        ]
    }


def test_accepts_the_complete_fixed_pass_set():
    assert proof_gate.validate(_passing_artifact()) == []


def test_rejects_skip_even_when_nine_other_cases_pass():
    artifact = _passing_artifact()
    artifact["results"][-1]["status"] = "SKIP"

    errors = proof_gate.validate(artifact)

    assert any("must have status PASS, found 'SKIP'" in error for error in errors)


def test_rejects_an_empty_result_set():
    errors = proof_gate.validate({"selected_cases": 10, "results": []})

    assert errors == ["expected exactly 10 result rows, found 0"]


def test_rejects_missing_or_wrong_selected_case_count():
    errors = proof_gate.validate({"selected_cases": 9, "results": []})

    assert errors == ["expected selected_cases=10, found 9"]
