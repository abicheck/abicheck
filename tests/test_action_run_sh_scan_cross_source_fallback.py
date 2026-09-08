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

"""``action/run.sh``'s ADR-068 Phase 4 item 1 migration (``mode: scan``
reimplemented internally as ``abicheck compare``) has a real correctness gap
a Codex review found on PR #1158: ``compare``'s pipeline runs the ADR-068
D4/D5 automatic cross-source-checks stage on every call (no front-end
opt-out), but ``scan --against``'s own, older mechanism for the same checks
(``cli_scan_baseline.py``'s ``_strip_automatic_cross_source_findings``) keeps
a persistent single-version finding advisory-only for a baseline comparison
unless explicitly promoted via ``--crosscheck KEY=error``. A real ``abicheck
compare`` subprocess run has no such stripping, so the exact same inputs
that ``scan --against`` reports as advisory (``COMPATIBLE_WITH_RISK``, exit
0) can score as a real API/ABI break under the migrated ``compare``
invocation (``API_BREAK``, exit 2) -- reproduced directly against
``catalog/cases/case148_xcheck_header_build_mismatch``'s snapshot used as
both operands.

The fix: ``run.sh`` inspects the migrated ``compare`` run's own JSON report
for any ``changes[].cross_source_evolution`` (present on, and only on, a
``Change`` the automatic stage itself added). When one is found, that
compare run's result is discarded entirely and the identical logical
invocation is re-run through the legacy ``abicheck scan`` CLI instead (which
already strips these correctly) -- see ``_run_abicheck_invocation``/the
``_SCAN_MIGRATED_TO_COMPARE`` block in ``run.sh``.

This module exercises the FULL script (not just the mode-branch region
``test_action_run_sh_scan_compare_migration.py`` extracts) with a stub
``abicheck`` on ``PATH`` that behaves differently for ``compare`` vs
``scan`` -- mirroring ``test_action_run_sh_compatible_with_risk.py``'s own
harness -- since the fallback only exists in the post-invocation "map exit
code to verdict" section, past where that narrower harness stops.

Per this repo's bug-fix-test-contract discipline (root ``AGENTS.md``, "A bug
fix's regression test targets the bug *class*, not the one reported
input"): the detection is a generic ``cross_source_evolution is not None``
check, not special-cased to any one of the 11 cross-source checks
(``abicheck/report/cross_source_evolution.py``,
``tests/parity/gaps.py``) or to case148's own
``header_build_context_mismatch`` -- so the parametrized class below proves
that genericity across several independently-chosen
``cross_source_evolution`` values/check kinds, not just the one reported
value, and a sibling class proves the *absence* of a finding takes the cheap
single-invocation path.
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


def _stub_abicheck(
    tmp_path: Path,
    *,
    compare_report: dict,
    compare_exit: int,
    scan_report: dict,
    scan_exit: int,
) -> tuple[Path, Path]:
    """A fake ``abicheck`` on ``PATH`` that answers differently for a
    ``compare`` vs a ``scan`` invocation -- writes the matching canned
    report to whatever ``-o`` names (or stdout, if no ``-o`` was given) and
    exits with the matching code. Also appends one line per invocation to
    a call-log file, so a test can assert exactly how many real
    invocations happened (1 for the common case, 2 for the fallback).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    compare_payload = tmp_path / "compare_payload.json"
    compare_payload.write_text(json.dumps(compare_report), encoding="utf-8")
    scan_payload = tmp_path / "scan_payload.json"
    scan_payload.write_text(json.dumps(scan_report), encoding="utf-8")
    call_log = tmp_path / "call_log.txt"
    call_log.write_text("", encoding="utf-8")
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "mode=''\n"
        "outfile=''\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ -z "$mode" && ( "$arg" == "compare" || "$arg" == "scan" ) ]]; then\n'
        '    mode="$arg"\n'
        "  fi\n"
        '  if [[ "$prev" == "-o" ]]; then\n'
        '    outfile="$arg"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        f'echo "$mode" >> "{call_log}"\n'
        'if [[ "$mode" == "compare" ]]; then\n'
        f'  payload="{compare_payload}"\n'
        f"  exitcode={compare_exit}\n"
        "else\n"
        f'  payload="{scan_payload}"\n'
        f"  exitcode={scan_exit}\n"
        "fi\n"
        'if [[ -n "$outfile" ]]; then\n'
        '  cp "$payload" "$outfile"\n'
        "else\n"
        '  cat "$payload"\n'
        "fi\n"
        'exit "$exitcode"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir, call_log


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    """Run ``action/run.sh`` end to end and return its ``GITHUB_OUTPUT``
    key/value pairs, plus stdout/stderr/exit code/step summary."""
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
    outputs: dict[str, object] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_stdout"] = proc.stdout
    outputs["_stderr"] = proc.stderr
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


