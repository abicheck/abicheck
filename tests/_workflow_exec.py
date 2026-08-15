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

"""Execute a workflow's ``run:`` steps for real, in a throwaway workspace.

The reusable-workflow tests are almost entirely structural — they assert that
a step's ``run:`` is the literal string ``rm -rf .check-single-baseline``, that
a clear step precedes its download, that a step carries no ``env:``. Those are
good, cheap guards against a careless edit, and they are also the shape that
let #705 ship: asserting the *text* of a workflow proves nothing about what the
text does when a hostile value reaches it. #758 had to add the executing test
afterwards.

This runs the step's actual shell (or ``python3 -c``) body against a real
temporary workspace with a real ``$GITHUB_OUTPUT``, so a test can assert the
*effect*: what got deleted, what survived, and exactly which records were
written.

Only ``run:`` steps are executable — a ``uses:`` step is someone else's action
and stays structural.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def load_workflow(name: str) -> dict[str, Any]:
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def find_run_step(workflow: str, job: str, step_name: str) -> dict[str, Any]:
    """The named step, which must have a ``run:`` body.

    Looking the step up by its display name is deliberate: if someone renames
    or deletes it, this raises instead of silently testing nothing.
    """
    steps = load_workflow(workflow)["jobs"][job].get("steps", [])
    for step in steps:
        if step.get("name") == step_name:
            if "run" not in step:
                raise AssertionError(
                    f"step {step_name!r} has no `run:` body (uses: "
                    f"{step.get('uses')!r}) — only run steps are executable"
                )
            return step
    available = [s.get("name") for s in steps if "run" in s]
    raise AssertionError(
        f"no step named {step_name!r} in {workflow}:{job}. Runnable steps: {available}"
    )


@dataclass
class StepResult:
    returncode: int
    stdout: str
    stderr: str
    #: Raw lines appended to $GITHUB_OUTPUT, in order — the *records*, so a
    #: test can catch an injected extra record, not just a wrong value.
    output_lines: list[str]
    workspace: Path

    @property
    def outputs(self) -> dict[str, str]:
        """Last-wins parse, matching how the runner reads $GITHUB_OUTPUT."""
        parsed: dict[str, str] = {}
        for line in self.output_lines:
            if "=" in line:
                key, _, value = line.partition("=")
                parsed[key] = value
        return parsed

    def tree(self) -> set[str]:
        """Every path under the workspace, relative and POSIX-style."""
        return {
            p.relative_to(self.workspace).as_posix() for p in self.workspace.rglob("*")
        }


def _bash() -> str:
    """A real bash, bypassing Windows' WSL launcher stub.

    Mirrors ``test_action_run_sh_helpers._bash_executable``.
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return "bash"


def run_step(
    step: dict[str, Any],
    *,
    workspace: Path,
    env: dict[str, str] | None = None,
) -> StepResult:
    """Execute one ``run:`` step inside *workspace*.

    The environment is built from scratch rather than inherited, so a test
    cannot pass by accident because the developer's own shell happened to
    export something the workflow relies on.
    """
    github_output = workspace / "_github_output"
    github_output.write_text("", encoding="utf-8")

    step_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace),
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(workspace / "_runner_temp"),
    }
    (workspace / "_runner_temp").mkdir(exist_ok=True)
    # The step's own declared env, with unresolved ${{ }} expressions left to
    # the caller to substitute — a test that forgets is passing a literal
    # expression, which is visible rather than silently empty.
    step_env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
    step_env.update(env or {})

    proc = subprocess.run(
        # `-e` as well as pipefail: the runner invokes a `run:` body as
        # `bash -e {0}` (and `-eo pipefail` for `shell: bash`), so without it a
        # command failing mid-body left returncode 0 here while the real step
        # failed — every `assert result.returncode == 0` in the workflow tests
        # was weaker than the thing it models (CodeRabbit review).
        [_bash(), "-eo", "pipefail", "-c", step["run"]],
        cwd=workspace,
        env=step_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # `$GITHUB_OUTPUT` is written by the step, not by us, so its bytes are
    # whatever the runner's shell produced. On Windows a non-ASCII input
    # reaches Git Bash through the ANSI code page and comes back as cp1252,
    # and decoding it as UTF-8 raised — the harness failing on the hostile
    # input instead of judging the step's answer about it. Decoding leniently
    # does not weaken any assertion: an undecodable byte becomes U+FFFD, which
    # is not alphanumeric, so a byte that survived sanitization still fails
    # the checks that matter.
    decoded = github_output.read_bytes().decode("utf-8", errors="replace")
    lines = [line for line in decoded.splitlines() if line != ""]
    return StepResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        output_lines=lines,
        workspace=workspace,
    )


def make_workspace(base: Path, *, files: dict[str, str] | None = None) -> Path:
    """A checkout-shaped workspace, plus a sentinel tree *outside* it.

    The sentinel is what makes an escape detectable: a step that deletes or
    writes above its own directory shows up as a missing/extra sentinel rather
    than as a passing test.
    """
    workspace = base / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    outside = base / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "MUST_SURVIVE.txt").write_text("do not delete", encoding="utf-8")
    for rel, content in (files or {}).items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def outside_is_intact(base: Path) -> bool:
    return (base / "outside" / "MUST_SURVIVE.txt").is_file()


def have_bash() -> bool:
    """Is a real bash actually available?

    The `or os.name != "nt"` this used to carry short-circuited the lookup on
    every POSIX platform, so a machine without bash reported that it had one
    and callers ran the steps instead of skipping — the opposite of what the
    name promises (CodeRabbit review).
    """
    candidate = _bash()
    return Path(candidate).is_file() or shutil.which(candidate) is not None
