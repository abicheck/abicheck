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

import itertools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

#: Distinguishes the per-invocation step-body script files `run_step` writes,
#: so two calls sharing one tmp_path never race on the same name.
_BODY_COUNTER = itertools.count()

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: Adversarial values for a scalar caller-controlled input that reaches a
#: workflow/composite-action shell (or ``python3 -c``) step's environment,
#: shared across every consuming test module rather than duplicated per
#: script (bug-class-regression-testing.md Phase 8: the invariant is stated
#: over "every scalar input ... across the repository's other shell scripts
#: and composite-action steps", not one script's own private corpus).
#: Widen this list -- not a per-file copy -- when a new adversarial shape is
#: identified; every consumer picks it up automatically.
#:
#: The last eight entries were added under Phase 8 specifically to cover
#: shapes its own mechanism names that the original (#758-era, one-repro-
#: scoped) corpus did not yet include: a tab, a leading ``-`` (flag-shaped),
#: a value that itself looks like more than one CLI flag, both quote
#: characters, and both shell redirect operators. The empty string closes a
#: real edge this corpus previously never exercised: none of the earlier
#: fourteen entries had length zero, so "sanitizing nothing" was untested.
HOSTILE_SCALAR_CORPUS = [
    pytest.param("../../etc/passwd", id="path-traversal"),
    pytest.param("/absolute/path", id="absolute-path"),
    pytest.param("..", id="dotdot"),
    pytest.param("a/b/c", id="nested-path"),
    pytest.param("lib\nrepository=evil", id="newline-record-injection"),
    pytest.param("lib\rrepository=evil", id="carriage-return"),
    pytest.param("lib\x1frepository=evil", id="unit-separator"),
    pytest.param("lib; rm -rf /", id="shell-metacharacters"),
    pytest.param("$(whoami)", id="command-substitution"),
    pytest.param("`whoami`", id="backticks"),
    pytest.param("lib name with spaces", id="spaces"),
    pytest.param("lüb-éà", id="non-ascii"),
    pytest.param("*", id="glob"),
    pytest.param("x" * 300, id="very-long"),
    pytest.param("lib\ttab-separated", id="tab"),
    pytest.param("-rf", id="leading-dash-flag-shaped"),
    pytest.param("--evil-flag --another", id="multiple-flags-shaped"),
    pytest.param('lib"quoted"', id="double-quote"),
    pytest.param("lib'quoted'", id="single-quote"),
    pytest.param("a>b", id="output-redirect"),
    pytest.param("a<b", id="input-redirect"),
    pytest.param("", id="empty-string"),
]

#: Characters ``actions/upload-artifact`` rejects in an artifact name, plus
#: the path separators that would make the name more than one path
#: component. Deliberately GitHub's real rule rather than an ASCII allowlist:
#: a sanitizer that keeps any ``str.isalnum()`` character must let a
#: genuinely valid non-ASCII name (e.g. "libpüppchen") survive, so asserting
#: bare ASCII here would be the test being wrong, not the sanitizer. Shared
#: for the same reason as ``HOSTILE_SCALAR_CORPUS`` above.
FORBIDDEN_ARTIFACT_NAME_CHARS = set('":<>|*?\r\n/\\')


#: Skips a test whose fixture needs a path containing a literal newline byte.
#: POSIX filesystems allow one -- which is exactly why the workflow-command
#: injection defenses (`_gha_escape`, `action/run.sh`'s own helper) exist and
#: are exercised with such a path. Windows rejects the character in a filename
#: at the OS level (`OSError: [WinError 123]`), so the attack shape cannot be
#: constructed there at all: the test has nothing to say about the defense on
#: that platform, rather than the defense being unverified. Shared here so the
#: three sites needing it state one reason, not three.
requires_newline_in_filenames = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows filenames cannot contain a literal newline (WinError 123)",
)


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
        """Last-wins parse, matching how the runner reads $GITHUB_OUTPUT.

        Handles both record shapes the real runner does: a plain
        ``key=value`` line, and GitHub's documented multiline form
        (``key<<DELIMITER``, one or more body lines, a line that is exactly
        ``DELIMITER``) -- used by a step whose output value may itself
        contain a newline (e.g. ``actions/check-target/action.yml``'s own
        ``effective-extra-args`` output, PR #1222). Without this, a
        multiline record's own delimiter/body lines (none of which contain
        a literal ``=`` in general) would be silently skipped by the plain
        parse below, and the key would appear missing rather than present
        with its real, possibly-multiline value.

        Note ``output_lines`` above already drops blank lines, so a blank
        body line inside a multiline record is not reconstructable here --
        a pre-existing limitation of that capture, not of this parse.
        """
        parsed: dict[str, str] = {}
        lines = self.output_lines
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if "<<" in line and "=" not in line.split("<<", 1)[0]:
                key, _, delimiter = line.partition("<<")
                i += 1
                body: list[str] = []
                while i < n and lines[i] != delimiter:
                    body.append(lines[i])
                    i += 1
                # lines[i] == delimiter here, or i == n on a malformed/
                # truncated record -- either way, stop collecting and move
                # past the delimiter line (if any) rather than raising, to
                # keep this a lenient test-harness parse.
                parsed[key] = "\n".join(body)
                i += 1
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                parsed[key] = value
            i += 1
        return parsed

    def tree(self) -> set[str]:
        """Every path under the workspace, relative and POSIX-style."""
        return {
            p.relative_to(self.workspace).as_posix() for p in self.workspace.rglob("*")
        }


