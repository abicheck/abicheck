# Copyright 2026 Nikolay Petrov
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

"""Zero-config build-system inference (ADR-032 amendment): ``--sources`` alone
must detect the build system and run abicheck's own query — no
``--allow-build-query`` flag, no manual compile step. Pure detection / command
construction tested here; the live subprocess is exercised behind a stub."""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.build_query import (
    ABICHECK_BUILD_DIR,
    detect_build_system,
    inferred_query_command,
    run_inferred_build_query,
)


def test_detect_cmake(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_bazel(tmp_path: Path):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    assert detect_build_system(tmp_path) == "bazel"


def test_detect_make(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    assert detect_build_system(tmp_path) == "make"


def test_cmake_wins_over_make_when_both_present(tmp_path: Path):
    # A CMake project often ships a convenience Makefile; CMake is authoritative.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    (tmp_path / "Makefile").write_text("all:\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_none_for_plain_dir(tmp_path: Path):
    assert detect_build_system(tmp_path) == ""
    assert detect_build_system(None) == ""


def test_cmake_command_is_fixed_and_uses_export_flag(tmp_path: Path):
    cmd = inferred_query_command("cmake", tmp_path)
    assert cmd is not None
    assert cmd[0] == "cmake"
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in cmd
    assert str(tmp_path / ABICHECK_BUILD_DIR) in cmd


def test_make_command_is_dry_run_only(tmp_path: Path):
    cmd = inferred_query_command("make", tmp_path)
    # Must never actually build: -n (dry run) is mandatory.
    assert cmd is not None and cmd[0] == "make" and "-n" in cmd


def test_unknown_system_has_no_command(tmp_path: Path):
    assert inferred_query_command("scons", tmp_path) is None


def test_run_skips_with_diagnostic_when_tool_missing(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    merged = BuildEvidence()
    extractors: list = []
    # Simulate cmake not installed.
    out = run_inferred_build_query(
        tmp_path, merged, extractors, which=lambda _tool: None
    )
    assert out is None
    assert len(extractors) == 1
    rec = extractors[0]
    assert rec.status == "skipped"
    assert "not installed" in rec.detail


def test_run_returns_none_for_non_build_tree(tmp_path: Path):
    merged = BuildEvidence()
    extractors: list = []
    assert run_inferred_build_query(tmp_path, merged, extractors) is None
    assert extractors == []  # nothing detected -> no noise
