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
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_ALIAS_START_MARKER = 'MODE="${INPUT_MODE:-compare}"'
_ALIAS_END_MARKER = 'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"'
# ADR-068 D2 / plan Phase 4 commit 1, and its 2026-09-10 amendment: `mode:
# scan` now routes unconditionally to `compare`/`compare --no-baseline` --
# there is no legacy-CLI branch left at all. The `--against`/
# `FORCE_AUDIT_ONLY` gating this file exercises lives in `_SCAN_HAS_BASELINE`'s
# own computation, evaluated once before either shape is assembled, so this
# is what the alias's effect is now tested through.
_SCAN_HAS_BASELINE_START_MARKER = "_SCAN_HAS_BASELINE=false"
_SCAN_HAS_BASELINE_END_MARKER = "\n\n# ADR-068's second 2026-09-09 amendment ruling table"


def _alias_region() -> str:
    """The mode/alias-normalization prelude, extracted verbatim from run.sh."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_ALIAS_START_MARKER)
    end = text.index(_ALIAS_END_MARKER, start) + len(_ALIAS_END_MARKER)
    return text[start:end]


def _against_region() -> str:
    """The scan-mode ``--against``/``FORCE_AUDIT_ONLY`` gating (now
    ``_SCAN_HAS_BASELINE``'s own computation), extracted verbatim from
    run.sh -- including the closing ``fi`` this time (unlike this module's
    other extraction helper), since ``_SCAN_HAS_BASELINE``'s own gating is a
    single, self-contained ``if`` block rather than a trailing statement."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_SCAN_HAS_BASELINE_START_MARKER)
    end = text.index(_SCAN_HAS_BASELINE_END_MARKER, start)
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


def _run_bash_script(
    script: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` via a real bash, from a temp file rather than an
    inline ``-c`` argument -- see ``test_action_run_sh_helpers._run_harness``
    for the full rationale (Windows argv-reconstruction/console-encoding
    mangling of a complex inline script)."""
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
            env=env,
            check=True,
        )
    finally:
        os.unlink(script_path)


class TestEstimateAliasesDryRun:
    def _run(self, env_extra: dict[str, str]) -> str:
        script = _alias_region() + '\necho "DRY_RUN=$INPUT_DRY_RUN"\n'
        env = {**os.environ, **env_extra}
        out = _run_bash_script(script, env)
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
    """`_SCAN_HAS_BASELINE` (the flag that now decides between `compare
    AGAINST ARTIFACT` and `compare --no-baseline ARTIFACT`) must read
    `false` exactly when the `audit: true` alias (`FORCE_AUDIT_ONLY`) forced
    an audit, regardless of whether `against`/`abi-baseline` resolved to a
    value elsewhere in the workflow -- the same behavior the pre-migration
    `--against` forwarding guard had."""

    def _run(self, env_extra: dict[str, str]) -> str:
        script = (
            'MODE="scan"\n'
            + _against_region()
            + '\necho "SCAN_HAS_BASELINE=$_SCAN_HAS_BASELINE"\n'
        )
        env = {**os.environ, **env_extra}
        out = _run_bash_script(script, env)
        return out.stdout

    def test_audit_true_forces_no_baseline_even_when_against_configured(
        self,
    ) -> None:
        out = self._run({"FORCE_AUDIT_ONLY": "true", "INPUT_AGAINST": "baseline.so"})
        assert "SCAN_HAS_BASELINE=false" in out

    def test_audit_false_forwards_against(self) -> None:
        out = self._run({"FORCE_AUDIT_ONLY": "false", "INPUT_AGAINST": "baseline.so"})
        assert "SCAN_HAS_BASELINE=true" in out
