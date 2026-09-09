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

"""The apt-install hardening contract:
``.github/actions/install-system-deps/install.sh``.

Bug class (`tests/regressions/manifest.py`,
``ci.unrelated_apt_source_gates_the_job``): a CI lane must not fail because
of a package repository none of its packages come from. ``apt-get update``
fails *as a whole* when any configured source fails, and GitHub's runner
images ship third-party vendor repositories (dl.google.com/linux/chrome-
stable, packages.microsoft.com) nothing here installs from. An
``update && install`` chain therefore aborts before ``install`` runs at all
-- which is exactly how a Google Chrome index serving a stale
``Packages.gz`` ("Hash Sum mismatch") took out five Examples Validation
jobs plus ``CI / e2e`` on main, none of which install Chrome.

These tests **execute the real script** against a simulated apt, rather than
asserting the text of the workflow files -- the #705 -> #758 lesson recorded
in AGENTS.md ("a workflow-injection defense that asserted file *text*
instead of executing the attack"). The failure is reproduced through the
script's own entry point, with the fake ``apt-get`` recording what it was
actually asked to do, so a regression that reinstates the gating (or breaks
the retry, timeout, or verification behaviour around it) fails here.

The generalization beyond the one reported input is deliberate and runs on
three axes: the *shape* of the update failure (exit status, and which of
apt's sources broke), the *package list*, and the *number* of failing
attempts. The last test then states the class invariant over every workflow
in the repository at once, so a new lane cannot reintroduce the gating
pattern in a file none of the executable tests happen to name.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPO_ROOT / ".github" / "actions" / "install-system-deps"
INSTALL_SH = ACTION_DIR / "install.sh"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# The real diagnostic dl.google.com served during the incident, verbatim
# enough to be recognisable in a log; the point is that apt exits non-zero
# while still reporting the index as usable ("old ones used instead").
CHROME_HASH_MISMATCH = (
    "E: Failed to fetch "
    "https://dl.google.com/linux/chrome-stable/deb/dists/stable/main/"
    "binary-amd64/Packages.gz  Hash Sum mismatch\n"
    "E: Some index files failed to download. "
    "They have been ignored, or old ones used instead."
)
MICROSOFT_403 = (
    "E: Failed to fetch https://packages.microsoft.com/ubuntu/24.04/prod/"
    "dists/noble/InRelease  403  Forbidden\n"
    "E: Some index files failed to download. "
    "They have been ignored, or old ones used instead."
)


#: The tests that *execute* `install.sh` need what the script itself needs: a
#: POSIX shell, GNU `timeout` (the script relies on `-k/--kill-after`, which
#: BSD/macOS `timeout` does not ship at all), and dpkg/sudo call semantics the
#: fake tools model. The unit-test matrix in `ci.yml` runs this suite on
#: macOS and Windows too, where none of that holds -- an unmarked module would
#: turn those legs red for a script that only ever runs on an Ubuntu runner.
#:
#: Deliberately a *capability* probe rather than a bare `sys.platform` check:
#: a Linux host without GNU `timeout` would otherwise fail the same way, and
#: the honest precondition is "the tools this script calls are here".
_HAS_POSIX_APT_HARNESS = (
    sys.platform.startswith("linux")
    and shutil.which("bash") is not None
    and shutil.which("timeout") is not None
)

requires_apt_harness = pytest.mark.skipif(
    not _HAS_POSIX_APT_HARNESS,
    reason="needs a POSIX shell and GNU timeout (install.sh runs on Ubuntu runners only)",
)


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_apt_env(
    tmp_path: Path,
    *,
    update_exit: int = 0,
    update_stderr: str = "",
    install_exit: int = 0,
    installed: tuple[str, ...] | None = None,
    install_fail_first: int = 0,
) -> tuple[dict[str, str], Path]:
    """Build a PATH holding a fake ``sudo``/``apt-get``/``dpkg-query``.

    ``installed`` is what dpkg reports as installed after a successful
    install (``None`` means "whatever install was asked for", the honest
    case). ``install_fail_first`` makes the first N install invocations fail, so a
    test can observe the retry loop rather than only its endpoints.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "apt.log"
    state = tmp_path / "installed"
    state.write_text("", encoding="utf-8")
    if installed is not None:
        state.write_text("\n".join(installed) + "\n", encoding="utf-8")

    # `sudo` simply drops its own name and execs the rest, like the real one
    # does for our purposes.
    _write(bindir / "sudo", '#!/usr/bin/env bash\nexec "$@"\n')

    _write(
        bindir / "apt-get",
        f"""#!/usr/bin/env bash
log={log!s}
state={state!s}
sub="$1"; shift
echo "apt-get $sub $*" >> "$log"
if [ "$sub" = update ]; then
  printf '%s\\n' {update_stderr!r} >&2
  exit {update_exit}
fi
if [ "$sub" = install ]; then
  n=$(grep -c '^apt-get install' "$log")
  if [ "$n" -le {install_fail_first} ]; then
    echo "E: Unable to locate package" >&2
    exit 100
  fi
  if [ {install_exit} -ne 0 ]; then
    exit {install_exit}
  fi
  if [ {"1" if installed is None else "0"} -eq 1 ]; then
    for a in "$@"; do
      case "$a" in -*) continue;; esac
      echo "$a" >> "$state"
    done
  fi
  exit 0
fi
exit 0
""",
    )

    # Real `dpkg-query -W` exits non-zero for a name it holds no record of
    # -- a package that was never installed -- and prints the status line
    # only for one it knows.
    _write(
        bindir / "dpkg-query",
        f"""#!/usr/bin/env bash
state={state!s}
pkg="${{@: -1}}"
if grep -qx "$pkg" "$state" 2>/dev/null; then
  printf 'install ok installed'
  exit 0
fi
exit 1
""",
    )

    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env["APT_INSTALL_RETRY_DELAY"] = "0"
    return env, log


