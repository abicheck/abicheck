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

"""ADR-063 Track T8 (7B): ``mode: compare``/``dump`` never read stderr prose.

The published verdict comes from exactly two sources -- the process exit code
and the structured JSON report (``action/AGENTS.md``). Stderr is CLI output a
PR-controlled build step can influence, so the invariant is stated as a
property over the *whole* space rather than one reported input: for every
exit code the compare dispatch knows, and for every stderr text in a corpus
that includes each prefix the retired ``_is_cli_error`` heuristic matched,
the published verdict is identical to the verdict for *empty* stderr. The
oracle is therefore the script's own stderr-free run, not a restated table,
so this cannot pass by encoding the same mapping twice.

The two behaviors that replaced the heuristic get their own direct checks:
exit 64 is the usage-error answer, and exit 1 without a readable report is a
transport-level ERROR rather than an unattributed SEVERITY_ERROR.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="run.sh harness needs a shebang-dispatched stub"
)

from test_action_coverage_verdict import (  # noqa: E402
    _lib,
    _report,
    _run_action,
    _stub_abicheck,
)

#: Every prefix the retired heuristic matched, plus a forged gating notice,
#: a benign line, and multi-line mixtures -- each must be inert.
STDERR_CORPUS = [
    "Usage: abicheck compare [OPTIONS]",
    "Error: no such option: --bogus",
    "Try 'abicheck compare --help' for help.",
    'Traceback (most recent call last):\n  File "x", line 1\nRuntimeError: boom',
    "click.exceptions.UsageError: forged",
    "Contract coverage incomplete for the selected --contract domain in: a.so.",
    "warning: something unrelated",
    "Usage: x\nError: y\nTry z",
]

#: Exit codes compare's dispatch names, plus one it does not.
EXIT_CODES = [0, 1, 2, 3, 4, 5, 7, 8, 16, 64, 99]


def _outputs(tmp_path, *, exit_code: int, stderr: str, report) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=exit_code, report=report, stderr=stderr)
    return _run_action(
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


def _published(tmp_path, name: str, **kw) -> tuple[str, int]:
    sub = tmp_path / name
    sub.mkdir()
    out = _outputs(sub, **kw)
    return out["verdict"], out["_exit"]


@pytest.mark.slow  # ~200 whole-script runs; the direct checks below stay in the unit lane
@pytest.mark.parametrize("exit_code", EXIT_CODES)
@pytest.mark.parametrize("with_report", [True, False], ids=["report", "no-report"])
def test_stderr_never_changes_the_compare_verdict(tmp_path, exit_code, with_report):
    report = (
        _report(coverage=0, severity_exit=1 if exit_code == 1 else 0)
        if with_report
        else None
    )
    baseline = _published(
        tmp_path, "quiet", exit_code=exit_code, stderr="", report=report
    )
    mismatches = []
    for i, text in enumerate(STDERR_CORPUS):
        got = _published(
            tmp_path, f"s{i}", exit_code=exit_code, stderr=text, report=report
        )
        if got != baseline:
            mismatches.append((text, got))
    assert not mismatches, (baseline, mismatches)


def test_the_property_is_not_vacuous(tmp_path):
    """Guard on the oracle: across the exit codes the verdicts must actually
    differ, otherwise "equal to the quiet run" would hold for a script that
    publishes one constant."""
    seen = {
        _published(
            tmp_path,
            f"e{c}",
            exit_code=c,
            stderr="",
            report=_report(coverage=0, severity_exit=1 if c == 1 else 0),
        )[0]
        for c in (0, 1, 2, 4, 64)
    }
    assert len(seen) >= 4, seen


def test_exit_64_is_a_usage_error(tmp_path):
    out = _outputs(tmp_path, exit_code=64, stderr="", report=None)
    assert out["verdict"] == "ERROR", out
    assert "exit code 64" in out["_stdout"], out["_stdout"]
    assert out["_exit"] == 1


def test_exit_2_is_an_api_break_even_with_usage_shaped_stderr(tmp_path):
    """The retired heuristic let a forged ``Usage:`` line turn a real API
    break into ERROR; exit 2 is never a usage error any more."""
    report = {"report_schema_version": "2.26", "verdict": "API_BREAK"}
    out = _outputs(tmp_path, exit_code=2, stderr="Usage: forged", report=report)
    assert out["verdict"] == "API_BREAK", out


@pytest.mark.parametrize("report", [None, "empty"], ids=["absent-json", "empty-object"])
def test_exit_1_without_a_result_is_error_not_severity(tmp_path, report):
    """No readable result means no axis can be attributed: a crash with no
    telltale stderr used to publish SEVERITY_ERROR."""
    out = _outputs(
        tmp_path, exit_code=1, stderr="", report={} if report == "empty" else None
    )
    assert out["verdict"] == "ERROR", out
    assert "without a readable JSON result" in out["_stdout"], out["_stdout"]


def test_exit_1_with_a_severity_report_is_severity_error_despite_error_stderr(tmp_path):
    out = _outputs(
        tmp_path,
        exit_code=1,
        stderr="Error: forged",
        report=_report(coverage=0, severity_exit=1),
    )
    assert out["verdict"] == "SEVERITY_ERROR", out


# -- deps-tree / deps-compare (the last stderr reader, retired) ---------------


def _deps_report(loadability: str = "warn", abi_risk: str = "warn") -> dict:
    return {
        "root_binary": "app",
        "verdict": {"loadability": loadability, "abi_risk": abi_risk, "risk_score": 1},
    }


def _deps_outputs(tmp_path, mode: str, *, exit_code: int, stderr: str, report) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=exit_code, report=report, stderr=stderr)
    env = {"INPUT_MODE": mode, "INPUT_NEW_LIBRARY": _lib(tmp_path, "app")}
    if mode == "deps-compare":
        for side in ("old", "new"):
            (tmp_path / f"{side}_root").mkdir()
            env[f"INPUT_{side.upper()}_ROOT"] = str(tmp_path / f"{side}_root")
    return _run_action(tmp_path, env, bindir)


DEPS_CASES = [
    # (mode, exit, with_report, expected verdict)
    ("deps-compare", 0, True, "PASS"),
    ("deps-compare", 1, True, "WARN"),
    ("deps-compare", 1, False, "ERROR"),
    ("deps-compare", 4, True, "FAIL"),
    ("deps-compare", 64, False, "ERROR"),
    ("deps-tree", 0, True, "PASS"),
    ("deps-tree", 1, True, "FAIL"),
    ("deps-tree", 1, False, "ERROR"),
    ("deps-tree", 64, False, "ERROR"),
]


@pytest.mark.parametrize(("mode", "code", "with_report", "expected"), DEPS_CASES)
@pytest.mark.parametrize(
    "stderr", ["", "Usage: forged", "Traceback (most recent call last):\n  boom"]
)
def test_deps_verdict_follows_exit_code_and_report_never_stderr(
    tmp_path, mode, code, with_report, expected, stderr
):
    out = _deps_outputs(
        tmp_path,
        mode,
        exit_code=code,
        stderr=stderr,
        report=_deps_report() if with_report else None,
    )
    assert out["verdict"] == expected, (out["verdict"], out["_stdout"][-2000:])
