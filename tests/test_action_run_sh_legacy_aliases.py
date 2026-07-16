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

"""Behavioral tests for ``action/run.sh``'s deprecated ``estimate``/``audit``
back-compat aliases (Codex review).

Removing the pre-dry-run/scan-reshape ``estimate``/``audit`` Action inputs
outright (rather than keeping them as functional aliases, mirroring the
existing ``allow-build-query`` no-op precedent) would silently break existing
workflows that still set them: GitHub Actions drops an input the action.yml
no longer declares with only a warning, so ``estimate: true`` would otherwise
silently run a real scan instead of the preview it used to produce, and
``audit: true`` would silently stop forcing a baseline-less hygiene lint once
a baseline/abi-baseline is configured elsewhere in the workflow -- a much
worse failure mode than a hard error.

These tests extract the relevant fragments verbatim from run.sh (the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_helpers.py``) rather than re-implementing the logic.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_ALIAS_START_MARKER = 'MODE="${INPUT_MODE:-compare}"'
_ALIAS_END_MARKER = 'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"'
_SCAN_MODE_MARKER = 'elif [[ "$MODE" == "scan" ]]; then'
_AGAINST_START_MARKER = 'add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"'
_AGAINST_END_MARKER = 'add_single_flag "--lang" "${INPUT_LANG:-}"'


def _alias_region() -> str:
    """The mode/alias-normalization prelude, extracted verbatim from run.sh."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_ALIAS_START_MARKER)
    end = text.index(_ALIAS_END_MARKER, start) + len(_ALIAS_END_MARKER)
    return text[start:end]


def _against_region() -> str:
    """The scan-mode ``--against``/``FORCE_AUDIT_ONLY`` gating, extracted
    verbatim from run.sh (excludes the closing marker line itself so the
    harness controls what runs after).

    ``--config`` also appears verbatim in the dump-mode branch, so the search
    for the start/end markers is anchored to begin only after the scan-mode
    branch itself starts, not the first (dump-mode) occurrence.
    """
    text = RUN_SH.read_text(encoding="utf-8")
    scan_branch = text.index(_SCAN_MODE_MARKER)
    start = text.index(_AGAINST_START_MARKER, scan_branch)
    end = text.index(_AGAINST_END_MARKER, start)
    return text[start:end]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale (GitHub windows-latest runners resolve a bare "bash" to a
    non-functional WSL stub ahead of Git for Windows' real bash).
    """
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


class TestEstimateAliasesDryRun:
    def _run(self, env_extra: dict[str, str]) -> str:
        script = _alias_region() + "\necho \"DRY_RUN=$INPUT_DRY_RUN\"\n"
        env = {**os.environ, **env_extra}
        out = subprocess.run(
            [_bash_executable(), "-c", script],
            capture_output=True, text=True, env=env, check=True,
        )
        return out.stdout

    def test_estimate_true_forces_dry_run_in_scan_mode(self) -> None:
        out = self._run({"INPUT_MODE": "scan", "INPUT_ESTIMATE": "true"})
        assert "DRY_RUN=true" in out

    def test_no_estimate_leaves_dry_run_unset(self) -> None:
        out = self._run({"INPUT_MODE": "scan"})
        assert "DRY_RUN=" in out and "DRY_RUN=true" not in out

    def test_estimate_true_ignored_outside_scan_mode(self) -> None:
        # Regression (Codex review): `estimate` was always scan-mode-only
        # (its action.yml description and the pre-dry-run run.sh only ever
        # consumed it inside the scan branch) -- a global normalization
        # would silently turn `abicheck compare ...` into a --dry-run no-op
        # for a workflow that (mistakenly or not) sets `estimate: true` on a
        # compare/dump/deps-tree/deps-compare step, exiting green without
        # running the actual ABI gate.
        out = self._run({"INPUT_MODE": "compare", "INPUT_ESTIMATE": "true"})
        assert "DRY_RUN=true" not in out

    def test_explicit_dry_run_survives_without_estimate(self) -> None:
        out = self._run({"INPUT_DRY_RUN": "true"})
        assert "DRY_RUN=true" in out


class TestAuditAliasSkipsAgainst:
    def _run(self, env_extra: dict[str, str]) -> list[str]:
        # add_single_flag is defined earlier in run.sh (line ~60); redefine a
        # minimal equivalent here since we only extract the alias region, not
        # the whole file, to keep the harness self-contained and fast.
        harness = (
            'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
            "CMD=()\n"
        )
        script = harness + _against_region() + '\nprintf \'%s\\n\' "${CMD[@]}"\n'
        env = {**os.environ, **env_extra}
        out = subprocess.run(
            [_bash_executable(), "-c", script],
            capture_output=True, text=True, env=env, check=True,
        )
        return out.stdout.splitlines()

    def test_audit_true_skips_against_even_when_configured(self) -> None:
        cmd = self._run({"FORCE_AUDIT_ONLY": "true", "INPUT_AGAINST": "baseline.so"})
        assert "--against" not in cmd

    def test_audit_false_forwards_against(self) -> None:
        cmd = self._run({"FORCE_AUDIT_ONLY": "false", "INPUT_AGAINST": "baseline.so"})
        assert "--against" in cmd
        assert "baseline.so" in cmd