def _scan_env(tmp_path: Path) -> dict[str, str]:
    """A single-artifact `scan --against` invocation with none of the
    still-scan-only capabilities set -- exactly the shape that migrates to
    a real `abicheck compare` subprocess (ADR-068 Phase 4 item 1's
    `_SCAN_USES_LEGACY_CLI` gate)."""
    return {
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "lib.so"),
        "INPUT_AGAINST": _lib(tmp_path, "baseline.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }


def _compare_report_with_finding(evolution: str, kind: str) -> dict:
    """A `compare`-shaped report carrying one automatic-stage cross-source
    finding (the stage's own D4/D5 semantics: any non-null
    `cross_source_evolution` value) that alone drives the verdict to
    API_BREAK -- exactly `compare`'s real, unmigrated behavior for
    case148's own snapshot pair."""
    return {
        "report_schema_version": "2.49",
        "verdict": "API_BREAK",
        "changes": [
            {
                "kind": kind,
                "symbol": "libfoo.so",
                "description": "a cross-source hygiene finding",
                "severity": "api_break",
                "cross_source_evolution": evolution,
            }
        ],
    }


def _scan_report_after_stripping() -> dict:
    """What `scan --against` itself really reports for the identical
    inputs, once `_strip_automatic_cross_source_findings` removes the
    advisory-only finding and recomputes the verdict -- schema 1.9+'s
    `diff`-nested shape."""
    return {
        "scan_schema_version": "1.9",
        "mode": "audit",
        "verdict": "COMPATIBLE_WITH_RISK",
        "exit_code": 0,
        "diff": {
            "verdict": "COMPATIBLE_WITH_RISK",
            "changes": [],
        },
    }


class TestCrossSourceFindingTriggersLegacyScanFallback:
    """A cross-source hygiene finding in the migrated `compare` run must not
    be published as-is -- the Action must fall back to the legacy `scan`
    CLI and publish *that* result instead, matching `scan --against`'s own
    baseline semantics."""

    @pytest.mark.parametrize(
        ("evolution", "kind"),
        [
            ("persistent", "header_build_context_mismatch"),
            ("introduced", "private_header_leak"),
            ("resolved", "exported_not_public"),
        ],
    )
    def test_final_verdict_matches_scan_not_compare(
        self, tmp_path: Path, evolution: str, kind: str
    ) -> None:
        bindir, _log = _stub_abicheck(
            tmp_path,
            compare_report=_compare_report_with_finding(evolution, kind),
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        outputs = _run_action(tmp_path, _scan_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["exit-code"] == "0", outputs
        assert outputs["_exit"] == 0, outputs

    def test_two_invocations_happen_compare_then_scan(self, tmp_path: Path) -> None:
        bindir, log = _stub_abicheck(
            tmp_path,
            compare_report=_compare_report_with_finding(
                "persistent", "header_build_context_mismatch"
            ),
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        _run_action(tmp_path, _scan_env(tmp_path), bindir)
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert calls == ["compare", "scan"], calls

    def test_notice_is_emitted_explaining_the_fallback(self, tmp_path: Path) -> None:
        bindir, _log = _stub_abicheck(
            tmp_path,
            compare_report=_compare_report_with_finding(
                "persistent", "header_build_context_mismatch"
            ),
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        outputs = _run_action(tmp_path, _scan_env(tmp_path), bindir)
        assert "cross-source" in outputs["_stdout"].lower(), outputs


class TestNoCrossSourceFindingUsesCompareResultDirectly:
    """The common case (no cross-source finding at all) must NOT re-run
    through the legacy CLI -- exactly one invocation, and the compare run's
    own result is published unchanged."""

    def test_compare_result_used_as_is(self, tmp_path: Path) -> None:
        clean_report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
        }
        bindir, _log = _stub_abicheck(
            tmp_path,
            compare_report=clean_report,
            compare_exit=0,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        outputs = _run_action(tmp_path, _scan_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_only_one_invocation_happens(self, tmp_path: Path) -> None:
        clean_report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
        }
        bindir, log = _stub_abicheck(
            tmp_path,
            compare_report=clean_report,
            compare_exit=0,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        _run_action(tmp_path, _scan_env(tmp_path), bindir)
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert calls == ["compare"], calls

    def test_real_api_break_with_no_cross_source_field_is_not_masked(
        self, tmp_path: Path
    ) -> None:
        """A genuine, non-cross-source API break (no `changes[].
        cross_source_evolution` at all) must still be published as-is --
        this fallback must never suppress a real break, only the specific
        advisory-vs-error mismatch it targets."""
        break_report = {
            "report_schema_version": "2.49",
            "verdict": "API_BREAK",
            "changes": [
                {
                    "kind": "function_removed",
                    "symbol": "foo",
                    "description": "removed",
                    "severity": "api_break",
                }
            ],
        }
        bindir, log = _stub_abicheck(
            tmp_path,
            compare_report=break_report,
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        outputs = _run_action(tmp_path, _scan_env(tmp_path), bindir)
        assert outputs["verdict"] == "API_BREAK", outputs
        assert outputs["exit-code"] == "2", outputs
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert calls == ["compare"], calls


class TestFallbackScopedToMigratedScanOnly:
    """Neither a real `mode: compare` invocation nor a `mode: scan` run that
    stayed on the legacy CLI (unmigrated capability) triggers this
    fallback -- it exists only for the specific migrated-to-compare path."""

    def test_native_compare_mode_is_unaffected_by_a_cross_source_finding(
        self, tmp_path: Path
    ) -> None:
        bindir, log = _stub_abicheck(
            tmp_path,
            compare_report=_compare_report_with_finding(
                "persistent", "header_build_context_mismatch"
            ),
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
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
        # `compare`'s own automatic cross-source stage is intentional,
        # documented D4/D5 behavior -- a real `mode: compare` step must
        # keep reporting it, never silently fall back to `scan`.
        assert outputs["verdict"] == "API_BREAK", outputs
        assert outputs["exit-code"] == "2", outputs
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert calls == ["compare"], calls

    def test_scan_that_stays_on_legacy_cli_never_double_invokes(
        self, tmp_path: Path
    ) -> None:
        """`budget` forces `_SCAN_USES_LEGACY_CLI=true` -- the single
        `scan` invocation this produces must not be mistaken for the
        fallback's second call (the fallback only exists on the
        `_SCAN_MIGRATED_TO_COMPARE` path)."""
        bindir, log = _stub_abicheck(
            tmp_path,
            compare_report=_compare_report_with_finding(
                "persistent", "header_build_context_mismatch"
            ),
            compare_exit=2,
            scan_report=_scan_report_after_stripping(),
            scan_exit=0,
        )
        env = _scan_env(tmp_path)
        env["INPUT_BUDGET"] = "15m"
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["_exit"] == 0, outputs
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert calls == ["scan"], calls
