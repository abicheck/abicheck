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

    def test_compile_commands_json_two_levels_deep_means_wrapper(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): a compile_commands.json two levels
        # below sources (e.g. sub/build/compile_commands.json) used to
        # still report producer=replay/ready=true here, but the inline
        # replay path this producer hands off to
        # (abicheck/buildsource/inline.py::_find_compile_db_in_dir) only
        # ever looks at the root or one immediate subdirectory -- it would
        # never find this DB, so the actual collection step would silently
        # collect zero source facts despite the false ready=true.
        deep_dir = tmp_path / "sub" / "build"
        deep_dir.mkdir(parents=True)
        (deep_dir / "compile_commands.json").write_text("[]")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "wrapper"

    def test_cmakelists_means_replay(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_bazel_workspace_means_replay(self, tmp_path: Path) -> None:
        (tmp_path / "WORKSPACE").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_bazel_bzlmod_module_file_means_replay(self, tmp_path: Path) -> None:
        # Regression (Codex review): bzlmod-only Bazel 6+ projects have no
        # WORKSPACE file at all, only MODULE.bazel -- omitting it here left
        # auto-detection inconsistent with abicheck/buildsource/
        # build_query.py's own Bazel marker set, which the replay path
        # actually queries.
        (tmp_path / "MODULE.bazel").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_empty_tree_means_wrapper(self, tmp_path: Path) -> None:
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "wrapper"

    def test_plain_makefile_means_replay(self, tmp_path: Path) -> None:
        # Regression (Codex review): this used to assert "wrapper" here, on
        # the assumption a bare Make/EPICS-style tree with no
        # compile_commands.json couldn't be replayed. But
        # abicheck/buildsource/build_query.py's inline replay path
        # auto-runs `make -B -n -k -w` and scrapes compile commands from
        # the transcript, so Make projects are replay-capable the same way
        # cmake/bazel ones are -- the bash heuristic was stale and
        # unnecessarily required wrapper instrumentation for these builds.
        (tmp_path / "Makefile").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_makefile_variant_names_mean_replay(self, tmp_path: Path) -> None:
        # GNUmakefile and lowercase makefile are the other two names
        # build_query.py's marker set recognizes alongside Makefile.
        (tmp_path / "GNUmakefile").write_text("")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"

    def test_many_nested_compile_dbs_still_means_replay(self, tmp_path: Path) -> None:
        # Regression (Codex review): the nested-compile-DB check used to be
        # `find ... | grep -q .`. Under this script's `set -o pipefail`, a
        # tree with enough matches/entries that find is still writing when
        # grep exits right after its first match gets find killed with
        # SIGPIPE (exit 141) -- pipefail turns that into the pipeline's
        # exit status even though grep itself matched, so the `if` silently
        # took the false branch and fell through to "wrapper" instead of
        # "replay". A small tree (like the sibling test above) essentially
        # never triggers the race; this needs enough entries that find's
        # write volume actually exceeds a pipe buffer while it is walking.
        for i in range(2000):
            d = tmp_path / f"dir{i}"
            d.mkdir()
            (d / "compile_commands.json").write_text("[]")
        result = _run_predicate(f'_detect_producer "{tmp_path}"')
        assert result.stdout.strip() == "replay"


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestIsAbsolutePath:
    """Regression guard (Codex review): OUTPUT/public-roots absolute-path
    resolution only recognized a leading '/', so an already-absolute
    Windows-style path (a drive letter, or a UNC share) was treated as
    relative and got $(pwd)/ nonsensically prepended -- e.g. output:
    'C:\\Users\\foo\\abicheck_inputs' became
    '/d/a/repo/C:\\Users\\foo\\abicheck_inputs'. This can be reproduced with
    Windows-style strings on a Linux test runner since it is pure string
    matching, no real Windows filesystem semantics involved."""

    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/abicheck_inputs",
            r"C:\Users\foo\abicheck_inputs",
            "D:/a/repo/abicheck_inputs",
            r"\\server\share\abicheck_inputs",
        ],
    )
    def test_recognizes_absolute_paths(self, path: str) -> None:
        # Single-quoted in the generated bash source (not double-quoted):
        # double quotes would collapse a UNC path's leading \\ to a single
        # \ before the argument even reaches _is_absolute_path, corrupting
        # the very prefix this test means to check. None of these fixture
        # paths contain a single quote.
        result = _run_predicate(
            f"if _is_absolute_path '{path}'; then echo true; else echo false; fi"
        )
        assert result.stdout.strip() == "true"

    @pytest.mark.parametrize("path", ["abicheck_inputs", "gen/include", "include"])
    def test_rejects_relative_paths(self, path: str) -> None:
        result = _run_predicate(
            f"if _is_absolute_path '{path}'; then echo true; else echo false; fi"
        )
        assert result.stdout.strip() == "false"

    def test_resolve_public_root_passes_through_windows_absolute_path(self) -> None:
        result = _run_predicate(r"_resolve_public_root 'C:\Users\foo\include'")
        assert result.stdout.strip() == r"C:\Users\foo\include"


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

    def test_auto_producer_with_missing_sources_dir_fails(self, tmp_path: Path) -> None:
        # Regression (Codex review): with producer: auto, a misspelled/
        # missing sources path looked identical to "no compile database
        # found here" to _detect_producer, which silently resolved to
        # wrapper instead of erroring -- a workflow expecting replay from a
        # real compile-DB tree would silently get wrapper's very different
        # setup instead of a loud failure about the bad path.
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "auto",
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

    def test_unknown_extractor_fails(self, tmp_path: Path) -> None:
        result, _, _ = _run_action({"INPUT_EXTRACTOR": "bogus"}, tmp_path)
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_unknown_install_deps_value_fails(self, tmp_path: Path) -> None:
        # Regression (CodeRabbit review): a misspelled value like "True" or
        # "yes" used to silently behave as false (every check is a literal
        # `== "true"` string comparison) instead of being rejected.
        result, _, _ = _run_action({"INPUT_INSTALL_DEPS": "yes"}, tmp_path)
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_verify_with_producer_auto_fails(self, tmp_path: Path) -> None:
        # Regression (CodeRabbit review): re-running auto-detection at
        # phase: verify can silently resolve to a different producer than
        # what phase: prepare resolved (e.g. the build step generated a
        # compile_commands.json as a side effect, flipping auto from
        # wrapper to replay) -- verify must be given the exact
        # prepare-resolved producer, never producer: auto.
        result, _, _ = _run_action(
            {"INPUT_PHASE": "verify", "INPUT_PRODUCER": "auto"}, tmp_path
        )
        assert result.returncode == 1
        assert "producer: auto" in result.stdout


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
        # Resolved to absolute (Codex review): abicheck-cc runs with cwd set
        # to the build directory in the documented recipe, not this
        # script's cwd, so a relative root must be resolved here first.
        assert env["ABICHECK_CC_HEADERS"] == str(tmp_path / "include")
        outputs = _parse_kv_file(github_output)
        assert outputs["producer"] == "wrapper"
        assert outputs["mode"] == "pack"
        assert outputs["ready"] == "false"
        # phase: prepare's job is done before the caller's own build step --
        # nothing here should claim readiness prematurely.

    def test_prepare_exports_absolute_inputs_dir(self, tmp_path: Path) -> None:
        # Regression (Codex review): the documented CMake compiler-launcher
        # recipe invokes abicheck-cc with cwd set to the *build* directory,
        # not this script's own cwd -- a relative ABICHECK_INPUTS_DIR/out=
        # (e.g. the documented default "abicheck_inputs") would then resolve
        # under build/ instead of the top-level pack _reset_output_dir/
        # phase: verify/pack-path all reference, so verification would
        # report an empty pack even though the build was instrumented.
        result, github_env, github_output = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": "abicheck_inputs",  # relative, the documented default
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        env = _parse_kv_file(github_env)
        assert env["ABICHECK_INPUTS_DIR"] == str(tmp_path / "abicheck_inputs")
        outputs = _parse_kv_file(github_output)
        assert outputs["pack-path"] == str(tmp_path / "abicheck_inputs")

    def test_prepare_clears_stale_output_dir(self, tmp_path: Path) -> None:
        # Regression (Codex review): init_inputs_pack() is idempotent across
        # repeated per-TU calls *within one build*, so a stale pack left
        # over from an earlier prepare/build/verify cycle (a reused
        # workspace, or two cycles sharing the default abicheck_inputs
        # path) used to survive a bare `mkdir -p` -- its old
        # source_facts/*.jsonl TU records would make a later verify see a
        # nonzero TU count and report ready=true even if this run's build
        # never actually invoked abicheck-cc.
        output = tmp_path / "abicheck_inputs"
        output.mkdir()
        (output / "manifest.json").write_text('{"kind": "abicheck_inputs"}')
        stale_facts = output / "source_facts"
        stale_facts.mkdir()
        stale_record = stale_facts / "stale.jsonl"
        stale_record.write_text('{"stale": true}\n')

        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(output),
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not stale_record.exists()

    def test_prepare_does_not_recursively_delete_unrelated_output_dir(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): output is a user-controlled Action
        # input -- the earlier stale-pack-clearing fix (test above) used a
        # blind `rm -rf "$OUTPUT"`, so a workflow that accidentally pointed
        # output: at an existing non-pack directory (the workspace root, a
        # shared build/source directory) would have this step recursively
        # delete whatever was there. Only the two known pack-content items
        # (manifest.json, source_facts/) should ever be removed.
        output = tmp_path / "abicheck_inputs"
        output.mkdir()
        unrelated_file = output / "important.txt"
        unrelated_file.write_text("do not delete me\n")
        unrelated_dir = output / "unrelated_dir"
        unrelated_dir.mkdir()
        (unrelated_dir / "also_important.txt").write_text("keep\n")

        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(output),
                "INPUT_INSTALL_DEPS": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert unrelated_file.read_text() == "do not delete me\n"
        assert (unrelated_dir / "also_important.txt").read_text() == "keep\n"

    def test_multiple_public_roots_joined_for_env_var(self, tmp_path: Path) -> None:
        # Regression: this runs the real system uname (no stub, unlike the
        # Windows-simulation test below), so on an actual windows-latest CI
        # runner (Git Bash reports uname -s as MINGW64_NT-...) run.sh's own
        # platform detection correctly joins with ';', not ':' -- a
        # hardcoded ':' expectation here failed the real Windows CI lane
        # even though the separator was correct for that platform. Expect
        # whatever the real OS's separator is, same as cc_wrapper.py's own
        # os.pathsep-based split.
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
        # Each root resolved to absolute (Codex review) before joining --
        # see test_prepare_writes_env_vars for why.
        expected = os.pathsep.join(
            [str(tmp_path / "include"), str(tmp_path / "gen/include")]
        )
        assert _parse_kv_file(github_env)["ABICHECK_CC_HEADERS"] == expected

    def test_multiple_public_roots_joined_with_semicolon_on_windows(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): cc_wrapper.py splits ABICHECK_CC_HEADERS
        # with Python's os.pathsep, which is ';' on Windows and ':'
        # everywhere else -- a hardcoded ':' join glued every root into one
        # unsplit string on a Windows runner instead of scoping to each of
        # them. Stubs `uname` (via a PATH-prepended fake binary) to report a
        # Windows-style value, since this test environment is Linux.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        uname_stub = fake_bin / "uname"
        uname_stub.write_text('#!/bin/sh\necho "MINGW64_NT-10.0-x86_64"\n')
        uname_stub.chmod(0o755)
        result, github_env, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_PUBLIC_ROOTS": "include\ngen/include",
                "INPUT_INSTALL_DEPS": "false",
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        expected = f"{tmp_path / 'include'};{tmp_path / 'gen/include'}"
        assert _parse_kv_file(github_env)["ABICHECK_CC_HEADERS"] == expected

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

    def test_verify_fails_on_manifest_only_pack_with_no_tu_records(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review): init_inputs_pack() writes manifest.json
        up front, before any TU is appended, so a build that never actually
        routed through the wrapper/plugin (or whose every extraction failed)
        still leaves a nonempty pack directory -- the old file-count check
        alone would have called this pack "ready"."""
        pytest.importorskip("abicheck")
        from abicheck.buildsource.inputs_emit import init_inputs_pack

        pack = tmp_path / "abicheck_inputs"
        init_inputs_pack(pack, library="libfoo")
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_OUTPUT": str(pack),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "zero" in result.stdout and "TU record" in result.stdout

    def test_verify_succeeds_on_populated_pack_dir(self, tmp_path: Path) -> None:
        pytest.importorskip("abicheck")
        from abicheck.buildsource.inputs_emit import (
            append_source_facts,
            init_inputs_pack,
        )
        from abicheck.buildsource.source_abi import SourceAbiTu

        pack = tmp_path / "abicheck_inputs"
        init_inputs_pack(pack, library="libfoo")
        append_source_facts(
            pack,
            [
                SourceAbiTu(
                    tu_id="cu://src/foo.cpp",
                    target_id="target://libfoo",
                    source="src/foo.cpp",
                )
            ],
        )
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

    def test_verify_reemits_producer_version_persisted_by_prepare(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): phase: verify used to
        # unconditionally emit producer-version="" regardless of producer,
        # clearing the clang-plugin identity phase: prepare had already
        # computed and persisted via GITHUB_ENV (ABICHECK_PRODUCER_VERSION
        # -- the same env-var persistence mechanism ABICHECK_PLUGIN_SO/
        # FLAGS already rely on to cross the prepare -> build -> verify
        # step boundary in a real workflow). A real clang-plugin build
        # needs a matching libclang-<N>-dev toolchain not guaranteed here,
        # so this simulates the persisted env var directly rather than
        # exercising phase: prepare, producer: clang-plugin end-to-end.
        pytest.importorskip("abicheck")
        from abicheck.buildsource.inputs_emit import (
            append_source_facts,
            init_inputs_pack,
        )
        from abicheck.buildsource.source_abi import SourceAbiTu

        pack = tmp_path / "abicheck_inputs"
        init_inputs_pack(pack, library="libfoo")
        append_source_facts(
            pack,
            [
                SourceAbiTu(
                    tu_id="cu://src/foo.cpp",
                    target_id="target://libfoo",
                    source="src/foo.cpp",
                )
            ],
        )
        result, _, github_output = _run_action(
            {
                "INPUT_PHASE": "verify",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_OUTPUT": str(pack),
                "ABICHECK_PRODUCER_VERSION": "llvm-18+plugin-sha256-abc123def456",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs = _parse_kv_file(github_output)
        assert outputs["producer-version"] == "llvm-18+plugin-sha256-abc123def456"

    def test_verify_producer_version_empty_for_wrapper(self, tmp_path: Path) -> None:
        # The wrapper producer has no ABICHECK_PRODUCER_VERSION to persist
        # (nothing about which compiler the caller's build actually
        # invoked is knowable from this side) -- verify must not invent one.
        pytest.importorskip("abicheck")
        from abicheck.buildsource.inputs_emit import (
            append_source_facts,
            init_inputs_pack,
        )
        from abicheck.buildsource.source_abi import SourceAbiTu

        pack = tmp_path / "abicheck_inputs"
        init_inputs_pack(pack, library="libfoo")
        append_source_facts(
            pack,
            [
                SourceAbiTu(
                    tu_id="cu://src/foo.cpp",
                    target_id="target://libfoo",
                    source="src/foo.cpp",
                )
            ],
        )
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
        assert outputs["producer-version"] == ""


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestClangPluginSmokeTestIsolation:
    """Regression guard (Codex review): the plugin smoke-test compile used
    to pass `out=$OUTPUT` -- the real pack directory the caller's build
    populates next -- so a smoke-test record could sit in the pack and mask
    a real collection failure (phase: verify only checks "the pack has at
    least one file"). The smoke compile must target an isolated scratch
    directory instead. This can't be exercised end-to-end without a real
    matching libclang-<N>-dev toolchain, so assert the invariant statically
    against the actual _prepare_clang_plugin source instead."""

    def test_smoke_compile_never_targets_output_dir(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        # Isolate just the smoke-test compiler invocation (between the
        # smoke.cpp printf and its own "smoke test passed" echo) -- the
        # *real* plugin_flags built further down legitimately reference
        # $OUTPUT for the caller's actual build, so a whole-function check
        # would false-positive on that unrelated, correct line.
        start = text.index("printf 'int abicheck_smoke_test")
        end = text.index("smoke test passed", start)
        smoke_block = text[start:end]
        assert "out=$smoke_out" in smoke_block, (
            "smoke-test compile must target the isolated $smoke_out scratch dir"
        )
        assert "out=$OUTPUT" not in smoke_block, (
            "smoke-test compile must not write into $OUTPUT -- the real pack "
            "directory -- or a smoke record could mask a real collection "
            "failure at phase: verify"
        )

    def test_prepare_resets_output_dir_before_use(self) -> None:
        # Regression (Codex review): same stale-pack-survives-mkdir-p issue
        # as the wrapper producer's end-to-end test above, but
        # _prepare_clang_plugin needs a real matching libclang-<N>-dev
        # toolchain to run at all, so assert the invariant statically
        # against the source instead (same technique as
        # test_smoke_compile_never_targets_output_dir above).
        text = RUN_SH.read_text(encoding="utf-8")
        start = text.index("_prepare_clang_plugin() {")
        end = text.index("\n_verify_pack() {", start)
        plugin_block = text[start:end]
        assert "_reset_output_dir\n" in plugin_block, (
            "_prepare_clang_plugin must clear any stale pack at $OUTPUT "
            "(via _reset_output_dir) before use, not a bare mkdir -p that "
            "leaves old source_facts/*.jsonl in place"
        )


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestWrapperInstallDepsFailurePropagates:
    """Regression guard (CodeRabbit review): _prepare_wrapper's
    install-deps.sh call used to swallow a real installer failure with
    `|| true`, reporting preparation success even though dependencies never
    installed -- the caller's build would then fail later with a confusing
    "command not found", or run uninstrumented and quietly produce an empty
    pack. Can't force the real install-deps.sh (which shells out to
    apt-get) to fail deterministically in a test environment, so assert the
    invariant statically against the actual _prepare_wrapper source instead,
    the same approach TestClangPluginSmokeTestIsolation uses."""

    def test_install_deps_failure_is_not_swallowed(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        start = text.index("_prepare_wrapper() {")
        end = text.index("_prepare_clang_plugin() {", start)
        wrapper_block = text[start:end]
        assert "install-deps.sh" in wrapper_block
        assert "|| true" not in wrapper_block, (
            "a failed install-deps.sh must not be silently treated as success"
        )
        assert "|| _fail" in wrapper_block, (
            "install-deps.sh's failure must propagate via _fail"
        )
