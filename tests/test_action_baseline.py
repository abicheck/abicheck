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

"""Behavioral tests for ``actions/baseline/run.sh``.

This script backs the ``abicheck baseline`` composite Action: it dumps a
JSON-described set of libraries into a baseline-set (one ``.abicheck.json``
per library plus a ``manifest.json`` -- see
``actions/baseline/build_manifest.py`` and
``tests/test_baseline_manifest.py`` for the pure-Python manifest logic this
script's ``build_manifest.py`` call feeds). This file covers the bash
orchestration layer: input validation, per-library dump invocation, the
optional self-compare validation pass, and one end-to-end run against real
compiled shared libraries.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "baseline"
RUN_SH = ACTION_DIR / "run.sh"

_GCC = shutil.which("gcc")
_ABICHECK = shutil.which("abicheck")


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


def _run_action(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Invoke the real script end-to-end with a GITHUB_OUTPUT file."""
    github_output = cwd / "github_output"
    github_output.write_text("")
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(github_output),
        "ACTION_PATH": str(ACTION_DIR),
        **env_extra,
    }
    result = subprocess.run(
        [_bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )
    return result, github_output


def _parse_kv_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _compile_shared_lib(src_text: str, out: Path) -> None:
    src = out.with_suffix(".c")
    src.write_text(src_text, encoding="utf-8")
    subprocess.run(
        [_GCC, "-shared", "-fPIC", "-g", str(src), "-o", str(out)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
class TestValidationInputRejected:
    def test_unknown_validation_value_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps([{"name": "foo", "artifact": "a.so"}]),
                "INPUT_VALIDATION": "bogus",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_missing_libraries_input_fails(self, tmp_path: Path) -> None:
        # No INPUT_LIBRARIES at all -- the ${VAR:?message} guard should
        # reject this before any JSON parsing is attempted.
        result, _ = _run_action({}, tmp_path)
        assert result.returncode != 0


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
class TestLibrariesJsonValidation:
    def test_invalid_json_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action({"INPUT_LIBRARIES": "not json"}, tmp_path)
        assert result.returncode == 1
        assert "not valid JSON" in result.stdout

    def test_non_array_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"INPUT_LIBRARIES": json.dumps({"name": "foo", "artifact": "a.so"})},
            tmp_path,
        )
        assert result.returncode == 1
        assert "non-empty JSON array" in result.stdout

    def test_empty_array_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action({"INPUT_LIBRARIES": "[]"}, tmp_path)
        assert result.returncode == 1
        assert "non-empty JSON array" in result.stdout

    def test_entry_missing_name_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"INPUT_LIBRARIES": json.dumps([{"artifact": "a.so"}])}, tmp_path
        )
        assert result.returncode == 1
        assert "entry 0" in result.stdout

    def test_entry_missing_artifact_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"INPUT_LIBRARIES": json.dumps([{"name": "foo"}])}, tmp_path
        )
        assert result.returncode == 1
        assert "entry 0" in result.stdout

    def test_second_entry_missing_key_is_still_caught(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "foo", "artifact": "a.so"}, {"name": "bar"}]
                )
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "entry 1" in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
@pytest.mark.skipif(not _ABICHECK, reason="needs abicheck on PATH")
class TestDumpFailurePaths:
    def test_nonexistent_artifact_fails_with_library_name(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "libfoo", "artifact": str(tmp_path / "no-such.so")}]
                ),
                "INPUT_OUTPUT_DIR": str(tmp_path / "out"),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "dump failed for library 'libfoo'" in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and _GCC and _ABICHECK),
    reason="needs an ELF host with gcc and abicheck on PATH",
)
class TestEndToEndBaselineSet:
    pytestmark = pytest.mark.integration

    def test_two_libraries_produce_a_manifest_with_freshness(
        self, tmp_path: Path
    ) -> None:
        libfoo = tmp_path / "libfoo.so"
        libbar = tmp_path / "libbar.so"
        _compile_shared_lib(
            "int abicheck_foo_add(int a, int b) { return a + b; }\n", libfoo
        )
        _compile_shared_lib(
            "int abicheck_bar_sub(int a, int b) { return a - b; }\n", libbar
        )
        output_dir = tmp_path / "baseline-out"
        libraries = json.dumps(
            [
                {"name": "libfoo", "artifact": str(libfoo)},
                {"name": "libbar", "artifact": str(libbar)},
            ]
        )

        result, github_output = _run_action(
            {
                "INPUT_LIBRARIES": libraries,
                "INPUT_OUTPUT_DIR": str(output_dir),
                "INPUT_PROJECT_REF": "v1.0.0",
                "INPUT_PROFILE": "linux-x86_64-gcc",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr

        assert (output_dir / "libfoo.abicheck.json").is_file()
        assert (output_dir / "libbar.abicheck.json").is_file()
        manifest_path = output_dir / "manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["project_ref"] == "v1.0.0"
        assert manifest["profile"] == "linux-x86_64-gcc"
        assert sorted(a["library"] for a in manifest["artifacts"]) == [
            "libbar",
            "libfoo",
        ]
        assert manifest["snapshot_schema"] is not None
        assert manifest["freshness"] == {"refresh_required": False, "reasons": []}

        outputs = _parse_kv_file(github_output)
        assert outputs["baseline-path"] == str(output_dir)
        assert outputs["manifest-path"] == str(manifest_path)
        assert outputs["library-count"] == "2"
        assert outputs["refresh-required"] == "false"
        assert "content-digest" in outputs

        # "all snapshots round-tripped cleanly" only prints when the
        # strict-validation self-compare pass actually ran.
        assert "all snapshots round-tripped cleanly" in result.stdout

    def test_added_library_against_previous_manifest_requires_refresh(
        self, tmp_path: Path
    ) -> None:
        libfoo = tmp_path / "libfoo.so"
        _compile_shared_lib(
            "int abicheck_foo_add(int a, int b) { return a + b; }\n", libfoo
        )
        output_dir = tmp_path / "baseline-out"
        first_result, first_output = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "libfoo", "artifact": str(libfoo)}]
                ),
                "INPUT_OUTPUT_DIR": str(output_dir),
            },
            tmp_path,
        )
        assert first_result.returncode == 0, first_result.stdout + first_result.stderr
        previous_manifest = output_dir / "manifest.json"

        libbar = tmp_path / "libbar.so"
        _compile_shared_lib(
            "int abicheck_bar_sub(int a, int b) { return a - b; }\n", libbar
        )
        second_output_dir = tmp_path / "baseline-out-2"
        result, github_output = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [
                        {"name": "libfoo", "artifact": str(libfoo)},
                        {"name": "libbar", "artifact": str(libbar)},
                    ]
                ),
                "INPUT_OUTPUT_DIR": str(second_output_dir),
                "INPUT_PREVIOUS_MANIFEST": str(previous_manifest),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs = _parse_kv_file(github_output)
        assert outputs["refresh-required"] == "true"
        assert "libbar" in outputs["refresh-reasons"]

    def test_validation_none_skips_self_compare(self, tmp_path: Path) -> None:
        libfoo = tmp_path / "libfoo.so"
        _compile_shared_lib(
            "int abicheck_foo_add(int a, int b) { return a + b; }\n", libfoo
        )
        result, _ = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "libfoo", "artifact": str(libfoo)}]
                ),
                "INPUT_OUTPUT_DIR": str(tmp_path / "baseline-out"),
                "INPUT_VALIDATION": "none",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Self-compare validation" not in result.stdout
