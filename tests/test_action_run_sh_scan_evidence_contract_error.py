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

"""Behavioral tests for ``action/run.sh``'s ``scan`` exit-1 VERDICT mapping
for ``_EvidenceContractError`` (ADR-037 D5, scan_engine.py) — extracted
verbatim, same discipline as the sibling ``test_action_run_sh_*.py`` files
(``test_action_run_sh_scan_not_comparable.py`` is the closest analog).

CLI cleanup phase two / ADR-064's own "still open" item: a full
cross-front-end parity pass between the native CLI and this composite
Action. ``cli_scan.py`` raises ``_EvidenceContractError`` as a
``click.ClickException`` (exit 1, stderr ``Error: <message>``) — the
identical stderr shape a bad flag or a crash produces, so before this fix
``_is_cli_error``'s own ``^Error:`` match won the exit-1 disambiguation
unconditionally, folding a well-formed command that merely lacked evidence
for its own pinned ``--depth`` into the same generic "CLI error" bucket a
syntax typo gets — even though the native CLI's ``--format json`` path
already writes a real, distinguishable ``verdict: "EVIDENCE_CONTRACT_ERROR"``
envelope for this abort (``_emit_scan_abort_report``/
``scan_abort_result_fields``). The final-exit-code block is exercised too,
mirroring ``test_action_run_sh_scan_not_comparable.py``'s own rationale: a
verdict newly split out of the generic ``ERROR`` bucket must carry its own
explicit ``FINAL_EXIT=1``, or the step silently starts passing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_CASE_START = "    case $ABICHECK_EXIT in\n"
_CASE_END = "    esac\n"
_FINAL_EXIT_SCAN_START = (
    'elif [[ "$MODE" == "scan" ]]; then\n'
    "  # scan: BREAKING/API_BREAK follow the fail-on flags"
)
_FINAL_EXIT_SCAN_END = "\nelse\n"
#: Includes the real `$OSTYPE`-detection preamble that sets
#: `_RUNNING_ON_WINDOWS`, not just the function body -- see
#: `_report_query_and_gated_fragment`'s own docstring for why a hardcoded
#: value here was a real, self-masking bug.
_IS_PATH_QUALIFIED_START = 'case "$OSTYPE" in'
_IS_PATH_QUALIFIED_END = "}\n"
_REPORT_QUERY_START = "_report_query() {\n"
_REPORT_QUERY_END = "PYQUERY\n}\n"
_EVIDENCE_CONTRACT_GATED_START = "_evidence_contract_gated() {\n"
_EVIDENCE_CONTRACT_GATED_END = "}\n"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _exit_case_fragment() -> str:
    """The ``case $ABICHECK_EXIT in ... esac`` block from the scan branch,
    extracted verbatim (the second ``elif [[ "$MODE" == "scan" ]]`` region,
    which maps ``ABICHECK_EXIT`` to ``VERDICT``)."""
    text = RUN_SH.read_text(encoding="utf-8")
    marker = 'elif [[ "$MODE" == "scan" ]]; then\n  # scan exit codes:'
    start = text.index(marker)
    case_start = text.index(_CASE_START, start)
    case_end = text.index(_CASE_END, case_start) + len(_CASE_END)
    return text[case_start:case_end]


def _extract(start_marker: str, end_marker: str) -> str:
    """One verbatim ``name() { ... }`` function body from ``run.sh``, found
    by its exact opening line and the first matching end-marker after it."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _report_query_and_gated_fragment() -> str:
    """The real ``$OSTYPE``-detection preamble (sets ``_RUNNING_ON_WINDOWS``),
    ``_is_path_already_qualified``, ``_report_query`` (which it calls), and
    ``_evidence_contract_gated`` (which calls ``_report_query``), extracted
    verbatim -- the real pipeline that turns a JSON report file into the
    boolean ``_evidence_contract_gated`` decision, unmodified.

    Including the real preamble (rather than a caller-supplied
    ``_RUNNING_ON_WINDOWS`` value) matters on a real Windows Git-Bash host:
    `.as_posix()`-converted paths keep their drive letter (``D:/...``), which
    ``_is_path_already_qualified`` only recognises via its
    ``$_RUNNING_ON_WINDOWS == "true"`` branch. An earlier revision hardcoded
    ``_RUNNING_ON_WINDOWS="false"`` in the caller instead, which silently
    misclassified every drive-letter path as *not* already-qualified,
    prepending a bogus ``$PWD/`` prefix -- ``_report_query`` then failed to
    open the (now-wrong) path and printed nothing, same observable outcome
    as a real near-miss. That made the hostile-value test (which expects
    ``GATED=0`` either way) pass for the wrong reason on windows-latest CI,
    and was only caught once a positive-path test (`GATED=1` expected)
    exposed it as a real failure there (windows-latest CI)."""
    return (
        _extract(_IS_PATH_QUALIFIED_START, _IS_PATH_QUALIFIED_END)
        + "\n"
        + _extract(_REPORT_QUERY_START, _REPORT_QUERY_END)
        + "\n"
        + _extract(_EVIDENCE_CONTRACT_GATED_START, _EVIDENCE_CONTRACT_GATED_END)
    )


