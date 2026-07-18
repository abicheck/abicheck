#!/usr/bin/env python3
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

"""Score an agent's attempt at an agent-evals/ task (CLAUDE.md "M1-5").

Run against a working tree that already contains the agent's changes on top
of the task's `base_commit`:

    python agent-evals/run_task.py --task add-change-kind-small

What it checks, in order (stopping at the first hard failure):

1. The manifest itself validates against schema/task-manifest.schema.json.
2. The working tree's diff from `base_commit` stays within `allowed_paths`,
   and does not touch this task's own `hidden_tests/` directory (the
   "edit-hidden-tests" forbidden action).
3. `required_checks` pass via `scripts/verify.py`.
4. `hidden_tests` pass via pytest, run only after step 3 — a change that
   doesn't pass its own project's gates hasn't earned a hidden-test run.

Emits a machine-readable JSON result to stdout (or `--json PATH`). This
runner does NOT attempt to detect every `forbidden` action in a manifest
(e.g. "weaken-existing-test", "expand-import-cycle-allowlist" require
semantic diff review a script can't reliably do) — those are flagged as
`"manual_review_required"` rather than silently treated as passing, so a
human/reviewer knows they still need to check.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover - dev dependency, see pyproject.toml
    jsonschema = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT / "agent-evals"
SCHEMA_PATH = EVALS_DIR / "schema" / "task-manifest.schema.json"

# forbidden tags this runner can actually detect vs. ones that need a human.
_AUTO_DETECTABLE_FORBIDDEN = frozenset({"edit-hidden-tests", "skip-required-checks"})


def _load_manifest(task_dir: Path) -> dict[str, Any]:
    manifest_path = task_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest.yaml in {task_dir}")
    with manifest_path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise SystemExit(f"{manifest_path}: expected a YAML mapping at top level")
    return loaded


def _validate_manifest(manifest: dict[str, Any]) -> list[str]:
    if jsonschema is None:
        return [
            "jsonschema not installed — skipped schema validation (install abicheck[dev])"
        ]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.path)}: {e.message}"
        for e in validator.iter_errors(manifest)
    ]


def _changed_paths(base_commit: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_commit}..HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        # Fall back to the working tree vs. base_commit (agent may not have
        # committed yet) rather than failing the whole run on a git error.
        proc = subprocess.run(
            ["git", "diff", "--name-only", base_commit],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    return [line for line in proc.stdout.splitlines() if line]


def _check_allowed_paths(
    changed: list[str], allowed_paths: list[str], task_name: str
) -> tuple[bool, list[str]]:
    violations = []
    hidden_tests_prefix = f"agent-evals/tasks/{task_name}/hidden_tests/"
    for path in changed:
        if path.startswith(hidden_tests_prefix):
            violations.append(
                f"{path}: edits hidden_tests/ (forbidden: edit-hidden-tests)"
            )
            continue
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed_paths):
            violations.append(f"{path}: outside allowed_paths")
    return (not violations, violations)


def _run_required_checks(profiles: list[str]) -> dict[str, Any]:
    results = {}
    for profile in profiles:
        receipt_path = EVALS_DIR / f".receipt-{profile}.json"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/verify.py",
                "--profile",
                profile,
                "--json",
                str(receipt_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        receipt: dict[str, Any] | None = None
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_path.unlink()
        results[profile] = {
            "returncode": proc.returncode,
            "passed": proc.returncode == 0,
            "receipt": receipt,
            "stdout_tail": "\n".join(proc.stdout.splitlines()[-30:]),
        }
    return results


def _run_hidden_tests(task_dir: Path, hidden_tests: list[str]) -> dict[str, Any]:
    test_paths = [str(task_dir / rel) for rel in hidden_tests]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *test_paths, "-v", "--tb=short"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-60:]),
    }


def score_task(
    task_name: str, *, base_commit_override: str | None = None
) -> dict[str, Any]:
    task_dir = EVALS_DIR / "tasks" / task_name
    manifest = _load_manifest(task_dir)

    schema_errors = _validate_manifest(manifest)
    if schema_errors:
        return {
            "task": task_name,
            "passed": False,
            "stage": "manifest-validation",
            "schema_errors": schema_errors,
        }

    base_commit = base_commit_override or manifest["base_commit"]
    changed = _changed_paths(base_commit)
    paths_ok, path_violations = _check_allowed_paths(
        changed, manifest["allowed_paths"], task_name
    )
    if not paths_ok:
        return {
            "task": task_name,
            "passed": False,
            "stage": "allowed-paths",
            "changed_paths": changed,
            "violations": path_violations,
        }

    checks = _run_required_checks(manifest["required_checks"])
    checks_ok = all(c["passed"] for c in checks.values())
    if not checks_ok:
        return {
            "task": task_name,
            "passed": False,
            "stage": "required-checks",
            "changed_paths": changed,
            "required_checks": checks,
        }

    hidden = _run_hidden_tests(task_dir, manifest["hidden_tests"])

    manual_review = sorted(
        set(manifest.get("forbidden", [])) - _AUTO_DETECTABLE_FORBIDDEN
    )

    return {
        "task": task_name,
        "passed": hidden["passed"],
        "stage": "hidden-tests",
        "changed_paths": changed,
        "required_checks": checks,
        "hidden_tests": hidden,
        "manual_review_required": manual_review,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task", required=True, help="Task directory name under agent-evals/tasks/"
    )
    parser.add_argument(
        "--base-commit",
        default=None,
        help="Override the manifest's base_commit (e.g. when scoring against a local rebase).",
    )
    parser.add_argument(
        "--json", metavar="PATH", default=None, help="Write the result JSON to PATH"
    )
    args = parser.parse_args(argv)

    result = score_task(args.task, base_commit_override=args.base_commit)

    rendered = json.dumps(result, indent=2)
    if args.json:
        Path(args.json).write_text(rendered + "\n", encoding="utf-8")
        print(f"result written to {args.json}")
    else:
        print(rendered)

    print(
        f"\n{args.task}: {'PASSED' if result['passed'] else 'FAILED'} (stage: {result['stage']})"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