def _run(env: dict[str, str], packages: str) -> subprocess.CompletedProcess[str]:
    env = dict(env)
    env["INPUT_PACKAGES"] = packages
    return subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _installs(log: Path) -> list[str]:
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [ln for ln in lines if ln.startswith("apt-get install")]


@requires_apt_harness
class TestUpdateFailureNeverGatesInstall:
    """The reported incident's class, executed rather than described."""

    # Axis 1: the shape of the `update` failure. The incident was a Chrome
    # index hash mismatch (exit 100); the same class covers any exit status
    # from any third-party source, so several independently-chosen ones run
    # here rather than only the reported value.
    @pytest.mark.parametrize(
        ("update_exit", "stderr"),
        [
            (100, CHROME_HASH_MISMATCH),
            (100, MICROSOFT_403),
            (1, CHROME_HASH_MISMATCH),
            (255, "E: Could not get lock /var/lib/apt/lists/lock"),
            (2, "E: Some index files failed to download."),
        ],
    )
    # Axis 2: the package list -- every real caller's list in this repo,
    # not just the gcc/g++/cmake one the incident happened to name.
    @pytest.mark.parametrize(
        "packages",
        [
            "gcc g++ cmake curl ca-certificates",
            "gcc g++ clang cmake curl ca-certificates",
            "zlib1g-dev curl ca-certificates",
            "binutils",
            "zstd binutils git cmake clang",
        ],
    )
    def test_install_still_runs_and_succeeds(
        self, tmp_path: Path, update_exit: int, stderr: str, packages: str
    ) -> None:
        env, log = _fake_apt_env(
            tmp_path, update_exit=update_exit, update_stderr=stderr
        )
        result = _run(env, packages)

        assert result.returncode == 0, result.stdout + result.stderr
        # The load-bearing assertion: `install` was actually reached, with
        # every requested package, despite `update` exiting non-zero.
        installs = _installs(log)
        assert len(installs) == 1, installs
        for pkg in packages.split():
            assert pkg in installs[0]

    def test_update_failure_is_reported_not_hidden(self, tmp_path: Path) -> None:
        """Tolerated is not the same as silent: the run still says so."""
        env, _ = _fake_apt_env(
            tmp_path, update_exit=100, update_stderr=CHROME_HASH_MISMATCH
        )
        result = _run(env, "gcc")

        assert result.returncode == 0
        assert "::warning::" in result.stdout
        assert "apt-get update" in result.stdout


