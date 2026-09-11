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

"""``actions/check-target/action.yml``'s "Generate assurance-overlay config"
step's own ``BASE_CONFIG`` absolutization -- just the ``_is_path_already_
qualified`` helper plus the ``_base_config_abs`` if-block, exercised in
isolation (Codex review, PR #1222, P1 finding).

Mirrors ``tests/test_action_release_topology_windows_paths.py`` exactly:
that module fixed and pinned the identical bug in ``action/run.sh``'s own
``base_source`` absolutization one round earlier. This step doesn't source
``run.sh`` -- every composite-action step in this file already duplicates
``run.sh``'s small shell snippets verbatim (tagged with a "Mirrors
action/run.sh's own ..." comment) rather than sharing a sourced library
file -- so the fix duplicates ``_is_path_already_qualified``'s exact logic
here too, and this test module duplicates ``test_action_release_topology_
windows_paths.py``'s own harness rather than importing it, the same
verbatim-extraction discipline every other bash-harness test module in this
directory already follows (see that module's own docstring).

Before the fix, an explicit ``build-config`` given as a Windows-style
absolute path (``C:/...``, ``C:\\...``, or a UNC ``\\\\server\\share\\...``)
was misclassified as RELATIVE by a POSIX-only ``case "$BASE_CONFIG" in /*)
... ;; *) _base_config_abs="$_real_pwd/$BASE_CONFIG" ;; esac`` test (none of
those forms start with ``/``), producing a malformed, doubled path
(``$_real_pwd/C:/Users/...``) the moment a Windows/Git-Bash runner enabled
``analysis-assurance-complete``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_TARGET_ACTION = _REPO_ROOT / "actions" / "check-target" / "action.yml"


def _overlay_step_run_source() -> str:
    data = yaml.safe_load(CHECK_TARGET_ACTION.read_text(encoding="utf-8"))
    step = next(s for s in data["runs"]["steps"] if s.get("id") == "assurance_overlay")
    run_body = step["run"]
    assert isinstance(run_body, str)
    return run_body


# Same two-marker verbatim-extraction discipline as
# test_action_release_topology_windows_paths.py's own
# `_path_qualified_helper_source` -- lifted from the step's parsed `run:`
# body (YAML's block-scalar dedent already applied) rather than a raw-text
# search of the file, since the file's own indentation is relative to the
# `run: |` block, not the shell script's own.
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"


def _path_qualified_helper_source() -> str:
    text = _overlay_step_run_source()
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


# Just the `_base_config_abs` if-block -- extracted on its own so a test can
# exercise the "already qualified, leave it alone" vs. "relative, add
# $_real_pwd/" decision directly, without running the rest of the step
# (which would immediately fail trying to `open()` a synthetic Windows path
# that has no real file behind it on this (Linux) test runner).
_BASE_CONFIG_ABS_START = '_base_config_abs=""'
_BASE_CONFIG_ABS_END = "\nfi\n"


def _base_config_abs_source() -> str:
    text = _overlay_step_run_source()
    start = text.index(_BASE_CONFIG_ABS_START)
    end = text.index(_BASE_CONFIG_ABS_END, start) + len(_BASE_CONFIG_ABS_END)
    return text[start:end]


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


def _bash_pwd(cwd: Path) -> str:
    """The real ``$PWD`` bash itself reports for *cwd* -- not ``str(cwd)``
    (see ``test_action_release_topology_windows_paths.py``'s own
    ``_bash_pwd`` for the full rationale: a Windows/Git-Bash runner's
    ``$PWD`` is always the MSYS POSIX form, never the native backslash form
    ``pathlib.Path`` prints there)."""
    result = subprocess.run(
        [_bash_executable(), "-c", "printf '%s' \"$PWD\""],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _run_bash_script(
    script: str,
    env_extra: dict[str, str] | None = None,
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run *script* via a real bash from a temp file (Windows argv-quoting
    safety, matching test_action_compile_context_parity.py's own
    _run_bash_script)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    env = {**os.environ, **(env_extra or {})}
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            cwd=cwd,
            timeout=30,
        )
    finally:
        os.unlink(script_path)


class TestAssuranceOverlayPreservesWindowsQualifiedBuildConfigPath:
    """Codex review, PR #1222 (P1): the "Generate assurance-overlay config"
    step's own ``BASE_CONFIG`` absolutization used to test ``case
    "$BASE_CONFIG" in /*) ... ;; *) ... ;; esac`` -- POSIX-only, so a genuine
    Windows-qualified path was misclassified as relative and got a spurious
    ``$_real_pwd/`` prefix prepended. The fix reuses
    ``_is_path_already_qualified`` -- byte-for-byte identical to
    ``action/run.sh``'s own helper of the same name -- gated on ``$OSTYPE``
    indicating a Windows host, exercised here by forcing ``$OSTYPE`` rather
    than relying on whatever platform actually runs this test.
    """

    def _absolutize(self, base_config: str, cwd: Path, *, windows: bool) -> str:
        script = (
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + _path_qualified_helper_source()
            + '\nBASE_CONFIG="$TEST_BASE_CONFIG"\n_real_pwd="$PWD"\n'
            + _base_config_abs_source()
            + 'printf "%s" "$_base_config_abs"\n'
        )
        result = _run_bash_script(
            script,
            {
                "OSTYPE": "msys" if windows else "linux-gnu",
                "TEST_BASE_CONFIG": base_config,
            },
            cwd=cwd,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_windows_drive_forward_slash_path_is_not_prefixed(
        self, tmp_path: Path
    ) -> None:
        windows_path = "C:/Users/x/.abicheck.yml"
        result = self._absolutize(windows_path, tmp_path, windows=True)
        assert result == windows_path

    def test_windows_drive_backslash_path_is_not_prefixed(self, tmp_path: Path) -> None:
        windows_path = r"C:\Users\x\.abicheck.yml"
        result = self._absolutize(windows_path, tmp_path, windows=True)
        assert result == windows_path

    def test_windows_unc_path_is_not_prefixed(self, tmp_path: Path) -> None:
        unc_path = r"\\server\share\.abicheck.yml"
        result = self._absolutize(unc_path, tmp_path, windows=True)
        assert result == unc_path

    def test_posix_absolute_path_is_not_prefixed_on_windows(
        self, tmp_path: Path
    ) -> None:
        posix_path = "/etc/abicheck/.abicheck.yml"
        result = self._absolutize(posix_path, tmp_path, windows=True)
        assert result == posix_path

    def test_same_drive_letter_shaped_path_is_prefixed_on_non_windows(
        self, tmp_path: Path
    ) -> None:
        """The identical text is a genuine POSIX-relative filename on a
        non-Windows host -- ``$OSTYPE`` gating means it still gets the
        ``$_real_pwd/`` prefix there, unlike the Windows-forced case
        above."""
        posix_like = "C:/Users/x/.abicheck.yml"
        result = self._absolutize(posix_like, tmp_path, windows=False)
        assert result == f"{_bash_pwd(tmp_path)}/{posix_like}"

    def test_ordinary_relative_path_still_gets_prefix_on_windows(
        self, tmp_path: Path
    ) -> None:
        """A genuinely relative path (no drive/UNC/root-relative form) must
        still be absolutized even when ``$OSTYPE`` is Windows -- the fix
        must not accidentally widen "already qualified" beyond the real
        Windows-qualified forms."""
        relative = ".abicheck.yml"
        result = self._absolutize(relative, tmp_path, windows=True)
        assert result == f"{_bash_pwd(tmp_path)}/{relative}"
