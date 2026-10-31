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

import yaml
from _workflow_exec import bash_executable, require_bash
from test_action_compile_set_forwarding import _run, _validate
from test_action_run_sh_set_input import _set_pairs

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


def _run_compare_raw(
    env_extra: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the real run.sh in compare mode against a fake `abicheck` on
    $PATH that records its own argv; returns the raw result plus the path
    the argv would have been captured to (may not exist if run.sh exited
    before ever invoking the stub)."""
    require_bash()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    # NUL-delimited argv: a `--set compile.options=[...]` value can carry a
    # space, which the space-joined capture above cannot separate.
    captured_argv0 = tmp_path / "captured_argv.nul"
    # Snapshots any --config <path> file's content while it still exists --
    # the real run.sh's own main EXIT trap removes it once the whole
    # process finishes, well before this test process ever gets to inspect
    # the original path (see _compile_overlay_from_cmd's own docstring).
    captured_config = tmp_path / "captured_config.json"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        f'printf \'%s\\0\' "$@" >> "{captured_argv0}"\n'
        'args=("$@")\n'
        "for ((_i = 0; _i < ${#args[@]}; _i++)); do\n"
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
        [bash_executable(), str(RUN_SH)],
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


class TestCompareModeSkipsEvidenceFlagsForDirectoryOperands:
    """The CLI's per-library release fan-out (directory/package operands --
    e.g. check-target's kind: bundle) rejects --sources/--build-info
    outright (_reject_evidence_flags_for_set_inputs) -- forwarding them
    here would turn every bundle comparison into a hard usage error
    instead of running it (Codex review, PR #625). --depth left that set
    (_EVIDENCE_SET_INPUT_FLAGS lists only sources/build_info/dump_manifest
    now): every rung of the public ladder is forwarded, with the floor
    enforced per member downstream. --config is NOT one of the rejected
    flags either -- the release fan-out still consumes the project
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
        assert "--depth headers" in cmd
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
        assert "--sources" not in cmd
        assert "--build-info" not in cmd
        assert "--depth headers" in cmd


class TestCompareModeFailsFastOnUnservableDirectoryEvidenceRequest:
    """A directory/package operand can never *collect inline* build/source
    evidence (the CLI's per-library release fan-out rejects
    --sources/--build-info/--dump-manifest outright), so silently dropping
    such a request would let the comparison run without the evidence and
    still report a clean/normal result -- e.g. missing a source-only break.
    Must fail loud instead (Codex review, PR #625).

    Scoped to those *inputs*, never to a ``--depth`` rung (Codex review, PR
    #1233): a member may itself be a pre-dumped snapshot carrying embedded
    L3/L4/L5 evidence, which satisfies ``build``/``source`` with no inline
    collection at all, so rejecting those rungs here denied a genuinely
    reachable configuration -- the same stale-guard defect this PR removes
    elsewhere. See ``test_action_run_sh_release_capability_parity.py``."""

    def test_depth_source_against_directory_operand_is_forwarded(
        self, tmp_path: Path
    ) -> None:
        """Reachability is a property of the members, not of the operand's
        cardinality: a directory of pre-dumped snapshots reaches ``source``,
        and a member that cannot says so as its own error downstream."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "source"}, tmp_path
        )
        assert "--depth source" in cmd

    def test_depth_build_against_directory_operand_is_forwarded(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "build"}, tmp_path
        )
        assert "--depth build" in cmd

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
        not regress into the new fail-fast path. It is now *forwarded*
        rather than dropped: the CLI rejects no rung on this path any more
        (`_resolve_depth_for_set_inputs`), so the rung a workflow pinned is
        the rung each member is held to."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "headers"}, tmp_path
        )
        assert "--depth headers" in cmd


class TestCompareModeDirectoryDepthAsymmetry:
    """The CLI accepts every rung of the public ladder for a
    directory/package operand (``_resolve_depth_for_set_inputs`` rejects
    none: the floor is enforced per member by ``enforce_requested_depth``
    and the ceiling by ``policy.depth_projection``), so the Action forwards
    the rung verbatim rather than dropping it -- every rung, ``build`` and
    ``source`` included, since a member may be a pre-dumped snapshot that
    already carries the evidence. What fails loud here is the inline
    evidence *inputs* (``--sources``/``--build-info``/``--compile-db``,
    rejected by ``_reject_evidence_flags_for_set_inputs``), never a rung.
    The ``headers`` rung's former ``::notice::``-and-drop is gone with the
    CLI restriction it restated -- see
    ``tests/test_action_run_sh_release_capability_parity.py`` for the
    class-level invariant."""

    def test_depth_binary_against_directory_operand_is_forwarded(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "binary"}, tmp_path
        )
        assert "--depth binary" in cmd

    def test_depth_headers_against_directory_operand_is_forwarded(
        self, tmp_path: Path
    ) -> None:
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        result, captured = _run_compare_raw(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "headers"}, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "does not honour --depth headers" not in result.stdout
        cmd = captured.read_text(encoding="utf-8").strip()
        assert "--depth headers" in cmd

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

    def test_depth_build_uppercase_is_lowercased_and_forwarded(
        self, tmp_path: Path
    ) -> None:
        """Case-insensitive like every other rung -- INPUT_DEPTH is a raw
        Action input and the CLI's own ``DepthParam.convert()`` accepts
        every case variant, so no spelling may be the one that drops it."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "BUILD"}, tmp_path
        )
        assert "--depth build" in cmd

    def test_depth_headers_uppercase_is_lowercased_and_forwarded(
        self, tmp_path: Path
    ) -> None:
        """Same case-insensitivity contract as ``binary`` above: INPUT_DEPTH
        is a raw Action input and the CLI's own ``DepthParam.convert()``
        accepts every case variant, so the forwarded token is the
        lowercased rung."""
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd = _run_compare(
            {"INPUT_NEW_LIBRARY": str(new_dir), "INPUT_DEPTH": "Headers"}, tmp_path
        )
        assert "--depth headers" in cmd


def _run_baseline_compare_raw(
    env_extra: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Like ``_run_compare_raw``, but drives ``run.sh``'s two-sided
    ``mode: compare`` branch with a real old-library operand by default --
    pass ``{"INPUT_OLD_LIBRARY": ""}`` in ``env_extra`` to instead exercise
    the audit-only shape (old-library omitted). Returns the raw
    result, the captured-argv path, and the captured-config path (mirrors
    ``_compile_overlay_from_cmd``'s own "snapshot the overlay file's
    content while run.sh is still running" rationale)."""
    require_bash()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    captured_config = tmp_path / "captured_config.json"
    captured_argv0 = tmp_path / "captured_argv.nul"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        f'printf \'%s\\0\' "$@" >> "{captured_argv0}"\n'
        'args=("$@")\n'
        "for ((_i = 0; _i < ${#args[@]}; _i++)); do\n"
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

    new_json = tmp_path / "new.json"
    against_json = tmp_path / "baseline.so"
    new_json.write_text("{}", encoding="utf-8")
    against_json.write_text("", encoding="utf-8")

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_OLD_LIBRARY": str(against_json),
        "INPUT_DEPTH": "headers",
        "INPUT_EXTRA_ARGS": "--pattern-verdicts",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        **env_extra,
    }
    # An explicitly empty old-library in env_extra (audit-only shape)
    # overrides the real default above -- a real "" value, not absence.
    if env.get("INPUT_OLD_LIBRARY") == "":
        del env["INPUT_OLD_LIBRARY"]
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    return result, captured, captured_config


class TestBaselineCompareTranslationPreservesMergedConfig:
    """Codex review, PR #1172, round 21, fresh evidence: a two-sided
    ``mode: compare`` request that ALSO supplies both ``build-config`` and
    a dedicated compile-context input (``gcc-path`` here) used to emit two
    conflicting ``--config`` tokens on the assembled command line --
    ``add_compile_context_flags`` above already merges ``build-config``
    into its own synthesized ``compile:`` overlay and appends ``--config``
    itself, but the branch's own unconditional
    ``add_single_flag "--config" "$INPUT_BUILD_CONFIG"`` right after it
    appended a second, raw ``--config $INPUT_BUILD_CONFIG`` -- Click keeps
    only the last occurrence, so the synthesized compiler/sysroot/macros
    overlay was silently discarded in favor of the operator's *original*,
    unmerged file. Same guard (``_cmd_has_config_flag``) the native
    ``dump``/``compare`` branches already carry around their own identical
    ``add_single_flag "--config"`` call."""

    def test_build_config_alone_still_forwards_it_unmerged(
        self, tmp_path: Path
    ) -> None:
        """Negative control: no compile-context input, only build-config --
        add_compile_context_flags's early-return means no synthesized
        overlay is created at all, so the guarded append must still fire
        and forward the raw build-config path directly (this branch's
        pre-existing, correct behavior for the no-collision case)."""
        build_config = tmp_path / "cfg.yml"
        build_config.write_text("release:\n  dso_only: true\n", encoding="utf-8")
        result, captured, _captured_config = _run_baseline_compare_raw(
            {"INPUT_BUILD_CONFIG": str(build_config)}, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cmd = captured.read_text(encoding="utf-8").strip()
        assert f"--config {build_config}" in cmd
        assert cmd.count("--config") == 1, cmd


def _compile_block(tmp_path: Path) -> dict[str, Any]:
    """The ``compile:`` block the captured argv's ``--set compile.K=V`` tokens
    describe (ADR-091 D4). The stub records argv NUL-delimited, so a value with
    a space (``compile.options=["-DMSG=hello world"]``) stays one token; the
    ``compile.`` prefix is stripped and each value parsed as JSON, the same
    shape the old overlay's ``compile`` mapping had."""
    captured_argv0 = tmp_path / "captured_argv.nul"
    assert captured_argv0.is_file(), "the abicheck stub never captured argv"
    argv = [
        tok for tok in captured_argv0.read_bytes().decode("utf-8").split("\0") if tok
    ]
    block: dict[str, Any] = {}
    for index, token in enumerate(argv):
        if token == "--set" and argv[index + 1].startswith("compile."):
            key, _, raw = argv[index + 1].partition("=")
            block[key.removeprefix("compile.")] = json.loads(raw)
    return block


class TestAuditOnlyCompareForwardsCrossCompilerFlags:
    """The live compile-context inputs reach the audit-only shape as ``--set``
    pairs (ADR-091 D4). ``compile-options`` is RETIRED (ADR-091 D7) and is
    refused by the validator instead."""

    def test_live_inputs_reach_the_cli_as_set_pairs(self, tmp_path: Path) -> None:
        argv = _run(
            tmp_path,
            "compare",
            INPUT_OLD_LIBRARY="",
            INPUT_COMPILER="/opt/cross/bin/aarch64-linux-gnu-g++",
            INPUT_SYSROOT="/opt/sysroots/aarch64",
        )
        assert "--no-baseline" in argv
        pairs = _set_pairs(argv)
        assert 'compile.compiler="/opt/cross/bin/aarch64-linux-gnu-g++"' in pairs
        assert 'compile.sysroot="/opt/sysroots/aarch64"' in pairs
        assert "--config" not in argv

    def test_retired_compile_options_is_refused_on_the_audit_shape(self) -> None:
        result = _validate(INPUT_COMPILE_OPTIONS="-D__ARM_NEON")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "compile-options" in result.stdout
        assert "set: compile.options=" in result.stdout


class TestCompareModeForwardsCrossCompilerFlags:
    """Two-sided compare forwards the live cross-compiler inputs as ``--set``."""

    def test_live_inputs_reach_the_cli_as_set_pairs(self, tmp_path: Path) -> None:
        argv = _run(
            tmp_path,
            "compare",
            INPUT_COMPILER="/opt/cross/bin/aarch64-linux-gnu-g++",
            INPUT_SYSROOT="/opt/sysroots/aarch64",
        )
        pairs = _set_pairs(argv)
        assert 'compile.compiler="/opt/cross/bin/aarch64-linux-gnu-g++"' in pairs
        assert 'compile.sysroot="/opt/sysroots/aarch64"' in pairs
        assert "--config" not in argv

    def test_none_set_adds_no_flags(self, tmp_path: Path) -> None:
        argv = _run(tmp_path, "compare")
        assert [p for p in _set_pairs(argv) if p.startswith("compile.")] == []
        assert "--config" not in argv

    def test_retired_compile_options_is_refused(self) -> None:
        result = _validate(INPUT_COMPILE_OPTIONS='-DMSG="hello world"')
        assert result.returncode != 0, result.stdout + result.stderr
        assert "set: compile.options=" in result.stdout


class TestBaselineCompareTranslationForwardsCompileSets:
    """Codex review, PR #1172, round 21: a two-sided compare that supplies both
    ``build-config`` and a compile-context input must carry exactly one
    ``--config`` (the user's file). A second, duplicate ``--config`` made Click
    keep only the last one, so the compile context was dropped. Under ADR-091 D4
    the compile context travels as ``--set``, so the same guard keeps one
    ``--config`` and the sets alongside it."""

    def test_compile_context_overlay_survives_the_translated_command_line(
        self, tmp_path: Path
    ) -> None:
        build_config = tmp_path / "cfg.yml"
        build_config.write_text("release:\n  dso_only: true\n", encoding="utf-8")
        result, captured, captured_config = _run_baseline_compare_raw(
            {
                "INPUT_BUILD_CONFIG": str(build_config),
                "INPUT_COMPILER": "/opt/cross/bin/aarch64-linux-gnu-g++",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert captured.is_file(), "abicheck stub was never invoked"
        tokens = captured.read_text(encoding="utf-8").split()
        assert "compare" in tokens
        assert tokens.count("--config") == 1, tokens
        assert tokens[tokens.index("--config") + 1] == str(build_config)
        assert (
            _compile_block(tmp_path)["compiler"]
            == "/opt/cross/bin/aarch64-linux-gnu-g++"
        )
        # The build-config's own release setting still reaches the CLI in that file.
        assert captured_config.is_file()
        assert (
            yaml.safe_load(captured_config.read_text(encoding="utf-8"))["release"][
                "dso_only"
            ]
            is True
        )

    def test_compile_context_alone_still_forwards_compile_sets(
        self, tmp_path: Path
    ) -> None:
        """Negative control: a compile-context input with no build-config still
        reaches the command as a set, and no ``--config`` is synthesized."""
        result, captured, _captured_config = _run_baseline_compare_raw(
            {"INPUT_COMPILER": "/opt/cross/bin/aarch64-linux-gnu-g++"}, tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--config" not in captured.read_text(encoding="utf-8").split()
        assert (
            _compile_block(tmp_path)["compiler"]
            == "/opt/cross/bin/aarch64-linux-gnu-g++"
        )
