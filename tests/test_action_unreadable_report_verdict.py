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

"""``action/run.sh`` must not publish COMPATIBLE for a result it cannot read.

The defect: ``_resolve_clean_exit_verdict`` opened with
``VERDICT="COMPATIBLE"`` and only ever *escalated* from a report it could
read, so an abicheck that exited 0 while writing no usable JSON report
published "No binary ABI break detected" -- a compatibility claim nothing had
established. The axis predicates (``_assurance_gated`` and friends) could not
close this on their own: each deliberately answers "not gated by this axis"
for an unreadable report, which ADR-063 Track T8 chose over reconstructing an
axis from forgeable stderr prose, and that choice is still right.

This file executes the *whole* of ``run.sh`` for each shape, rather than
extracting the verdict snippet. The reader's own unit-level invariants live in
``tests/test_action_report_query.py``; what is verified here is that the
verdict, the step's exit code, the Job Summary and the ``verdict`` output all
agree -- the previous fix in this area (PR #705 -> #758) shipped a defense that
asserted file *text* rather than executing the path, and this is the same
class of mistake to avoid.

Bug class: ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
RUN_SH = ACTION_DIR / "run.sh"

# POSIX only, for the same reason `test_action_coverage_verdict.py` states: this
# harness works by putting an *extensionless, shebang-dispatched* `abicheck` on
# PATH, because `run.sh` resolves the binary by name. Windows has neither the
# executable bit nor kernel shebang handling, and Git bash's `chmod` is a no-op
# on NTFS, so the stub is not runnable there — every test in the module then
# fails identically with the WSL launcher stub's own UTF-16 "no installed
# distributions" text instead of anything from `run.sh`. The behaviour under
# test is plain shell with no platform-dependent branch, and the Linux lane
# exercises all of it.
#
# Omitting this marker is what turned the windows-latest unit lane red on this
# PR: the sibling module documented the pitfall and this one did not copy it.
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)


def _stub_abicheck(tmp_path: Path, *, exit_code: int, payload: bytes | None) -> Path:
    """An abicheck that exits *exit_code* and writes *payload* to its ``-o`` path.

    ``payload=None`` writes nothing at all -- the "died after the exit code,
    before the report" shape, which is the one a real truncated/killed run
    produces and which no valid-JSON fixture can stand in for.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    body = [
        "#!/usr/bin/env bash",
        "prev=''",
        'for arg in "$@"; do',
        '  if [[ "$prev" == "-o" ]]; then',
    ]
    if payload is None:
        body.append("    :")
    else:
        blob = tmp_path / "payload.bin"
        blob.write_bytes(payload)
        body.append(f'    cp "{blob}" "$arg"')
    body += ["  fi", '  prev="$arg"', "done", f"exit {exit_code}"]
    stub = bindir / "abicheck"
    stub.write_text("\n".join(body) + "\n", encoding="utf-8")
    stub.chmod(0o755)
    return bindir


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


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
    outputs: dict = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_stdout"] = proc.stdout
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _compare_env(tmp_path: Path) -> dict[str, str]:
    return {
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }


#: Each is a distinct way for an exit-0 run to leave no readable result. The
#: reported instance was ``{}``; enumerating the class is the point -- a fix
#: tested against one member forecloses one input.
UNUSABLE_PAYLOADS = (
    ("no report written", None),
    ("zero bytes", b""),
    ("truncated json", b'{"verdict": "COMPATIBLE"'),
    ("not json", b"Killed\n"),
    ("json array", b"[]"),
    ("bare string", b'"COMPATIBLE"'),
    ("json null", b"null"),
    ("empty object", b"{}"),
    ("parsed but resultless", b'{"error": "write interrupted"}'),
    # Codex's counterexamples to the presence-only recognizer: both parse, both
    # are non-empty, and neither carries a verdict anything can read.
    ("null findings, no verdict", b'{"findings": null}'),
    ("no_baseline with no findings array", b'{"no_baseline": true}'),
)


