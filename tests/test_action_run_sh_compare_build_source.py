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

"""Behavioral test: ``action/run.sh``'s compare-mode branch forwards
build/source evidence (Codex review, PR #625).

``sources``/``build-info``/``compile-db``/``build-config``/``depth`` were
only ever forwarded to the CLI in ``dump``/``scan`` mode -- a ``compare``
mode invocation (the normal, non-audit path ``actions/check-target`` uses
for a ``--depth build``/``source`` check) silently dropped all five, so the
underlying ``compare`` CLI call never received the evidence needed to
actually reach that depth, regardless of what the report envelope later
claimed was requested. This runs the real ``action/run.sh`` end-to-end
(not the CLI itself, which is a fake shell stub on ``$PATH`` capturing its
own argv) to prove the fix reaches the real command line, not just that the
scripted intent looks right on paper.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


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


def _run_compare_raw(
    env_extra: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the real run.sh in compare mode against a fake `abicheck` on
    $PATH that records its own argv; returns the raw result plus the path
    the argv would have been captured to (may not exist if run.sh exited
    before ever invoking the stub)."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'echo \'{"verdict":"COMPATIBLE"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    abicheck_stub.chmod(0o755)

    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_json),
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        **env_extra,
    }
    result = subprocess.run(
        [_bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    return result, captured


def _run_compare(env_extra: dict[str, str], tmp_path: Path) -> str:
    """Like _run_compare_raw, but asserts success and returns the captured
    command line."""
    result, captured = _run_compare_raw(env_extra, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip()


class TestCompareModeForwardsBuildSourceEvidence:
    def test_sources_and_depth_reach_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_SOURCES": "/src", "INPUT_DEPTH": "source"}, tmp_path)
        assert "--sources new=/src" in cmd
        assert "--depth source" in cmd

    def test_build_info_reaches_the_cli_scoped_to_new_side(
        self, tmp_path: Path
    ) -> None:
        cmd = _run_compare({"INPUT_BUILD_INFO": "/build"}, tmp_path)
        assert "--build-info new=/build" in cmd

    def test_compile_db_falls_back_when_build_info_unset(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_COMPILE_DB": "/compile_commands.json"}, tmp_path)
        assert "--build-info new=/compile_commands.json" in cmd

    def test_build_info_takes_precedence_over_compile_db(self, tmp_path: Path) -> None:
        cmd = _run_compare(
            {
                "INPUT_BUILD_INFO": "/build",
                "INPUT_COMPILE_DB": "/compile_commands.json",
            },
            tmp_path,
        )
        assert "--build-info new=/build" in cmd
        assert "compile_commands.json" not in cmd

    def test_build_config_reaches_the_cli_as_config(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_BUILD_CONFIG": "/cfg.yml"}, tmp_path)
        assert "--config /cfg.yml" in cmd

    def test_no_evidence_inputs_adds_no_flags(self, tmp_path: Path) -> None:
        cmd = _run_compare({}, tmp_path)
        assert "--sources" not in cmd
        assert "--build-info" not in cmd
        assert "--config" not in cmd
        assert "--depth" not in cmd


def _run_scan(env_extra: dict[str, str], tmp_path: Path) -> str:
    """Like _run_compare, but drives run.sh's scan-mode branch instead."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'echo \'{"scan_schema_version":"1.2","verdict":"COMPATIBLE","exit_code":0}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    abicheck_stub.chmod(0o755)

    artifact = tmp_path / "new.json"
    artifact.write_text("{}", encoding="utf-8")

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": str(artifact),
        "INPUT_ADD_JOB_SUMMARY": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        **env_extra,
    }
    result = subprocess.run(
        [_bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip()


class TestScanModeForwardsCrossCompilerFlags:
    """Same gap as compare mode above, in scan mode's branch (Codex
    review, PR #625)."""

    def test_all_four_reach_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_scan(
            {
                "INPUT_GCC_PATH": "/opt/cross/bin/aarch64-linux-gnu-g++",
                "INPUT_GCC_PREFIX": "aarch64-linux-gnu-",
                "INPUT_GCC_OPTIONS": "-D__ARM_NEON",
                "INPUT_SYSROOT": "/opt/sysroots/aarch64",
            },
            tmp_path,
        )
        assert "--gcc-path /opt/cross/bin/aarch64-linux-gnu-g++" in cmd
        assert "--gcc-prefix aarch64-linux-gnu-" in cmd
        assert "--gcc-options -D__ARM_NEON" in cmd
        assert "--sysroot /opt/sysroots/aarch64" in cmd


class TestCompareModeForwardsCrossCompilerFlags:
    """--gcc-path/--gcc-prefix/--gcc-options/--sysroot are documented root-
    Action inputs and both dump AND compare/scan support them at the CLI
    level, but were previously only wired into dump mode's branch --
    a cross-target compare/scan silently fell back to the host toolchain
    for header parsing and could produce false ABI results (Codex review,
    PR #625)."""

    def test_all_four_reach_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_compare(
            {
                "INPUT_GCC_PATH": "/opt/cross/bin/aarch64-linux-gnu-g++",
                "INPUT_GCC_PREFIX": "aarch64-linux-gnu-",
                "INPUT_GCC_OPTIONS": "-D__ARM_NEON",
                "INPUT_SYSROOT": "/opt/sysroots/aarch64",
            },
            tmp_path,
        )
        assert "--gcc-path /opt/cross/bin/aarch64-linux-gnu-g++" in cmd
        assert "--gcc-prefix aarch64-linux-gnu-" in cmd
        assert "--gcc-options -D__ARM_NEON" in cmd
        assert "--sysroot /opt/sysroots/aarch64" in cmd

    def test_none_set_adds_no_flags(self, tmp_path: Path) -> None:
        cmd = _run_compare({}, tmp_path)
        assert "--gcc-path" not in cmd
        assert "--gcc-prefix" not in cmd
        assert "--gcc-options" not in cmd
        assert "--sysroot" not in cmd


class TestCompareModeSkipsEvidenceFlagsForDirectoryOperands:
    """The CLI's per-library release fan-out (directory/package operands --
    e.g. check-target's kind: bundle) rejects --sources/--build-info/
    --depth outright (_reject_evidence_flags_for_set_inputs) -- forwarding
    them here would turn every bundle comparison into a hard usage error
    instead of running it (Codex review, PR #625). --config is NOT one of
    the rejected flags (_EVIDENCE_SET_INPUT_FLAGS lists only depth/sources/
    build_info) -- the release fan-out still consumes the project
    .abicheck.yml, so it must keep reaching the CLI even for a directory
    operand (Codex review, second round)."""

    def test_directory_new_library_gets_no_evidence_flags_but_keeps_config(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old-bundle"
        new_dir = tmp_path / "new-bundle"
        old_dir.mkdir()
        new_dir.mkdir()
        cmd = _run_compare(
            {
                "INPUT_OLD_LIBRARY": str(old_dir),
                "INPUT_NEW_LIBRARY": str(new_dir),
                "INPUT_BUILD_CONFIG": "/cfg.yml",
                "INPUT_DEPTH": "headers",
            },
            tmp_path,
        )
        assert "--sources" not in cmd
        assert "--build-info" not in cmd
        assert "--depth" not in cmd
        assert "--config /cfg.yml" in cmd

    def test_directory_old_library_alone_also_skips_evidence_flags(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old-bundle"
        old_dir.mkdir()
        new_json = tmp_path / "new.json"
        new_json.write_text("{}", encoding="utf-8")
        cmd = _run_compare(
            {
                "INPUT_OLD_LIBRARY": str(old_dir),
                "INPUT_NEW_LIBRARY": str(new_json),
                "INPUT_DEPTH": "headers",
            },
            tmp_path,
        )
        assert "--depth" not in cmd


class TestCompareModeFailsFastOnUnservableDirectoryEvidenceRequest:
    """A directory/package operand can never actually collect build/source
    evidence (the CLI's per-library release fan-out rejects it outright),
    so silently dropping a real evidence request there would let the
    comparison run without the evidence and still report a clean/normal
    result -- e.g. missing a source-only break. Must fail loud instead
    (Codex review, PR #625)."""

    def test_depth_source_against_directory_operand_fails(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "source"}, tmp_path
        )
        assert result.returncode != 0
        assert not captured.is_file(), "abicheck stub must never be invoked"

    def test_depth_build_against_directory_operand_fails(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "build"}, tmp_path
        )
        assert result.returncode != 0
        assert not captured.is_file()

    def test_explicit_sources_against_directory_operand_fails_even_without_depth(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_SOURCES": "/src"}, tmp_path
        )
        assert result.returncode != 0
        assert not captured.is_file()

    def test_explicit_build_info_against_directory_operand_fails(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_BUILD_INFO": "/build"}, tmp_path
        )
        assert result.returncode != 0
        assert not captured.is_file()

    def test_headers_depth_against_directory_operand_still_succeeds(
        self, tmp_path: Path
    ) -> None:
        """binary/headers never needed sources/build-info to begin with --
        nothing requested is actually unservable, so this must keep working,
        not regress into the new fail-fast path."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "headers"}, tmp_path
        )
        assert "--depth" not in cmd
