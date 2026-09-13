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
import shutil
from collections.abc import Mapping
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
        """The invariant the defect broke, on whatever platform runs this.

        Deliberately unguarded, unlike every migrated call site: this test
        never shells out, so there is no stub to run -- and a bash-less or
        stub-only machine is precisely the state where the two functions
        disagreed, so skipping there would retire the assertion exactly where
        it earns its keep (CodeRabbit review; the guard was inserted here by
        the migration sweep, not by anyone reading the test).

        Stated over three states rather than two, which is the correction the
        de-guarding forced (Codex review). `have_bash() is not
        is_wsl_launcher_stub(bash_executable())` reads as the invariant but is
        only true on two of the three: on a genuinely bash-less host
        `have_bash()` is False while `bash_executable()` returns its
        documented `"bash"` fallback, which is not a stub, so the shorthand
        claims disagreement where there is none -- it would have turned the
        skip into a *failure* on exactly the host the paragraph above says
        this test is for. What the two functions actually promise is: when
        one reports a bash, the other hands back a real, runnable one; when
        it does not, the other hands back the fallback and claims nothing."""
        resolved = bash_executable()
        if have_bash():
            assert not is_wsl_launcher_stub(resolved)
            assert shutil.which(resolved) or Path(resolved).is_file(), (
                f"have_bash() is True but {resolved!r} is not a runnable bash"
            )
        else:
            assert resolved == "bash", (
                "with no real bash the resolver must return its documented "
                f"fallback rather than {resolved!r}, which would read as a "
                "claim that this machine has one"
            )


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


class TestRunStepNeverFabricatesAVanishedWorkspace:
    """The harness itself must not re-create the directory it runs the step in.

    Stated against the real `run_step`, not against the guard it calls: the
    guard existing proves nothing about whether this harness still reaches for
    `mkdir`, and a `mkdir(parents=True, exist_ok=True)` here reads as harmless
    housekeeping. It is not -- with the workspace reaped between preparation
    and the step, it hands the body an empty checkout, and a body that does not
    happen to need the seeded files exits 0 and returns plausible outputs for a
    workspace that no longer exists (Codex review, PR #1292).

    The body below is exactly that kind: it writes an output and never touches
    the seed, so it is the case that *passes* if the fabrication comes back.
    """

    def test_a_workspace_reaped_before_the_step_fails_loudly(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path, files={"seed.txt": "seeded"})
        step = {"run": 'echo "answer=ok" >> "$GITHUB_OUTPUT"'}

        class _ReapingEnv(Mapping):
            """Removes the workspace at the moment the step env is assembled.

            A `Mapping` rather than a `dict` subclass on purpose: `dict.update`
            takes a C fast path for dict subclasses and never calls an
            overridden `keys`/`items`, so a subclass hook silently never fires
            and the test passes for the wrong reason. It also carries a real
            entry, because `run_step` merges it as `env or {}` and an empty
            mapping is falsy -- which is how the first version of this test
            passed while deleting nothing at all.
            """

            def __init__(self, target: Path) -> None:
                self._target = target

            def __iter__(self):
                shutil.rmtree(self._target, ignore_errors=True)
                return iter(("REAPED",))

            def __len__(self) -> int:
                return 1

            def __getitem__(self, key):
                if key == "REAPED":
                    return "1"
                raise KeyError(key)

        with pytest.raises(AssertionError) as excinfo:
            run_step(step, workspace=workspace, env=_ReapingEnv(workspace))

        assert not workspace.exists(), (
            "the harness must not have re-created the workspace; a step run in "
            "a fabricated empty one returns an answer for a checkout that is gone"
        )
        # Either guard may be the one that fires -- which depends only on where
        # in the sequence the reaper landed -- so the assertion is on the
        # consequence they share: no answer was produced for a checkout that is
        # gone. The message must still name the workspace.
        assert str(workspace) in str(excinfo.value), excinfo.value

    def test_an_intact_workspace_still_runs_normally(self, tmp_path: Path) -> None:
        """Vacuity guard: the ordinary path must be untouched by the check."""

        workspace = make_workspace(tmp_path, files={"seed.txt": "seeded"})
        result = run_step(
            {"run": 'echo "answer=ok" >> "$GITHUB_OUTPUT"'}, workspace=workspace
        )
        assert result.returncode == 0
        assert result.output_lines == ["answer=ok"]
        assert (workspace / "seed.txt").read_text(encoding="utf-8") == "seeded"


def test_run_step_contains_no_call_that_creates_the_workspace() -> None:
    """Structural, because the behavioural test above cannot reach this.

    A reaper that strikes before `run_writing_env_file` prepares the output
    file trips *that* guard first, so an end-to-end test can never distinguish
    a `run_step` that would have fabricated the workspace from one that would
    not -- the window between preparation and the step is not reachable from
    outside. What is checkable is the thing that regressed: this harness must
    not contain a call that creates the directory it was handed. `mkdir` on
    `$RUNNER_TEMP` (which the harness owns) stays allowed; `mkdir` on the
    workspace itself, or on its parent, does not.

    The direct behaviour of the guard this leaves in its place is covered by
    `TestAVanishedWorkspaceIsNeverFabricated` in
    `tests/test_tmp_tree_resilience.py`.
    """

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(_workflow_exec.run_step))
    creations = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"mkdir", "makedirs"}
        and ast.unparse(node.func.value)
        in {"workspace", "workspace.parent", "body.parent"}
    ]
    assert creations == [], (
        "run_step must not create the workspace (or its parent): the caller "
        "seeds fixtures there, and a fabricated empty one lets a step that "
        f"does not need them return a plausible answer. Found: {creations}"
    )


