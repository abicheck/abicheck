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

"""Attacker-controlled free text must never reach a `run:` script body.

Bug class `trust_boundary.shell_workflow_injection`, at the workflow layer
rather than inside one Action script. A PR title, body, branch name or
commit message is text a stranger chooses. Interpolated directly into a
`run:` block, `${{ github.event.pull_request.title }}` is substituted
*before* the shell sees the script, so a title of
``x"; touch /tmp/PWNED; #`` becomes a command. Passed through `env:`
instead, the same text is a variable value the shell never parses.

This repository already uses the safe pattern at both places free text
enters (`bugfix-test-contract.yml`'s `PR_BODY`/`BUGFIX_CONTRACT_TITLE`,
each with a comment saying why) — but nothing enforced it, so the next
workflow to add such a step had no guard. The six workflows this PR edits
put their new expressions in `concurrency.group`; the structural half below
is what *proves* that rather than asserting it in a PR description.

Both halves matter, and the second is why this file exists in the shape it
does. #705 shipped a defense asserted through the *text* of a YAML file and
#758 had to add the test that executes the attack. So the structural scan
is paired with a real execution of the real step against hostile payloads,
plus a negative control: the identical harness pointed at a deliberately
unsafe script must report the injection. Without that control, a harness
that silently never executed anything would pass both other tests.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from _workflow_exec import bash_executable, have_bash, require_bash
from _workflow_files import WORKFLOW_DIR, read_repo_text, workflow_paths

#: Contexts whose value is free text chosen by whoever opened the PR,
#: issue or comment. Numeric ids and SHAs (`pull_request.number`,
#: `base.sha`) are deliberately absent: GitHub constrains their shape, they
#: are not injectable, and flagging them would bury the real finding under
#: a dozen false positives that a future reader would learn to ignore.
UNTRUSTED_TEXT_CONTEXTS = (
    "github.event.pull_request.title",
    "github.event.pull_request.body",
    "github.event.pull_request.head.ref",
    "github.event.pull_request.head.label",
    "github.event.issue.title",
    "github.event.issue.body",
    "github.event.comment.body",
    "github.event.review.body",
    "github.event.discussion.title",
    "github.event.discussion.body",
    "github.event.head_commit.message",
    "github.event.workflow_run.head_branch",
    "github.head_ref",
)

#: Payloads a hostile PR body could carry. Each would run a command or
#: forge a workflow command if the value were substituted into the script
#: rather than bound to a variable.
HOSTILE_PAYLOADS = (
    'x"; touch PWNED; #',
    "x'; touch PWNED; #",
    "$(touch PWNED)",
    "`touch PWNED`",
    "legit body\n::error::forged\n::set-output name=x::y",
    '$(printf PWNED)\n"; touch PWNED; #',
    "${IFS}touch${IFS}PWNED",
)


def _steps() -> list[tuple[str, str, int, dict]]:
    """Every step of every workflow, tagged with where it came from."""
    found = []
    for path in workflow_paths():
        doc = yaml.safe_load(read_repo_text(path))
        for job_name, job in ((doc or {}).get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for index, step in enumerate(job.get("steps") or []):
                if isinstance(step, dict):
                    found.append((path.name, job_name, index, step))
    return found


_STEPS = _steps()


def test_the_survey_found_workflow_steps() -> None:
    """Guards the scan itself — a parsing change matching nothing would
    make the structural assertion below vacuous."""
    assert len(_STEPS) > 100, len(_STEPS)
    assert any("run" in step for *_, step in _STEPS)


def test_untrusted_text_never_interpolated_into_a_run_body() -> None:
    """The structural invariant, repo-wide rather than per known site."""
    violations = []
    for name, job, index, step in _STEPS:
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for context in UNTRUSTED_TEXT_CONTEXTS:
            if context in run:
                violations.append(f"{name}:{job}[{index}] interpolates {context}")
    assert not violations, (
        "attacker-controlled free text is substituted into a shell script "
        "before the shell parses it — pass it through `env:` and reference "
        f"the variable instead: {violations}"
    )


def test_untrusted_text_reaches_the_shell_only_as_an_env_value() -> None:
    """The complement: where these contexts *are* used, it is as an `env:`
    value. Without this, deleting every such step would satisfy the test
    above while removing the functionality rather than securing it."""
    env_uses = [
        (name, job, index, key)
        for name, job, index, step in _STEPS
        for key, value in (step.get("env") or {}).items()
        if isinstance(value, str)
        for context in UNTRUSTED_TEXT_CONTEXTS
        if context in value
    ]
    assert env_uses, (
        "no workflow reads attacker-controlled free text through env any "
        "more — if that is intentional, this module's premise changed"
    )


def _run_body_step(script: str, payload: str, workdir: Path) -> tuple[str | None, bool]:
    """Execute *script* with PR_BODY bound to *payload*, as the real step
    does, and report what landed plus whether the attack fired.

    Returns `None` for the written body when the file is absent, and never
    asserts on the exit status: against a deliberately unsafe script a
    quote-breaking payload rewrites the command itself, so the redirect can
    fail or vanish entirely. An earlier version read the file eagerly and
    raised `FileNotFoundError` there -- the harness crashed on exactly the
    case it exists to detect, reporting a test error instead of "the attack
    fired". Both outcomes are observations to return, not failures here.

    The environment is replaced rather than extended, so a variable leaking
    in from the developer's shell cannot change what the step does. On
    Windows that replacement has to be partial: Git bash is a real POSIX
    shell but it still needs the host's own `PATH` and `SystemRoot` to find
    its utilities, and handing it the POSIX-only `PATH` below left the step
    unable to run at all -- every payload then came back as "nothing was
    written", which reads exactly like a passing negative control.
    `test_the_harness_can_execute_a_trivial_step` is what makes that state
    visible rather than silently reassuring.
    """
    require_bash()
    env = {
        "PR_BODY": payload,
        "RUNNER_TEMP": str(workdir),
        "PATH": "/usr/bin:/bin",
    }
    if os.name == "nt":
        env = {**os.environ, **env, "PATH": os.environ.get("PATH", "")}
    subprocess.run(
        [bash_executable(), "-c", script],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    body = workdir / "pr-body.md"
    written = body.read_text(encoding="utf-8") if body.exists() else None
    return written, (workdir / "PWNED").exists()


def _real_pr_body_script() -> str:
    """The real step body from bugfix-test-contract.yml's "Write PR body to
    a file".

    Read out of the workflow rather than retyped, so this module cannot go
    on testing a string the workflow itself stopped using.
    """
    doc = yaml.safe_load(read_repo_text(WORKFLOW_DIR / "bugfix-test-contract.yml"))
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "PR_BODY" in (step.get("env") or {}):
                return str(step["run"])
    pytest.fail("bugfix-test-contract.yml no longer has a PR_BODY step")


#: The executing half needs a real POSIX shell. On a Windows runner without
#: Git for Windows, `bash` resolves to the WSL launcher stub, which prints
#: its own UTF-16LE "no installed distributions" text and exits 1 -- so an
#: unguarded run asserts against WSL's output rather than against the step,
#: reporting a security regression that did not happen. The structural half
#: above is pure YAML parsing and stays unconditional.
_needs_bash = pytest.mark.skipif(not have_bash(), reason="needs a real bash")


@_needs_bash
def test_the_harness_can_execute_a_trivial_step(tmp_path: Path) -> None:
    """Every other result in this module is uninterpretable without this.

    `_run_body_step` reports "nothing was written" both when a payload
    destroyed the redirect and when the shell never ran at all -- and the
    negative controls below treat "nothing was written" as *the attack
    fired*, so a harness that cannot execute anything makes this module
    report a clean, fully-passing security check while testing nothing.
    That is not hypothetical: handing Git bash a POSIX-only `PATH` on the
    Windows lane produced precisely that state.

    So prove the harness works against a script with no payload in it at
    all, whose only job is to write the file the others look for.
    """
    written, pwned = _run_body_step(
        'printf %s "$PR_BODY" > pr-body.md', "sentinel", tmp_path
    )
    assert written == "sentinel", (
        "the harness could not execute a trivial step, so every other "
        "result in this module is meaningless -- not evidence of safety"
    )
    assert not pwned


@_needs_bash
@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_hostile_pr_body_is_data_not_commands(payload: str, tmp_path: Path) -> None:
    """Executes the attack against the real step rather than asserting the
    YAML contains a quote."""
    written, pwned = _run_body_step(_real_pr_body_script(), payload, tmp_path)
    assert not pwned, f"payload executed: {payload!r}"
    assert written == payload, "the body must land verbatim, as data"


@_needs_bash
@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_the_harness_detects_a_genuinely_unsafe_step(
    payload: str, tmp_path: Path
) -> None:
    """Negative control. The same harness against a script that
    interpolates the value the way `${{ }}` substitution would must catch
    at least one payload — otherwise the two tests above prove nothing.

    Asserted over the payload set rather than per payload: a single
    payload is shell-specific (backticks, `$()`, quote-breaking and the
    `::error::` newline forgery each need a different surrounding
    context), so requiring every one to fire would pin the control to the
    one unsafe script written here instead of to the harness."""
    unsafe = f'printf %s "{payload}" > pr-body.md'
    written, pwned = _run_body_step(unsafe, payload, tmp_path)
    if pwned or written != payload:
        return  # The attack fired, or corrupted the command around it.
    pytest.skip(f"payload {payload!r} is inert in this unsafe shape")


@_needs_bash
def test_at_least_one_payload_fires_against_the_unsafe_script(
    tmp_path: Path,
) -> None:
    """The control above skips per payload; this makes the *set* binding —
    if no payload could ever fire, the harness cannot detect injection and
    the passing tests above are vacuous."""
    fired = 0
    for i, payload in enumerate(HOSTILE_PAYLOADS):
        workdir = tmp_path / f"case{i}"
        workdir.mkdir()
        unsafe = f'printf %s "{payload}" > pr-body.md'
        written, pwned = _run_body_step(unsafe, payload, workdir)
        if pwned or written != payload:
            fired += 1
    assert fired, (
        "no payload executed against a deliberately unsafe script — the "
        "harness cannot detect an injection, so the safe-path tests above "
        "are not evidence of anything"
    )
