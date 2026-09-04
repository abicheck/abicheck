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

**2026-09-04: `--artifact-set` joined this exit code too.** Until this
date, `--artifact-set` (`service_scan._aggregate_scan_set_verdict`)
floored its own set-level exit at the generic 1 for this same axis, so a
`format: text` artifact-set Action step had no way to tell it apart from a
plain CLI error/crash -- the exit-1 dispatch below used to carry a
JSON-report `compat_verdict` check just for that case (see the removed
`test_exit_1_artifact_set_evidence_contract_error_from_json_report`/
`..._beats_cli_error_stub` in this file's own git history). Re-reading
`_aggregate_scan_set_verdict` showed that floor was never load-bearing --
`run_scan_set` rejects `severity_preset`/`exit_code_scheme` outright, so
the severity scheme's own "1 = addition/quality error" meaning never
applied to a set, and nothing else ever produced exit 1 except the
sibling `BUNDLE_INCOMPLETE` floor -- so the set now reports the identical
dedicated 7 instead, closing this gap by removing the shared exit code
rather than adding another JSON check.
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
    artifact_set: bool = False,
) -> subprocess.CompletedProcess:
    # Stub every helper the extracted case-block calls -- this test is
    # scoped to the mapping itself. No `_evidence_contract_gated` stub any
    # more: exit 7 is its own `case` arm, dispatched purely on the numeric
    # exit code, with no predicate to stub. `_json_report_src`/`_report_query`
    # were the pair exit 1's `--artifact-set` EVIDENCE_CONTRACT_ERROR check
    # used to read, before that check moved to exit 7 (2026-09-04,
    # `service_scan._aggregate_scan_set_verdict`'s own "Design decision"
    # note) -- no longer stubbed here since exit 1's dispatch no longer
    # reads them at all.
    stubs = f"""
_resolve_clean_exit_verdict() {{ VERDICT="COMPATIBLE"; }}
_severity_gate_exit() {{ echo "{severity_exit}"; }}
_is_cli_error() {{ return {0 if is_cli_error else 1}; }}
_coverage_gated() {{ return 1; }}
_assurance_gated() {{ return 1; }}
_escalate_verdict_to_report() {{ :; }}
"""
    script = (
        stubs
        + f"ABICHECK_EXIT={abicheck_exit}\n"
        + f'SCAN_ARTIFACT_SET="{"liba.so libb.so" if artifact_set else ""}"\n'
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


def test_exit_7_artifact_set_also_maps_to_evidence_contract_error_verdict():
    """`--artifact-set` shares the identical dedicated exit code 7 as of
    2026-09-04 (`service_scan._aggregate_scan_set_verdict`'s own "Design
    decision" note) -- this closes the last `--artifact-set`/`format: text`
    signal gap the cli-cleanup-phase-two plan's PR G2 section had left
    open (a `format: text` artifact-set step previously had no way to tell
    this abort apart from a genuine CLI error, since the set's own process
    exit used to floor generically at 1). No JSON report needed, same as
    the single-artifact case -- the numeric exit code alone dispatches."""
    result = _run_exit_mapping(7, artifact_set=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout
    # The artifact-set-specific message names the report's per_artifact
    # entries rather than "the command's own error message above" (there
    # is no single command-level stderr line for a --artifact-set member
    # abort the way there is for a single binary).
    assert "per_artifact entries" in result.stdout


def test_exit_7_ignores_is_cli_error_stub_for_artifact_set_too():
    """Same unconditional-dispatch guarantee as the single-artifact case,
    for `--artifact-set`."""
    result = _run_exit_mapping(7, is_cli_error=True, artifact_set=True)
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


def test_exit_1_artifact_set_no_longer_reaches_evidence_contract_error():
    """As of 2026-09-04 (`service_scan._aggregate_scan_set_verdict`'s own
    "Design decision" note), a `--artifact-set` member's evidence-contract
    abort no longer floors the *set's* own exit at the generic 1 at all --
    it uses the identical dedicated exit 7 the single-binary path always
    has (see `test_exit_7_artifact_set_also_maps_to_evidence_contract_
    error_verdict` above). This closes the entire class of forgery the
    superseded JSON-`compat_verdict`-at-exit-1 mechanism was exposed to
    (a `--artifact-set` member's report is derived from an
    attacker-influenced library/build, so trusting its `compat_verdict`
    field at a *shared* exit code was exactly the kind of un-kernel-
    verified signal this axis's single-binary history (this module's own
    docstring) already rejected three times over) by removing the shared
    exit code, not by hardening the string match further: exit 1 with
    `--artifact-set` set is now indistinguishable, by design, from a plain
    CLI error/crash at exit 1, since a real evidence-contract abort can no
    longer produce that exit code at all."""
    result = _run_exit_mapping(1, is_cli_error=True, artifact_set=True)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" not in result.stdout
    assert "VERDICT=ERROR" in result.stdout


def test_evidence_contract_error_still_fails_the_step():
    """A verdict split out of the generic ``ERROR`` bucket must carry its
    own explicit ``FINAL_EXIT=1``, or the step silently starts passing."""
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


def _real_artifact_set_scan_no_evidence(tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real ``abicheck scan --artifact-set ... --depth source`` CLI
    against two minimal, real ELF shared objects with no source evidence --
    the genuine `--artifact-set` member abort end to end (2026-09-04,
    closing the last `--artifact-set`/`format: text` signal gap)."""
    import struct

    def _write_elf_shared_object_stub(path: Path) -> None:
        data = bytearray(64)
        data[0:4] = b"\x7fELF"
        data[4] = 2  # ELFCLASS64
        data[5] = 1  # little-endian
        struct.pack_into("<H", data, 16, 3)  # e_type = ET_DYN
        struct.pack_into("<Q", data, 32, 0)  # e_phoff = 0
        struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
        path.write_bytes(bytes(data))

    p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
    _write_elf_shared_object_stub(p1)
    _write_elf_shared_object_stub(p2)

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "scan",
            "--artifact-set",
            str(p1),
            "--artifact-set",
            str(p2),
            "--depth",
            "source",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_real_artifact_set_cli_run_exits_7(tmp_path):
    """End-to-end: the real `--artifact-set` CLI's own process exit for a
    member's evidence-contract abort is exactly 7 (the dedicated code, not
    the generic 1 it used to floor at), and the JSON/text report body
    names the responsible axis."""
    proc = _real_artifact_set_scan_no_evidence(tmp_path)
    assert proc.returncode == 7, (proc.returncode, proc.stdout, proc.stderr)
    assert "EVIDENCE_CONTRACT_ERROR" in proc.stdout, proc.stdout


def test_real_artifact_set_cli_exit_code_dispatches_through_the_real_run_sh_case_block(
    tmp_path,
):
    """Feed the *real* `--artifact-set` exit code into the *real,
    unmodified* `case $ABICHECK_EXIT in ... esac` block extracted from
    `run.sh` (with `SCAN_ARTIFACT_SET` set, matching a real artifact-set
    Action step) -- proves the Python-side exit code and the bash-side
    dispatch actually agree, and that the artifact-set-specific message
    fires."""
    proc = _real_artifact_set_scan_no_evidence(tmp_path)
    assert proc.returncode == 7, (proc.returncode, proc.stdout, proc.stderr)

    stubs = """
_resolve_clean_exit_verdict() { VERDICT="COMPATIBLE"; }
_severity_gate_exit() { echo "0"; }
_is_cli_error() { return 1; }
_coverage_gated() { return 1; }
_assurance_gated() { return 1; }
_escalate_verdict_to_report() { :; }
"""
    script = (
        stubs
        + f"ABICHECK_EXIT={proc.returncode}\n"
        + 'SCAN_ARTIFACT_SET="liba.so libb.so"\n'
        + 'STDERR_CONTENT=""\n'
        + _exit_case_fragment()
        + '\necho "VERDICT=$VERDICT"\n'
    )
    result = _run_bash_script(script)
    assert result.returncode == 0, result.stderr
    assert "VERDICT=EVIDENCE_CONTRACT_ERROR" in result.stdout
    assert "per_artifact entries" in result.stdout