class TestEveryStepGetsAPrivateTmpdir:
    """Bug class ``harness.constructed_environment_drops_ambient_mitigation``.

    ``run_step`` builds its environment from scratch, deliberately, so a
    step cannot pass because the developer's shell exported something. The
    cost of that is silent: a variable the *surrounding job* sets as a
    mitigation is dropped on the floor, and the step runs under the exact
    condition the job had just moved away from. That is not hypothetical --
    `.github/workflows/ci.yml` points the unit-test job's ``TMPDIR`` at
    ``$RUNNER_TEMP`` because something prunes ``/tmp`` on the hosted runners
    mid-job, and `actions/check-target/action.yml`'s assurance-overlay step
    still failed with ``cd: /tmp/tmp.XjdPkLm7IE: No such file or directory``
    afterwards, because its own ``mktemp -d`` was still resolving against
    ``/tmp``.

    The invariant is stated over *arbitrary* step bodies rather than that
    one step: whatever a step body asks the system for a temporary file, it
    must land in a directory this harness owns, per step -- and a caller
    that states its own ``TMPDIR`` must still win.
    """

    @staticmethod
    def _tmpdir_probe() -> dict[str, str]:
        return {
            "run": (
                'printf "tmpdir=%s\\n" "${TMPDIR:-<unset>}" >> "$GITHUB_OUTPUT"\n'
                'd="$(mktemp -d)"\n'
                'printf "made=%s\\n" "$d" >> "$GITHUB_OUTPUT"\n'
                'f="$(mktemp)"\n'
                'printf "file=%s\\n" "$f" >> "$GITHUB_OUTPUT"\n'
            )
        }

    def test_tmpdir_is_a_harness_owned_per_step_directory(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        result = run_step(self._tmpdir_probe(), workspace=workspace)

        assert result.returncode == 0, result.stderr
        tmpdir = Path(result.outputs["tmpdir"])
        assert tmpdir.parent == workspace.parent, (
            "a step's $TMPDIR must be a directory this harness allocated "
            f"beside the workspace, not {tmpdir}"
        )
        # Outside the workspace and outside $RUNNER_TEMP, so `tree()` and the
        # `$RUNNER_TEMP` assertions keep seeing only the step's own output.
        assert workspace not in tmpdir.parents
        for name in ("made", "file"):
            created = Path(result.outputs[name])
            assert tmpdir in created.parents, (
                f"`mktemp` resolved {created} outside the step's own $TMPDIR"
            )

    def test_mktemp_does_not_resolve_against_the_shared_system_temp(
        self, tmp_path: Path
    ) -> None:
        """The property that actually failed in CI, stated directly.

        `/tmp` is the thing being escaped, so assert against it by identity
        rather than trusting the allocation site above to keep being right:
        a future refactor that points `$TMPDIR` back at the shared temp
        would still satisfy "it is a directory somebody allocated".
        """
        import tempfile

        workspace = make_workspace(tmp_path)
        result = run_step(self._tmpdir_probe(), workspace=workspace)

        assert result.returncode == 0, result.stderr
        shared = {Path("/tmp"), Path(tempfile.gettempdir())}
        for name in ("made", "file"):
            created = Path(result.outputs[name])
            assert created.parent not in shared, (
                f"{name} landed directly in the shared system temp ({created})"
            )

    def test_two_steps_do_not_share_a_tmpdir(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        first = run_step(self._tmpdir_probe(), workspace=workspace)
        second = run_step(self._tmpdir_probe(), workspace=workspace)

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert first.outputs["tmpdir"] != second.outputs["tmpdir"]

    def test_the_step_tmpdir_is_removed_afterwards(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        result = run_step(self._tmpdir_probe(), workspace=workspace)

        assert result.returncode == 0, result.stderr
        assert not Path(result.outputs["tmpdir"]).exists()

    @pytest.mark.parametrize("source", ["step-env", "caller-env"])
    def test_an_explicit_tmpdir_still_wins(self, tmp_path: Path, source: str) -> None:
        """Several tests set `TMPDIR` on purpose (a relative value, a value
        nested under an input path) to exercise a script's own handling of
        it. The harness default must not outrank either spelling."""
        workspace = make_workspace(tmp_path)
        chosen = tmp_path / "chosen_tmp"
        chosen.mkdir()
        step = dict(self._tmpdir_probe())
        env = None
        if source == "step-env":
            step["env"] = {"TMPDIR": str(chosen)}
        else:
            env = {"TMPDIR": str(chosen)}

        result = run_step(step, workspace=workspace, env=env)

        assert result.returncode == 0, result.stderr
        assert Path(result.outputs["tmpdir"]) == chosen
        assert Path(result.outputs["made"]).parent == chosen
