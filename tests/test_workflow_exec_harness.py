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

import os
from pathlib import Path

import _workflow_exec
import pytest
import yaml
from _workflow_exec import (
    REPO_ROOT,
    _is_under_windows_system_dir,
    bash_executable,
    have_bash,
    is_wsl_launcher_stub,
    make_workspace,
    require_bash,
    run_step,
    select_real_bash,
)

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
    remaining = total_length - len(head)
    whole, partial = divmod(remaining, len(filler))
    body = head + filler * whole
    if partial:
        # A truncated final comment line, so the body is *exactly* the
        # requested length rather than rounded up past it -- otherwise the
        # `limit - 1` case in the sweep below silently ran at or above the
        # limit and proved nothing about the boundary (CodeRabbit review,
        # PR #1230). `partial >= 1`, and a comment line of any length is
        # still a valid, no-op shell line, so this never changes behavior.
        body += "#" * (partial - 1) + "\n"
    return body


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
    body = _padded_body(size)
    # The sweep is only about the boundary if the body really is that long.
    assert len(body) == size
    result = run_step(
        {"run": body},
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
    padded = _padded_body(longest)
    assert len(padded) == longest
    result = run_step(
        {"run": padded},
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
    # Nor left behind beside it: the script is deleted in a `finally`, so a
    # caller whose workspace parent is not a pytest-managed temporary
    # directory never accumulates one file per step (CodeRabbit review).
    assert not list(workspace.parent.glob("_step_body_*.sh"))


def test_step_body_script_is_cleaned_up_even_when_the_body_fails(
    tmp_path: Path,
) -> None:
    """The cleanup is in a `finally`, so a non-zero exit is covered too."""
    workspace = make_workspace(tmp_path)
    result = run_step({"run": "exit 3\n"}, workspace=workspace)
    assert result.returncode == 3
    assert not list(workspace.parent.glob("_step_body_*.sh"))


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


#: Directory-name shapes that are legal on the running platform and awkward for
#: a path that reaches a shell. The first three hold everywhere; the last two are
#: POSIX-only (Windows forbids both characters in a filename), and the backslash
#: is the one that matters most: the harness has to translate separators for Git
#: Bash without corrupting a POSIX name that legitimately contains one.
_AWKWARD_PARENT_NAMES = [
    pytest.param("plain", id="plain"),
    pytest.param("with space", id="space"),
    pytest.param("wïth-ünicode", id="non-ascii"),
    *(
        []
        if os.name == "nt"
        else [
            pytest.param("with\\backslash", id="backslash"),
            pytest.param("with'quote", id="single-quote"),
        ]
    ),
]


@pytest.mark.parametrize("parent_name", _AWKWARD_PARENT_NAMES)
def test_run_step_executes_under_an_awkward_parent_directory(
    tmp_path: Path, parent_name: str
) -> None:
    """The step-body script path must survive the platform's own legal names.

    Codex review (PR #1230): the path handed to bash was rewritten with an
    unconditional ``str(body).replace("\\\\", "/")``. On POSIX a backslash is an
    ordinary filename character, so a workspace under ``with\\backslash/`` was
    executed from ``with/backslash/`` — every `run_step` call under such a path
    failing with "No such file or directory". Stated here as the general
    invariant (a path legal on this platform works) over several independently
    awkward shapes, rather than a single repro of the backslash case.
    """
    base = tmp_path / parent_name
    base.mkdir()
    workspace = make_workspace(base)
    result = run_step(
        {"run": _padded_body(256)}, workspace=workspace, env={"PADDED_SIZE": "256"}
    )
    assert result.returncode == 0, result.stderr
    assert result.outputs["size"] == "256"


class TestBashResolutionNeverFallsBackToWhatItRejected:
    """Bug class: a resolver that rejects a candidate must not then return it.

    `bash_executable()` rejects Windows' WSL launcher stub -- and an earlier
    revision then returned the bare string `"bash"` anyway, which every direct
    caller resolved straight back to the stub it had just rejected, while
    `have_bash()` reported True because `is_wsl_launcher_stub("bash")` is
    False for an unresolved name and `shutil.which("bash")` then found the
    stub (Codex review, P2). The reject was decorative: nothing downstream
    changed, so the migrated modules still failed instead of skipping.

    Exercised through `select_real_bash`, the pure half of the decision,
    against real directories built under `tmp_path`. Deliberately not by
    monkeypatching `os.name` to "nt": `pathlib.Path()` picks its flavour from
    exactly that, so the patch makes every `Path(...)` in the function under
    test raise instead of running -- the test would be exercising the patch.
    """

    @staticmethod
    def _layout(tmp_path: Path, *, stub: bool, real: bool) -> tuple[str, list[str]]:
        """A fake `%SystemRoot%` plus the bash candidates PATH would yield."""
        system_root = tmp_path / "Windows"
        system32 = system_root / "System32"
        system32.mkdir(parents=True)
        git_bin = tmp_path / "Git" / "bin"
        git_bin.mkdir(parents=True)
        candidates = []
        if stub:
            (system32 / "bash.exe").write_text("stub", encoding="utf-8")
            candidates.append(str(system32 / "bash.exe"))
        if real:
            (git_bin / "bash.exe").write_text("real", encoding="utf-8")
            candidates.append(str(git_bin / "bash.exe"))
        return str(system_root), candidates

    def test_a_stub_only_machine_resolves_to_nothing(self, tmp_path: Path) -> None:
        """The half that made whole modules fail instead of skip: the answer
        must be an explicit "none", never the stub under another name."""
        root, candidates = self._layout(tmp_path, stub=True, real=False)
        assert select_real_bash(candidates, system_root=root) is None

    def test_a_real_bash_after_the_stub_is_still_found(self, tmp_path: Path) -> None:
        """`shutil.which` answers with the first match only, so rejecting it
        used to end the search rather than continue past it."""
        root, candidates = self._layout(tmp_path, stub=True, real=True)
        chosen = select_real_bash(candidates, system_root=root)
        assert chosen is not None and chosen.endswith(
            "Git/bin/bash.exe".replace("/", os.sep)
        )

    def test_a_real_bash_before_the_stub_is_found_too(self, tmp_path: Path) -> None:
        """Order-independence, so the case above cannot pass by accident of
        the order the fixture happens to build."""
        root, candidates = self._layout(tmp_path, stub=True, real=True)
        chosen = select_real_bash(list(reversed(candidates)), system_root=root)
        assert chosen is not None
        assert not _is_under_windows_system_dir(chosen, root)

    def test_no_candidates_at_all_resolve_to_nothing(self, tmp_path: Path) -> None:
        root, _ = self._layout(tmp_path, stub=False, real=False)
        assert select_real_bash([], system_root=root) is None

    @pytest.mark.parametrize("sub", ["System32", "SysWOW64", "Sysnative"])
    def test_every_system_directory_alias_is_recognized(
        self, tmp_path: Path, sub: str
    ) -> None:
        """All three spellings of the same launcher, not just the one the
        report named."""
        system_root = tmp_path / "Windows"
        directory = system_root / sub
        directory.mkdir(parents=True)
        stub = directory / "bash.exe"
        stub.write_text("stub", encoding="utf-8")
        assert _is_under_windows_system_dir(str(stub), str(system_root)) is True
        assert select_real_bash([str(stub)], system_root=str(system_root)) is None

    def test_a_real_bash_is_never_mistaken_for_the_stub(self, tmp_path: Path) -> None:
        """The other direction, so a rule that rejected everything -- which
        would also make `have_bash()` always False -- fails here."""
        root, candidates = self._layout(tmp_path, stub=False, real=True)
        assert _is_under_windows_system_dir(candidates[0], root) is False
        assert select_real_bash(candidates, system_root=root) == candidates[0]

    def test_the_two_public_functions_agree_on_this_machine(self) -> None:
        """The invariant the defect broke, on whatever platform runs this:
        `have_bash()` is true exactly when what `bash_executable()` returns is
        a real bash. Their disagreement was the whole finding."""
        assert have_bash() is (not is_wsl_launcher_stub(bash_executable()))


class TestRequireBashSkipsRatherThanRunsTheStub:
    """`bash_executable()` still returns a `str` on a machine with no bash --
    every call site hands it straight to `subprocess` -- so a caller that
    shells out has to ask `have_bash()` and skip. Without that, the resolver
    can be perfectly correct and the calling module still fails against the
    WSL launcher's own error text (Codex review, P2: the migrated modules had
    the resolver fixed under them but no guard of their own).
    """

    def test_it_skips_when_no_real_bash_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_workflow_exec, "have_bash", lambda: False)
        with pytest.raises(BaseException) as excinfo:
            require_bash()
        # `pytest.skip` raises `Skipped`, which is a BaseException subclass --
        # caught by type name so this does not depend on the private import
        # path of pytest's outcome classes.
        assert type(excinfo.value).__name__ == "Skipped"

    def test_it_does_not_skip_when_a_real_bash_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other direction, so a guard that always skipped -- which would
        silently empty every shell-based module -- fails here."""
        monkeypatch.setattr(_workflow_exec, "have_bash", lambda: True)
        require_bash()