@requires_apt_harness
class TestInstallIsTheGate:
    """Moving the gate must not remove it."""

    def test_unavailable_package_still_fails_loudly(self, tmp_path: Path) -> None:
        env, log = _fake_apt_env(tmp_path, install_exit=100)
        env["APT_INSTALL_ATTEMPTS"] = "3"
        result = _run(env, "definitely-not-a-real-package")

        assert result.returncode == 1
        assert "::error::" in result.stdout
        assert "definitely-not-a-real-package" in result.stdout
        # Axis 3: it retried rather than giving up on the first failure.
        assert len(_installs(log)) == 3

    @pytest.mark.parametrize("failures_before_success", [1, 2])
    def test_a_transient_install_failure_is_retried_to_success(
        self, tmp_path: Path, failures_before_success: int
    ) -> None:
        env, log = _fake_apt_env(
            tmp_path,
            update_exit=100,
            update_stderr=CHROME_HASH_MISMATCH,
            install_fail_first=failures_before_success,
        )
        env["APT_INSTALL_ATTEMPTS"] = "3"
        result = _run(env, "gcc g++")

        assert result.returncode == 0, result.stdout + result.stderr
        assert len(_installs(log)) == failures_before_success + 1

    def test_install_reporting_success_without_installing_is_caught(
        self, tmp_path: Path
    ) -> None:
        """`apt-get install` exiting 0 is necessary, not sufficient.

        With `update` no longer gating, the post-condition check is the only
        thing standing between a degraded index and a lane that claims a
        toolchain it does not have.
        """
        env, _ = _fake_apt_env(tmp_path, installed=("gcc",))
        env["APT_INSTALL_ATTEMPTS"] = "2"
        result = _run(env, "gcc g++")

        assert result.returncode == 1
        assert "g++" in result.stdout

    @pytest.mark.parametrize(
        ("requested", "actually_installed"),
        [
            ("gcc g++", ("gcc",)),
            ("gcc g++ cmake", ("gcc", "cmake")),
            ("zstd binutils git cmake clang", ("zstd", "binutils", "git")),
            ("binutils", ()),
        ],
    )
    def test_a_partially_installed_set_is_caught(
        self, tmp_path: Path, requested: str, actually_installed: tuple[str, ...]
    ) -> None:
        """Every shortfall is caught, not only a single missing package."""
        env, _ = _fake_apt_env(tmp_path, installed=actually_installed)
        env["APT_INSTALL_ATTEMPTS"] = "2"
        result = _run(env, requested)

        assert result.returncode == 1
        for pkg in set(requested.split()) - set(actually_installed):
            assert pkg in result.stdout

    def test_apt_options_in_the_list_are_not_verified_as_packages(
        self, tmp_path: Path
    ) -> None:
        """An option token is not a package dpkg could ever know about."""
        env, _ = _fake_apt_env(tmp_path)
        result = _run(env, "--no-install-recommends gcc")

        assert result.returncode == 0, result.stdout + result.stderr

    def test_empty_package_list_is_a_usage_error(self, tmp_path: Path) -> None:
        env, log = _fake_apt_env(tmp_path)
        result = _run(env, "")

        assert result.returncode == 2
        assert not _installs(log)


