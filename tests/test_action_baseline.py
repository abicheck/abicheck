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

    def test_duplicate_library_name_fails(self, tmp_path: Path) -> None:
        # A generated matrix producing two entries with the same name would
        # otherwise have the second dump silently overwrite the first's
        # $OUTPUT_DIR/$name.abicheck.json while the manifest still lists two
        # artifact rows for it (Codex review).
        result, _ = _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [
                        {"name": "libfoo", "artifact": "a.so"},
                        {"name": "libfoo", "artifact": "b.so"},
                    ]
                )
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "duplicate library name" in result.stdout
        assert "libfoo" in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
class TestStaleOutputCleared:
    def test_stale_snapshot_removed_before_dump(self, tmp_path: Path) -> None:
        # Regression (Codex review): a library removed/renamed since an
        # earlier run at this same output-dir used to leave its old
        # *.abicheck.json sitting there -- invisible to the new run's
        # manifest.json/content-digest, but still physically present for a
        # caller that publishes/uploads the whole directory. Runs
        # regardless of whether the dump itself succeeds (no abicheck
        # needed on PATH): the cleanup happens before the dump loop starts.
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        (output_dir / "stale-lib.abicheck.json").write_text("{}")
        (output_dir / "manifest.json").write_text("{}")

        _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "libfoo", "artifact": str(tmp_path / "no-such.so")}]
                ),
                "INPUT_OUTPUT_DIR": str(output_dir),
            },
            tmp_path,
        )
        assert not (output_dir / "stale-lib.abicheck.json").exists()
        assert not (output_dir / "manifest.json").exists()

    def test_previous_manifest_pointing_at_output_dir_survives_cleanup(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): a workflow that restores the previous
        # baseline set into output-dir before regenerating (an in-place
        # refresh) points previous-manifest at output-dir/manifest.json --
        # the stale-snapshot cleanup must not delete that same file out from
        # under build_manifest.py before it gets a chance to read it.
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        previous_manifest = output_dir / "manifest.json"
        previous_manifest.write_text('{"sentinel": "keep-me"}')

        _run_action(
            {
                "INPUT_LIBRARIES": json.dumps(
                    [{"name": "libfoo", "artifact": str(tmp_path / "no-such.so")}]
                ),
                "INPUT_OUTPUT_DIR": str(output_dir),
                "INPUT_PREVIOUS_MANIFEST": str(previous_manifest),
            },
            tmp_path,
        )
        assert previous_manifest.is_file()
        assert previous_manifest.read_text() == '{"sentinel": "keep-me"}'


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
@pytest.mark.skipif(not _ABICHECK, reason="needs abicheck on PATH")
class TestDumpFailurePaths:
    # Shells out to the real abicheck CLI -- excluded from the default fast
    # lane so it doesn't silently run just because abicheck happens to be
    # installed in a dev environment (CodeRabbit review).
    pytestmark = pytest.mark.integration

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


_DUMP_LOOP_START = 'echo "::group::Dump baseline-set into $OUTPUT_DIR"'
_DUMP_LOOP_END = 'echo "::endgroup::"'


def _dump_loop_region() -> str:
    """The per-library dump loop, extracted verbatim from run.sh -- the same
    "parse the real file, don't hand-copy it" discipline as
    ``test_action_run_sh_legacy_aliases.py`` / ``..._dry_run_baseline.py``."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_DUMP_LOOP_START)
    end = text.index(_DUMP_LOOP_END, start) + len(_DUMP_LOOP_END)
    return text[start:end]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/baseline/run.sh not found")
class TestDumpLoopFieldSplitting:
    """Regression: a library entry with `include` set but `header` omitted
    (an empty field between two non-empty ones) used to have its `include`
    value shift into `header` -- bash's word-splitting always treats a
    literal tab in IFS as whitespace and collapses the adjacent empty field,
    no matter what IFS is set to. run.sh now delimits with ASCII Unit
    Separator (\\x1f) instead, which bash does not treat as whitespace."""

    def _run_dump_loop(self, libraries: list[dict[str, str]]) -> str:
        script = (
            '_fail() { echo "::error::$1"; exit 1; }\n'
            'abicheck() { echo "CMD_ARGS:$*"; return 0; }\n'
            f"LIBRARIES_JSON='{json.dumps(libraries)}'\n"
            'OUTPUT_DIR="$PWD"\n'
            'BUILD_INFO=""\n'
            'DEPTH=""\n'
            'PROJECT_REF=""\n' + _dump_loop_region()
        )
        result = subprocess.run(
            [_bash_executable(), "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def test_include_without_header_stays_include(self) -> None:
        stdout = self._run_dump_loop(
            [{"name": "libfoo", "artifact": "a.so", "include": "include"}]
        )
        [cmd_line] = [
            line for line in stdout.splitlines() if line.startswith("CMD_ARGS:")
        ]
        assert "-I include" in cmd_line
        assert "-H" not in cmd_line.split()

    def test_header_without_include_stays_header(self) -> None:
        stdout = self._run_dump_loop(
            [{"name": "libfoo", "artifact": "a.so", "header": "include/foo.h"}]
        )
        [cmd_line] = [
            line for line in stdout.splitlines() if line.startswith("CMD_ARGS:")
        ]
        assert "-H include/foo.h" in cmd_line
        assert "-I" not in cmd_line.split()

    def test_both_header_and_include_are_kept_separate(self) -> None:
        stdout = self._run_dump_loop(
            [
                {
                    "name": "libfoo",
                    "artifact": "a.so",
                    "header": "include/foo.h",
                    "include": "include",
                }
            ]
        )
        [cmd_line] = [
            line for line in stdout.splitlines() if line.startswith("CMD_ARGS:")
        ]
        assert "-H include/foo.h" in cmd_line
        assert "-I include" in cmd_line
