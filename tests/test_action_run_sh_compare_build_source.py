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

import json
import os
import subprocess
from pathlib import Path
from typing import Any

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


def _compile_overlay_from_cmd(cmd: str, tmp_path: Path) -> dict[str, Any]:
    """Read back the synthesized ``compile:`` block a ``--config <path>``
    token in *cmd* points at (Phase 7: dump/single-pair compare forward the
    cross-compiler inputs via a synthesized config overlay now, not
    individually-forwarded ``--compiler``/``--sysroot``/... flags -- see
    ``add_compile_context_flags`` in ``action/run.sh``).

    Reads the fake ``abicheck`` stub's own COPY of that file
    (``captured_config.json``, made by ``_run_compare_raw`` while the real
    ``run.sh`` process was still running), not the original path itself:
    the overlay is created under ``$RUNNER_TEMP`` and cleaned up by
    ``run.sh``'s own main ``EXIT`` trap once the script finishes, so by the
    time this test process inspects the *original* path (well after
    ``subprocess.run`` returns) it has already been removed -- the same
    "the leak this session fixed is now correctly closed, so a post-exit
    read of the original file no longer works" class this session's other
    harness-based tests hit."""
    tokens = cmd.split()
    assert "--config" in tokens, cmd
    captured_config = tmp_path / "captured_config.json"
    assert captured_config.is_file(), (
        "the fake abicheck stub never captured a --config file's content"
    )
    with open(captured_config, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("compile", {})


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
    # Snapshots any --config <path> file's content while it still exists --
    # the real run.sh's own main EXIT trap removes it once the whole
    # process finishes, well before this test process ever gets to inspect
    # the original path (see _compile_overlay_from_cmd's own docstring).
    captured_config = tmp_path / "captured_config.json"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'args=("$@")\n'
        'for ((_i = 0; _i < ${#args[@]}; _i++)); do\n'
        '  if [[ "${args[_i]}" == "--config" ]]; then\n'
        f'    cp "${{args[$((_i + 1))]}}" "{captured_config}" 2>/dev/null || true\n'
        "    break\n"
        "  fi\n"
        "done\n"
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


class TestCompareModeForwardsChangeFocusInputs:
    """``since``/``changed-path`` were only ever forwarded to the CLI in
    scan mode's branch -- github-action-source-scans.md already documents
    ``mode: compare`` as taking "the identical depth/since/changed-path/
    sources/build-info inputs mode: scan does", but the compare branch
    silently dropped both: ``since:``'s scope-narrowing value was ignored
    and a pinned ``depth: source`` replayed the whole target instead of the
    PR's changed files (Codex review, fresh evidence)."""

    def test_since_reaches_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_SINCE": "origin/main"}, tmp_path)
        assert "--since origin/main" in cmd

    def test_changed_path_reaches_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_CHANGED_PATH": "src/foo.cpp"}, tmp_path)
        assert "--changed-path src/foo.cpp" in cmd

    def test_neither_input_adds_no_flags(self, tmp_path: Path) -> None:
        cmd = _run_compare({}, tmp_path)
        assert "--since" not in cmd
        assert "--changed-path" not in cmd


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
        assert "--compiler /opt/cross/bin/aarch64-linux-gnu-g++" in cmd
        assert "--compiler-prefix aarch64-linux-gnu-" in cmd
        assert "--compiler-option -D__ARM_NEON" in cmd
        assert "--sysroot /opt/sysroots/aarch64" in cmd


class TestCompareModeForwardsCrossCompilerFlags:
    """The gcc-path/gcc-prefix/gcc-options/sysroot inputs are documented root-
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
        compile_blk = _compile_overlay_from_cmd(cmd, tmp_path)
        # gcc_path wins over gcc_prefix when both are given (the merged
        # compile.compiler field can only hold one).
        assert compile_blk["compiler"] == "/opt/cross/bin/aarch64-linux-gnu-g++"
        assert compile_blk["options"] == ["-D__ARM_NEON"]
        assert compile_blk["sysroot"] == "/opt/sysroots/aarch64"

    def test_none_set_adds_no_flags(self, tmp_path: Path) -> None:
        cmd = _run_compare({}, tmp_path)
        assert "--config" not in cmd
        assert "--compiler" not in cmd
        assert "--compiler-prefix" not in cmd
        assert "--compiler-option" not in cmd
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


class TestCompareModeDirectoryDepthAsymmetry:
    """D1/D2: the CLI now accepts an explicit ``--depth binary`` for a
    directory/package operand (it requests strictly less evidence than the
    per-library fan-out already collects by default, so there is nothing
    about it the fan-out can't provide) -- the Action must forward it rather
    than drop it. ``--depth headers`` is still rejected by the CLI on this
    path (no per-library evidence-floor enforcement yet), so it stays
    dropped, but now with a visible ``::notice::`` instead of vanishing
    silently (the prior behaviour, asymmetric with the neighbouring
    compile-context guard, which always fails loud for what it can't
    honour)."""

    def test_depth_binary_against_directory_operand_is_forwarded(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "binary"}, tmp_path
        )
        assert "--depth binary" in cmd

    def test_depth_headers_against_directory_operand_emits_notice(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "headers"}, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::notice::" in result.stdout
        assert "--depth headers" in result.stdout
        cmd = captured.read_text(encoding="utf-8").strip()
        assert "--depth" not in cmd

    def test_depth_binary_uppercase_is_still_forwarded(self, tmp_path: Path) -> None:
        """Codex review, PR #1016: INPUT_DEPTH is a raw, unvalidated Action
        input string, and the CLI's own DepthParam.convert() accepts every
        case variant -- a case-sensitive bash comparison here previously
        matched none of them, silently dropping `depth: BINARY` with no
        forwarding and no ::notice:: either."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "BINARY"}, tmp_path
        )
        assert "--depth binary" in cmd

    def test_depth_build_uppercase_against_directory_operand_still_fails(
        self, tmp_path: Path
    ) -> None:
        """The fail-loud guard for build/source must not be case-sensitive
        either -- a case mismatch there would silently skip the guard
        entirely (defeating its purpose) rather than merely dropping the
        flag, letting the comparison run without the requested evidence."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "BUILD"}, tmp_path
        )
        assert result.returncode != 0
        assert not captured.is_file(), "abicheck stub must never be invoked"

    def test_depth_headers_uppercase_still_emits_notice(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "Headers"}, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::notice::" in result.stdout
        assert "--depth headers" in result.stdout
        cmd = captured.read_text(encoding="utf-8").strip()
        assert "--depth" not in cmd
