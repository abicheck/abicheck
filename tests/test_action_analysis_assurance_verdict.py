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

"""The composite Action's mapping for P0.4's analysis-assurance exit
(``--require-complete-analysis``, ``abicheck/analysis_assurance.py``).

Before this, ``action/run.sh`` had no notion of this axis at all: an exit 1
caused by incomplete ``analysis_assurance`` fell through the existing exit-1
disambiguation and was mislabeled -- ``SEVERITY_ERROR`` on ``compare`` (a
*severity-policy* failure it is not) or ``ERROR`` on ``scan`` (an
*operational* failure it is not). The axis is neither: like ADR-049's
contract-coverage axis it never rewrites the compatibility verdict, only
floors the exit code, and is unconditional (no ``fail-on-*`` flag can turn
it off).

The Action gates on this axis via a dedicated ``require-complete-analysis``
boolean input (mirroring ``fail-on-breaking``), the terminal fix for a class
of bug found across three Codex review rounds while this axis's detection
was still inferred from other signals (an unanchored stderr grep, then a
``$CMD``-array token scan) -- see ``action/run.sh``'s own comment above
``_assurance_gated()`` for the full history.

Mirrors ``test_action_coverage_verdict.py``'s own harness style (real
subprocess through ``run.sh``, a shebang-dispatched ``abicheck`` stub on
``PATH``) rather than importing it: this repo's own convention for these
``test_action_*`` modules is that each carries its own copy rather than a
shared import (see ``CLAUDE.md`` "M1-1"'s adjacent note in ``AGENTS.md`` on
the ``~24 test_action_*`` modules predating a canonical resolver).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parent.parent / "action"
RUN_SH = ACTION_DIR / "run.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)

#: The exact wording `assurance_floor_diagnostic` (analysis_assurance.py)
#: emits. `_assurance_gated()` in run.sh used to grep for this substring on
#: stderr when no JSON report was readable; ADR-063 Track T8 removed that
#: fallback, so the line is now only ever inert text in these fixtures --
#: which is exactly what several tests below assert.
ASSURANCE_STDERR = (
    "Analysis assurance incomplete (status='partial') under "
    "--require-complete-analysis: header context asymmetric between old and "
    "new. Exit code floored to 1. (P0.4 analysis-assurance axis). Use "
    "--format json for the full analysis_assurance block."
)

COVERAGE_STDERR = (
    "Contract coverage incomplete for the selected --contract domain: "
    "old/export_table. Exit code floored to 1."
)

ASSURANCE_BLOCK = {
    "schema_version": 1,
    "status": "partial",
    "notes": ["header context asymmetric between old and new"],
}

#: `_assurance_gated()` requires this dedicated boolean input to be `true`
#: before it will even look at the report -- the load-bearing check. Every
#: test below that expects the axis to actually gate must pass this, and a
#: readable JSON report whose `analysis_assurance.status` is not "complete".
REQUIRE_COMPLETE_ANALYSIS_INPUT = {"INPUT_REQUIRE_COMPLETE_ANALYSIS": "true"}


def _stub_abicheck(
    tmp_path: Path,
    *,
    exit_code: int,
    report: dict | None,
    stderr: str = "",
) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(report or {}), encoding="utf-8")
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" ]]; then\n'
        f'    cp "{payload}" "$arg"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        + (f'printf "%s\\n" {json.dumps(stderr)} >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    summary = tmp_path / "step_summary"
    summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}",
            "ACTION_PATH": str(ACTION_DIR),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(runner_temp),
            "INPUT_ADD_JOB_SUMMARY": "true",
            **env_extra,
        }
    )
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_stdout"] = proc.stdout
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


class TestScanMapsTheAssuranceExit:
    def test_an_assurance_gated_scan_is_not_an_operational_error(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert outputs["exit-code"] == "1", outputs
        assert "ANALYSIS_INCOMPLETE" in outputs["_summary"], outputs["_summary"]
        assert "header context asymmetric" in outputs["_summary"], outputs["_summary"]

    def test_the_step_fails_unconditionally_even_with_fail_on_breaking_false(
        self, tmp_path: Path
    ) -> None:
        """No ``fail-on-*`` flag disables this axis, matching the
        contract-coverage axis's own unconditional gate."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": "false",
                "INPUT_FAIL_ON_API_BREAK": "false",
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_run_without_the_flag_is_unaffected(self, tmp_path: Path) -> None:
        """`require-complete-analysis` is unset -> exit 1 with neither
        coverage nor assurance signal stays a plain ERROR, exactly as it
        always did -- regardless of the stderr diagnostic being absent too
        in this particular fixture."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "ERROR", outputs


class TestCompareMapsTheAssuranceExit:
    def test_an_assurance_gated_compare_is_not_a_severity_failure(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert outputs["exit-code"] == "1", outputs
        assert "ANALYSIS_INCOMPLETE" in outputs["_summary"], outputs["_summary"]

    def test_a_severity_gate_and_assurance_gap_both_survive_together(
        self, tmp_path: Path
    ) -> None:
        """When the severity axis also gates at 1, SEVERITY_ERROR keeps the
        verdict label (the pre-existing coverage-vs-severity precedence),
        but the summary still names the assurance gap so it isn't silently
        dropped."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "analysis_assurance": ASSURANCE_BLOCK,
                "severity": {
                    "config": {},
                    "categories": {},
                    "exit_code": 1,
                    "blocking": True,
                    "blocking_categories": ["addition"],
                },
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs
        assert "also reports incomplete analysis assurance" in outputs["_stdout"], (
            outputs["_stdout"]
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_coverage_gap_and_assurance_gap_both_survive_together(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "analysis_assurance": ASSURANCE_BLOCK,
                "contract_coverage_exit_contribution": 1,
                "contract_coverage_failures": [
                    {
                        "provider": "export_table",
                        "side": "old",
                        "record_id": "old/export_table",
                        "reason": "search_incomplete",
                        "status": "unavailable",
                        "completeness": "none",
                        "mode": "exports",
                        "suppressible": False,
                    }
                ],
            },
            stderr=f"{COVERAGE_STDERR}\n{ASSURANCE_STDERR}",
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs
        assert "also reports incomplete analysis assurance" in outputs["_stdout"], (
            outputs["_stdout"]
        )
        assert outputs["_exit"] == 1, outputs

    def test_the_step_fails_unconditionally_even_with_fail_on_breaking_false(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": "false",
                "INPUT_FAIL_ON_API_BREAK": "false",
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_run_without_the_flag_is_unaffected(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs


class TestHostileReportContentCannotExecute:
    """This diff touches the action/workflow trust boundary (``action/
    run.sh``): the captured stderr and the ``assurance_notes`` Python query
    both carry content an ``abicheck`` invocation produced from its own
    inputs, which can themselves come from a
    PR (a header, a symbol name, a policy note) an attacker controls.
    Asserting the *text* of the shell/Python changes does not prove a
    hostile value can't cause a side effect when run.sh's own shell
    interpolates it into a job-summary ``echo`` -- so these tests actually
    execute ``run.sh`` against a report/stderr crafted to look like a shell
    command substitution or an injection into the grep pattern, and assert
    the attempted payload never ran (no marker file materializes) while the
    run still reaches the correct, unspoofed verdict.
    """

    def test_a_command_substitution_in_assurance_notes_does_not_execute(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "pwned"
        payload = f"$(touch {marker})"
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {
                    "analysis_assurance": {
                        "schema_version": 1,
                        "status": "partial",
                        "notes": [payload],
                    }
                },
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert not marker.exists(), (
            "the crafted analysis_assurance note executed as a shell command"
        )
        # The verdict computation is unaffected by the hostile payload --
        # it is carried through as inert text in the summary, not evaluated.
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert payload in outputs["_summary"], outputs["_summary"]

    def test_the_real_diagnostic_text_cannot_spoof_the_gate_without_the_input(
        self, tmp_path: Path
    ) -> None:
        """The core anti-spoofing property (Codex review, P1, now closed by
        the dedicated input): stderr can contain content an attacker
        influences (a header/symbol name echoed back into some *other*,
        unrelated diagnostic), so `_assurance_gated()` must not trust
        stderr at all -- it first requires the dedicated
        `require-complete-analysis` input to be `true`, which
        attacker-controlled report/stderr content cannot forge (it isn't
        even read for this decision). Here the stderr line is the *exact*
        real diagnostic text (not a near-miss/mention) and the report's own
        status is genuinely `partial` -- everything a real gated run would
        show -- except the input was never set. Must still resolve to the
        pre-existing catch-all, not a spoofed ANALYSIS_INCOMPLETE."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                # Deliberately no REQUIRE_COMPLETE_ANALYSIS_INPUT here.
            },
            bindir,
        )
        # Falls through to the pre-existing catch-all, exactly as it does
        # with no diagnostic at all -- not a spoofed ANALYSIS_INCOMPLETE.
        assert outputs["verdict"] == "ERROR", outputs

    @pytest.mark.parametrize(
        "stderr_text",
        [
            ASSURANCE_STDERR,
            "some unrelated warning mentioning analysis assurance "
            "incomplete and require-complete-analysis but not the real "
            "diagnostic shape",
            "",
        ],
        ids=["real-diagnostic", "near-miss-mention", "no-diagnostic"],
    )
    def test_no_stderr_text_gates_this_axis_without_a_json_report(
        self, tmp_path: Path, stderr_text: str
    ) -> None:
        """ADR-063 Track T8: with no readable JSON report, stderr answers
        nothing at all for this axis -- not even the *exact* substring the
        retired fallback matched ("Analysis assurance incomplete ... under
        --require-complete-analysis"), and even with the dedicated input
        genuinely set.

        Parametrized over the real code-emitted diagnostic, a near-miss that
        merely mentions the same words, and no diagnostic whatsoever,
        because the property is that none of them differ: the class this
        closes is "a text channel deciding a published axis", not one
        particular forged spelling. The structured
        `analysis_assurance.status` is the only signal, so all three fall
        through to the pre-existing catch-all."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=None,
            stderr=stderr_text,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "ERROR", outputs

    def test_shell_metacharacters_in_the_stderr_diagnostic_do_not_execute(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "pwned2"
        hostile_stderr = (
            f"Analysis assurance incomplete (status='partial') under "
            f"--require-complete-analysis: `; touch {marker} ; ` "
            f"$({marker}) header context asymmetric. Exit code floored to 1."
        )
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=hostile_stderr,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert not marker.exists(), (
            "shell metacharacters in the captured stderr diagnostic executed"
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs


class TestOptionValueDoesNotSpoofTheFlag:
    """Two rounds of Codex-found false positives from inferring this flag
    from *other* signals, both now structurally impossible with the
    dedicated `require-complete-analysis` input in place -- neither
    `$CMD` nor `extra-args` is consulted for this decision at all anymore:

    1. Scanning the fully-built `$CMD` array for the literal
       `--require-complete-analysis` token is not equivalent to "the flag
       was passed", because `$CMD` also carries values a *different*,
       structured Action input supplied. `output-file:
       --require-complete-analysis` is exactly such a case -- it
       legitimately produces the adjacent tokens `-o
       --require-complete-analysis` in `$CMD`, with the real CLI's own
       option parser consuming the second token as `-o`'s filename
       argument, never parsing it as a flag.
    2. Scoping the scan to `extra-args`'s own split tokens closed (1) but
       not an identical collision *within* `extra-args` itself -- e.g.
       `--header --require-complete-analysis` (a real `--header
       old=|new=PATH` option consuming the next token as its own value)
       still false-positives, since no token-scan of any kind can prove a
       token was parsed as *this* flag rather than as some other option's
       argument.
    """

    def test_an_output_file_named_like_the_flag_does_not_gate(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                # The collision Codex described: this is a plain path value
                # for a structured, unrelated input -- not the flag.
                "INPUT_OUTPUT_FILE": "--require-complete-analysis",
                # Deliberately no REQUIRE_COMPLETE_ANALYSIS_INPUT: the flag
                # was never actually requested this run.
            },
            bindir,
        )
        # Falls through to the pre-existing catch-all, exactly as it does
        # with no diagnostic and no flag at all -- not a spoofed
        # ANALYSIS_INCOMPLETE from the -o value alone.
        assert outputs["verdict"] == "ERROR", outputs

    def test_an_extra_args_option_value_named_like_the_flag_does_not_gate(
        self, tmp_path: Path
    ) -> None:
        """Codex review, third finding: `extra-args: --header
        --require-complete-analysis` is a real `--header` option consuming
        the next token as its own value, not the flag -- and, unlike an
        `extra-args`-token scan, the dedicated input never even looks at
        `extra-args`'s contents, so this collision cannot reach the gate at
        all regardless of what `extra-args` contains."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_EXTRA_ARGS": "--header --require-complete-analysis",
                # Deliberately no REQUIRE_COMPLETE_ANALYSIS_INPUT: the flag
                # was never actually requested via the dedicated input.
            },
            bindir,
        )
        assert outputs["verdict"] == "ERROR", outputs


class TestEscalatedAssuranceGateIsNamedCorrectly:
    """Codex review: when a BREAKING/API_BREAK finding is demoted to
    compatibility exit 0 by severity policy but incomplete assurance
    raises the *actual* exit to 1, `_escalate_verdict_to_report()` leaves
    `GATE_TIER=ANALYSIS_INCOMPLETE`. `_blocking_gate_note()` (the helper
    that explains an escalated verdict in the job summary) had no
    assurance case before this fix, so it fell through to the generic
    "the severity policy gated this run" branch -- false, since severity
    demoted the finding rather than gating it, and the axis that actually
    produced the exit is orthogonal to severity entirely."""

    def test_an_escalated_breaking_report_names_assurance_not_severity(
        self, tmp_path: Path
    ) -> None:
        # A BREAKING report whose severity block resolves to exit 0 (the
        # finding was demoted), but analysis_assurance is incomplete under
        # the flag -- the real exit-1 cause is assurance alone.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "BREAKING",
                "analysis_assurance": ASSURANCE_BLOCK,
                "severity": {
                    "config": {},
                    "categories": {},
                    "exit_code": 0,
                    "blocking": False,
                    "blocking_categories": [],
                },
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "BREAKING", outputs
        assert "analysis-assurance axis" in outputs["_summary"], outputs["_summary"]
        assert (
            "severity policy gated this run" not in outputs["_summary"]
        ), outputs["_summary"]


class TestReleaseStyleCompareRejectsTheAssuranceInput:
    """Codex review (P2): the CLI's per-library release fan-out has no
    single `analysis_assurance` result to gate on and rejects
    `--require-complete-analysis` outright for a directory/package
    operand -- so `run.sh` used to just skip forwarding the flag for that
    shape, silently running a release compare ungated even though the
    caller explicitly asked for the assurance gate. Must fail loud (the
    step itself errors) instead, the same "explicit unsupported request,
    not a silent no-op" treatment the L2 compile-context and evidence-flag
    guards already give their own release-incompatible inputs."""

    def test_a_directory_old_library_with_the_flag_fails_the_step(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old_release"
        old_dir.mkdir()
        bindir = _stub_abicheck(tmp_path, exit_code=0, report={"verdict": "COMPATIBLE"})
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(old_dir),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs
        assert "does not support require-complete-analysis" in outputs["_stdout"], (
            outputs["_stdout"]
        )

    def test_a_single_pair_compare_with_the_flag_is_unaffected(
        self, tmp_path: Path
    ) -> None:
        """Sanity: the new guard must not misfire for the ordinary
        single-pair case the rest of this module already exercises."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "analysis_assurance": ASSURANCE_BLOCK,
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                **REQUIRE_COMPLETE_ANALYSIS_INPUT,
            },
            bindir,
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
