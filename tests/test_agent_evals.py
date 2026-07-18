# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structural tests for agent-evals/ (CLAUDE.md "M1-5").

Guards the manifest schema/shape and run_task.py's own plumbing (path
matching, manifest loading) — NOT whether any particular task's hidden test
is currently red, since a task's underlying gap may legitimately get closed
by unrelated work later (see agent-evals/README.md).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT / "agent-evals"
SCHEMA_PATH = EVALS_DIR / "schema" / "task-manifest.schema.json"
TASKS_DIR = EVALS_DIR / "tasks"

_spec = importlib.util.spec_from_file_location(
    "abicheck_agent_evals_run_task", EVALS_DIR / "run_task.py"
)
assert _spec and _spec.loader
run_task = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run_task
_spec.loader.exec_module(run_task)


def _task_dirs() -> list[Path]:
    return sorted(p for p in TASKS_DIR.iterdir() if p.is_dir())


def test_schema_file_is_valid_json_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
def test_manifest_validates_against_schema(task_dir: Path) -> None:
    manifest = run_task._load_manifest(task_dir)
    errors = run_task._validate_manifest(manifest)
    assert not errors, f"{task_dir.name}/manifest.yaml: {errors}"


@pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
def test_manifest_name_matches_directory(task_dir: Path) -> None:
    manifest = run_task._load_manifest(task_dir)
    assert manifest["name"] == task_dir.name


@pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
def test_manifest_base_commit_exists_in_history(task_dir: Path) -> None:
    import subprocess

    manifest = run_task._load_manifest(task_dir)
    proc = subprocess.run(
        ["git", "cat-file", "-e", manifest["base_commit"]],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"{task_dir.name}/manifest.yaml: base_commit "
        f"{manifest['base_commit']!r} is not a known commit in this repo"
    )


@pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
def test_hidden_test_files_exist(task_dir: Path) -> None:
    manifest = run_task._load_manifest(task_dir)
    for rel in manifest["hidden_tests"]:
        assert (task_dir / rel).is_file(), f"{task_dir.name}: missing hidden test {rel}"


@pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
def test_forbidden_tags_are_known_to_schema(task_dir: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    known = set(schema["properties"]["forbidden"]["items"]["enum"])
    manifest = run_task._load_manifest(task_dir)
    assert set(manifest.get("forbidden", [])) <= known


def test_load_manifest_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run_task._load_manifest(tmp_path / "does-not-exist")


def test_load_manifest_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    task_dir = tmp_path / "bad-task"
    task_dir.mkdir()
    (task_dir / "manifest.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run_task._load_manifest(task_dir)


class TestCheckAllowedPaths:
    def test_all_paths_within_allowlist_pass(self) -> None:
        ok, violations = run_task._check_allowed_paths(
            changed=["abicheck/model.py", "tests/test_model.py"],
            allowed_paths=["abicheck/*.py", "tests/**"],
            task_name="some-task",
        )
        assert ok
        assert violations == []

    def test_path_outside_allowlist_fails(self) -> None:
        ok, violations = run_task._check_allowed_paths(
            changed=["abicheck/model.py", "abicheck/cli.py"],
            allowed_paths=["abicheck/model.py"],
            task_name="some-task",
        )
        assert not ok
        assert any("abicheck/cli.py" in v for v in violations)

    def test_editing_own_hidden_tests_is_flagged_even_if_path_allowed(self) -> None:
        ok, violations = run_task._check_allowed_paths(
            changed=["agent-evals/tasks/some-task/hidden_tests/test_x.py"],
            allowed_paths=["agent-evals/**"],
            task_name="some-task",
        )
        assert not ok
        assert any("edit-hidden-tests" in v for v in violations)

    def test_no_changes_passes_trivially(self) -> None:
        ok, violations = run_task._check_allowed_paths(
            changed=[],
            allowed_paths=["abicheck/*.py"],
            task_name="some-task",
        )
        assert ok
        assert violations == []
