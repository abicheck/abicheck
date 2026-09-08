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

"""End-to-end proof that ``action/run.sh``'s ``ast-frontend``/``sysroot``/
``nostdinc`` inputs produce a real, working ``abicheck`` invocation.

Phase 7b (PR #1153, docs/contribute/plans/one-comparison-product.md) demoted
the CLI's own ``--ast-frontend``/``--sysroot``/``--nostdinc`` flags to
``.abicheck.yml``'s ``compile:`` block -- but ``action/run.sh`` kept building
those as literal CLI flags for all three call sites that forward them (dump
mode, compare mode's single-pair path, scan mode), so every one of those
Action inputs made the real invocation exit 64 (UsageError). The fix folds
them into a scratch ``.abicheck.yml`` merged with any caller-supplied
build-config and forwarded via ``--config`` instead
(``_resolve_effective_build_config`` in run.sh).

``tests/test_action_compile_context_parity.py`` (a sibling module) already
covers the *generated CMD array* in isolation (a fast, no-compiler-needed
unit test extracting just the relevant fragment) -- but that alone doesn't
prove the merged config the fragment produces is one the real CLI actually
accepts and acts on: AGENTS.md's "third-party-boundary tests must exercise
the real public API at realistic scale, not just internal arithmetic" applies
here as much as it does to a storage format -- the "external dependency" is
this Action's own contract with the ``abicheck`` CLI it invokes. This module
runs the **complete, unmodified** ``action/run.sh`` end to end, against a
real compiled shared library and a real installed ``abicheck`` (castxml/gcc/
clang all present), and asserts the process exit code -- not a rendered CMD
array -- proving these inputs no longer produce exit 64.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc"),
    pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang"),
]


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
    }


def _build_lib(tmp_path: Path, name: str, body: str, header: str) -> tuple[Path, Path]:
    """Compile a tiny, self-contained (no system includes needed) real .so
    plus its header -- self-contained so a --nostdinc run doesn't fail on an
    unrelated missing system header, keeping the test scoped to what it
    actually checks."""
    header_path = tmp_path / f"{name}.h"
    header_path.write_text(header, encoding="utf-8")
    src_path = tmp_path / f"{name}.c"
    src_path.write_text(f'#include "{name}.h"\n' + body, encoding="utf-8")
    lib_path = tmp_path / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(lib_path), str(src_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    return lib_path, header_path


_HEADER = "int add(int a, int b);\n"
_BODY = "int add(int a, int b) { return a + b; }\n"


class TestRealDumpWithCompileContextInputs:
    """dump mode: the first of the three call sites the PR body named."""

    def test_ast_frontend_sysroot_nostdinc_all_set_together(
        self, tmp_path: Path
    ) -> None:
        lib, hdr = _build_lib(tmp_path, "foo", _BODY, _HEADER)
        out_file = tmp_path / "out.json"
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "dump",
            "INPUT_NEW_LIBRARY": str(lib),
            "INPUT_NEW_HEADER": str(hdr),
            "INPUT_LANG": "c",
            "INPUT_AST_FRONTEND": "clang",
            "INPUT_SYSROOT": "/usr",
            "INPUT_NOSTDINC": "true",
            "INPUT_OUTPUT_FILE": str(out_file),
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode == 0, res.stdout + "\n---STDERR---\n" + res.stderr
        assert res.returncode != 64
        data = json.loads(out_file.read_text(encoding="utf-8"))
        functions = data["sections"]["declarations"]["payload"]["functions"]
        assert any(f.get("name") == "add" for f in functions), data
        # ast-frontend: clang really took effect (not silently ignored),
        # confirmed via the debug section's own recorded producer.
        assert data["sections"]["debug"]["payload"]["ast_producer"] == "clang"

    def test_merges_with_a_caller_supplied_build_config(self, tmp_path: Path) -> None:
        """The operator's own build-config survives the merge untouched on
        disk, and the run still succeeds (proves the scratch file is a
        separate copy, not an in-place rewrite of the caller's file)."""
        lib, hdr = _build_lib(tmp_path, "foo2", _BODY, _HEADER)
        build_config = tmp_path / "my.abicheck.yml"
        original_text = "severity:\n  preset: strict\n"
        build_config.write_text(original_text, encoding="utf-8")
        out_file = tmp_path / "out2.json"
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "dump",
            "INPUT_NEW_LIBRARY": str(lib),
            "INPUT_NEW_HEADER": str(hdr),
            "INPUT_LANG": "c",
            "INPUT_AST_FRONTEND": "clang",
            "INPUT_BUILD_CONFIG": str(build_config),
            "INPUT_OUTPUT_FILE": str(out_file),
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode == 0, res.stdout + "\n---STDERR---\n" + res.stderr
        assert build_config.read_text(encoding="utf-8") == original_text
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["sections"]["debug"]["payload"]["ast_producer"] == "clang"

    def test_relative_build_config_path_still_resolves(self, tmp_path: Path) -> None:
        """Regression pin: the merge helper runs Python from inside
        $_PY_SAFE_DIR (a *different* CWD) for import-shadowing safety, so a
        relative build-config path must be anchored to the caller's own CWD
        before that `cd` happens, or the merge can't find a file that
        genuinely exists."""
        lib, hdr = _build_lib(tmp_path, "foo3", _BODY, _HEADER)
        (tmp_path / "my_relative.abicheck.yml").write_text(
            "severity:\n  preset: strict\n", encoding="utf-8"
        )
        out_file = tmp_path / "out3.json"
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "dump",
            "INPUT_NEW_LIBRARY": str(lib),
            "INPUT_NEW_HEADER": str(hdr),
            "INPUT_LANG": "c",
            "INPUT_AST_FRONTEND": "clang",
            "INPUT_BUILD_CONFIG": "my_relative.abicheck.yml",
            "INPUT_OUTPUT_FILE": str(out_file),
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode == 0, res.stdout + "\n---STDERR---\n" + res.stderr

    def test_no_compile_context_inputs_needs_no_merge(self, tmp_path: Path) -> None:
        """No ast-frontend/sysroot/nostdinc set: the ordinary, unaffected
        case must keep working exactly as before (no scratch file, no
        Python merge step required)."""
        lib, hdr = _build_lib(tmp_path, "foo4", _BODY, _HEADER)
        out_file = tmp_path / "out4.json"
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "dump",
            "INPUT_NEW_LIBRARY": str(lib),
            "INPUT_NEW_HEADER": str(hdr),
            "INPUT_LANG": "c",
            "INPUT_OUTPUT_FILE": str(out_file),
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode == 0, res.stdout + "\n---STDERR---\n" + res.stderr


class TestRealCompareWithCompileContextInputs:
    """compare mode's single-pair path: the second call site."""

    def test_single_pair_ast_frontend_succeeds(self, tmp_path: Path) -> None:
        old_lib, _ = _build_lib(tmp_path, "old", _BODY, _HEADER)
        new_lib, new_hdr = _build_lib(tmp_path, "new", _BODY, _HEADER)
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_lib),
            "INPUT_NEW_LIBRARY": str(new_lib),
            "INPUT_HEADER": str(new_hdr),
            "INPUT_LANG": "c",
            "INPUT_AST_FRONTEND": "clang",
            "INPUT_FORMAT": "json",
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode != 64, res.stdout + "\n---STDERR---\n" + res.stderr
        # 0 (identical), 2 (source break), 4 (ABI break) are all real
        # verdicts -- 64 (UsageError) is the one outcome this fix rules out.
        assert res.returncode in (0, 2, 4), res.stdout + "\n---STDERR---\n" + res.stderr

    def test_release_style_operand_still_rejects_the_inputs(
        self, tmp_path: Path
    ) -> None:
        """The pre-existing directory/package guard must keep firing --
        this fix only changes the single-pair path's forwarding mechanism,
        never the release fan-out's own "can't honor this" rejection."""
        old_dir = tmp_path / "old_release"
        old_dir.mkdir()
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_dir),
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_AST_FRONTEND": "clang",
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode == 1
        assert "does not support ast-frontend" in res.stdout


class TestRealScanWithCompileContextInputs:
    """scan mode: the third call site (found by reading the file, not
    assumed -- the PR body named only two of the three)."""

    def test_scan_ast_frontend_succeeds(self, tmp_path: Path) -> None:
        lib, hdr = _build_lib(tmp_path, "scanme", _BODY, _HEADER)
        env = {
            **_base_env(tmp_path),
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY": str(lib),
            "INPUT_PUBLIC_HEADER_DIR": str(hdr),
            "INPUT_LANG": "c",
            "INPUT_AST_FRONTEND": "clang",
            "INPUT_FORMAT": "json",
        }
        res = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert res.returncode != 64, res.stdout + "\n---STDERR---\n" + res.stderr


def test_ast_frontend_is_no_longer_a_real_cli_flag_anywhere() -> None:
    """Pins WHY the fix is needed, independent of run.sh: Phase 7b really
    did remove --ast-frontend/--sysroot/--nostdinc from the CLI entirely, so
    a future revert of the run.sh side alone would still be broken."""
    for cmd in ("dump", "compare", "scan"):
        res = subprocess.run(
            ["abicheck", cmd, "--help"], capture_output=True, text=True, check=True
        )
        assert "--ast-frontend" not in res.stdout, cmd
        assert "--sysroot" not in res.stdout, cmd
        assert "--nostdinc" not in res.stdout, cmd