class TestPackagesAreNotInterpolatedIntoTheShell:
    """The action passes `packages` through the environment, not the command
    line, so a workflow expression can never be spliced into the shell that
    runs apt (AGENTS.md's #705 -> #758 lesson: execute the attack)."""

    @requires_apt_harness
    def test_shell_metacharacters_in_packages_do_not_execute(
        self, tmp_path: Path
    ) -> None:
        canary = tmp_path / "pwned"
        env, _ = _fake_apt_env(tmp_path, install_exit=100)
        env["APT_INSTALL_ATTEMPTS"] = "1"

        result = _run(env, f"gcc; touch {canary}")

        assert not canary.exists(), "package list reached a shell as code"
        assert result.returncode == 1

    def test_action_yml_passes_packages_through_env(self) -> None:
        action = yaml.safe_load((ACTION_DIR / "action.yml").read_text())
        (step,) = action["runs"]["steps"]
        assert step["env"]["INPUT_PACKAGES"] == "${{ inputs.packages }}"
        assert "${{" not in step["run"]


#: Commands that genuinely *absorb* a failure: each exits 0 no matter what
#: preceded it. Deliberately a small allowlist rather than "the line contains
#: `||`" -- that weaker rule accepts `apt-get update || exit 1`, `|| false`,
#: or `|| apt-get update` (a retry whose own failure still aborts), which
#: reintroduce the exact gating this change removes while keeping the guard
#: green (Codex review, P2 on PR #1182).
#:
#: `printf` is deliberately NOT here: it exits non-zero on a bad format
#: (`printf '%d' abc`), so it is not unconditionally absorbing the way
#: `true`, `:` and a plain `echo` are.
_NON_FAILING_SINKS = frozenset({"true", ":", "echo"})

#: Commands that end the shell (or the enclosing function) where they stand,
#: so no later fallback in the chain is reachable at all.
_CONTROL_TERMINATORS = frozenset({"exit", "return", "exec"})


def update_failure_is_absorbed(line: str) -> bool:
    """True when this line's ``apt-get update`` cannot abort its step.

    ``a || b || c`` short-circuits at the *first* success, so the line exits
    0 if **any** fallback succeeds -- not only the last one. (`false || true
    || false` exits 0; an earlier revision of this predicate claimed
    otherwise, and differential-testing it against real bash --
    ``test_predicate_agrees_with_real_bash`` below -- is what caught it.)
    A fallback that always succeeds therefore absorbs the failure wherever
    it sits in the chain.

    Checking a fallback's first word is not enough, though: a pipeline takes
    its status from its last element, so ``|| echo warn | false`` exits 1
    while opening with an allowlisted command, and ``|| echo hi; false``
    replaces the status outright with a second statement (Codex review,
    second P2 on PR #1182). So any shell operator other than ``||`` after
    the first ``||`` disqualifies the line: ``;``, ``&``, ``&&``, ``|`` and
    redirections all make the outcome something this cannot vouch for, and a
    guard that cannot vouch for a line should flag it, not pass it.

    Quoting is respected -- the ``;`` inside ``echo "...; ..."`` is text, not
    a separator -- which is why this tokenizes rather than scanning
    characters. Anything unparseable is reported as NOT absorbed.
    """
    if "||" not in line:
        return False

    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return False

    punctuation = set(lexer.punctuation_chars)

    def is_operator(tok: str) -> bool:
        return bool(tok) and set(tok) <= punctuation

    if "||" not in tokens:
        return False
    after = tokens[tokens.index("||") + 1 :]
    if any(is_operator(tok) and tok != "||" for tok in after):
        return False

    # Split the remaining fallbacks on `||`; any one of them succeeding ends
    # the chain at 0.
    segments: list[list[str]] = [[]]
    for tok in after:
        if tok == "||":
            segments.append([])
        else:
            segments[-1].append(tok)

    for segment in segments:
        if not segment:
            continue
        head = segment[0]
        # `sudo true` absorbs exactly as `true` does; skip a leading sudo.
        if head == "sudo" and len(segment) > 1:
            head = segment[1]
        if head in _NON_FAILING_SINKS:
            return True
        if head in _CONTROL_TERMINATORS:
            # Nothing after this runs, so a later sink is unreachable:
            # `|| exit 1 || true` never reaches `true` and bash exits 1
            # (Codex review, third P2 on PR #1182).
            return False
    return False


