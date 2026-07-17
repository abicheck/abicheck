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

"""Behavioral tests for ``actions/collect-facts/run.sh``.

This script backs the ``abicheck collect-facts`` composite Action: it
resolves which source-facts producer to use (replay / wrapper / clang-
plugin -- see ``docs/user-guide/producing-source-facts.md`` for the
decision tree it mirrors), runs that producer's collection step, and
verifies the resulting pack. Wrapper and clang-plugin need the caller's own
build command to run *between* collection and verification, hence the
``phase: prepare`` / ``phase: verify`` split this file exercises.

Tests avoid actually building the Clang plugin (needs a matching
libclang-<N>-dev toolchain not guaranteed in every test environment) --
those paths are exercised only up to their first fast-failing precondition
(missing compiler, bad producer/phase value).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "collect-facts"
RUN_SH = ACTION_DIR / "run.sh"
_HELPERS_MARKER = "# ---------------------------------------------------------------------------\n# Resolve producer"


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


def _helpers_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_HELPERS_MARKER)
    return text[:idx]


def _run_predicate(call: str) -> subprocess.CompletedProcess[str]:
    """Source the pure-helper region and evaluate a call, capturing stdout."""
    script = _helpers_region() + f"\n{call}\n"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [_bash_executable(), script_path], capture_output=True, text=True
        )
    finally:
        os.unlink(script_path)


def _run_action(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Invoke the real script end-to-end with GITHUB_ENV/GITHUB_OUTPUT files."""
    github_env = cwd / "github_env"
    github_output = cwd / "github_output"
    github_env.write_text("")
    github_output.write_text("")
    env = {
        **os.environ,
        "GITHUB_ENV": str(github_env),
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
    return result, github_env, github_output


def _parse_kv_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestDetectProducer:
    def test_compile_commands_json_at_root_means_replay(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_compile_commands_json_nested_means_replay(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "compile_commands.json").write_text("[]")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_cmakelists_means_replay(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_bazel_workspace_means_replay(self, tmp_path: Path) -> None:
        (tmp_path / "WORKSPACE").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_empty_tree_means_wrapper(self, tmp_path: Path) -> None:
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "wrapper"

    def test_plain_makefile_means_wrapper(self, tmp_path: Path) -> None:
        # A bare Makefile with no compile DB and no cmake/bazel project file
        # cannot be replayed without generating one first -- wrapper is the
        # safer default (never silently picks clang-plugin).
        (tmp_path / "Makefile").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "wrapper"


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestLlvmMajorParsing:
    @pytest.mark.parametrize(
        ("version_output", "expected"),
        [
            ("clang version 18.1.3", "18"),
            ("Ubuntu clang version 18.1.3-1ubuntu1", "18"),
            ("Debian clang version 16.0.6 (25)", "16"),
            ("Apple clang version 15.0.0 (clang-1500.3.9.4)", "15"),
            ("gcc (Ubuntu 13.2.0-4ubuntu3) 13.2.0", ""),
            ("", ""),
        ],
    )
    def test_parses_major(self, version_output: str, expected: str) -> None:
        result = _run_predicate(f'_llvm_major_from_version_string "{version_output}"')
        assert result.stdout.strip() == expected


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestPhaseNeedsExternalBuildStep:
    @pytest.mark.parametrize(
        ("phase", "producer", "expected"),
        [
            ("prepare", "wrapper", True),
            ("prepare", "clang-plugin", True),
            ("auto", "wrapper", True),
            ("prepare", "replay", False),
            ("auto", "replay", False),
            ("verify", "wrapper", False),
        ],
    )
    def test_matrix(self, phase: str, producer: str, expected: bool) -> None:
        result = _run_predicate(
            f'if _phase_needs_external_build_step "{phase}" "{producer}"; '
            "then echo true; else echo false; fi"
        )
        assert result.stdout.strip() == str(expected).lower()


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestInvalidInputsRejected:
    def test_unknown_producer_fails(self, tmp_path: Path) -> None:
        result, _, _ = _run_action({"INPUT_PRODUCER": "bogus"}, tmp_path)
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_unknown_phase_fails(self, tmp_path: Path) -> None:
        result, _, _ = _run_action({"INPUT_PHASE": "bogus"}, tmp_path)
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_missing_sources_dir_fails(self, tmp_path: Path) -> None:
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "replay",
                "INPUT_SOURCES": "/no/such/dir",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "does not exist" in result.stdout

    def test_clang_plugin_missing_compiler_fails_fast(self, tmp_path: Path) -> None:
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_COMPILER": "no-such-compiler-binary-xyz",
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "not found on PATH" in result.stdout


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestReplayProducer:
    def test_prepare_needs_no_collection_step(self, tmp_path: Path) -> None:
        sources = tmp_path / "src"
        sources.mkdir()
        (sources / "compile_commands.json").write_text("[]")
        result, _, github_output = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "replay",
                "INPUT_SOURCES": str(sources),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs = _parse_kv_file(github_output)
        assert outputs["producer"] == "replay"
        assert outputs["mode"] == "inline"
        assert outputs["pack-path"] == ""
        assert outputs["ready"] == "true"

    def test_auto_producer_with_compile_db_resolves_to_replay(
        self, tmp_path: Path
    ) -> None:
        sources = tmp_path / "src"
        sources.mkdir()
        (sources / "compile_commands.json").write_text("[]")
        result, _, github_output = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "auto",
                "INPUT_SOURCES": str(sources),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _parse_kv_file(github_output)["producer"] == "replay"

    def test_verify_on_replay_is_a_no_op_success(self, tmp_path: Path) -> None:
        sources = tmp_path / "src"
        sources.mkdir()
        result, _, github_output = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "replay",
                "INPUT_SOURCES": str(sources),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _parse_kv_file(github_output)["ready"] == "true"


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestWrapperProducer:
    def test_prepare_writes_env_vars(self, tmp_path: Path) -> None:
        sources = tmp_path / "src"
        sources.mkdir()
        result, github_env, github_output = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_SOURCES": str(sources),
                "INPUT_OUTPUT": str(tmp_path / "abicheck_inputs"),
                "INPUT_LIBRARY": "foo",
                "INPUT_PUBLIC_ROOTS": "include",
                "INPUT_EXTRACTOR": "clang",
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        env = _parse_kv_file(github_env)
        assert env["ABICHECK_INPUTS_DIR"] == str(tmp_path / "abicheck_inputs")
        assert env["ABICHECK_CC_EXTRACTOR"] == "clang"
        assert env["ABICHECK_CC_LIBRARY"] == "foo"
        assert env["ABICHECK_CC_HEADERS"] == "include"
        outputs = _parse_kv_file(github_output)
        assert outputs["producer"] == "wrapper"
        assert outputs["mode"] == "pack"
        assert outputs["ready"] == "false"
        # phase: prepare's job is done before the caller's own build step --
        # nothing here should claim readiness prematurely.

    def test_multiple_public_roots_joined_for_env_var(self, tmp_path: Path) -> None:
        result, github_env, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_PUBLIC_ROOTS": "include\ngen/include",
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            _parse_kv_file(github_env)["ABICHECK_CC_HEADERS"] == "include:gen/include"
        )

    def test_verify_fails_on_missing_pack_dir(self, tmp_path: Path) -> None:
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(tmp_path / "never-created"),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "no pack directory" in result.stdout

    def test_verify_fails_on_empty_pack_dir(self, tmp_path: Path) -> None:
        pack = tmp_path / "abicheck_inputs"
        pack.mkdir()
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(pack),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "empty" in result.stdout

    def test_verify_succeeds_on_populated_pack_dir(self, tmp_path: Path) -> None:
        pack = tmp_path / "abicheck_inputs"
        pack.mkdir()
        (pack / "foo.jsonl").write_text('{"kind": "function"}\n')
        result, _, github_output = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(pack),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs = _parse_kv_file(github_output)
        assert outputs["ready"] == "true"
        assert outputs["mode"] == "pack"
        assert outputs["pack-path"] == str(pack)
