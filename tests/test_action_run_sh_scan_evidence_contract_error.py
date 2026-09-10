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

"""Behavioral tests for ``action/run.sh``'s ``scan`` VERDICT mapping for
``_EvidenceContractError`` (ADR-037 D5, scan_engine.py) — extracted
verbatim, same discipline as the sibling ``test_action_run_sh_*.py`` files
(``test_action_run_sh_scan_not_comparable.py`` is the closest analog).

CLI cleanup phase two / ADR-064's own "still open" item: a full
cross-front-end parity pass between the native CLI and this composite
Action. This axis's classification signal has been through four
iterations, the first three each shown forgeable in turn:

1. Originally: `_EvidenceContractError` raised a generic
   `click.ClickException` (exit 1, stderr `Error: <message>`) -- the
   identical shape a bad flag or a crash produces, so `_is_cli_error`'s own
   `^Error:` match won the exit-1 disambiguation unconditionally, folding a
   well-formed command that merely lacked evidence for its own pinned
   `--depth` into the same generic "CLI error" bucket a syntax typo gets.
2. 2026-09-03: a stable stderr marker line, matched by
   `_evidence_contract_gated()` via `grep -q` -- an unanchored substring
   match a crafted diagnostic could spoof.
3. 2026-09-03, second round (Codex): the marker match tightened to
   whole-line (`grep -Fxq`), still forgeable since a legal Unix filename
   may itself contain embedded newlines.
4. 2026-09-03, third round (Codex): the marker moved to a private
   temp-file path passed as an environment variable
   (`$ABICHECK_EVIDENCE_CONTRACT_MARKER_FILE`) -- still forgeable, since a
   PR-controlled build script spawned during this scan's own evidence
   collection inherits (or, even after the variable is popped from
   `os.environ`, can recover from `/proc/<pid>/environ`) the same
   information.

**Current design (2026-09-03, fourth round): a dedicated process exit
code.** `cli_scan.py`'s `_EXIT_EVIDENCE_CONTRACT_ERROR = 7` is this
process's own choice, made once at its own `sys.exit()` call, and reported
to its trusted parent shell by the OS kernel via `wait()` -- no subprocess
this run spawns can alter its own ancestor's eventual exit status. The
`case $ABICHECK_EXIT in ... 7) ...` dispatch below needs no helper
predicate, no JSON report, and no stderr/environment signal at all: the
numeric exit code alone is unambiguous and un-spoofable. The final-exit-
code block is exercised too, mirroring
``test_action_run_sh_scan_not_comparable.py``'s own rationale: a verdict
newly split out of the generic ``ERROR`` bucket must carry its own
explicit ``FINAL_EXIT=1``, or the step silently starts passing.

**2026-09-09: `--artifact-set` is retired** (ADR-068's second amendment,
ruling (b)), so this axis has one shape again. The set-level cases that
lived here -- and the exit-1 JSON-report ``compat_verdict`` check the set's
own pre-2026-09-04 generic-1 floor had needed -- went with the mode; see
this file's own git history for both.

**2026-09-10: the legacy `scan` CLI's own dedicated exit-code dispatch is
gone.** `mode: scan` now routes unconditionally through
`compare`/`compare --no-baseline` (ADR-068's second 2026-09-09 amendment
and its 2026-09-10 amendment), so there is no longer a separate
`elif [[ "$_CLI_MODE" == "scan" ]]` dispatch in ``run.sh`` -- the shared
`compare` exit-code `case` block gained its own `7) VERDICT=
"EVIDENCE_CONTRACT_ERROR"` arm instead (the axis is real on this path too:
`report/no_baseline.py`'s own `evidence_contract` axis, and the two-sided
`compare --depth build/source` floor `EVIDENCE_CONTRACT_ERROR`'s own
docstring already documented as live-verified). This module's extraction
markers were updated to the shared block; the assertions themselves are
unchanged, since the mapping's *behavior* did not change -- only which
`case` block in ``run.sh`` now hosts it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_CASE_START = "    case $ABICHECK_EXIT in\n"
_CASE_END = "    esac\n"
_FINAL_EXIT_SCAN_START = 'elif [[ "$MODE" == "scan" ]]; then\n  # Keyed on the raw `$MODE` input'
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
    """The ``case $ABICHECK_EXIT in ... esac`` block from the shared
    `compare` exit-code dispatch, extracted verbatim -- since ADR-068's
    2026-09-10 amendment, `mode: scan` has no dedicated exit-code dispatch
    of its own any more: every `mode: scan` request (audit-only or
    baseline) is served by `compare`'s own shared block, including this
    module's exit-7 arm."""
    text = RUN_SH.read_text(encoding="utf-8")
    marker = "else\n  # compare exit codes:"
    start = text.index(marker)
    case_start = text.index(_CASE_START, start)
    case_end = text.index(_CASE_END, case_start) + len(_CASE_END)
    return text[case_start:case_end]


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

    This module's own dispatch scripts (extracted run.sh fragments plus
    this file's own harness text) run to several KB with many nested
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
    content directly."""
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
    is_cli_error: bool = False,
    severity_exit: str = "0",
) -> subprocess.CompletedProcess:
    # Stub every helper the extracted case-block calls -- this test is
    # scoped to the mapping itself. No `_evidence_contract_gated` stub any
    # more: exit 7 is its own `case` arm, dispatched purely on the numeric
    # exit code, with no predicate to stub. `_json_report_src`/`_report_query`
    # were the pair exit 1's `--artifact-set` EVIDENCE_CONTRACT_ERROR check
    # used to read, before that check moved to exit 7 (2026-09-04,
    # `service_scan._aggregate_scan_set_verdict`'s own "Design decision"
    # note) -- no longer stubbed here since exit 1's dispatch no longer
    # reads them at all. `_scope_gated` is new here since ADR-068's
    # 2026-09-10 amendment folded `mode: scan` onto `compare`'s own shared
    # exit-1 disambiguation, which checks a third axis (ADR-065 S2's
    # completeness) the old scan-only dispatch never had.
    stubs = f"""
