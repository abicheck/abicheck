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

"""End-to-end *use-case* validation for the source scan levels (`scan --depth`).

The fast unit lane drives the L3/L4/L5 collection with ``subprocess`` mocked, so
it never exercises the real cmake/clang toolchain — and therefore can't catch
lifetime / cwd bugs in the inferred-build path. This file runs an *actual*
``abicheck scan`` over a real CMake C++ project at each ``--depth`` and asserts
the coverage matrix that each level promises.

It is the regression guard for the kind of bug that only shows up end-to-end:
e.g. the S2 preprocessor scan running ``clang -E`` with a compile unit's
``directory`` (the out-of-tree inferred cmake build dir) as cwd, *after* that dir
was cleaned up — which mocked-subprocess unit tests cannot see.

Marked ``integration`` (excluded from the fast lane) and additionally gated on
the specific tools it shells out to (clang, cmake, gcc/g++), so it skips cleanly
where those are absent instead of erroring.
"""

from __future__ import annotations

import json
import shutil

import pytest
from click.testing import CliRunner

from abicheck.cli import main

# This use case needs the real source toolchain: cmake (zero-config L3 inference),
# clang/clang++ (L2 AST via --ast-frontend clang, L4 replay, L5 graph, S2
# preprocessor), and gcc/g++ to compile the .so for L0/L1.
_REQUIRED_TOOLS = ("clang", "clang++", "cmake", "gcc", "g++")
_MISSING = [t for t in _REQUIRED_TOOLS if shutil.which(t) is None]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(_MISSING),
        reason=f"scan-levels use case needs {', '.join(_REQUIRED_TOOLS)}; "
        f"missing: {', '.join(_MISSING) or 'none'}",
    ),
]


_FOO_H = """\
#ifndef FOO_H
#define FOO_H
namespace foo {
class Widget {
public:
  Widget();
  int value() const;
  void set_value(int v);
private:
  int v_;
};
int add(int a, int b);
}
#endif
"""

_FOO_CPP = """\
#include "foo.h"
namespace foo {
Widget::Widget() : v_(0) {}
int Widget::value() const { return v_; }
void Widget::set_value(int v) { v_ = v; }
int add(int a, int b) { return a + b; }
}
"""

_CMAKELISTS = """\
cmake_minimum_required(VERSION 3.10)
project(foo CXX)
add_library(foo SHARED foo.cpp)
target_include_directories(foo PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
set_target_properties(foo PROPERTIES VERSION 1.0.0 SOVERSION 1)
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory):
    """A real CMake C++ library: source tree + a compiled .so (built once)."""
    import subprocess

    root = tmp_path_factory.mktemp("scan_levels_proj")
    (root / "include").mkdir()
    (root / "include" / "foo.h").write_text(_FOO_H)
    (root / "foo.cpp").write_text(_FOO_CPP)
    (root / "CMakeLists.txt").write_text(_CMAKELISTS)
    so = root / "libfoo.so.1.0.0"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            f"-I{root / 'include'}",
            "-o",
            str(so),
            str(root / "foo.cpp"),
        ],
        check=True,
        capture_output=True,
    )
    (root / "libfoo.so").symlink_to(so.name)
    return root


def _scan(project, depth: str) -> dict:
    """Run `abicheck scan --depth <depth>` over the project; return parsed JSON."""
    res = CliRunner().invoke(
        main,
        [
            "scan",
            "--binary",
            str(project / "libfoo.so"),
            "-H",
            str(project / "include"),
            "--sources",
            str(project),
            "--depth",
            depth,
            "--ast-frontend",
            "clang",
            "--format",
            "json",
        ],
    )
    assert res.exit_code in (0, 1, 2, 4), (
        f"unexpected exit {res.exit_code} for --depth {depth}\n{res.output}"
    )
    # The report is the JSON document; tolerate a trailing "Report written" note.
    start = res.output.index("{")
    return json.loads(res.output[start:])


def _coverage(report: dict) -> dict[str, str]:
    """Map each coverage layer name → its status string."""
    return {row["layer"]: row["status"] for row in report.get("coverage", [])}


def test_binary_depth_collects_only_l0_l1(project):
    cov = _coverage(_scan(project, "binary"))
    assert cov["L0_binary"] == "present"
    # No source layers collected at binary depth.
    assert cov["L2_header"] == "skipped"
    assert cov["L3_build"] == "not_collected"
    assert cov["L4_source_abi"] == "not_collected"


def test_headers_depth_adds_l2_ast(project):
    cov = _coverage(_scan(project, "headers"))
    assert cov["L0_binary"] == "present"
    assert cov["L2_header"] == "present"  # clang AST frontend parsed the header
    assert cov["L3_build"] == "not_collected"


def test_build_depth_runs_zero_config_cmake_and_preprocessor(project):
    # --depth build → zero-config cmake inference produces L3 (no compile DB in the
    # tree), and the S2 preprocessor scan must actually RUN over that build context.
    cov = _coverage(_scan(project, "build"))
    assert cov["L2_header"] == "present"
    assert cov["L3_build"] == "present", "zero-config cmake inference should yield L3"
    # Regression guard (the lifetime bug): the inferred cmake build dir must still
    # exist when the preprocessor scan uses it as cwd — i.e. S2 ran, not "failed"
    # with a deleted-cwd error. Before the fix this was a hard clang -E failure.
    assert cov["preprocessor_scan"] == "present", (
        "S2 preprocessor scan must run against the inferred build dir, not fail "
        "because the dir was cleaned up before it ran"
    )


@pytest.mark.parametrize("depth", ["source", "full"])
def test_source_depths_add_l4_replay_and_l5_graph(project, depth):
    cov = _coverage(_scan(project, depth))
    assert cov["L3_build"] == "present"
    assert cov["preprocessor_scan"] == "present"
    # L4 replay runs (parsed, even if symbol matching is partial) and L5 folds.
    assert cov["L4_source_abi"] in ("present", "partial")
    assert cov["L5_source_graph"] == "present"


def test_depth_progression_is_monotone(project):
    # Each deeper level collects a superset of the shallower level's source layers:
    # a use-case-level invariant that catches a level silently regressing.
    def present_source_layers(depth: str) -> set[str]:
        cov = _coverage(_scan(project, depth))
        return {
            name
            for name in ("L2_header", "L3_build", "L4_source_abi", "L5_source_graph")
            if cov.get(name) in ("present", "partial")
        }

    binary = present_source_layers("binary")
    headers = present_source_layers("headers")
    build = present_source_layers("build")
    source = present_source_layers("source")
    assert binary <= headers <= build <= source
    assert "L2_header" in headers
    assert "L3_build" in build
    assert {"L4_source_abi", "L5_source_graph"} <= source


def test_no_inferred_build_dir_leak_after_scan(project):
    # The out-of-tree inferred cmake build dir must be removed once the scan ends
    # (it is owned by the scan orchestrator's finally) — no per-run temp-dir leak.
    import tempfile
    from pathlib import Path

    try:
        uid = __import__("os").getuid()
    except AttributeError:
        pytest.skip("POSIX-only leak check")
    root = Path(tempfile.gettempdir()) / f"abicheck-{uid}"
    _scan(project, "build")
    leaked = list(root.glob("cmake-*/")) if root.is_dir() else []
    # Only ever-present `.lock` marker files may remain; never a build *directory*.
    assert not leaked, f"inferred cmake build dir leaked after scan: {leaked}"