def _final_exit_scan_fragment() -> str:
    """The scan branch of the final-exit-code ``if/elif`` chain, extracted
    verbatim, stopping right before the sibling ``compare`` branch (see
    ``test_action_run_sh_scan_not_comparable.py`` for the full rationale)."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_FINAL_EXIT_SCAN_START)
    end = text.index(_FINAL_EXIT_SCAN_END, start)
    fragment = text[start:end]
    assert fragment.startswith("elif ")
    return "if " + fragment[len("elif ") :]


def _run_bash_script(
    script: str, *, timeout: float = 30
) -> subprocess.CompletedProcess:
    """Run *script* via a real bash, from a temp file rather than an inline
    ``-c`` argument.

    This module's own dispatch/gating scripts (extracted run.sh fragments
    plus this file's own harness text) run to several KB with many nested
    double quotes. Passed as a single subprocess argv string, Windows
    reconstructs that argv via ``list2cmdline`` (MSVCRT backslash/quote
    escaping rules) and Git Bash's own MSYS runtime then re-parses the
    resulting command line with its own, not-quite-identical rules -- the
    two disagree on a large, quote-heavy argument and can corrupt it,
    observed on windows-latest CI as a bash parse error ("unexpected EOF
    while looking for matching `)'") on a script that is valid bash and
    passes identically on every other platform. This is exactly the
    established fix for that class of gap in this file's own siblings
    (``test_action_run_sh_helpers.py``'s ``_run_harness``,
    ``test_action_run_sh_py_safe_path.py``'s ``_run_bash_script``): a file
    on disk needs no argv reconstruction at all, since bash reads its own
    content directly. An earlier revision of this module's own malicious-
    fixture test tried to work around a narrower instance of the same class
    (a raw Windows backslash path embedded in the inline script) with
    ``Path.as_posix()`` alone -- confirmed on windows-latest CI insufficient,
    since the corruption is triggered by the script's own pre-existing
    quote/backslash density (e.g. the real ``_is_path_already_qualified``'s
    ``\\\\*`` pattern), not by any one interpolated path."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        os.unlink(script_path)


def _run_exit_mapping(
    abicheck_exit: int,
    *,
    evidence_contract_gated: bool = False,
    is_cli_error: bool = False,
    severity_exit: str = "0",
) -> subprocess.CompletedProcess:
    # Stub every helper the extracted case-block calls -- this test is
    # scoped to the mapping itself.
    stubs = f"""
_resolve_clean_exit_verdict() {{ VERDICT="COMPATIBLE"; }}
_severity_gate_exit() {{ echo "{severity_exit}"; }}
_evidence_contract_gated() {{ return {0 if evidence_contract_gated else 1}; }}
_is_cli_error() {{ return {0 if is_cli_error else 1}; }}
_coverage_gated() {{ return 1; }}
_assurance_gated() {{ return 1; }}
_escalate_verdict_to_report() {{ :; }}
"""
    script = (
        stubs
        + f"ABICHECK_EXIT={abicheck_exit}\n"
        + 'STDERR_CONTENT=""\n'
        + _exit_case_fragment()
        + '\necho "VERDICT=$VERDICT"\n'
    )
    return _run_bash_script(script)


def test_exit_1_evidence_contract_error_maps_to_its_own_verdict():
    """The signal the CLI now provides (a JSON verdict of
    EVIDENCE_CONTRACT_ERROR) must win over the generic CLI-error bucket,
    even though both conditions are simultaneously true on real stderr
    (Click's ClickException always prints "Error: ...")."""
    result = _run_exit_mapping(1, evidence_contract_gated=True, is_cli_error=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout


def test_exit_1_plain_cli_error_still_maps_to_error():
    """A genuine bad-flag/crash abort (no evidence-contract JSON signal)
    must still classify as the generic ERROR bucket, unchanged."""
    result = _run_exit_mapping(1, evidence_contract_gated=False, is_cli_error=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=ERROR" in result.stdout


def test_exit_1_severity_error_unaffected():
    """A severity-scheme gate at exit 1 (no evidence-contract abort, no CLI
    error) must still classify as SEVERITY_ERROR, unchanged."""
    result = _run_exit_mapping(
        1, evidence_contract_gated=False, is_cli_error=False, severity_exit="2"
    )
    assert result.returncode == 0, result.stderr
    assert "VERDICT=SEVERITY_ERROR" in result.stdout


def test_evidence_contract_error_still_fails_the_step():
    """Before this fix, an evidence-contract abort read as VERDICT=ERROR,
    which the final-exit-code block's own `[[ "$VERDICT" == "ERROR" ]]`
    branch (outside the scan-specific fragment under test here) always
    failed. Splitting it into its own verdict removes that automatic path,
    so the scan-specific block must carry an explicit twin or the step
    would silently start passing on this exact abort (Codex review)."""
    script = _final_exit_scan_fragment() + 'fi\necho "FINAL_EXIT=$FINAL_EXIT"\n'
    script = (
        'MODE="scan"\n'
        'VERDICT="EVIDENCE_CONTRACT_ERROR"\n'
        'GATE_TIER=""\n'
        'ADVISORY_BREAK="false"\n'
        'INPUT_FAIL_ON_BREAKING="true"\n'
        'INPUT_FAIL_ON_API_BREAK="false"\n'
        '_severity_gate_categories() { echo ""; }\n'
        "_coverage_gated() { return 1; }\n"
        "FINAL_EXIT=0\n" + script
    )
    result = _run_bash_script(script)
    assert result.returncode == 0, result.stderr
    assert "FINAL_EXIT=1" in result.stdout


def _run_real_gated_pipeline(
    tmp_path: Path, verdict: str
) -> subprocess.CompletedProcess:
    """Execute the real, unmodified ``_is_path_already_qualified``/
    ``_report_query``/``_evidence_contract_gated`` pipeline (extracted
    verbatim from run.sh) against a JSON report whose ``verdict`` field is
    *verdict* -- prints ``GATED=1``/``GATED=0`` depending on the outcome.
    Shared by the exact-sentinel positive test and the hostile-value
    negative test below, so both exercise the identical real pipeline
    rather than two independently-drifting copies of the harness."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"verdict": verdict}), encoding="utf-8")

    py_safe_dir = tmp_path / "py-safe"
    py_safe_dir.mkdir()
    # Forward-slash form of every interpolated filesystem path -- harmless
    # either way once `_run_bash_script` writes this script to a real file
    # (a literal backslash inside a double-quoted bash string is passed
    # through unchanged), but kept for readability/consistency with the
    # rest of this file's Windows-path handling.
    py_bin = Path(sys.executable).as_posix()
    py_safe_dir_posix = py_safe_dir.as_posix()
    report_posix = report.as_posix()
    script = (
        _report_query_and_gated_fragment()
        + f"""
_PY_BIN="{py_bin}"
_PY_SAFE_DIR="{py_safe_dir_posix}"
_json_report_src() {{ echo "{report_posix}"; }}
if _evidence_contract_gated; then
  echo "GATED=1"
else
  echo "GATED=0"
fi
"""
    )
    return _run_bash_script(script)


def test_evidence_contract_gated_matches_the_real_sentinel(tmp_path):
    """The positive case the exit-1 dispatch tests above all stub away:
    every one of ``test_exit_1_evidence_contract_error_maps_to_its_own_
    verdict``/``test_exit_1_plain_cli_error_still_maps_to_error``/
    ``test_exit_1_severity_error_unaffected`` replaces
    ``_evidence_contract_gated`` with a stub, and the hostile-value test
    below only proves a near-miss does *not* gate -- none of the five
    tests in this module would fail if the real
    ``_report_query``/``_evidence_contract_gated`` pipeline were broken to
    always return false (a regression that would silently restore the
    exact pre-fix misclassification for every genuine
    ``EVIDENCE_CONTRACT_ERROR`` report), since that pipeline's *positive*
    path was never executed anywhere in this module (Codex review, fresh
    evidence). Runs the identical real pipeline as the hostile-value test,
    via the shared ``_run_real_gated_pipeline`` helper, with the exact
    sentinel string a real ``_emit_scan_abort_report`` envelope carries."""
    result = _run_real_gated_pipeline(tmp_path, "EVIDENCE_CONTRACT_ERROR")
    assert result.returncode == 0, result.stderr
    assert "GATED=1" in result.stdout


def test_evidence_contract_gated_treats_hostile_json_verdict_as_inert_data(tmp_path):
    """``_evidence_contract_gated`` reads a JSON report's ``verdict`` field
    and compares it against a fixed literal — proven here by *executing*
    the real, unmodified ``_is_path_already_qualified``/``_report_query``/
    ``_evidence_contract_gated`` pipeline against a crafted, adversarial
    report file, not by asserting the script's text (the class of gap
    #705 shipped and #758 had to close with an executing test: a
    text-only assertion proves nothing about behaviour under a hostile
    value).

    The report's ``verdict`` is data straight from a comparison report on
    every real invocation — but this test does not trust that abicheck's
    own writer only ever emits one of its fixed sentinel strings; it
    supplies a value engineered to look dangerous if the pipeline ever
    stopped being a plain string comparison: command-substitution syntax
    (`` `...` ``, ``$(...)``) and a value that merely *resembles* the real
    sentinel (extra trailing content) rather than equalling it exactly.
    Two properties are checked by execution, not by reading: (1) no shell
    command from the payload ever runs (a marker file the payload tries to
    create must not exist afterwards), and (2) a near-miss string must not
    compare equal — a substring/prefix match would be its own, different
    injection-adjacent bug (an attacker-influenced value that merely
    starts with the sentinel could otherwise forge a false positive)."""
    marker = tmp_path / "pwned"
    hostile_verdict = (
        f"EVIDENCE_CONTRACT_ERROR`touch {marker}`$(touch {marker}); touch {marker} #"
    )
    result = _run_real_gated_pipeline(tmp_path, hostile_verdict)
    assert result.returncode == 0, result.stderr
    # A near-miss (extra trailing content) must not compare equal to the
    # real sentinel -- only an exact match may gate.
    assert "GATED=0" in result.stdout
    # Nothing in the hostile value's command-substitution/backtick payload
    # ever executed as a shell command.
    assert not marker.exists(), (
        "hostile JSON verdict value executed as a shell command "
        f"(marker file {marker} was created)"
    )
