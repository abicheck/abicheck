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
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPO_ROOT / "actions" / "collect-facts"
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


def _native_abspath(p: Path) -> str:
    """Expected form of an absolute path as ``run.sh`` resolves it.

    ``run.sh``'s ``_native_pwd`` helper always resolves absolute paths
    through bash (forward-slash) or, on a real Windows runner where a genuine
    ``cygpath.exe`` ships with Git for Windows and is always on ``PATH``,
    through ``cygpath -m`` (drive letter + forward slashes, e.g.
    ``C:/Users/...``) -- never through native Windows backslashes. A raw
    ``str(Path)`` comparison only happens to match on Linux/macOS (where
    ``Path`` is already forward-slash); on Windows it would compare against
    ``str(WindowsPath)``'s backslashes, which ``run.sh`` never produces.
    ``Path.as_posix()`` renders exactly the same drive+forward-slash form as
    ``cygpath -m`` on any platform, so it is the correct expectation here.
    """
    return p.as_posix()


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
        # run.sh's _verify_pack invokes a bare `python3 -c` with cwd=tmp_path
        # (not this pytest process's own sys.path) to import
        # abicheck.buildsource.inputs_validate. pytest itself can always
        # import abicheck via pyproject.toml's `pythonpath = ["."]`, but that
        # only extends the pytest process's own sys.path -- if whichever
        # `python3` resolves on PATH inside the subprocess has no site-
        # packages entry for abicheck (e.g. it isn't the same interpreter
        # pytest was invoked from), the subprocess fails with
        # ModuleNotFoundError before ever reaching pack validation (Codex
        # review). Mirror pytest's own mechanism explicitly instead of
        # depending on ambient python3 resolution.
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])
        ),
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
class TestLlvmMajorFromPredefinedMacros:
    """Regression (Codex review): a vendor compiler built on top of Clang
    (e.g. Intel's icpx/icx) prints its own product version in --version, not
    an LLVM/Clang number -- _llvm_major_from_version_string's "clang version
    N" regex cannot ever match it. __clang_major__ is the value that
    actually has to match for the plugin to load, and every Clang-based
    compiler (vendor or not) defines it, so this probe is compiler-agnostic
    where the --version parser is not."""

    def _fake_compiler(self, tmp_path: Path, *defines: str) -> Path:
        script = tmp_path / "fake-compiler"
        printf_lines = "\n".join(f'  printf \'{line}\\n\'' for line in defines)
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-dM" ]; then\n'
            f"{printf_lines}\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        )
        script.chmod(0o755)
        return script

    def test_parses_clang_major_from_macro_dump(self, tmp_path: Path) -> None:
        # Simulates a vendor compiler (e.g. icpx) whose --version would not
        # match "clang version N" at all, but which still defines
        # __clang_major__ correctly under -dM -E.
        compiler = self._fake_compiler(
            tmp_path, "#define __clang_major__ 16", "#define __clang_minor__ 0"
        )
        result = _run_predicate(f'_llvm_major_from_predefined_macros "{compiler}"')
        assert result.stdout.strip() == "16"

    def test_ignores_other_defines(self, tmp_path: Path) -> None:
        compiler = self._fake_compiler(
            tmp_path,
            "#define __clang_minor__ 3",
            "#define __clang_major__ 18",
            "#define __GNUC__ 4",
        )
        result = _run_predicate(f'_llvm_major_from_predefined_macros "{compiler}"')
        assert result.stdout.strip() == "18"

    def test_empty_when_compiler_does_not_support_dM(self, tmp_path: Path) -> None:
        # gcc (or any non-Clang compiler) rejects -dM -E -x c++ - in a way
        # that doesn't emit __clang_major__ -- callers must fall back to
        # _llvm_major_from_version_string instead of failing outright.
        script = tmp_path / "fake-gcc"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)
        result = _run_predicate(f'_llvm_major_from_predefined_macros "{script}"')
        assert result.stdout.strip() == ""

    def test_empty_when_compiler_not_found(self) -> None:
        result = _run_predicate(
            '_llvm_major_from_predefined_macros "/no/such/compiler-xyz"'
        )
        assert result.stdout.strip() == ""


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestBundledLlvmCmakePrefix:
    """Regression (Codex review): the clang-plugin build previously always
    apt-get-installed clang-N/llvm-N-dev/libclang-N-dev and looked up
    llvm-config-N --cmakedir, with no way to point it at a vendor toolchain's
    own bundled LLVM/Clang (e.g. Intel's icpx/icx under $CMPLR_ROOT, which
    apt does not carry at all)."""

    def test_explicit_override_wins(self, tmp_path: Path) -> None:
        result = _run_predicate(
            f'_bundled_llvm_cmake_prefix "{tmp_path}/explicit" "{tmp_path}/cmplr" '
            f'"{tmp_path}/cmplr/bin/icpx"'
        )
        assert result.stdout.strip() == f"{tmp_path}/explicit"

    def test_detects_cmplr_root_with_llvm_cmake_package(self, tmp_path: Path) -> None:
        cmplr_root = tmp_path / "cmplr"
        (cmplr_root / "lib" / "cmake" / "llvm").mkdir(parents=True)
        # The resolved compiler must live under $CMPLR_ROOT for auto-use to
        # apply -- see test_empty_when_compiler_not_under_cmplr_root below.
        compiler_path = cmplr_root / "bin" / "icpx"
        result = _run_predicate(
            f'_bundled_llvm_cmake_prefix "" "{cmplr_root}" "{compiler_path}"'
        )
        # Plain string concatenation ("$cmplr_root/lib/cmake"), not a
        # pathlib-style join -- str(cmplr_root / "lib" / "cmake") uses
        # backslashes on Windows and would spuriously mismatch this
        # function's forward-slash output there.
        assert result.stdout.strip() == f"{cmplr_root}/lib/cmake"

    def test_empty_when_cmplr_root_unset(self) -> None:
        result = _run_predicate('_bundled_llvm_cmake_prefix "" "" ""')
        assert result.stdout.strip() == ""

    def test_empty_when_cmplr_root_has_no_llvm_cmake_package(
        self, tmp_path: Path
    ) -> None:
        # $CMPLR_ROOT set (e.g. some other vendor env var reuse) but it
        # doesn't actually bundle an LLVM CMake package -- must not be
        # mistaken for one.
        cmplr_root = tmp_path / "cmplr"
        cmplr_root.mkdir()
        compiler_path = cmplr_root / "bin" / "icpx"
        result = _run_predicate(
            f'_bundled_llvm_cmake_prefix "" "{cmplr_root}" "{compiler_path}"'
        )
        assert result.stdout.strip() == ""

    def test_empty_when_compiler_not_under_cmplr_root(self, tmp_path: Path) -> None:
        # Regression (Codex review): a job can source an Intel oneAPI
        # environment (setting $CMPLR_ROOT) for unrelated tooling while
        # `compiler:` still resolves to a different, unrelated Clang (e.g.
        # the default clang++ on PATH) -- auto-using $CMPLR_ROOT's bundled
        # LLVM in that case would build the plugin against a toolchain that
        # is not the one -fplugin= actually loads later. Only auto-apply
        # when the resolved compiler binary is itself under $CMPLR_ROOT.
        cmplr_root = tmp_path / "cmplr"
        (cmplr_root / "lib" / "cmake" / "llvm").mkdir(parents=True)
        unrelated_compiler = tmp_path / "usr" / "bin" / "clang++"
        result = _run_predicate(
            f'_bundled_llvm_cmake_prefix "" "{cmplr_root}" "{unrelated_compiler}"'
        )
        assert result.stdout.strip() == ""


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
        # INPUT_OUTPUT is given here as an already-absolute path (str(tmp_path
        # / ...) is native-backslash on a real Windows runner) --
        # _is_absolute_path() in run.sh recognizes a drive-letter-prefixed
        # path and passes it through verbatim, never routing it through
        # _native_pwd/cygpath -m. Only a *relative* INPUT_OUTPUT triggers that
        # resolution (see test_prepare_exports_absolute_inputs_dir below), so
        # the expectation here is str(), not _native_abspath().
        assert env["ABICHECK_INPUTS_DIR"] == str(tmp_path / "abicheck_inputs")
        assert env["ABICHECK_CC_EXTRACTOR"] == "clang"
        assert env["ABICHECK_CC_LIBRARY"] == "foo"
        # Resolved to absolute (Codex review): abicheck-cc runs with cwd set
        # to the build directory in the documented recipe, not this
        # script's cwd, so a relative root must be resolved here first.
        assert env["ABICHECK_CC_HEADERS"] == _native_abspath(tmp_path / "include")
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
        assert env["ABICHECK_INPUTS_DIR"] == _native_abspath(
            tmp_path / "abicheck_inputs"
        )
        outputs = _parse_kv_file(github_output)
        assert outputs["pack-path"] == _native_abspath(tmp_path / "abicheck_inputs")

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
            [
                _native_abspath(tmp_path / "include"),
                _native_abspath(tmp_path / "gen/include"),
            ]
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
        expected = f"{_native_abspath(tmp_path / 'include')};{_native_abspath(tmp_path / 'gen/include')}"
        assert _parse_kv_file(github_env)["ABICHECK_CC_HEADERS"] == expected

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "This test fakes cygpath via a PATH-prepended stub to verify "
            "run.sh invokes it -- on a real Windows runner, Git Bash's own "
            "bash.exe launcher prepends its bundled usr/bin (with the "
            "genuine cygpath.exe) ahead of any user-supplied PATH entry, so "
            "the real cygpath always wins over the stub and the assertion "
            "can never observe the stub's WINCONV: marker. Exercised on "
            "Linux/macOS, where PATH prepending is respected normally."
        ),
    )
    def test_output_dir_converted_from_msys_path_on_windows(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): Git Bash/MSYS's own `pwd` reports its
        # POSIX-style view of the filesystem (e.g. /d/a/repo/repo), not a
        # Windows path. ABICHECK_INPUTS_DIR is read by the native Windows
        # Python/Clang toolchain the Action installs, which has no notion of
        # an MSYS root and would resolve /d/... as a relative path (a
        # literal "d" directory) under the current drive instead of the
        # intended location -- run.sh must convert through `cygpath -m`
        # rather than joining `$(pwd)` directly on MINGW/MSYS/CYGWIN. Stubs
        # both `uname` and `cygpath` (via a PATH-prepended fake bin dir),
        # since this test environment is Linux; the stub `cygpath` prefixes
        # its output so the test can tell it was actually invoked rather
        # than silently falling back to raw `pwd`.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "uname").write_text('#!/bin/sh\necho "MINGW64_NT-10.0-x86_64"\n')
        (fake_bin / "uname").chmod(0o755)
        (fake_bin / "cygpath").write_text('#!/bin/sh\necho "WINCONV:$2"\n')
        (fake_bin / "cygpath").chmod(0o755)

        result, github_env, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_INSTALL_DEPS": "false",
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        expected = f"WINCONV:{tmp_path / 'abicheck_inputs'}"
        assert _parse_kv_file(github_env)["ABICHECK_INPUTS_DIR"] == expected

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "This test relies on cygpath being genuinely absent from PATH "
            "to exercise the raw-pwd fallback -- on a real Windows runner, "
            "cygpath.exe ships with Git for Windows and is always present, "
            "so 'unavailable' can never be reproduced there and run.sh "
            "always takes the cygpath -m branch instead. Exercised on "
            "Linux/macOS, which have no real cygpath to interfere."
        ),
    )
    def test_output_dir_uses_raw_pwd_when_cygpath_unavailable(
        self, tmp_path: Path
    ) -> None:
        # cygpath is normally always present alongside Git Bash, but must
        # not be a hard requirement -- fall back to plain `pwd` rather than
        # failing the whole step if it's missing for some reason.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "uname").write_text('#!/bin/sh\necho "MINGW64_NT-10.0-x86_64"\n')
        (fake_bin / "uname").chmod(0o755)

        result, github_env, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "wrapper",
                "INPUT_INSTALL_DEPS": "false",
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        expected = str(tmp_path / "abicheck_inputs")
        assert _parse_kv_file(github_env)["ABICHECK_INPUTS_DIR"] == expected

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

    def test_subprocess_env_includes_repo_root_on_pythonpath(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression (Codex review): _verify_pack's `python3 -c` does a
        # fresh PATH lookup for python3, independent of whichever
        # interpreter this pytest process itself runs under -- pytest can
        # always import abicheck via pyproject.toml's `pythonpath = ["."]`,
        # but that only extends the pytest process's own sys.path. If the
        # subprocess's python3 resolves to a different interpreter (or one
        # with no editable abicheck install at all), it fails with
        # ModuleNotFoundError before ever reaching pack validation. Verify
        # _run_action's env directly (intercepting subprocess.run rather
        # than spinning up a real second interpreter, since abicheck's
        # required pyyaml/click/pyelftools dependencies would still need
        # to be independently present in any such interpreter regardless
        # of PYTHONPATH -- this test isolates the one thing this specific
        # fix controls: whether REPO_ROOT reaches the subprocess env).
        captured: dict[str, dict[str, str]] = {}

        def _fake_run(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            captured["env"] = kwargs["env"]  # type: ignore[assignment]
            return subprocess.CompletedProcess(
                args=(), returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        _run_action({"INPUT_PHASE": "verify", "INPUT_PRODUCER": "wrapper"}, tmp_path)

        pythonpath_entries = captured["env"]["PYTHONPATH"].split(os.pathsep)
        assert str(REPO_ROOT) in pythonpath_entries

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