def bash_executable() -> str:
    """A real bash, bypassing Windows' WSL launcher stub.

    On GitHub's windows-latest runners ``%SystemRoot%\\System32\\bash.exe`` is
    the WSL launcher, present even with no distro installed, and a bare
    ``["bash", ...]`` call can resolve to it ahead of Git for Windows' real
    bash depending on inherited PATH order. It then prints WSL's own
    "no installed distributions" text (UTF-16LE, so it does not even match a
    substring assertion) and exits 1, which reads as every test in the file
    failing at once for no stated reason.

    This is the canonical copy. Roughly two dozen ``test_action_*`` modules
    still carry their own private ``_bash_executable``, each written before
    there was a shared home for it -- a new module should import this one
    rather than clone a twenty-fifth, which is exactly how
    ``test_action_run_sh_build_info_conflict`` shipped with a bare ``bash``
    and reddened the Windows lane. Migrating the existing copies is a
    separate, mechanical change and deliberately not done here.

    ``GIT_BASH_PATH`` is honoured first so a runner with Git installed
    somewhere unusual can point at it.
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).exists():
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

    # The body goes to a real script file rather than `bash -c <body>`, which
    # is both what the runner itself does (`bash -e {0}`, a generated script)
    # and the only form that has no length ceiling: Windows caps a process
    # command line at 32767 characters, so a long `run:` body (the
    # `assurance_overlay` step in `actions/check-target/action.yml` is ~50 KB)
    # raised `FileNotFoundError: [WinError 206] The filename or extension is
    # too long` before bash ever started — every executing test in the module
    # failing for a reason unrelated to the step it models. The file lives
    # beside the workspace, not inside it, so `StepResult.tree()` and the
    # `$RUNNER_TEMP` assertions keep seeing only what the step itself created.
    #
    # Written as explicit bytes, never `write_text`: on Windows the default
    # `newline=None` translates every "\n" to "\r\n", and Git Bash keeps that
    # carriage return inside shell tokens -- a heredoc delimiter line becomes
    # `EOF\r`, so the heredoc never terminates, and `set -euo pipefail`-style
    # bodies break on the trailing CR (Codex review, PR #1230). The body must
    # reach bash byte-for-byte as the YAML holds it.
    body = workspace.parent / f"_step_body_{os.getpid()}_{next(_BODY_COUNTER)}.sh"
    body.write_bytes(step["run"].encode("utf-8"))
    # Git Bash wants forward slashes, but a backslash is a legal *filename*
    # character on POSIX, so rewriting one unconditionally would corrupt a real
    # path (a workspace under `with\backslash/` would be run from
    # `with/backslash/`, i.e. "No such file or directory" -- Codex review,
    # PR #1230). Convert only where the separator actually differs.
    script_arg = body.as_posix() if os.name == "nt" else str(body)
    try:
        proc = subprocess.run(
            # `-e` as well as pipefail: the runner invokes a `run:` body as
            # `bash -e {0}` (and `-eo pipefail` for `shell: bash`), so without
            # it a command failing mid-body left returncode 0 here while the
            # real step failed — every `assert result.returncode == 0` in the
            # workflow tests was weaker than the thing it models (CodeRabbit
            # review).
            [bash_executable(), "-eo", "pipefail", script_arg],
            cwd=workspace,
            env=step_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        # Unconditional, so a timeout or a spawn failure does not leave the
        # script behind either: a caller passing a workspace whose parent is
        # not itself a pytest-managed temporary directory would otherwise
        # accumulate one file per step (CodeRabbit review, PR #1230).
        body.unlink(missing_ok=True)
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
    candidate = bash_executable()
    return Path(candidate).is_file() or shutil.which(candidate) is not None