class TestExitZeroWithoutAReadableReport:
    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_never_publishes_compatible(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") != "COMPATIBLE", f"{label}: {outputs}"
        assert outputs.get("verdict") == "REPORT_UNREADABLE", f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_fails_the_step(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 1, f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_is_not_waived_by_fail_on_breaking_false(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        # `fail-on-breaking: false` says "do not fail me for an ABI break". It
        # is not a statement that an unverifiable result should be reported as
        # a pass, and every other non-compatibility axis in this script
        # (budget overflow, evidence-contract error, coverage, assurance,
        # scope) is likewise unconditional.
        env = _compare_env(tmp_path) | {
            "INPUT_FAIL_ON_BREAKING": "false",
            "INPUT_FAIL_ON_API_BREAK": "false",
        }
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["_exit"] == 1, f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_the_summary_says_no_result_was_established(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        # A `case` arm was added for this verdict specifically because a bash
        # `case` with no match renders *nothing* -- the failure mode that once
        # left COMPATIBLE_WITH_RISK with an empty summary. A verdict whose
        # whole job is to say "no result" must not render as silence.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        summary = outputs["_summary"]
        assert "REPORT_UNREADABLE" in summary, f"{label}: {summary!r}"
        assert "no compatibility result" in summary.lower(), f"{label}: {summary!r}"
        assert "No binary ABI break detected" not in summary, f"{label}: {summary!r}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_an_error_annotation_names_the_cause(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert "::error::" in outputs["_stdout"], f"{label}: {outputs['_stdout']!r}"
        assert "JSON report" in outputs["_stdout"], f"{label}: {outputs['_stdout']!r}"


class TestAReadableReportIsUnaffected:
    """The other half of the invariant: this must not fail working runs.

    Every assertion above is satisfiable by a script that fails
    unconditionally, which would break every real green check -- so the
    passing paths are pinned here too, including the two exit-0 tiers that
    are *not* plain COMPATIBLE.
    """

    def test_a_clean_report_still_publishes_compatible(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_an_advisory_break_still_reports_the_break(self, tmp_path: Path) -> None:
        # The combination the review singled out: a real break the severity
        # policy demoted to exit 0. Both facts must survive -- the verdict says
        # BREAKING even though the step is not failed by this axis.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "BREAKING"}
            ).encode(),
        )
        env = _compare_env(tmp_path) | {"INPUT_FAIL_ON_BREAKING": "false"}
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["verdict"] == "BREAKING", outputs

    def test_a_risk_tier_is_still_reported(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE_WITH_RISK"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs

    def test_a_legacy_report_without_a_schema_version_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # An older abicheck's report predates both `report_schema_version`'s
        # current value and the assurance contribution. It is readable, so it
        # must still produce a verdict -- the fix is about unreadable reports,
        # not old ones.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps({"verdict": "COMPATIBLE"}).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs


class TestContradictoryAssuranceSchema:
    """A report claiming schema >= 2.40 while omitting the assurance axis.

    Not "cannot tell": the document's own version claim rules out the legacy
    explanation, so the axis is unreportable from a report that was required
    to report it. That is an invalid result, and
    ``_assurance_gated``'s deliberate fail-open would otherwise pass it.
    """

    def _contradictory(self, tmp_path: Path) -> dict:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "2.41",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        return _run_action(tmp_path, _compare_env(tmp_path), bindir)

    def test_fails_the_step(self, tmp_path: Path) -> None:
        outputs = self._contradictory(tmp_path)
        assert outputs["_exit"] == 1, outputs
        assert "analysis_assurance_exit_contribution" in outputs["_stdout"], outputs[
            "_stdout"
        ]

    def test_the_published_verdict_is_not_compatible(self, tmp_path: Path) -> None:
        """The exit code is not the only thing a consumer reads.

        Regression for a real defect in this very change (Codex review, P2,
        reproduced): the contradiction was detected only at the FINAL_EXIT fold,
        which runs *after* the verdict output, the job summary and the PR comment
        are published. So the step failed while publishing
        `verdict=COMPATIBLE` and "No binary ABI break detected" -- a false clean
        result for any workflow that branches on the output or runs under
        `continue-on-error`, which is the exact failure this axis exists to
        prevent, reintroduced one layer out.

        `test_fails_the_step` above did not catch it because it asserted the exit
        code and the log text only. That is why this file's contract row is
        "verdict, gate and exit code checked independently" -- asserting one and
        assuming the others agree is how they came to disagree.
        """
        outputs = self._contradictory(tmp_path)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["verdict"] != "COMPATIBLE", outputs

    def test_the_summary_does_not_claim_no_break(self, tmp_path: Path) -> None:
        outputs = self._contradictory(tmp_path)
        summary = outputs["_summary"]
        assert "No binary ABI break detected" not in summary, summary
        assert "REPORT_UNREADABLE" in summary, summary

    def test_a_pre_2_40_report_is_accepted(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "2.39",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_a_current_report_with_no_assurance_block_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # The common case, and the one an earlier draft of this check failed:
        # `analysis_assurance` is attached only when the result carries a real
        # one, so a perfectly ordinary current-schema report has neither key
        # and must not be read as internally inconsistent.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_a_current_report_carrying_the_axis_is_accepted(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "4.4",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                    "analysis_assurance_exit_contribution": 0,
                }
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs


class TestTheScopeOfTheCheckIsDeliberate:
    """The boundary of `_json_report_expected`, pinned as behavior.

    The check covers a JSON report the *caller* asked for (`format: json` plus
    `output-file`), not the internal `--write json=` sidecar `run.sh` injects
    for its own PR-comment/annotation rendering when the primary format is not
    json. Recording that boundary here keeps a future widening a deliberate
    decision rather than an accident -- and the widening is specifically not
    the right fix, since exit 0 is genuine evidence that abicheck's own gate
    passed (see `action/AGENTS.md`, "The residual").
    """

    def test_a_non_json_format_run_without_a_sidecar_still_reports_compatible(
        self, tmp_path: Path
    ) -> None:
        # The documented residual, asserted rather than described: at exit 0
        # with no sidecar, the tier is unverified but the run's acceptance is
        # not, so the step stays green. Closing this needs a new verdict value
        # ("accepted, tier unverified"), which is an output-contract change.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "markdown",
            },
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_requested_json_report_is_covered(self, tmp_path: Path) -> None:
        # The same stub, the same missing report, differing only in whether the
        # caller asked for JSON -- so this pair isolates exactly what
        # `_json_report_expected` keys on, rather than each case proving it
        # separately against a different stub.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


def _stub_stdout_only(tmp_path: Path, *, stdout: str) -> Path:
    """An abicheck that writes nothing to ``-o`` and prints *stdout* instead.

    The documented `format: json` stdout mode -- no `output-file` at all, the
    report goes to stdout. An empty *stdout* is the failure this shape can
    reach.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "abicheck"
    body = "#!/usr/bin/env bash\n"
    if stdout:
        blob = tmp_path / "stdout.txt"
        blob.write_text(stdout, encoding="utf-8")
        body += f'cat "{blob}"\n'
    body += "exit 0\n"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return bindir


class TestEveryCallerRequestedJsonModeIsCovered:
    """`format: json` is the request; where the report lands is a separate choice.

    The predicate keyed on `format: json` **plus** `output-file`, which left two
    modes the caller had explicitly asked JSON for uncovered -- the documented
    stdout mode, and a caller-supplied `extra-args --write json=PATH`. In both,
    an exit-0 run that produced nothing still published COMPATIBLE (Codex
    review, P2). The internal `--write json=` sidecar the Action injects for
    itself stays excluded; `TestTheScopeOfTheCheckIsDeliberate` pins that half.
    """

    def _env(self, tmp_path: Path, **extra: str) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            **extra,
        }

    def test_json_to_stdout_with_no_output_is_caught(self, tmp_path: Path) -> None:
        bindir = _stub_stdout_only(tmp_path, stdout="")
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_json_to_stdout_carrying_a_real_report_still_passes(
        self, tmp_path: Path
    ) -> None:
        # The negative control for the case above: the same mode, a real report,
        # must still publish its verdict -- otherwise the fix breaks stdout mode
        # outright.
        bindir = _stub_stdout_only(
            tmp_path,
            stdout=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ),
        )
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_json_to_stdout_that_is_resultless_is_caught(self, tmp_path: Path) -> None:
        bindir = _stub_stdout_only(tmp_path, stdout='{"error": "write interrupted"}')
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_a_caller_supplied_write_json_path_is_caught(self, tmp_path: Path) -> None:
        # `format: markdown` plus the caller's own `--write json=` -- the JSON
        # request arrives through the passthrough, which makes it no less the
        # caller's. The stub honors neither, so nothing arrives.
        target = tmp_path / "caller.json"
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path,
                INPUT_FORMAT="markdown",
                INPUT_EXTRA_ARGS=f"--write json={target}",
            ),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestEveryRepeatableWriteDestinationIsChecked:
    """`--write` is repeatable, so every `json=` destination is a real request.

    `compare` declares `--write` with `multiple=True` (ADR-068 D4, "one
    analysis, several artifacts"), and every named artifact is written. The
    extractor behind this predicate used to *clear* an already-found `json=`
    path whenever a later `--write` named another format — matching a stale
    comment that called the option scalar and last-wins. So
    `--write json=a.json --write markdown=b.md` reported no requested JSON path
    at all, and a missing `a.json` left an exit-0 run publishing COMPATIBLE
    (Codex review, P2).

    The two contradictory claims in the tree were settled against the option
    declaration itself, not either comment.
    """

    def _env(self, tmp_path: Path, extra_args: str) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "markdown",
            "INPUT_EXTRA_ARGS": extra_args,
        }

    def _stub_writing(self, tmp_path: Path, *, honor: set[str]) -> Path:
        """An abicheck that writes only the `json=` destinations in *honor*.

        Lets a test name two JSON destinations and have exactly one arrive —
        the shape the clearing bug hid.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        payload = json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"})
        lines = ["#!/usr/bin/env bash"]
        for name in sorted(honor):
            lines.append(f"printf '%s' '{payload}' > {name}")
        lines.append("exit 0")
        stub = bindir / "abicheck"
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    def test_a_json_write_followed_by_another_format_is_still_required(
        self, tmp_path: Path
    ) -> None:
        # The exact reported case: the json destination is named first, a
        # non-json `--write` follows, and nothing writes the json one.
        target = tmp_path / "a.json"
        bindir = self._stub_writing(tmp_path, honor=set())
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path, f"--write json={target} --write markdown={tmp_path / 'b.md'}"
            ),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_the_same_combination_passes_when_the_json_destination_arrives(
        self, tmp_path: Path
    ) -> None:
        # Negative control: identical extra-args, but the stub honors the json
        # destination. Without this, the fix is satisfiable by rejecting every
        # multi-write invocation.
        target = tmp_path / "a.json"
        bindir = self._stub_writing(tmp_path, honor={str(target)})
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path, f"--write json={target} --write markdown={tmp_path / 'b.md'}"
            ),
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_second_json_destination_is_checked_too(self, tmp_path: Path) -> None:
        # "Track all caller-supplied JSON destinations" — one arriving does not
        # excuse another that did not, so a readable first destination must not
        # mask an absent second.
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        bindir = self._stub_writing(tmp_path, honor={str(first)})
        outputs = _run_action(
            tmp_path,
            self._env(tmp_path, f"--write json={first} --write json={second}"),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_both_json_destinations_arriving_passes(self, tmp_path: Path) -> None:
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        bindir = self._stub_writing(tmp_path, honor={str(first), str(second)})
        outputs = _run_action(
            tmp_path,
            self._env(tmp_path, f"--write json={first} --write json={second}"),
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_resultless_destination_is_caught(self, tmp_path: Path) -> None:
        target = tmp_path / "a.json"
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "abicheck"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s' '{{\"error\": \"write interrupted\"}}' > {target}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"--write json={target}"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestAContradictoryReportAtANonZeroExit:
    """At a nonzero exit the compatibility verdict is readable — so it is kept.

    `_resolve_clean_exit_verdict` runs only at exit 0, so a self-contradictory
    report at exit 2 keeps the verdict the dispatch derived and the `FINAL_EXIT`
    check supplies the failure. Codex review raised this as the exit-0 finding
    generalized, and the code/doc mismatch it named was real — `action.yml`
    promised `REPORT_UNREADABLE` for this report shape without qualifying by
    exit path. I fixed that by scoping the documentation, not by overriding the
    verdict: at exit 2 the report's compatibility result IS established and
    readable, the two axes are orthogonal, and replacing a real break with "no
    result was established" would discard evidence — the exact opposite of what
    this value exists for.

    The decision is pinned here so a later round does not quietly reverse it:
    the step still fails and still explains itself, which is what the axis owes.
    """

    def _contradictory_at(
        self, tmp_path: Path, *, exit_code: int, verdict: str
    ) -> dict:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=exit_code,
            payload=json.dumps(
                {
                    "report_schema_version": "2.41",
                    "verdict": verdict,
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        return _run_action(tmp_path, _compare_env(tmp_path), bindir)

    @pytest.mark.parametrize("exit_code,verdict", ((2, "API_BREAK"), (4, "BREAKING")))
    def test_the_real_compatibility_verdict_survives(
        self, tmp_path: Path, exit_code: int, verdict: str
    ) -> None:
        outputs = self._contradictory_at(tmp_path, exit_code=exit_code, verdict=verdict)
        assert outputs["verdict"] == verdict, outputs
        assert outputs["verdict"] != "REPORT_UNREADABLE", outputs

    @pytest.mark.parametrize("exit_code,verdict", ((2, "API_BREAK"), (4, "BREAKING")))
    def test_the_step_still_fails_and_still_explains_itself(
        self, tmp_path: Path, exit_code: int, verdict: str
    ) -> None:
        # Keeping the verdict must not cost the gate or the diagnostic: those
        # are what the assurance axis owes regardless of which label is
        # published.
        outputs = self._contradictory_at(tmp_path, exit_code=exit_code, verdict=verdict)
        assert outputs["_exit"] == 1, outputs
        assert "analysis_assurance_exit_contribution" in outputs["_stdout"], outputs[
            "_stdout"
        ]

    def test_exit_zero_still_publishes_report_unreadable(self, tmp_path: Path) -> None:
        # The contrast that makes the scoping coherent: at exit 0 there is no
        # other readable result, so the fallthrough would publish COMPATIBLE and
        # the verdict override is required.
        outputs = self._contradictory_at(tmp_path, exit_code=0, verdict="COMPATIBLE")
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestADryRunIsNotAMissingReport:
    """A dry run writes no report *by design* — that is not a failure.

    `compare --dry-run` performs no analysis and only previews the command, so
    `format: json` plus `dry-run: true` legitimately produces nothing. The
    requested-report validation added in this change turned that into
    `REPORT_UNREADABLE` for every two-sided preview — a regression this change
    introduced, because the pre-existing dry-run early return was gated on the
    audit-only shape (Codex review, P2).

    Widened to both shapes, and in the truthful direction: the two-sided case
    used to publish `COMPATIBLE`, which claimed a result no analysis produced.
    `DRY_RUN` is honest for both.
    """

    def _dry_run(self, tmp_path: Path, **extra: str) -> dict:
        # Writes nothing at all, exactly as a real `--dry-run` does.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        return _run_action(tmp_path, _compare_env(tmp_path) | extra, bindir)

    def test_a_two_sided_dry_run_publishes_dry_run(self, tmp_path: Path) -> None:
        outputs = self._dry_run(tmp_path, INPUT_DRY_RUN="true")
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["verdict"] != "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_two_sided_dry_run_does_not_publish_compatible(
        self, tmp_path: Path
    ) -> None:
        # The other half of the correction: a preview never established a
        # compatibility result, so COMPATIBLE was wrong before too.
        outputs = self._dry_run(tmp_path, INPUT_DRY_RUN="true")
        assert outputs["verdict"] != "COMPATIBLE", outputs

    def test_an_effective_dry_run_via_extra_args_is_also_exempt(
        self, tmp_path: Path
    ) -> None:
        # `extra-args --dry-run` leaves INPUT_DRY_RUN false, so the predicate
        # has to consult the passthrough as well — the same
        # nominal-versus-effective split `_effective_format` handles.
        outputs = self._dry_run(tmp_path, INPUT_EXTRA_ARGS="--dry-run")
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_real_run_that_writes_nothing_is_still_caught(
        self, tmp_path: Path
    ) -> None:
        # The control that keeps the exemption narrow: without the dry-run
        # flag, the identical stub (writing nothing) must still be reported.
        outputs = self._dry_run(tmp_path)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


#: Every way one named destination can fail to hold a usable report *this run*
#: produced. `stale` is the odd one out and the point of the enumeration: the
#: document is valid, parses, carries a real verdict, and was written by some
#: earlier run — indistinguishable from a good report by content alone.
UNUSABLE_DESTINATION_STATES = (
    "absent",
    "zero bytes",
    "empty object",
    "no result",
    "truncated",
    "stale",
)

_GOOD_REPORT = json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"})

_STATE_PAYLOADS = {
    "zero bytes": "",
    "empty object": "{}",
    "no result": '{"error": "write interrupted"}',
    "truncated": '{"verdict": "COMPATIBLE"',
}


class TestEveryRequestedDestinationIsJudgedOnItsOwn:
    """A requested destination must hold a report *this invocation* produced.

    Two review findings (Codex, P2, both reproduced) with one root cause: the
    validation asked "is there a readable report somewhere" rather than "did
    this run produce a usable report at every destination it was asked for".

    * `_report_validity` followed `_json_report_src`, which is a *fallback
      chain* — so a missing `format: json` primary was masked by a valid
      `extra-args --write json=secondary.json`, and the step published a
      compatibility verdict though the requested output never arrived.
    * the per-`--write` loop checked parseability without freshness — so a
      leftover document (or one a PR author committed, `extra-args` being
      PR-controlled) read as one this run had written.

    The invariant, stated over the cross-product rather than the two reported
    inputs: *for any single requested destination left in any unusable state,
    with every other destination valid and freshly written, the step must
    publish REPORT_UNREADABLE and fail.* One destination arriving never
    answers for another, and content alone never establishes authorship.
    """

    def _paths(self, tmp_path: Path) -> tuple[Path, Path]:
        return tmp_path / "primary.json", tmp_path / "secondary.json"

    def _stub(self, tmp_path: Path, writes: dict[Path, str]) -> Path:
        """An abicheck writing exactly *writes* (destination -> literal text)."""
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        lines = ["#!/usr/bin/env bash"]
        for index, (dest, text) in enumerate(sorted(writes.items())):
            # Via a file, so no payload text has to survive shell quoting.
            blob = tmp_path / f"blob{index}.bin"
            blob.write_text(text, encoding="utf-8")
            lines.append(f'cp "{blob}" "{dest}"')
        lines.append("exit 0")
        stub = bindir / "abicheck"
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    def _env(self, tmp_path: Path, primary: Path, secondary: Path) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(primary),
            "INPUT_EXTRA_ARGS": f"--write json={secondary}",
        }

    def _arrange(
        self, tmp_path: Path, broken: str, state: str
    ) -> tuple[Path, dict[str, str]]:
        """Set up so *broken* ("primary"/"secondary") is in *state*, the other good."""
        primary, secondary = self._paths(tmp_path)
        target = primary if broken == "primary" else secondary
        other = secondary if broken == "primary" else primary
        writes = {other: _GOOD_REPORT}
        if state == "stale":
            # Pre-exists with perfectly valid content this run never wrote.
            target.write_text(_GOOD_REPORT, encoding="utf-8")
        elif state != "absent":
            writes[target] = _STATE_PAYLOADS[state]
        return self._stub(tmp_path, writes), self._env(tmp_path, primary, secondary)

    @pytest.mark.parametrize("broken", ("primary", "secondary"))
    @pytest.mark.parametrize("state", UNUSABLE_DESTINATION_STATES)
    def test_one_unusable_destination_is_never_masked_by_the_other(
        self, tmp_path: Path, broken: str, state: str
    ) -> None:
        bindir, env = self._arrange(tmp_path, broken, state)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", (broken, state, outputs)
        assert outputs["_exit"] == 1, (broken, state, outputs)

    @pytest.mark.parametrize("broken", ("primary", "secondary"))
    @pytest.mark.parametrize("state", UNUSABLE_DESTINATION_STATES)
    def test_the_diagnostic_names_the_destination_that_failed(
        self, tmp_path: Path, broken: str, state: str
    ) -> None:
        # A verdict alone leaves the user hunting; and naming the *other*
        # destination is exactly the confusion the fallback chain caused.
        #
        # Scoped to the `::error::` annotations, not to stdout as a whole: every
        # destination path also appears in the echoed command line, so a
        # substring search over all output passes even when no diagnostic was
        # emitted at all (caught by mutation-testing this assertion).
        bindir, env = self._arrange(tmp_path, broken, state)
        outputs = _run_action(tmp_path, env, bindir)
        primary, secondary = self._paths(tmp_path)
        named = primary if broken == "primary" else secondary
        other = secondary if broken == "primary" else primary
        errors = [
            line
            for line in outputs["_stdout"].splitlines()
            if line.startswith("::error::")
        ]
        blamed = [line for line in errors if str(named) in line]
        assert blamed, (broken, state, errors)
        assert not [line for line in errors if str(other) in line], (
            broken,
            state,
            errors,
        )

    def test_both_destinations_freshly_written_is_compatible(
        self, tmp_path: Path
    ) -> None:
        # The control that keeps every assertion above from being satisfiable
        # by rejecting any multi-destination invocation outright.
        primary, secondary = self._paths(tmp_path)
        bindir = self._stub(tmp_path, {primary: _GOOD_REPORT, secondary: _GOOD_REPORT})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_pre_existing_destination_the_run_overwrites_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # Freshness must key on "did this run write it", not "did it pre-exist":
        # re-running a workflow step over an existing report.json is ordinary.
        primary, secondary = self._paths(tmp_path)
        primary.write_text(json.dumps({"verdict": "BREAKING"}), encoding="utf-8")
        secondary.write_text(json.dumps({"verdict": "BREAKING"}), encoding="utf-8")
        bindir = self._stub(tmp_path, {primary: _GOOD_REPORT, secondary: _GOOD_REPORT})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_stale_destination_is_not_read_as_this_runs_verdict(
        self, tmp_path: Path
    ) -> None:
        # The forgery the freshness check exists to stop, stated as its own
        # case: a PR-committed report claiming COMPATIBLE must not be able to
        # answer for a run that wrote nothing at all.
        primary, secondary = self._paths(tmp_path)
        primary.write_text(_GOOD_REPORT, encoding="utf-8")
        secondary.write_text(_GOOD_REPORT, encoding="utf-8")
        bindir = self._stub(tmp_path, {})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs
