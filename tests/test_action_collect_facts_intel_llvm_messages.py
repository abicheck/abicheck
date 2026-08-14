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

"""Behavioral tests for two `actions/collect-facts/run.sh` log/error
messages that explain the Intel oneAPI (icx/icpx) Clang plugin guardrail's
reasoning to a reader, rather than the guardrail's pass/fail behavior
itself (see ``test_action_collect_facts.py::TestClangPluginIntelLlvmRefusal``
for that).

Split out as its own file (mirroring the existing ``test_action_run_sh_*``
sibling-file pattern for ``action/run.sh``) rather than appended to
``test_action_collect_facts.py``, which was already at this repo's 2000-line
AI-readiness hard cap.

Both messages were fixed after a real, independently-reproduced end-to-end
re-run against Intel(R) oneAPI DPC++/C++ Compiler 2026.1.1 (build 20260724)
and a real upstream `apt.llvm.org` LLVM 22 -- see
`contrib/abicheck-clang-plugin/README.md`'s Intel oneAPI "Status update"
section and `contrib/abicheck-clang-plugin/AGENTS.md`'s "LLVM-major
sensitivity" section for the full findings these tests guard.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPO_ROOT / "actions" / "collect-facts"
RUN_SH = ACTION_DIR / "run.sh"


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


def _fake_dm_compiler(
    tmp_path: Path, *defines: str, name: str = "fake-compiler"
) -> Path:
    """A fake compiler that answers -dM -E with the given #define lines and
    otherwise fails -- for tests that only exercise LLVM-major/vendor
    detection, not an actual compile. Mirrors
    ``test_action_collect_facts.py``'s own helper of the same name."""
    script = tmp_path / name
    printf_lines = "\n".join(f"  printf '{line}\\n'" for line in defines)
    script.write_text(
        f'#!/bin/sh\nif [ "$1" = "-dM" ]; then\n{printf_lines}\n  exit 0\nfi\nexit 1\n'
    )
    script.chmod(0o755)
    return script


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestClangPluginBundledPrefixProvenanceMessage:
    """Regression: the "using vendor-bundled LLVM/Clang CMake package" log
    line used to fire identically whether the prefix was auto-detected
    under the resolved compiler's own $CMPLR_ROOT (real, if incomplete,
    evidence it is that compiler's own SDK) or supplied explicitly via
    llvm-cmake-prefix (caller-supplied and unverified -- it can just as
    easily point at an ordinary same-major upstream/apt LLVM package). A
    real re-run pointed llvm-cmake-prefix at a plain apt.llvm.org LLVM 22
    and got the unconditional "vendor-bundled" wording even though the
    resulting plugin still crashed inside real icx (see contrib/abicheck-
    clang-plugin/README.md's Intel oneAPI "Status update" section) -- the
    message must not claim more confidence than either path actually
    earns."""

    def test_explicit_llvm_cmake_prefix_is_marked_unverified(
        self, tmp_path: Path
    ) -> None:
        prefix = tmp_path / "explicit-prefix"
        (prefix / "lib" / "cmake" / "llvm").mkdir(parents=True)
        (prefix / "lib" / "cmake" / "clang").mkdir(parents=True)
        compiler = _fake_dm_compiler(tmp_path, "#define __clang_major__ 18")
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_COMPILER": str(compiler),
                "INPUT_LLVM_CMAKE_PREFIX": str(prefix),
                "INPUT_INSTALL_DEPS": "false",
                "CMPLR_ROOT": "",
            },
            tmp_path,
        )
        # cmake configure fails against this fake, empty prefix -- fine, the
        # assertion only concerns the log line printed before that point.
        assert "using explicitly-supplied llvm-cmake-prefix" in result.stdout
        assert "NOT verified" in result.stdout
        assert "auto-detected the vendor-bundled" not in result.stdout

    def test_auto_detected_cmplr_root_prefix_is_marked_confirmed(
        self, tmp_path: Path
    ) -> None:
        cmplr_root = tmp_path / "cmplr-root"
        (cmplr_root / "lib" / "cmake" / "llvm").mkdir(parents=True)
        (cmplr_root / "lib" / "cmake" / "clang").mkdir(parents=True)
        bin_dir = cmplr_root / "bin"
        bin_dir.mkdir()
        compiler = _fake_dm_compiler(
            tmp_path, "#define __clang_major__ 18", name="cmplr-root/bin/fake-compiler"
        )
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_COMPILER": str(compiler),
                "INPUT_INSTALL_DEPS": "false",
                "CMPLR_ROOT": str(cmplr_root),
            },
            tmp_path,
        )
        assert "auto-detected the vendor-bundled" in result.stdout
        assert "confirmed to live under" in result.stdout
        assert "using explicitly-supplied llvm-cmake-prefix" not in result.stdout


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/collect-facts/run.sh not found"
)
class TestClangPluginSmokeFailureMessage:
    """Regression: the smoke-test failure message used to unconditionally
    read as "not the same LLVM major", which is actively wrong for a
    downstream fork like Intel's icx/icpx -- a real re-run reproduced this
    exact smoke failure with the LLVM major, RTTI, and standard-library ABI
    all already matched on both sides (see contrib/abicheck-clang-plugin/
    README.md's Intel oneAPI "Status update" section: the crash is inside
    FactsAction::CreateASTConsumer/deriveRootsFromIncludes, from
    frontend-internal object-layout drift a fork's own patches can
    introduce independently of major/RTTI/stdlib). The message must name
    that possibility when the resolved compiler is this fork, and must
    NOT claim it (or regress the plain not-same-major wording) for an
    ordinary vanilla-Clang mismatch."""

    def test_intel_llvm_smoke_failure_names_frontend_abi_drift(
        self, tmp_path: Path
    ) -> None:
        compiler = _fake_dm_compiler(
            tmp_path,
            "#define __clang_major__ 22",
            "#define __INTEL_LLVM_COMPILER 20260101",
        )
        artifact = tmp_path / "libabicheck-facts.so"
        artifact.write_bytes(b"fake-plugin-binary")
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_COMPILER": str(compiler),
                "INPUT_PLUGIN_ARTIFACT": str(artifact),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "downstream LLVM fork" in result.stdout
        assert "same" in result.stdout and "LLVM major" in result.stdout
        assert "frontend object-layout parity" in result.stdout
        assert (
            "not the same LLVM major (22) the plugin was built against"
            not in result.stdout
        )

    def test_vanilla_clang_smoke_failure_keeps_major_mismatch_wording(
        self, tmp_path: Path
    ) -> None:
        compiler = _fake_dm_compiler(tmp_path, "#define __clang_major__ 18")
        artifact = tmp_path / "libabicheck-facts.so"
        artifact.write_bytes(b"fake-plugin-binary")
        result, _, _ = _run_action(
            {
                "INPUT_PHASE": "prepare",
                "INPUT_PRODUCER": "clang-plugin",
                "INPUT_COMPILER": str(compiler),
                "INPUT_PLUGIN_ARTIFACT": str(artifact),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert (
            "not the same LLVM major (18) the plugin was built against" in result.stdout
        )
        assert "downstream LLVM fork" not in result.stdout
        assert "frontend object-layout parity" not in result.stdout
