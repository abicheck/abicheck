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

"""Behavioral tests for ``action/run.sh``'s ``--dry-run``/``abi-baseline``
interaction (Codex review).

``dry-run`` is documented in ``action.yml`` as "always exits 0", but the
baseline auto-fetch block used to run (and ``exit 1`` on a missing
release/token/asset) before any mode branch ever consulted
``INPUT_DRY_RUN`` -- so a workflow previewing its config with `dry-run: true`
plus an `abi-baseline` that hadn't been published yet got a hard failure
instead of the promised no-op preview.

These tests extract the relevant fragment verbatim from run.sh (the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_legacy_aliases.py``) rather than re-implementing the
logic, and stub out ``gh`` (not available/authenticated in the test
environment) with a shell function.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "_baseline_unavailable() {"
_END_MARKER = 'if [[ "$MODE" == "dump" ]]; then'


def _baseline_region() -> str:
    """The baseline auto-fetch block, extracted verbatim from run.sh."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start)
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


_FAILING_GH_STUB = 'gh() { return 1; }\n'


class TestDryRunToleratesUnavailableBaseline:
    def _run(
        self, env_extra: dict[str, str], *, gh_stub: str = _FAILING_GH_STUB,
    ) -> subprocess.CompletedProcess[str]:
        # MODE is set earlier in run.sh (outside the extracted region); the
        # baseline block reads it, so the harness must set it too.
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            + gh_stub
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-} '
            'AGAINST=${INPUT_AGAINST:-}"\n'
        )
        env = {**os.environ, **env_extra}
        return subprocess.run(
            [_bash_executable(), "-c", script],
            capture_output=True, text=True, env=env, check=False,
        )

    def test_non_dry_run_still_fails_hard_on_unavailable_baseline(self) -> None:
        """Baseline gate for real invocations is unchanged: still exit 1."""
        result = self._run(
            {"INPUT_MODE": "compare", "INPUT_ABI_BASELINE": "latest-release"}
        )
        assert result.returncode == 1
        assert "REACHED_END" not in result.stdout

    def test_dry_run_exits_0_instead_of_failing_on_unavailable_baseline(self) -> None:
        """Regression: --dry-run must never hard-fail on a missing baseline
        (action.yml documents dry-run as "always exits 0")."""
        result = self._run({
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "INPUT_DRY_RUN": "true",
        })
        assert result.returncode == 0, result.stderr
        assert "::warning::" in result.stdout
        # Nothing else to preview (no other old-library given) -- the block
        # reports and exits before reaching the harness's trailing echo.
        assert "REACHED_END" not in result.stdout

    def test_dry_run_with_explicit_old_library_still_proceeds(self) -> None:
        """An explicitly-given old-library must not be discarded just because
        the (redundant) baseline fetch also failed under --dry-run."""
        result = self._run({
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "INPUT_DRY_RUN": "true",
            "INPUT_OLD_LIBRARY": "libfoo.so.1",
        })
        assert result.returncode == 0, result.stderr
        assert "REACHED_END OLD_LIBRARY=libfoo.so.1" in result.stdout

    def test_scan_mode_dry_run_exits_0_instead_of_failing(self) -> None:
        result = self._run(
            {"INPUT_MODE": "scan", "INPUT_ABI_BASELINE": "latest-release",
             "INPUT_DRY_RUN": "true"}
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END" not in result.stdout

    def test_direct_file_path_baseline_unaffected(self, tmp_path: Path) -> None:
        """A direct existing-file abi-baseline never calls gh at all — must
        keep working exactly as before, dry-run or not."""
        baseline = tmp_path / "abi-baseline.json"
        baseline.write_text("{}")
        result = self._run(
            {"INPUT_MODE": "compare", "INPUT_ABI_BASELINE": str(baseline)},
            gh_stub="",
        )
        assert result.returncode == 0, result.stderr
        assert f"REACHED_END OLD_LIBRARY={baseline}" in result.stdout
