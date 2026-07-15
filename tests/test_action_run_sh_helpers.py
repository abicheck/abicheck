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

"""Behavioral tests for ``action/run.sh``'s multi-value input splitting (P2.2).

``run.sh`` runs the actual ``abicheck`` invocation at the bottom of the file
(reading ``INPUT_*`` env vars and exiting with the tool's exit code), so it
cannot be sourced wholesale in a unit test. Instead this extracts just the
helper-function region (``_split_multi_value``/``add_flag``/``add_sided_flag``/
``add_single_flag``, everything before the "Build the abicheck command"
marker) and sources *that* alongside a small harness — the same "parse the
real file, don't hand-copy it" discipline as ``test_action_run_contract.py``,
so a future edit to the real functions is exercised here too, not a stale copy.

``add_flag``/``add_sided_flag`` used unquoted ``for item in $value`` word-
splitting, which explicitly could not support a path containing a space (a
Codex/report finding, P2.2). The fix prefers newline-separated items (a YAML
block-scalar Action input, e.g. ``headers: |``), which preserves embedded
spaces, and falls back to legacy whitespace-splitting only for a single-line
value (the documented back-compat form).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_MARKER = "# Build the abicheck command"


def _helpers_region() -> str:
    """The function-definitions header of run.sh, up to the assembly marker."""
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_MARKER)
    return text[:idx]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    On GitHub's windows-latest runners, ``%SystemRoot%\\System32\\bash.exe``
    is the WSL launcher stub — present even with no distro installed — and a
    bare ``["bash", ...]`` subprocess call can resolve to it ahead of Git for
    Windows' real bash depending on the calling process's inherited PATH
    order. The stub exits immediately (non-zero) without running anything,
    which looks identical to every helper-function test failing at once with
    no bash-level diagnostic (Codex/CI investigation, PR #551). Prefer Git for
    Windows' own bash explicitly on that platform; every other platform keeps
    using whatever "bash" already resolves to on PATH.
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


def _run_harness(harness: str) -> str:
    """Source the real helper functions + *harness*, return CMD joined by '\\x1f'.

    Writes the assembled script to a real file (UTF-8, explicit ``\\n`` line
    endings) and runs ``bash <path>`` rather than ``bash -c <string>``: passing
    a script containing non-ASCII characters (run.sh's comments use em-dashes)
    as a subprocess argv string hits Windows console/argv-encoding mangling
    and was flaky under macOS's stock bash 3.2 (exit 127) — a file sidesteps
    both, and matches how run.sh is actually invoked in production.
    """
    script = (
        _helpers_region()
        + "\nCMD=()\n"
        + harness
        # ${CMD[@]+"${CMD[@]}"} (not plain "${CMD[@]}"): pre-4.4 bash — macOS's
        # stock 3.2 included — treats an empty array subscripted with [@]
        # under `set -u` as an unbound-variable error and aborts the script
        # (the same bug run.sh itself works around at its PR-comment loop).
        + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout


def _cmd_items(stdout: str) -> list[str]:
    return [item for item in stdout.split("\x1f") if item]


def _run_predicate(call: str) -> bool:
    """Source the real helper functions and evaluate a boolean-returning call
    (e.g. an ``_is_release_style_operand "path"`` invocation), returning
    whether it exited zero (true) or non-zero (false)."""
    script = _helpers_region() + f"\nif {call}; then exit 0; else exit 1; fi\n"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddFlagSplitting:
    def test_legacy_space_separated_single_line(self) -> None:
        # Back-compat: the documented single-line "space-separated" form.
        out = _run_harness('add_flag "-H" "inc/a inc/b"')
        assert _cmd_items(out) == ["-H", "inc/a", "-H", "inc/b"]

    def test_newline_separated_preserves_spaces(self) -> None:
        # A YAML block scalar (`headers: |`) input — one path per line,
        # including a path containing a space.
        out = _run_harness('add_flag "-H" $\'inc/a\\npath with spaces/inc\\ninc/c\'')
        assert _cmd_items(out) == [
            "-H", "inc/a", "-H", "path with spaces/inc", "-H", "inc/c",
        ]

    def test_empty_value_adds_nothing(self) -> None:
        out = _run_harness('add_flag "-H" ""')
        assert _cmd_items(out) == []

    def test_single_value_no_separator(self) -> None:
        out = _run_harness('add_flag "-H" "inc/only"')
        assert _cmd_items(out) == ["-H", "inc/only"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddSidedFlagSplitting:
    def test_legacy_space_separated_single_line(self) -> None:
        out = _run_harness('add_sided_flag "--header" "old" "inc/a inc/b"')
        assert _cmd_items(out) == [
            "--header", "old=inc/a", "--header", "old=inc/b",
        ]

    def test_newline_separated_preserves_spaces(self) -> None:
        out = _run_harness(
            'add_sided_flag "--header" "new" $\'inc/a\\npath with spaces/inc\''
        )
        assert _cmd_items(out) == [
            "--header", "new=inc/a", "--header", "new=path with spaces/inc",
        ]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestIsReleaseStyleOperand:
    """``compare`` mode now skips its --secondary-format optimization for
    directory/package operands, since the release fan-out engine rejects
    that flag — verified defect: it previously hard-failed a working
    directory/package compare under MODE=compare (Codex review, PR #557)."""

    def test_directory_is_release_style(self, tmp_path) -> None:
        d = tmp_path / "libdir"
        d.mkdir()
        assert _run_predicate(f'_is_release_style_operand "{d}"')

    def test_plain_file_is_not_release_style(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so.1"
        f.write_text("", encoding="utf-8")
        assert not _run_predicate(f'_is_release_style_operand "{f}"')

    def test_json_snapshot_is_not_release_style(self, tmp_path) -> None:
        f = tmp_path / "snapshot.json"
        f.write_text("{}", encoding="utf-8")
        assert not _run_predicate(f'_is_release_style_operand "{f}"')

    @pytest.mark.parametrize("suffix", [
        ".rpm", ".deb", ".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst",
        ".tgz", ".conda", ".whl",
    ])
    def test_package_extensions_are_release_style(self, tmp_path, suffix) -> None:
        f = tmp_path / f"libfoo{suffix}"
        f.write_text("", encoding="utf-8")
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_package_extension_matched_case_insensitively(self, tmp_path) -> None:
        f = tmp_path / "libfoo.RPM"
        f.write_text("", encoding="utf-8")
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_missing_path_is_not_release_style(self) -> None:
        # A nonexistent path isn't a directory and doesn't match a package
        # extension by name — the required-args guard in run.sh catches a
        # genuinely missing operand before this check ever runs.
        assert not _run_predicate('_is_release_style_operand "/no/such/path.so"')
