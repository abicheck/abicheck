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

"""Codex review, fresh evidence: an audit-only (no-baseline) dry run --
``old-library``/``abi-baseline`` both omitted, ``dry-run: true`` -- runs
``compare --no-baseline --dry-run``, which performs no analysis and writes
no JSON report at all (mutually exclusive with ``-o``/``--write``, see
``action/run.sh``'s own comment on the compare-mode command assembly).

``_resolve_clean_exit_verdict`` used to recognize an audit only through the
(now absent) report's ``no_baseline`` discriminator, so a dry-run preview
with no report to read fell through to the plain ``VERDICT=COMPATIBLE``
default -- "No binary ABI break detected" for a preview that never
compared, or even examined, anything. This module pins the fix: a
no-baseline dry run must publish its own ``DRY_RUN`` verdict, distinct from
both ``COMPATIBLE`` and ``AUDIT_CLEAN`` (which asserts a real audit ran and
found nothing), and must never fail the step.

Mirrors ``test_action_run_sh_compatible_with_risk.py``'s harness (stub
``abicheck`` on ``PATH``, real end-to-end ``run.sh`` execution) rather than
restating the shell logic in Python.
"""

from __future__ import annotations

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


def _stub_abicheck_dry_run(tmp_path: Path) -> Path:
    """A fake ``abicheck`` on PATH that behaves like a real ``--dry-run``:
    prints a preview to stdout, writes no report anywhere (no ``-o``/
    ``--write`` handling at all -- a dry run never receives either flag
    from ``run.sh``, so a stub that *did* write a file would test the wrong
    thing), and exits 0."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "DRY RUN: would execute compare --no-baseline ..." >&2\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    """Run ``action/run.sh`` and return its ``GITHUB_OUTPUT`` key/value pairs."""
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


class TestNoBaselineDryRunPublishesDryRunVerdict:
    def test_dedicated_dry_run_input_publishes_dry_run_not_compatible(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck_dry_run(tmp_path)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_DRY_RUN": "true",
            },
            bindir,
        )
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["exit-code"] == "0", outputs
        assert outputs["_exit"] == 0, outputs

    def test_effective_dry_run_via_extra_args_also_publishes_dry_run(
        self, tmp_path: Path
    ) -> None:
        """The same fix applies to an *effective* dry run reached only
        through ``extra-args --dry-run`` (``INPUT_DRY_RUN`` stays false),
        matching every other dry-run-detection site in run.sh."""
        bindir = _stub_abicheck_dry_run(tmp_path)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_EXTRA_ARGS": "--dry-run",
            },
            bindir,
        )
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["exit-code"] == "0", outputs
        assert outputs["_exit"] == 0, outputs

    def test_job_summary_does_not_claim_no_break_detected(self, tmp_path: Path) -> None:
        """The whole point of the fix: a preview that never ran a
        comparison must not read as a clean compatibility result."""
        bindir = _stub_abicheck_dry_run(tmp_path)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_DRY_RUN": "true",
            },
            bindir,
        )
        assert "Verdict: DRY_RUN" in outputs["_summary"], outputs
        assert "No binary ABI break detected" not in outputs["_summary"], outputs
        assert "Verdict: AUDIT_CLEAN" not in outputs["_summary"], outputs

    def test_real_no_baseline_audit_with_a_written_report_is_unaffected(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: a real (non-dry-run) audit that actually wrote
        a report must keep resolving through the existing
        ``no_baseline_audit`` report query, not this new dry-run shortcut."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        payload = tmp_path / "payload.json"
        payload.write_text(
            # `suppressed_findings` beside `findings`: the real audit emitter
            # always writes both, and the reader requires both (an absent
            # `suppressed_findings` cannot establish that policy suppressed
            # nothing -- ADR-067).
            '{"report_schema_version": "2.49", "verdict": null, '
            '"no_baseline": true, "findings": [], "suppressed_findings": []}',
            encoding="utf-8",
        )
        stub = bindir / "abicheck"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "prev=''\n"
            'for arg in "$@"; do\n'
            '  if [[ "$prev" == "-o" || "$prev" == "--write" ]]; then\n'
            f'    cp "{payload}" "${{arg#*=}}" 2>/dev/null || cp "{payload}" "$arg"\n'
            "  fi\n"
            '  prev="$arg"\n'
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "AUDIT_CLEAN", outputs
        assert outputs["exit-code"] == "0", outputs
