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

"""``dump``/``compare``/``scan`` L2 compile-context forwarding parity in
``action/run.sh`` (AGENTS.md P0 "fix Action compile-context forwarding
parity").

The three CLI subcommands all share ``compile_context_options``
(``--ast-frontend``/``--gcc-path``/``--gcc-prefix``/``--gcc-options``/
``--sysroot``/``--nostdinc``, ADR-037 D3) — but ``action/run.sh`` used to
forward all six only in ``dump`` mode, only ``--ast-frontend`` in ``compare``
mode (behind a comment incorrectly claiming the rest were "dump-only flags...
not exposed on the compare CLI"), and none of them in ``scan`` mode. These
tests extract each mode's compile-context region verbatim from run.sh (the
same "parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_legacy_aliases.py``) and assert parity.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_DUMP_MODE_MARKER = 'if [[ "$MODE" == "dump" ]]; then'
_COMPARE_MODE_MARKER = 'elif [[ "$MODE" == "compare" ]]; then'
_SCAN_MODE_MARKER = 'elif [[ "$MODE" == "scan" ]]; then'

_COMPILE_CONTEXT_START = 'add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"'
# All three modes' regions end at the nostdinc if-block; anchor past its
# closing "fi" so the extracted fragment is syntactically complete.
_COMPILE_CONTEXT_END = (
    'if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then\n    CMD+=(--nostdinc)\n  fi'
)


def _compile_context_region(mode_marker: str) -> str:
    """Extract one mode's compile-context flag-forwarding block verbatim."""
    text = RUN_SH.read_text(encoding="utf-8")
    mode_start = text.index(mode_marker)
    start = text.index(_COMPILE_CONTEXT_START, mode_start)
    end = text.index(_COMPILE_CONTEXT_END, start) + len(_COMPILE_CONTEXT_END)
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


_FULL_ENV = {
    "INPUT_AST_FRONTEND": "clang",
    "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
    "INPUT_GCC_PREFIX": "aarch64-linux-gnu-",
    "INPUT_GCC_OPTIONS": "-DFOO=1",
    "INPUT_SYSROOT": "/opt/sysroot",
    "INPUT_NOSTDINC": "true",
}


def _run_region(mode_marker: str, env_extra: dict[str, str]) -> list[str]:
    # add_single_flag is defined earlier in run.sh; redefine a minimal
    # equivalent here since only the compile-context region is extracted,
    # not the whole file (keeps the harness self-contained and fast).
    harness = 'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\nCMD=()\n'
    script = (
        harness
        + _compile_context_region(mode_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    out = subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return out.stdout.splitlines()


class TestCompileContextForwardingParity:
    """dump/compare/scan must forward the identical flag set."""

    def test_dump_forwards_all_six_flags(self) -> None:
        cmd = _run_region(_DUMP_MODE_MARKER, _FULL_ENV)
        assert "--ast-frontend" in cmd and "clang" in cmd
        assert "--gcc-path" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--gcc-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--gcc-options" in cmd and "-DFOO=1" in cmd
        assert "--sysroot" in cmd and "/opt/sysroot" in cmd
        assert "--nostdinc" in cmd

    def test_compare_forwards_all_six_flags(self) -> None:
        """Regression: compare used to forward only --ast-frontend, behind a
        comment incorrectly claiming the rest are dump-only — the CLI's
        `compare` command has shared `compile_context_options` (ADR-037 D3)
        the whole time."""
        cmd = _run_region(_COMPARE_MODE_MARKER, _FULL_ENV)
        assert "--ast-frontend" in cmd and "clang" in cmd
        assert "--gcc-path" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--gcc-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--gcc-options" in cmd and "-DFOO=1" in cmd
        assert "--sysroot" in cmd and "/opt/sysroot" in cmd
        assert "--nostdinc" in cmd

    def test_scan_forwards_all_six_flags(self) -> None:
        """Regression: scan forwarded none of these, even though
        `cli_scan.py` shares the identical `compile_context_options`
        decorator with dump (ADR-037 D3 / ADR-035 amendment)."""
        cmd = _run_region(_SCAN_MODE_MARKER, _FULL_ENV)
        assert "--ast-frontend" in cmd and "clang" in cmd
        assert "--gcc-path" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--gcc-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--gcc-options" in cmd and "-DFOO=1" in cmd
        assert "--sysroot" in cmd and "/opt/sysroot" in cmd
        assert "--nostdinc" in cmd

    def test_compare_omits_unset_flags(self) -> None:
        cmd = _run_region(_COMPARE_MODE_MARKER, {})
        assert "--gcc-path" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd

    def test_scan_omits_unset_flags(self) -> None:
        cmd = _run_region(_SCAN_MODE_MARKER, {})
        assert "--gcc-path" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