class TestNoWorkflowGatesInstallOnUpdate:
    """The class invariant, over every workflow at once.

    The executable tests above prove the shared script behaves; this proves
    no lane bypasses it by hand-rolling the pattern again. It is a structural
    check by necessity -- a workflow's own step wiring needs a real runner to
    execute -- and it is the *complement* of the tests above, not a
    substitute for them.
    """

    @staticmethod
    def _run_scripts() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in (data.get("jobs") or {}).values():
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and isinstance(step.get("run"), str):
                        out.append((path.name, step["run"]))
        return out

    def test_no_step_lets_apt_get_update_abort_it(self) -> None:
        offenders: list[str] = []
        for name, script in self._run_scripts():
            for line in script.splitlines():
                stripped = line.strip()
                if not re.search(r"\bapt-get\s+update\b", stripped):
                    continue
                if stripped.lstrip().startswith("#"):
                    continue
                if not update_failure_is_absorbed(stripped):
                    offenders.append(f"{name}: {stripped}")
        assert not offenders, (
            "`apt-get update`'s exit status must never gate a step -- an "
            "unrelated third-party source on the runner image fails it. Use "
            "./.github/actions/install-system-deps (or absorb the failure "
            "with `||`) instead:\n" + "\n".join(offenders)
        )

    def test_the_shared_action_is_what_lanes_use(self) -> None:
        """At least the lanes that broke in the incident route through it."""
        users = {
            path.name
            for path in WORKFLOWS_DIR.glob("*.yml")
            if "install-system-deps" in path.read_text(encoding="utf-8")
        }
        for expected in ("ci.yml", "examples-validation.yml"):
            assert expected in users, f"{expected} no longer uses the shared action"

    def test_install_sh_exists(self) -> None:
        """Portable half: the script the action names is really there."""
        assert INSTALL_SH.exists()

    @requires_apt_harness
    def test_install_sh_is_executable_and_syntactically_valid(self) -> None:
        assert os.access(INSTALL_SH, os.X_OK)
        assert subprocess.run(["bash", "-n", str(INSTALL_SH)]).returncode == 0


