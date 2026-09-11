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

"""Contract tests for ``tests/_workflow_exec.py``'s own ``run_step``.

Bug class this closes, stated as an invariant rather than one reproducer:
**a step's ``run:`` body length must not affect whether the harness can
execute it.** ``run_step`` used to spawn ``bash -c <body>``, which put the
whole body on the child's command line -- fine on POSIX, but Windows caps a
command line at 32767 characters, so the moment
``actions/check-target/action.yml``'s ``assurance_overlay`` step's body grew
past that (it is ~50 KB today) every executing test in
``tests/test_reusable_workflows_require_complete_analysis.py`` and its
siblings failed on the windows-latest lane with ``FileNotFoundError:
[WinError 206] The filename or extension is too long`` -- a harness spawn
error, not a judgement about the step. The fix writes the body to a real
script file, which is also what the runner itself does (``bash -e {0}``).

The generalized test below therefore sweeps body sizes across and far past
that ceiling, and separately pins the *largest real body in the repository*
(so a future step that grows past a new platform limit is caught here, at
the harness, instead of as an unexplained red lane), rather than asserting
only that one 50 KB step now runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from _workflow_exec import REPO_ROOT, have_bash, make_workspace, run_step

pytestmark = pytest.mark.skipif(not have_bash(), reason="bash not available")

#: Windows' own ``CreateProcess`` command-line ceiling -- the boundary the
#: sweep below is built around.
_WINDOWS_COMMAND_LINE_LIMIT = 32767


def _padded_body(total_length: int) -> str:
    """A real, side-effect-visible step body padded to *total_length* chars.

    The padding is trailing comment lines, so the body's *behavior* is
    identical at every size and the only variable under test is its length.
    """
    head = 'printf "%s\\n" "size=$PADDED_SIZE" >> "$GITHUB_OUTPUT"\n'
    if total_length <= len(head):
        return head
    filler = "# padding\n"
    pad = (total_length - len(head)) // len(filler) + 1
    return head + filler * pad


def _real_run_bodies() -> list[str]:
    """Every ``run:`` body in the repo's workflows and composite actions."""
    bodies: list[str] = []
    documents = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    documents += sorted((REPO_ROOT / "actions").glob("*/action.yml"))
    for path in documents:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        step_lists = []
        for job in (data.get("jobs") or {}).values():
            if isinstance(job, dict):
                step_lists.append(job.get("steps") or [])
        runs = data.get("runs")
        if isinstance(runs, dict):
            step_lists.append(runs.get("steps") or [])
        for steps in step_lists:
            for step in steps:
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    bodies.append(step["run"])
    return bodies


@pytest.mark.parametrize(
    "size",
    [
        64,
        4_096,
        _WINDOWS_COMMAND_LINE_LIMIT - 1,
        _WINDOWS_COMMAND_LINE_LIMIT,
        _WINDOWS_COMMAND_LINE_LIMIT + 1,
        64_000,
        250_000,
    ],
)
def test_run_step_executes_a_body_of_any_length(tmp_path: Path, size: int) -> None:
    workspace = make_workspace(tmp_path)
    result = run_step(
        {"run": _padded_body(size)},
        workspace=workspace,
        env={"PADDED_SIZE": str(size)},
    )
    assert result.returncode == 0, result.stderr
    assert result.outputs["size"] == str(size)


def test_largest_real_step_body_is_executable_by_the_harness(tmp_path: Path) -> None:
    """The repository's own longest ``run:`` body, at its real length.

    Padded to that length rather than executed verbatim (a real body needs
    its own inputs, which is its own module's job): what this pins is that
    the harness can *spawn* a body that big, which is exactly what broke.
    """
    bodies = _real_run_bodies()
    assert bodies, "no run: steps discovered — the sweep above would be vacuous"
    longest = max(len(body) for body in bodies)
    workspace = make_workspace(tmp_path)
    result = run_step(
        {"run": _padded_body(longest)},
        workspace=workspace,
        env={"PADDED_SIZE": str(longest)},
    )
    assert result.returncode == 0, result.stderr
    assert result.outputs["size"] == str(longest)


def test_step_body_script_is_not_left_inside_the_workspace(tmp_path: Path) -> None:
    """The script file must not show up in what the step itself produced.

    ``StepResult.tree()`` and several ``$RUNNER_TEMP`` assertions in the
    workflow tests enumerate the workspace, so the harness's own scratch
    file living there would silently change what those tests see.
    """
    workspace = make_workspace(tmp_path)
    result = run_step(
        {"run": _padded_body(40_000)},
        workspace=workspace,
        env={"PADDED_SIZE": "40000"},
    )
    assert result.returncode == 0, result.stderr
    assert not [name for name in result.tree() if "_step_body" in name]


def test_body_reaches_bash_byte_for_byte(tmp_path: Path) -> None:
    """No newline translation between the YAML body and bash.

    Codex review (PR #1230): writing the script with `Path.write_text` used
    Python's default `newline=None`, which rewrites every ``\\n`` to
    ``\\r\\n`` on Windows. Git Bash keeps that carriage return inside shell
    tokens, so a heredoc delimiter line becomes ``EOF\\r`` and never
    terminates the heredoc -- a real body like the ``assurance_overlay``
    step's would fail differently rather than run. The oracle here is the
    shell's own heredoc/quoting behavior, not the harness's notion of a
    newline: this body cannot succeed under CRLF.
    """
    workspace = make_workspace(tmp_path)
    run = (
        "cat <<'MARKER_EOF' >> \"$GITHUB_OUTPUT\"\n"
        "marker=intact\n"
        "MARKER_EOF\n"
        'value="no-trailing-cr"\n'
        'printf "%s\\n" "value=$value" >> "$GITHUB_OUTPUT"\n'
    )
    result = run_step({"run": run}, workspace=workspace)
    assert result.returncode == 0, result.stderr
    assert result.outputs["marker"] == "intact"
    # A surviving CR would ride along at the end of the value rather than
    # failing the shell, so assert the exact string, not a prefix.
    assert result.outputs["value"] == "no-trailing-cr"
    assert "\r" not in "".join(result.output_lines)
