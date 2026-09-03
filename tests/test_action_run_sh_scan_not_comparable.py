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

"""Behavioral tests for ``action/run.sh``'s ``scan`` exit-code-to-VERDICT
mapping at exit 6 (``NOT_COMPARABLE``, ADR-050 D2) — extracted verbatim
(same discipline as the sibling ``test_action_run_sh_*.py`` files) rather
than hand-duplicated.

Before this fix, exit 6 fell through to the generic ``*) VERDICT="ERROR"``
case, and ``_maybe_post_pr_comment``'s own ``[[ "$VERDICT" == "ERROR" ]]``
guard then skipped posting entirely — so a real, JSON-report-carrying
``NOT_COMPARABLE`` scan result silently produced no sticky PR comment, even
though ``pr_comment_scan.py`` renders it as a blocking "analysis incomplete"
finding (see ``test_pr_comment_scan.py``'s matching JSON-layer test). The
final-exit-code block is exercised too, so the fix doesn't accidentally
turn a scope/profile mismatch into a passing step.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_CASE_START = "    case $ABICHECK_EXIT in\n"
_CASE_END = "    esac\n"
_FINAL_EXIT_START = "if [[ \"$VERDICT\" == \"ERROR\" ]]; then\n"
_FINAL_EXIT_SCAN_START = (
    'elif [[ "$MODE" == "scan" ]]; then\n'
    "  # scan: BREAKING/API_BREAK follow the fail-on flags"
)
_FINAL_EXIT_SCAN_END = "\nelse\n"


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


def _final_exit_scan_fragment() -> str:
    """The scan branch of the final-exit-code ``if/elif`` chain, extracted
    verbatim, stopping right before the sibling ``compare`` branch. The
    extracted text starts mid-chain (``elif ... then``); the leading
    ``elif`` is swapped for ``if`` so it stands alone as a complete,
    balanced ``if ... fi`` (the caller appends the closing ``fi``) --
    everything between ``then`` and the swapped keyword is otherwise
    byte-for-byte what run.sh has.
    """
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

    This module's own dispatch scripts (the extracted run.sh ``case ...
    esac`` fragment plus this file's own harness text) run to several KB
    with many nested double quotes. Passed as a single subprocess argv
    string, Windows reconstructs that argv via ``list2cmdline`` (MSVCRT
    backslash/quote escaping rules) and Git Bash's own MSYS runtime then
    re-parses the resulting command line with its own, not-quite-identical
    rules -- the two disagree on a large, quote-heavy argument and can
    corrupt it, observed on windows-latest CI as a bash parse error
    ("unexpected end of file from `case' command") once this PR's edits to
    run.sh's scan exit-code dispatch (adding the exit-7 arm, restructuring
    the exit-1 arm) grew the extracted fragment past whatever threshold the
    prior, shorter version stayed under -- a script that is valid bash and
    passes identically on every other platform. This is exactly the
    established fix for that class of gap in this file's own siblings
    (``test_action_run_sh_helpers.py``'s ``_run_harness``,
    ``test_action_run_sh_scan_evidence_contract_error.py``'s own identical
    ``_run_bash_script``): a file on disk needs no argv reconstruction at
    all, since bash reads its own content directly."""
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


def _run_exit_mapping(abicheck_exit: int) -> subprocess.CompletedProcess:
    # Stub every helper the extracted case-block calls -- this test is
    # scoped to the mapping itself (see the fuller sticky-comment/CI
    # coverage in test_action_run_sh_scan_pr_comment.py and
    # test_pr_comment_scan.py for the two ends this mapping connects).
    stubs = """
_resolve_clean_exit_verdict() { VERDICT="COMPATIBLE"; }
_severity_gate_exit() { echo "0"; }
_is_cli_error() { return 1; }
_coverage_gated() { return 1; }
_escalate_verdict_to_report() { :; }
"""
    script = (
        stubs
        + f"ABICHECK_EXIT={abicheck_exit}\n"
        + "STDERR_CONTENT=\"\"\n"
        + _exit_case_fragment()
        + '\necho "VERDICT=$VERDICT"\n'
    )
    return _run_bash_script(script)


def test_exit_6_maps_to_not_comparable_not_error():
    result = _run_exit_mapping(6)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=NOT_COMPARABLE" in result.stdout


def test_exit_4_still_maps_to_breaking():
    result = _run_exit_mapping(4)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=BREAKING" in result.stdout


def test_exit_99_unknown_still_maps_to_error():
    result = _run_exit_mapping(99)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=ERROR" in result.stdout


def test_not_comparable_still_fails_the_step():
    script = _final_exit_scan_fragment() + "fi\necho \"FINAL_EXIT=$FINAL_EXIT\"\n"
    script = (
        'MODE="scan"\n'
        'VERDICT="NOT_COMPARABLE"\n'
        'GATE_TIER=""\n'
        'ADVISORY_BREAK="false"\n'
        'INPUT_FAIL_ON_BREAKING="true"\n'
        'INPUT_FAIL_ON_API_BREAK="false"\n'
        "_severity_gate_categories() { echo \"\"; }\n"
        "_coverage_gated() { return 1; }\n"
        "FINAL_EXIT=0\n"
        + script
    )
    result = _run_bash_script(script)
    assert result.returncode == 0, result.stderr
    assert "FINAL_EXIT=1" in result.stdout