_resolve_clean_exit_verdict() {{ VERDICT="COMPATIBLE"; }}
_severity_gate_exit() {{ echo "{severity_exit}"; }}
_is_cli_error() {{ return {0 if is_cli_error else 1}; }}
_coverage_gated() {{ return 1; }}
_assurance_gated() {{ return 1; }}
_scope_gated() {{ return 1; }}
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


def test_exit_7_maps_to_evidence_contract_error_verdict():
    """Exit code 7 (`cli_scan.py`'s `_EXIT_EVIDENCE_CONTRACT_ERROR`) maps
    unconditionally to `VERDICT=EVIDENCE_CONTRACT_ERROR` -- no JSON report,
    no stderr content, no environment variable needed, since the dispatch
    is a plain `case` match on the exit code alone."""
    result = _run_exit_mapping(7)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout


def test_exit_7_ignores_stderr_and_is_cli_error_stub():
    """Even when `_is_cli_error` would (wrongly) return true for exit 7,
    the `case` arm for 7 is reached unconditionally -- `_is_cli_error` is
    never even consulted for this exit code, unlike exit 1's shared
    bucket."""
    result = _run_exit_mapping(7, is_cli_error=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout


def test_exit_1_plain_cli_error_still_maps_to_error():
    """A genuine bad-flag/crash abort at exit 1 must still classify as the
    generic ERROR bucket, unaffected by evidence-contract-error moving to
    its own exit code."""
    result = _run_exit_mapping(1, is_cli_error=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=ERROR" in result.stdout


def test_exit_1_severity_error_unaffected():
    """A severity-scheme gate at exit 1 (no CLI error) must still classify
    as SEVERITY_ERROR, unaffected by evidence-contract-error moving off
    exit 1 entirely. `severity_exit="1"` is `compute_exit_code`'s own value
    for an error-level `addition`/`quality_issues` finding -- `"2"` is
    `potential_breaking`'s code, a different category this docstring does
    not claim to model (CodeRabbit review, fresh evidence: the mismatch
    meant this test never actually validated the addition/quality-error
    exit-decision contract it describes)."""
    result = _run_exit_mapping(1, is_cli_error=False, severity_exit="1")
    assert result.returncode == 0, result.stderr
    assert "VERDICT=SEVERITY_ERROR" in result.stdout


def test_evidence_contract_error_still_fails_the_step():
    """A verdict split out of the generic ``ERROR`` bucket must carry its
    own explicit ``FINAL_EXIT=1``, or the step silently starts passing."""
    script = _final_exit_scan_fragment() + 'fi\necho "FINAL_EXIT=$FINAL_EXIT"\n'
    script = (
        'MODE="scan"\n'
        '_CLI_MODE="scan"\n'
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


def _real_scan_no_evidence(tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real ``abicheck scan --depth source`` CLI against a real
    snapshot with no source evidence -- the genuine
    ``_EvidenceContractError`` (pinned-depth raise site) end to end."""
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot, AccessLevel, Function, Visibility
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
                access=AccessLevel.PUBLIC,
            )
        ],
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
    )
    snap_path = tmp_path / "new.abi.json"
    snap_path.write_text(snapshot_to_json(snap), encoding="utf-8")

    return subprocess.run(
        [sys.executable, "-m", "abicheck", "scan", str(snap_path), "--depth", "source"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_real_cli_run_exits_7_with_an_error_prefixed_message(tmp_path):
    """End-to-end: the real CLI's own exit code for this abort is exactly
    7 (not 1, and not any other value), and stderr keeps the human-facing
    ``Error: <message>`` shape ``click.ClickException`` used to produce, so
    existing log-reading tooling/eyeballs see the same prefix as before."""
    proc = _real_scan_no_evidence(tmp_path)
    assert proc.returncode == 7, (proc.returncode, proc.stderr)
    assert proc.stderr.startswith("Error: "), proc.stderr
    assert "source evidence" in proc.stderr, proc.stderr


def test_real_cli_exit_code_dispatches_through_the_real_run_sh_case_block(tmp_path):
    """Feed the *real* exit code the CLI just produced into the *real,
    unmodified* ``case $ABICHECK_EXIT in ... esac`` block extracted from
    ``run.sh`` -- proves the Python-side exit code and the bash-side
    dispatch actually agree on the number 7, rather than two
    independently-drifting halves of the same contract."""
    proc = _real_scan_no_evidence(tmp_path)
    assert proc.returncode == 7, (proc.returncode, proc.stderr)

    stubs = """
_resolve_clean_exit_verdict() { VERDICT="COMPATIBLE"; }
_severity_gate_exit() { echo "0"; }
_is_cli_error() { return 1; }
_coverage_gated() { return 1; }
_assurance_gated() { return 1; }
_scope_gated() { return 1; }
_escalate_verdict_to_report() { :; }
"""
    script = (
        stubs
        + f"ABICHECK_EXIT={proc.returncode}\n"
        + 'STDERR_CONTENT=""\n'
        + _exit_case_fragment()
        + '\necho "VERDICT=$VERDICT"\n'
    )
    result = _run_bash_script(script)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout
