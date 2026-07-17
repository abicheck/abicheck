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

"""Behavioral tests for ``action/validate-inputs.sh``.

This script runs as the composite Action's very first step (action.yml),
before Python setup, system-dependency installation, or `pip install
abicheck` -- it exists to fail fast on a mode/input combination that can
never work (a directory/package handed to ``dump``/``scan``, which have no
per-library fan-out) or that used to silently do the wrong thing (an
unsupported ``format`` for the mode used to warn and fall back instead of
erroring, which is unsafe paired with ``upload-sarif``).

These tests invoke the real script as a subprocess (not an extracted
fragment) since it is small and fully self-contained. A separate drift-guard
class checks its duplicated ``_is_release_style_operand`` copy against
``action/run.sh``'s original on the same fixture set.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"
RUN_SH = ACTION_DIR / "run.sh"


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
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


def _run_validate(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [_bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestDefaultsPassValidation:
    def test_no_inputs_at_all_passes(self) -> None:
        result = _run_validate({})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_plain_compare_passes(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_FORMAT": "sarif"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestDumpRejectsDirectoryOrPackage:
    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate(
            {"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(lib_dir)}
        )
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout
        assert "dump" in result.stdout

    def test_package_extension_is_rejected(self, tmp_path: Path) -> None:
        pkg = tmp_path / "libfoo.rpm"
        pkg.write_text("")
        result = _run_validate({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(pkg)})
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout

    def test_plain_binary_passes(self, tmp_path: Path) -> None:
        lib = tmp_path / "libfoo.so.1"
        lib.write_text("")
        result = _run_validate({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(lib)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_source_only_dump_with_no_new_library_passes(self) -> None:
        # dump's new-library is optional (source-only dump); the required-
        # ness check lives in run.sh, not this validator.
        result = _run_validate({"INPUT_MODE": "dump"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestScanRejectsDirectoryOrPackage:
    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "release" / "lib" / "intel64"
        lib_dir.mkdir(parents=True)
        result = _run_validate(
            {"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(lib_dir)}
        )
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout
        assert "scan" in result.stdout

    def test_plain_binary_passes(self, tmp_path: Path) -> None:
        lib = tmp_path / "libfoo.so.1"
        lib.write_text("")
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(lib)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_json_snapshot_passes(self, tmp_path: Path) -> None:
        snap = tmp_path / "baseline.json"
        snap.write_text("{}")
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(snap)})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestFormatIsHardErrorNotSilentFallback:
    @pytest.mark.parametrize("fmt", ["sarif", "html"])
    def test_scan_rejects_unsupported_format(self, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": fmt})
        assert result.returncode == 1
        assert "does not support format" in result.stdout
        assert "warning" not in result.stdout.lower()

    @pytest.mark.parametrize("fmt", ["text", "json"])
    def test_scan_accepts_supported_format(self, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": fmt})
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    @pytest.mark.parametrize("fmt", ["sarif", "html"])
    def test_deps_modes_reject_unsupported_format(self, mode: str, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": mode, "INPUT_FORMAT": fmt})
        assert result.returncode == 1
        assert "does not support format" in result.stdout

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    @pytest.mark.parametrize("fmt", ["markdown", "json"])
    def test_deps_modes_accept_supported_format(self, mode: str, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": mode, "INPUT_FORMAT": fmt})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_compare_still_allows_sarif(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_FORMAT": "sarif"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestUploadSarif:
    def test_upload_sarif_with_compare_and_sarif_format_passes(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_FORMAT": "sarif",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_upload_sarif_with_non_compare_mode_is_rejected(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "scan",
                "INPUT_FORMAT": "json",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 1
        assert "upload-sarif is only meaningful with mode: compare" in result.stdout

    def test_upload_sarif_with_non_sarif_format_is_rejected(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_FORMAT": "json",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 1
        assert "upload-sarif requires format: sarif" in result.stdout

    def test_upload_sarif_false_default_never_triggers_the_check(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": "json"})
        assert result.returncode == 0, result.stdout + result.stderr


# ─────────────────────────────────────────────────────────────────────────
# Drift guard: validate-inputs.sh intentionally duplicates run.sh's
# `_is_release_style_operand()` (documented at the top of validate-inputs.sh
# as to why: this step must have zero dependency on run.sh's layout). Run
# both copies against the same fixtures and assert identical verdicts so a
# future edit to one classifier that isn't mirrored in the other is caught
# here instead of shipping a validator that disagrees with run.sh itself.
# ─────────────────────────────────────────────────────────────────────────

_RUN_SH_MARKER = "# Build the abicheck command"
_VALIDATE_SH_MARKER = "_fail() {"


def _run_sh_operand_fn() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_RUN_SH_MARKER)
    return text[:idx]


def _validate_sh_operand_fn() -> str:
    text = VALIDATE_SH.read_text(encoding="utf-8")
    idx = text.index(_VALIDATE_SH_MARKER)
    return text[:idx]


def _classify(fn_region: str, path: str) -> bool:
    script = (
        fn_region
        + f'\nif _is_release_style_operand "{path}"; then exit 0; else exit 1; fi\n'
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


@pytest.mark.skipif(
    not (VALIDATE_SH.is_file() and RUN_SH.is_file()),
    reason="action scripts not found",
)
class TestClassifierParityWithRunSh:
    @pytest.mark.parametrize(
        "kind", ["directory", "plain_file", "json_snapshot", "rpm_extension"]
    )
    def test_both_copies_agree(self, tmp_path: Path, kind: str) -> None:
        if kind == "directory":
            path = tmp_path / "dir"
            path.mkdir()
        elif kind == "plain_file":
            path = tmp_path / "libfoo.so.1"
            path.write_text("")
        elif kind == "json_snapshot":
            path = tmp_path / "snap.json"
            path.write_text("{}")
        else:
            path = tmp_path / "libfoo.rpm"
            path.write_text("")

        run_sh_verdict = _classify(_run_sh_operand_fn(), str(path))
        validate_sh_verdict = _classify(_validate_sh_operand_fn(), str(path))
        assert run_sh_verdict == validate_sh_verdict, (
            f"run.sh and validate-inputs.sh disagree on {kind} ({path}): "
            f"run.sh={run_sh_verdict} validate-inputs.sh={validate_sh_verdict}"
        )
