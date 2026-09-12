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

"""``action/run.sh``'s ``_merge_config_overlay_with_discovered_project_
config`` -- just its ``base_source`` absolutization if-block, exercised in
isolation.

Split out of ``test_action_release_topology_config.py`` (a `debt.yaml`
`no_growth`-tracked module) purely to keep that file under the AI-readiness
`file-size` gate's test-module line cap -- this class and its two
dedicated helpers (``_base_source_absolutize_source``, ``_bash_pwd``) are
fully self-contained modulo the small set of bash-harness primitives
(``RUN_SH``, ``_path_qualified_helper_source``, ``bash_executable``,
``_run_bash_script``) this file re-extracts verbatim rather than importing
from its sibling, mirroring every other bash-harness test module in this
directory's own established convention of not cross-importing test code
(``test_action_compile_context_parity.py`` duplicates the identical
``bash_executable``/``_run_bash_script`` pair rather than importing them).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


# `_merge_config_overlay_with_discovered_project_config`'s own
# `base_source` absolutization (Codex review, PR #1159, fourth round) now
# delegates to `_is_path_already_qualified` (a real Windows drive/UNC/
# root-relative path must not get a `$PWD/` prefix) rather than a
# POSIX-only `!= /*` test -- so any harness including the merge function
# must also define this helper (and the `$OSTYPE`-derived
# `$_RUNNING_ON_WINDOWS` it reads), the same verbatim-extraction discipline
# `test_action_run_sh_py_safe_path.py`'s own
# `_path_qualified_helper_source` already established.
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"


def _path_qualified_helper_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


# Just the `base_source` absolutization if-block inside the merge helper
# (Codex review, PR #1159, fourth round) -- extracted on its own so a test
# can exercise the "already qualified, leave it alone" vs. "relative, add
# $PWD/" decision directly, without needing to run the full merge (which
# would otherwise fail on "does not exist" for a synthetic Windows path
# that has no real file behind it on this test runner, and would also let
# Python's own `Path(...).resolve()` re-derive an absolute path from a
# relative one, masking exactly the distinction this test needs to see).
_BASE_SOURCE_ABSOLUTIZE_START = 'if ! _is_path_already_qualified "$base_source"; then'
_BASE_SOURCE_ABSOLUTIZE_END = "\n  fi\n"


def _base_source_absolutize_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_BASE_SOURCE_ABSOLUTIZE_START)
    end = text.index(_BASE_SOURCE_ABSOLUTIZE_END, start) + len(
        _BASE_SOURCE_ABSOLUTIZE_END
    )
    return text[start:end]


def _bash_pwd(cwd: Path) -> str:
    """The real ``$PWD`` bash itself reports for *cwd* -- not ``str(cwd)``
    (mirrors ``test_action_run_sh_severity_summary.py::TestReportPathAnchoring
    ._bash_pwd``: on a Windows Git-Bash runner, ``$PWD`` inside bash is
    always the MSYS POSIX form (``/c/Users/...``), never the native
    backslash form Python's ``pathlib.Path`` prints there. Comparing a
    script's own ``$PWD``-anchored output against ``f"{tmp_path}/..."``
    would compare two different path *representations* of the same
    directory, not two different directories. A no-op on POSIX hosts, where
    both forms already coincide)."""
    result = subprocess.run(
        [bash_executable(), "-c", "printf '%s' \"$PWD\""],
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
    check: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run *script* via a real bash from a temp file (Windows argv-quoting
    safety, matching test_action_compile_context_parity.py's own
    _run_bash_script -- see that module's docstring for the exact reason)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    env = {**os.environ, **(env_extra or {})}
    try:
        return subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            text=True,
            env=env,
            check=check,
            cwd=cwd,
            timeout=30,
        )
    finally:
        os.unlink(script_path)


class TestReleaseTopologyOverlayPreservesWindowsQualifiedBuildConfigPath:
    """Codex review, PR #1159 (P1, fourth round): the merge helper's
    ``base_source`` absolutization used to test ``[[ "$base_source" != /*
    ]]`` -- POSIX-only, so a genuine Windows-qualified path (a drive letter
    like ``C:\\...``, a UNC path ``\\\\server\\share\\...``, or a
    root-relative ``\\foo``) was misclassified as relative and got a
    spurious ``$PWD/`` prefix prepended, producing a malformed path that
    then failed inside the isolated Python subprocess even though the
    identical path already works fine when passed straight to the native
    CLI. The fix reuses ``_is_path_already_qualified`` -- the same helper
    ``$_PY_BIN`` canonicalization and ``_report_query``'s path anchoring
    already use -- which recognizes these Windows-only forms, but only when
    ``$OSTYPE`` indicates a Windows host (Git Bash/MSYS reports ``msys``);
    exercised here by forcing ``$OSTYPE`` rather than relying on whatever
    platform actually runs this test (mirrors
    ``test_action_run_sh_severity_summary.py::TestReportPathAnchoring``'s
    own established pattern for the identical helper).

    The two "gets prefixed with ``$PWD``" cases below compare against this
    module's own ``_bash_pwd(tmp_path)`` rather than ``tmp_path`` itself --
    a real Windows/Git-Bash CI runner's ``$PWD`` is always the MSYS POSIX
    form (``/c/Users/...``), never the native backslash form Python's
    ``pathlib.Path`` prints there, so comparing against a bare ``tmp_path``
    failed on every Windows CI run regardless of whether the absolutization
    logic itself was correct (mirrors ``TestReportPathAnchoring``'s own
    ``_bash_pwd`` fix for the identical mismatch).

    These tests exercise just the absolutization if-block in isolation
    (``_base_source_absolutize_source``), not the full merge -- a
    synthetic Windows path has no real file behind it on this (Linux) test
    runner, so running it through the full merge would fail on "does not
    exist" for either branch and, worse, let Python's own
    ``Path(...).resolve()`` re-derive an absolute path from a relative one,
    masking exactly the bash-level distinction this test needs to observe.
    """

    def _absolutize(self, base_source: str, cwd: Path, *, windows: bool) -> str:
        script = (
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + _path_qualified_helper_source()
            + '\nbase_source="$TEST_BASE_SOURCE"\n'
            + _base_source_absolutize_source()
            + 'printf "%s" "$base_source"\n'
        )
        # `$OSTYPE` is forced explicitly (see class docstring) so both
        # branches are exercised regardless of the host actually running
        # this test.
        result = _run_bash_script(
            script,
            {
                "OSTYPE": "msys" if windows else "linux-gnu",
                "TEST_BASE_SOURCE": base_source,
            },
            cwd=cwd,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_windows_drive_path_is_not_prefixed_with_pwd(self, tmp_path: Path) -> None:
        windows_path = "C:/Users/runner/work/repo/config.yml"
        result = self._absolutize(windows_path, tmp_path, windows=True)
        assert result == windows_path

    def test_windows_unc_path_is_not_prefixed_with_pwd(self, tmp_path: Path) -> None:
        unc_path = r"\\server\share\config.yml"
        result = self._absolutize(unc_path, tmp_path, windows=True)
        assert result == unc_path

    def test_windows_root_relative_path_is_not_prefixed_with_pwd(
        self, tmp_path: Path
    ) -> None:
        root_relative = r"\foo\config.yml"
        result = self._absolutize(root_relative, tmp_path, windows=True)
        assert result == root_relative

    def test_same_drive_letter_shaped_path_is_prefixed_on_non_windows(
        self, tmp_path: Path
    ) -> None:
        """The identical text is a genuine POSIX-relative filename on a
        non-Windows host (e.g. a file literally named ``C:`` is unusual but
        legal on Linux/macOS) -- ``$OSTYPE`` gating means it still gets the
        ``$PWD/`` prefix there, unlike the Windows-forced case above."""
        posix_like = "C:/Users/runner/work/repo/config.yml"
        result = self._absolutize(posix_like, tmp_path, windows=False)
        assert result == f"{_bash_pwd(tmp_path)}/{posix_like}"

    def test_ordinary_relative_path_still_gets_pwd_prefix_on_windows(
        self, tmp_path: Path
    ) -> None:
        """A genuinely relative path (no drive/UNC/root-relative form) must
        still be absolutized even when ``$OSTYPE`` is Windows -- the fix
        must not accidentally widen "already qualified" beyond the real
        Windows-qualified forms."""
        relative = ".abicheck.yml"
        result = self._absolutize(relative, tmp_path, windows=True)
        assert result == f"{_bash_pwd(tmp_path)}/{relative}"
