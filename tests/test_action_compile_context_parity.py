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
# dump/scan have no release fan-out, so their regions end at the nostdinc
# if-block; anchor past its closing "fi" so the extracted fragment is
# syntactically complete.
_COMPILE_CONTEXT_END = (
    'if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then\n    CMD+=(--nostdinc)\n  fi'
)

# compare's region is structurally different (Codex review: these flags are
# gated to the single-pair path there, since the release fan-out rejects
# them outright) — it starts at the gating comment, not at the first
# add_single_flag, and its nostdinc if-block is nested one level deeper
# inside the release-style/single-pair if/else, ending at the *outer* "fi".
_COMPARE_COMPILE_CONTEXT_START = (
    "# The L2 compile-context flags (--ast-frontend/--gcc-*/--sysroot/"
)
_COMPARE_COMPILE_CONTEXT_END = (
    'if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then\n'
    "      CMD+=(--nostdinc)\n"
    "    fi\n"
    "  fi"
)

# _is_release_style_operand is defined once, well before any mode branch;
# compare's extracted region calls it, so the harness needs its real
# definition rather than a hand-copied stub (same "parse the real file"
# discipline as the rest of this module).
_IS_RELEASE_STYLE_OPERAND_START = "_is_release_style_operand() {"
_IS_RELEASE_STYLE_OPERAND_END = "\n}\n"


def _is_release_style_operand_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_IS_RELEASE_STYLE_OPERAND_START)
    end = text.index(_IS_RELEASE_STYLE_OPERAND_END, start) + len(
        _IS_RELEASE_STYLE_OPERAND_END
    )
    return text[start:end]


def _compile_context_region(
    mode_marker: str, start_marker: str = _COMPILE_CONTEXT_START
) -> str:
    """Extract one mode's compile-context flag-forwarding block verbatim."""
    text = RUN_SH.read_text(encoding="utf-8")
    mode_start = text.index(mode_marker)
    start = text.index(start_marker, mode_start)
    end_marker = (
        _COMPARE_COMPILE_CONTEXT_END
        if start_marker == _COMPARE_COMPILE_CONTEXT_START
        else _COMPILE_CONTEXT_END
    )
    end = text.index(end_marker, start) + len(end_marker)
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


def _run_region(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str = _COMPILE_CONTEXT_START,
) -> tuple[list[str], str]:
    # add_single_flag is defined earlier in run.sh; redefine a minimal
    # equivalent here since only the compile-context region is extracted,
    # not the whole file (keeps the harness self-contained and fast).
    # _is_release_style_operand is extracted from the real file (only
    # compare's region calls it, but defining it unconditionally is
    # harmless for dump/scan).
    harness = (
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        + _is_release_style_operand_source()
        + "\nCMD=()\n"
    )
    script = (
        harness
        + _compile_context_region(mode_marker, start_marker)
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
    return out.stdout.splitlines(), out.stderr


def _run_region_raw(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str = _COMPILE_CONTEXT_START,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`_run_region`, but ``check=False`` and returns the raw
    result -- for a region that's now expected to ``exit 1``, where
    ``check=True`` would raise before the caller could inspect anything."""
    harness = (
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        + _is_release_style_operand_source()
        + "\nCMD=()\n"
    )
    script = (
        harness
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestCompileContextForwardingParity:
    """dump/compare/scan must forward the identical flag set."""

    def test_dump_forwards_all_six_flags(self) -> None:
        cmd, _ = _run_region(_DUMP_MODE_MARKER, _FULL_ENV)
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
        the whole time. A single-pair (non-directory/package) old/new-library
        is required here since compare's forwarding is now gated to that
        path (see the release-style tests below)."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": "old.so",
            "INPUT_NEW_LIBRARY": "new.so",
        }
        cmd, _ = _run_region(_COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START)
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
        cmd, _ = _run_region(_SCAN_MODE_MARKER, _FULL_ENV)
        assert "--ast-frontend" in cmd and "clang" in cmd
        assert "--gcc-path" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--gcc-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--gcc-options" in cmd and "-DFOO=1" in cmd
        assert "--sysroot" in cmd and "/opt/sysroot" in cmd
        assert "--nostdinc" in cmd

    def test_compare_omits_unset_flags(self) -> None:
        cmd, _ = _run_region(
            _COMPARE_MODE_MARKER,
            {"INPUT_OLD_LIBRARY": "old.so", "INPUT_NEW_LIBRARY": "new.so"},
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "--gcc-path" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd

    def test_scan_omits_unset_flags(self) -> None:
        cmd, _ = _run_region(_SCAN_MODE_MARKER, {})
        assert "--gcc-path" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd

    def test_compare_fails_loud_for_compile_context_against_release_style_operand(
        self,
    ) -> None:
        """Regression (Codex review): the CLI hard-rejects these flags for
        directory/package operands (a UsageError, exit 64) since the
        per-library release fan-out never threads a CompileContext to each
        pair's header dump. A prior fix gated them to the single-pair path
        but only warned and continued for a directory operand — silently
        running the comparison with headers parsed under the wrong
        macros/sysroot/frontend instead of the intended cross-compile
        context. Must fail loud instead (a second Codex round), matching
        the evidence-flags guard's already-established treatment of the
        same "explicitly-configured input the fan-out can't honor" shape."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": str(RUN_SH.parent),  # any real directory
            "INPUT_NEW_LIBRARY": "new.so",
        }
        result = _run_region_raw(
            _COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START
        )
        assert result.returncode != 0
        assert "not support" in result.stdout

    def test_compare_release_style_succeeds_when_context_unset(self) -> None:
        """Companion: a plain directory/package compare with no compile-
        context inputs configured must still succeed (only fails when a
        flag was actually configured and would be dropped)."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr

    def test_compare_release_style_succeeds_with_ast_frontend_auto(self) -> None:
        """Regression (Codex review, second round): "auto" is the
        documented no-op spelling of ast-frontend -- it resolves to the
        same default castxml selection as leaving the input unset entirely
        (see the input's description in action.yml), so a workflow that
        spells it out explicitly requests nothing the release fan-out
        could actually drop. Must not trip the fail-loud guard, unlike a
        real frontend choice such as "clang"."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert "--ast-frontend" not in cmd

    def test_compare_release_style_fails_with_ast_frontend_clang(self) -> None:
        """Companion: an actual, non-"auto" frontend choice still trips
        the guard -- only the documented no-op spelling is exempt."""
        result = _run_region_raw(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "clang",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert result.returncode != 0
        assert "not support" in result.stdout