class TestUpdateFailureAbsorptionPredicate:
    """The guard's own primitive, tested directly.

    A repository-wide structural scan is only as good as the predicate
    deciding what counts as an offender, and that predicate is exactly the
    kind of reusable rule AGENTS.md asks to be stated as invariants rather
    than trusted because one caller happens to pass.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "sudo apt-get update -qq || true",
            "sudo apt-get update || :",
            'sudo apt-get update || echo "::warning::index stale, continuing"',
            "sudo apt-get update -qq || sudo apt-get update -qq || true",
            "apt-get update||true",
            "sudo apt-get update || sudo true",
            # A `;` inside quotes is text, not a statement separator -- this
            # is clang-plugin.yml's real line.
            'sudo apt-get update || echo "::warning::stale; continuing"',
        ],
    )
    def test_absorbed_forms_are_accepted(self, line: str) -> None:
        assert update_failure_is_absorbed(line)

    @pytest.mark.parametrize(
        "line",
        [
            # The bare gating form this whole change exists to remove.
            "sudo apt-get update -qq && sudo apt-get install -y gcc",
            "sudo apt-get update -qq",
            # Forms a bare `"||" in line` check would have wrongly accepted.
            "sudo apt-get update || exit 1",
            "sudo apt-get update || false",
            "sudo apt-get update || sudo apt-get update",
            "sudo apt-get update -qq || apt-get install -y gcc",
            "sudo apt-get update || return 1",
            "sudo apt-get update ||",
            # Unparseable is not a pass.
            "sudo apt-get update || 'unterminated",
            # A pipeline takes its status from its LAST element, and a second
            # statement replaces it outright -- an allowlisted first word
            # proves nothing (Codex review, second P2; each verified to exit
            # 1 under real bash).
            "sudo apt-get update || echo warning | false",
            "sudo apt-get update || echo hi; false",
            "sudo apt-get update || true && false",
            "sudo apt-get update || echo hi & false",
            # printf is not unconditionally absorbing: a bad format exits 1.
            "sudo apt-get update || printf '%d' abc",
        ],
    )
    def test_gating_forms_are_rejected(self, line: str) -> None:
        assert not update_failure_is_absorbed(line)

    def test_a_sink_anywhere_in_a_chain_absorbs(self) -> None:
        """`||` short-circuits at the first success, so position is irrelevant.

        Both of these exit 0 under real bash. An earlier revision asserted
        the second was NOT absorbed, on a "only the last link decides"
        reading that is simply wrong -- the kind of self-derived oracle
        AGENTS.md warns about, caught by the differential test below.
        """
        assert update_failure_is_absorbed("apt-get update || false || true")
        assert update_failure_is_absorbed("apt-get update || true || false")

    # One fallback command per entry, spanning every shape this predicate has
    # had to reason about: absorbing sinks, plain failures, pipelines whose
    # last element decides, statement separators, control terminators, and a
    # stand-in for a retried `apt-get update`. Crossed with itself below, so
    # the suite covers the *combinations* rather than the handful of forms a
    # reviewer happened to name -- three consecutive review rounds each found
    # one more single case, which is the signature of a class that wants
    # generating rather than enumerating. (The cross-product then found
    # `exec false`, which no reviewer had named.)
    _FALLBACKS = (
        "true",
        ":",
        "false",
        "exit 1",
        "return 1",
        "exec false",
        "echo hi",
        'echo "::warning::stale; continuing"',
        "echo hi | false",
        "echo hi; false",
        "printf '%d' abc",
        "sudo true",
        "false && true",
        "true && false",
    )

    @requires_apt_harness
    @pytest.mark.parametrize("first", _FALLBACKS)
    def test_single_fallback_is_sound_against_real_bash(self, first: str) -> None:
        self._assert_sound(f"CMD || {first}")

    @requires_apt_harness
    @pytest.mark.parametrize("second", _FALLBACKS)
    @pytest.mark.parametrize("first", _FALLBACKS)
    def test_chained_fallbacks_are_sound_against_real_bash(
        self, first: str, second: str
    ) -> None:
        self._assert_sound(f"CMD || {first} || {second}")

    @requires_apt_harness
    @pytest.mark.parametrize(
        "form", ["CMD", "CMD && echo installed", "CMD -qq && CMD2 -y gcc"]
    )
    def test_non_fallback_forms_are_sound_against_real_bash(self, form: str) -> None:
        self._assert_sound(form)

    @staticmethod
    def _assert_sound(form: str) -> None:
        """The oracle is bash itself, not this module's own reasoning.

        AGENTS.md asks a general invariant to be checked "against a stated
        oracle that is not the same formula/helper the implementation itself
        uses". The failing `apt-get update` is stood in for by `false`, and
        bash's own exit status for the whole line is ground truth.

        The invariant is **one-directional, by design**: whenever the
        predicate says "absorbed", bash must really exit 0. That is the
        direction with consequences -- the one that would wave a still-gating
        line past the repository guard. The converse is deliberately not
        asserted: the predicate refuses to vouch for a fallback containing
        `&&`, `|`, or `;`, and some such lines do survive in bash
        (`false || false && true || true` exits 0). Flagging one asks a human
        to look, which is the safe failure for a guard; the accepted-forms
        tests above are what keep that conservatism from degrading into
        "flags everything".

        Not decoration: this has falsified three separate beliefs of mine
        about shell semantics -- chain ordering, pipeline status, and
        reachability past `exit` -- each of which a hand-written assertion
        would have agreed with.
        """
        executable = form.replace("CMD2", "false").replace("CMD", "false")
        real_exit = subprocess.run(
            ["bash", "-c", executable], capture_output=True
        ).returncode
        predicted = update_failure_is_absorbed(
            form.replace("CMD2", "apt-get install").replace("CMD", "apt-get update")
        )
        if predicted:
            assert real_exit == 0, (
                f"{form!r}: predicate says the failure is absorbed, but bash "
                f"exits {real_exit} -- the guard would pass a gating line"
            )
