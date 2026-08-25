"""Regression coverage for project-cell evidence and consumer extraction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECK_PROJECT = ROOT / ".github" / "workflows" / "check-project.yml"
CHECK_TARGET = ROOT / "actions" / "check-target" / "action.yml"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(container: dict[str, Any]) -> list[dict[str, Any]]:
    return container["steps"]


def test_consumer_compile_context_uses_a_separate_extraction() -> None:
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    assert (
        "matrix.consumer_compile_ast_frontend"
        in run_step["with"]["consumer-ast-frontend"]
    )
    assert "matrix.consumer_compile_gcc_path" in run_step["with"]["consumer-gcc-path"]
    assert (
        "matrix.consumer_compile_gcc_options"
        in run_step["with"]["consumer-gcc-options"]
    )

    target = _load(CHECK_TARGET)
    steps = _steps(target["runs"])
    consumer = next(
        step
        for step in steps
        if step.get("name") == "Extract candidate consumer context"
    )
    assert consumer["with"]["mode"] == "dump"
    assert consumer["with"]["new-library"] == "${{ inputs.new-library }}"
    assert consumer["with"]["ast-frontend"] == "${{ inputs.consumer-ast-frontend }}"
    analysis = next(step for step in steps if step.get("name") == "Run analysis")
    assert "check-target-consumer.abi.json" in analysis["with"]["new-library"]
    assert "consumer_context.outcome == 'success'" in analysis["with"]["header"]


def test_target_evidence_path_is_forwarded_per_matrix_cell() -> None:
    project = _load(CHECK_PROJECT)
    steps = _steps(project["jobs"]["check"])
    candidate = next(
        step
        for step in steps
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    assert ".get('evidence', {}).get('path'" in candidate["run"]
    run_step = next(step for step in steps if step.get("name") == "Run check-target")
    assert run_step["with"]["evidence-pack-path"] == (
        "${{ steps.candidate.outputs.evidence-pack }}"
    )
    assert run_step["with"]["evidence-producer"].startswith(
        "${{ steps.candidate.outputs.evidence-producer"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_selects_only_the_current_targets_evidence(tmp_path: Path) -> None:
    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    (tmp_path / "candidate").mkdir()
    (tmp_path / "candidate" / "libmath.so").write_text("binary")
    build_root = tmp_path / "build-output"
    (build_root / "evidence" / "core").mkdir(parents=True)
    (build_root / "evidence" / "math").mkdir(parents=True)
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "evidence_producer": {"kind": "clang-plugin"},
                "targets": [
                    {"id": "core", "evidence": {"path": "evidence/core"}},
                    {"id": "math", "evidence": {"path": "evidence/math"}},
                ],
            }
        )
    )
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(
                {"kind": "target", "name": "math", "binary_pattern": "*.so"}
            ),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text().splitlines()
        if "=" in line
    )
    assert outputs["evidence-pack"] == str((build_root / "evidence" / "math").resolve())
    assert outputs["evidence-producer"] == "clang-plugin"
    assert "evidence/core" not in outputs["evidence-pack"]


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
@pytest.mark.parametrize("malicious_path", ["../outside", "/tmp/outside"])
def test_resolver_rejects_escape_without_outside_side_effects(
    tmp_path: Path, malicious_path: str
) -> None:
    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    (tmp_path / "candidate").mkdir()
    (tmp_path / "candidate" / "libcore.so").write_text("binary")
    build_root = tmp_path / "build-output"
    build_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    (build_root / "build-output.json").write_text(
        json.dumps({"targets": [{"id": "core", "evidence": {"path": malicious_path}}]})
    )
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(
                {"kind": "target", "name": "core", "binary_pattern": "*.so"}
            ),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "absolute or escapes" in result.stderr
    assert sentinel.read_text() == "unchanged"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]
    assert "evidence-pack=" not in github_output.read_text()
