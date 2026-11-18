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

"""Struct-return classification from real header dumps (castxml and clang).

The pair verdicts are checked against execution by
``formal/experiments/struct_ret_fragment.py`` (gcc-built libraries, both
header backends, every case). The single-record shapes are the psABI rules
that experiment confirms by execution.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model.call_interface import RetShape
from abicheck.model.sysv_aggregate import struct_returns_in
from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command, abicheck_env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"),
        reason="x86-64 SysV only",
    ),
]

_ROOT = Path(__file__).resolve().parent.parent


def _require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")


def _dump(tmp: Path, header: str, source: str, lang: str, frontend: str) -> object:
    ext = "c" if lang == "c" else "cpp"
    (tmp / "api.h").write_text(header, encoding="utf-8")
    (tmp / f"api.{ext}").write_text(source, encoding="utf-8")
    lib = tmp / "libapi.so"
    cc = "gcc" if lang == "c" else "g++"
    subprocess.run(
        [cc, "-shared", "-fPIC", str(tmp / f"api.{ext}"), "-o", str(lib)], check=True
    )
    cfg = tmp / "cfg.yml"
    cfg.write_text(
        f"compile:\n  frontend: {frontend}\n  lang: {lang}\n", encoding="utf-8"
    )
    out = tmp / "snap.json"
    run = subprocess.run(
        abicheck_command("dump", lib, "-H", tmp / "api.h", "--config", cfg, "-o", out),
        capture_output=True,
        env=abicheck_env(),
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return load_snapshot(str(out))


_C_HEADER = """
struct in { int x; unsigned y; };
struct nest { struct in a; long *p[1]; };
typedef struct { unsigned a : 3; unsigned b : 5; long c; } bits;
struct two { long a, b; };
struct dd { double a, b; };
struct big { long a, b, c; };
union u { long a; double b; };
struct mixed { int a; double b; };
struct nest f_nest(void);
bits f_bits(void);
struct two f_two(void);
struct dd f_dd(void);
struct big f_big(void);
union u f_union(void);
struct mixed f_mixed(void);
"""
_C_SOURCE = (
    '#include "api.h"\n'
    "struct nest f_nest(void){struct nest r={{0}};return r;}\n"
    "bits f_bits(void){bits r={0};return r;}\n"
    "struct two f_two(void){struct two r={0};return r;}\n"
    "struct dd f_dd(void){struct dd r={0};return r;}\n"
    "struct big f_big(void){struct big r={0};return r;}\n"
    "union u f_union(void){union u r={0};return r;}\n"
    "struct mixed f_mixed(void){struct mixed r={0};return r;}\n"
)
_C_EXPECTED = {
    "f_nest": RetShape.INT2,
    "f_bits": RetShape.INT2,
    "f_two": RetShape.INT2,
    "f_dd": RetShape.SSE2,
    "f_big": RetShape.MEMORY,
    "f_union": None,
    "f_mixed": None,
}


@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_c_struct_returns_are_classified_from_the_header(
    tmp_path: Path, frontend: str
) -> None:
    _require("gcc", frontend)
    snap = _dump(tmp_path, _C_HEADER, _C_SOURCE, "c", frontend)
    got = {}
    for fn in snap.declarations.functions:  # type: ignore[attr-defined]
        r = struct_returns_in(snap)(fn)  # type: ignore[arg-type]
        got[fn.name] = None if r is None else r.shape
    assert got == _C_EXPECTED


_CXX_HEADER = """
struct plain { long a, b; };
struct owning { long a, b; ~owning(); };
struct base { long a; };
struct derived : base { long b; };
struct nocopy {
    long a, b;
    nocopy(long x, long y) : a(x), b(y) {}
    nocopy(const nocopy &) = delete;
    nocopy(nocopy &&) = delete;
};
extern "C" plain g_plain();
extern "C" nocopy g_nocopy();
extern "C" owning g_owning();
extern "C" derived g_derived();
"""
_CXX_SOURCE = (
    '#include "api.h"\nowning::~owning() {}\n'
    'extern "C" plain g_plain() { return plain{1, 2}; }\n'
    'extern "C" owning g_owning() { return owning{1, 2}; }\n'
    'extern "C" derived g_derived() { return derived{}; }\n'
    'extern "C" nocopy g_nocopy() { return nocopy(1, 2); }\n'
)


@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_a_non_trivial_cxx_record_is_never_given_a_register_shape(
    tmp_path: Path, frontend: str
) -> None:
    """``owning`` and ``nocopy`` are returned in memory (Itanium 3.1.1); the
    header facts do not prove that, so they stay unclassified rather than
    reading INT2. clang's trait cannot tell ``nocopy`` from ``plain``, so a
    clang C++ dump classifies neither."""
    _require("g++", frontend)
    snap = _dump(tmp_path, _CXX_HEADER, _CXX_SOURCE, "c++", frontend)
    fns = {f.name: f for f in snap.declarations.functions}  # type: ignore[attr-defined]
    plain = struct_returns_in(snap)(fns["g_plain"])  # type: ignore[arg-type]
    if frontend == "castxml":
        assert plain is not None and plain.shape is RetShape.INT2
    else:
        assert plain is None
    assert struct_returns_in(snap)(fns["g_nocopy"]) is None  # type: ignore[arg-type]
    assert struct_returns_in(snap)(fns["g_owning"]) is None  # type: ignore[arg-type]
    # A base class is outside the modelled layout.
    assert struct_returns_in(snap)(fns["g_derived"]) is None  # type: ignore[arg-type]


def test_every_member_layout_change_agrees_with_execution(tmp_path: Path) -> None:
    _require("gcc", "castxml", "clang")
    spec = importlib.util.spec_from_file_location(
        "struct_ret_fragment",
        _ROOT / "formal" / "experiments" / "struct_ret_fragment.py",
    )
    assert spec is not None and spec.loader is not None
    frag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frag)
    res = frag.check(tmp_path, frag.space(), ("gcc",), ("castxml", "clang"))
    for name in ("gcc/castxml", "gcc/clang"):
        assert res["runs"][name]["disagree"] == 0, res["runs"][name]["examples"]
        assert res["runs"][name]["agree"] == 1190
