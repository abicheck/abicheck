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

"""Integration tests for the clang L2 header backend (ADR-003 extension).

These run the **live** ``clang -ast-dump=json`` frontend over real headers and a
real ELF shared library, and assert two things the unit lane cannot:

1. A clang-only host still produces a header-aware (``from_headers``) snapshot
   with public-surface scoping — the P1 gap from the UXL field run.
2. The clang- and castxml-derived snapshots are **schema-equivalent enough** to
   act as a parity oracle: the same public functions, types, enums, and typedefs
   surface from both frontends.

Gated on clang + g++ being present; skipped otherwise.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import Visibility

# Scoped to **Linux/ELF** — the clang L2 backend's target (P1: clang-only Linux
# CI images). The cross-platform binary-build conventions diverge in ways
# unrelated to the backend logic: on macOS clang/g++ emit Mach-O dylibs, and on
# Windows clang defaults to the **MSVC** ABI (``?add@lib@@…``) while MinGW g++
# exports **Itanium** names, so a clang-built AST and a g++-built binary use
# different mangling schemes and never match. The pure-parser unit suite
# (``test_dumper_clang.py``) covers the backend logic on every platform.
#
# NB: deliberately *not* marked ``integration`` — that marker's Linux gate
# requires castxml (tests/conftest.py ``_integration_skip_reason``), but the
# whole point here is the **castxml-absent** host. Each test instead self-skips
# on its own real tool requirement (clang + g++; the parity test additionally
# needs castxml).
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 backend integration test is ELF/Linux-scoped (see module docstring)",
)

_HEADER = """
#pragma once
namespace lib {

struct Point { int x; int y; };

enum class Color { Red, Green = 5, Blue };

int add(int a, int b) noexcept;
void scale(Point* p, double factor);

constexpr int kVersion = 3;

using handle_t = int;

class Widget {
public:
    int value() const;
private:
    int hidden_;
};

}  // namespace lib
"""

_SOURCE = """
#include "api.h"
namespace lib {
int add(int a, int b) noexcept { return a + b; }
void scale(Point* p, double factor) { p->x = int(p->x * factor); }
int Widget::value() const { return hidden_; }
}  // namespace lib
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def built_lib(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tiny ELF .so + its public header, returning (so_path, header)."""
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for the clang L2 backend integration test")
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def test_clang_backend_produces_header_aware_snapshot(
    built_lib: tuple[Path, Path],
) -> None:
    so, header = built_lib
    snap = dump(so, [header], header_backend="clang")
    assert snap.from_headers is True
    names = {f.name for f in snap.functions}
    assert {"add", "scale"} <= names
    # noexcept + public scoping flowed through the clang frontend.
    add = next(f for f in snap.functions if f.name == "add")
    assert add.is_noexcept is True
    assert add.visibility == Visibility.PUBLIC
    assert "lib::kVersion" in snap.constants
    assert snap.constants["lib::kVersion"] == "3"
    assert "handle_t" in snap.typedefs


def test_clang_and_castxml_snapshots_agree_on_public_surface(
    built_lib: tuple[Path, Path],
) -> None:
    if not _have("castxml"):
        pytest.skip("castxml required for the clang↔castxml parity oracle")
    so, header = built_lib
    clang_snap = dump(so, [header], header_backend="clang")
    castxml_snap = dump(so, [header], header_backend="castxml")

    def public_funcs(snap: object) -> set[str]:
        return {
            f.mangled
            for f in snap.functions  # type: ignore[attr-defined]
            if f.visibility == Visibility.PUBLIC
        }

    # The exported (public) function set must match between frontends.
    assert public_funcs(clang_snap) == public_funcs(castxml_snap)
    # Both see the same named record and enum types.
    assert {t.name for t in clang_snap.types} >= {"Point", "Widget"}
    assert {t.name for t in castxml_snap.types} & {t.name for t in clang_snap.types} >= {
        "Point",
        "Widget",
    }
    assert {e.name for e in clang_snap.enums} == {e.name for e in castxml_snap.enums}
