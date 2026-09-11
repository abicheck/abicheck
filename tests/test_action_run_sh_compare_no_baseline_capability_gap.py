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

"""Coverage for ``compare --no-baseline``'s own capability gaps as wired
into ``action/run.sh`` -- originally found (Codex review, PR #1210) against
the now-removed ``mode: scan``'s own translation to ``compare
--no-baseline``, and now real, first-class behavior of ``mode: compare``'s
audit-only shape (old-library and abi-baseline both omitted) since
ADR-068's Action-input-lifecycle amendment retired ``mode: scan`` outright:

- ``since``/``changed-path`` work for a two-sided compare, but
  ``compare --no-baseline`` hard-rejects both as a usage error (ADR-068 D2)
  -- forwarding them unconditionally would turn a caller's request into an
  exit-64 failure deep in the run instead of a clear, upfront explanation.
- ``budget`` works for a two-sided compare (``compare``'s own ``--budget``
  guard, exit 5) but is rejected upfront for the audit-only shape, since
  ``compare --no-baseline``'s wall-clock guard is not wired to that path.
- ``require-complete-analysis`` -- unlike the two gaps above -- is NOT
  withheld from the audit-only shape: `compare --no-baseline` accepts the
  flag and gives it real teeth (an incomplete analysis-assurance
  candidate-side finding fails the step), so this Action forwards it
  unconditionally for every single-pair shape, two-sided and audit-only
  alike.
- ``used-by``/``used-by-manifest``/``required-symbol``/``required-symbols``
  (ADR-043 consumer/entrypoint scoping) work for a two-sided compare, but
  ``compare --no-baseline`` hard-rejects all four -- scoping a comparison
  to a real consumer needs two versions to compare, and an audit has none.

This module locks down the fix: the audit-only shape (old-library and
abi-baseline both omitted) setting since/changed-path/budget is rejected
upfront with a clear ``::error::`` naming the actual gap and its
replacement (the same "reject, don't silently narrow" treatment as the
hard-retired inputs in ``test_action_run_contract.py``) instead of reaching
``compare``'s own usage error deep in the run; a two-sided compare (either
old-library or abi-baseline set) still forwards all three, plus
require-complete-analysis; and the audit-only shape forwards
require-complete-analysis too, since that one is a genuine, working
capability rather than a documented no-op.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

RUN_SH = _REPO / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'
_REAL_ABICHECK = shutil.which("abicheck")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or _REAL_ABICHECK is None,
    reason="needs a POSIX shell, a real abicheck on PATH, and action/run.sh",
)

_NON_GATING_CASE = "case143_audit_accidental_export"


def _snapshot_path(case_name: str) -> Path:
    path = example_catalog.case_dir(case_name) / "snapshot.abi.json"
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _run_action(tmp_path: Path, env_extra: dict[str, str]) -> dict[str, object]:
    """Same harness as ``test_action_run_sh_audit_gate.py``: runs the real
    ``action/run.sh`` end to end and returns its ``GITHUB_OUTPUT`` pairs
    plus the raw process result. Tolerates a nonzero exit (the whole point
    of the rejection tests below)."""
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "INPUT_MODE": "compare",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        "RUNNER_TEMP": str(runner_temp),
        **env_extra,
    }
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs: dict[str, object] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_returncode"] = proc.returncode
    outputs["_stdout"] = proc.stdout
    outputs["_stderr"] = proc.stderr
    return outputs


class TestAuditOnlyCompareRejectsSinceChangedPathBudgetUpfront:
    """The audit-only shape (old-library and abi-baseline both omitted)
    setting since/changed-path/budget fails fast with a clear, specific
    ``::error::`` naming the actual gap -- never a bare Click usage error
    surfaced deep in the run, and never a silent drop."""

    def test_since_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_SINCE": "origin/main",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support since" in outputs["_stdout"], outputs
        assert "old-library" in outputs["_stdout"], outputs

    def test_changed_path_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_CHANGED_PATH": "src/foo.c",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support changed-path" in outputs["_stdout"], outputs

    def test_budget_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_BUDGET": "15m",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support budget" in outputs["_stdout"], outputs

    def test_follow_deps_is_rejected(self, tmp_path: Path) -> None:
        # Codex review, PR #1223: `--follow-deps` was still reaching the CLI
        # on the audit-only shape even though `compare --no-baseline`
        # rejects it outright (the DT_NEEDED walk isn't wired to that path,
        # abicheck/frontends/cli/commands/no_baseline_rulings.py) -- caught
        # here, upfront, the same as since/changed-path/budget above.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_FOLLOW_DEPS": "true",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support follow-deps" in outputs["_stdout"], outputs

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_USED_BY", "app1"),
            ("INPUT_USED_BY_MANIFEST", "manifest.json"),
            ("INPUT_REQUIRED_SYMBOL", "abi_do_thing"),
            ("INPUT_REQUIRED_SYMBOLS", "symbols.txt"),
        ],
    )
    def test_consumer_scoping_inputs_are_rejected(
        self, tmp_path: Path, env_name: str, value: str
    ) -> None:
        # Codex review, PR #1223, round 5: used-by/used-by-manifest/
        # required-symbol/required-symbols were still reaching the CLI on
        # the audit-only shape even though `compare --no-baseline` rejects
        # all four outright (consumer/entrypoint scoping needs two versions
        # to compare, abicheck/frontends/cli/commands/no_baseline_
        # rulings.py) -- caught here, upfront, same as since/changed-path/
        # budget/follow-deps above.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                env_name: value,
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "used-by" in outputs["_stdout"], outputs
        assert "required-symbol" in outputs["_stdout"], outputs

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_OLD_HEADER", "old_include/foo.h"),
            ("INPUT_OLD_INCLUDE", "old_include/"),
            ("INPUT_OLD_VERSION", "1.0.0"),
        ],
    )
    def test_old_sided_inputs_are_rejected(
        self, tmp_path: Path, env_name: str, value: str
    ) -> None:
        # Codex review, PR #1223, round 8: old-header/old-include/
        # old-version were silently dropped (never forwarded) on the
        # audit-only shape -- there is no OLD side for this evidence to
        # describe, and the CLI's own _reject_old_sided_inputs
        # (abicheck/frontends/cli/commands/no_baseline_rulings.py) rejects
        # an explicitly OLD-scoped value outright rather than accepting
        # it silently. Caught here, upfront, same as the other audit-only
        # capability gaps above.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                env_name: value,
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support" in outputs["_stdout"], outputs

    def test_old_version_default_placeholder_does_not_trigger_rejection(
        self, tmp_path: Path
    ) -> None:
        # Codex review, PR #1223, round 11 (P1): old-version's Action-level
        # default is the literal placeholder 'old' (action.yml), so GitHub
        # Actions always populates INPUT_OLD_VERSION with at least 'old' --
        # never actually empty on a real invocation. A bare truthiness
        # check therefore rejected *every* audit-only invocation, not just
        # ones that explicitly set old-version to something else.
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_OLD_VERSION": "old",
            },
        )
        assert outputs["_returncode"] == 0, outputs


class TestAuditOnlyCompareUnaffectedWhenTheseInputsAreUnset:
    """The common case -- an audit-only compare that never touches since/
    changed-path/budget -- is unaffected by any of the guards above: it
    still reaches `compare --no-baseline` and completes normally."""

    def test_plain_audit_only_compare_still_succeeds(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        assert outputs["_returncode"] == 0, outputs


class TestAuditOnlyCompareRequireCompleteAnalysisIsForwarded:
    """Unlike since/changed-path/budget, require-complete-analysis is NOT
    withheld from the audit-only shape: `compare --no-baseline` accepts the
    flag and gives it real teeth (an incomplete analysis-assurance
    candidate-side finding fails the step). This fixture's own stored
    snapshot carries complete assurance, so setting the flag does not
    change its outcome here -- this test proves the flag reaches the CLI
    without erroring (a CLI that rejected it outright would fail this run
    at exit 64, not agree with the no-flag run), not that the flag is inert
    in general (`TestAuditOnlyCompareForwardsRequireCompleteAnalysis`
    below proves it reaches the assembled command line directly)."""

    def test_require_complete_analysis_does_not_error_and_does_not_change_this_fixtures_outcome(
        self, tmp_path: Path
    ) -> None:
        without_flag = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        with_flag = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            },
        )
        assert without_flag["_returncode"] == 0, without_flag
        assert with_flag["_returncode"] == 0, with_flag
        assert with_flag.get("verdict") == without_flag.get("verdict")


# --- Static CMD-assembly checks for the two-sided-compare shape -----------
#
# A real two-sided end-to-end run needs two real artifacts (a baseline and
# a candidate) rather than one committed snapshot fixture; the static
# harness below asserts directly on the assembled `CMD` array instead,
# which needs no real binaries at all.


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


_CMD_MARKER = "__ABICHECK_TEST_CMD_START__"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    script = (
        _mode_branches_region()
        + f"\nprintf '%s' '{_CMD_MARKER}'"
        + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    marker_index = result.stdout.rfind(_CMD_MARKER)
    assert marker_index != -1, result.stdout
    payload = result.stdout[marker_index + len(_CMD_MARKER) :]
    return [item for item in payload.split("\x1f") if item]


_BASELINE_INPUTS = {
    "INPUT_MODE": "compare",
    "INPUT_NEW_LIBRARY": "lib.so",
    "INPUT_OLD_LIBRARY": "baseline.so",
}


class TestTwoSidedCompareStillForwardsSinceChangedPathBudget:
    def test_since_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_SINCE": "origin/main"})
        assert "--since" in cmd, cmd
        assert "origin/main" in cmd, cmd

    def test_changed_path_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_CHANGED_PATH": "src/foo.c"})
        assert "--changed-path" in cmd, cmd
        assert "src/foo.c" in cmd, cmd

    def test_budget_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_BUDGET": "15m"})
        assert "--budget" in cmd, cmd
        assert "15m" in cmd, cmd

    def test_require_complete_analysis_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true"})
        assert "--require-complete-analysis" in cmd, cmd

    def test_follow_deps_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_FOLLOW_DEPS": "true"})
        assert "--follow-deps" in cmd, cmd


class TestAuditOnlyCompareForwardsRequireCompleteAnalysis:
    """`compare --no-baseline` genuinely accepts require-complete-analysis
    (live-verified against the real CLI), so unlike since/changed-path/
    budget, this Action forwards it unconditionally for the audit-only
    shape rather than withholding it."""

    def test_flag_is_present_in_cmd_when_input_is_true(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            }
        )
        assert "--require-complete-analysis" in cmd, cmd


class TestAuditOnlyCompareNeverForwardsBudget:
    def test_budget_flag_is_absent_from_cmd(self) -> None:
        # The early-rejection preflight (tested via the real end-to-end
        # harness above) means an audit-only run reaching this point never
        # has INPUT_BUDGET set -- but the command-assembly branch itself
        # must still never emit --budget unconditionally either, since the
        # static harness here bypasses that preflight (it starts partway
        # through the file, at the mode-branches region).
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "lib.so",
            }
        )
        assert "--budget" not in cmd, cmd
