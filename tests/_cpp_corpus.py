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

"""A generated, *real* C++ library of any size, built and dumped through
the production pipeline -- the realistic corpus the synthetic workloads in
``_compare_workloads.py`` cannot be.

Synthetic snapshots are cheap and exact, but they are only as realistic as
whoever wrote them: no mangling quirks, no castxml spelling, no template
instantiations, no real surface facts. This module writes a header and a
source file for a library of ``n`` "units", compiles it with ``g++``, and
returns ``dumper.dump()``'s snapshot, so every gate built on it measures the
code path a user's ``abicheck dump``/``compare`` actually runs.

Each unit ``i`` contributes, in namespace ``lib::m<i % NAMESPACES>``:

* a ``struct Data<i>`` with a few fields (layout facts);
* a ``class Svc<i>`` with virtual methods (vtable facts);
* a free function and an overload set (mangling, overload identity);
* a function template ``tmpl<i>`` explicitly instantiated twice;
* an ``enum class Mode<i>``;
* a typedef chain ``Alias<i> -> Data<i>``;
* a ``Ctx`` struct whose short name collides across namespaces.

``change_fraction`` makes ``v2`` change that share of the units: a field is
appended to ``Data<i>`` (a layout break that propagates to every function
using it), a parameter type widens, and one enumerator moves.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

NAMESPACES = 8


def toolchain_available() -> bool:
    return shutil.which("g++") is not None and shutil.which("castxml") is not None


def _changed(i: int, fraction: float) -> bool:
    # Deterministic and evenly spread: unit i changes when it falls in the
    # first `fraction` of every block of 100.
    return (i % 100) < round(fraction * 100)


def _header(n: int, v2: bool, fraction: float) -> str:
    out = ["#pragma once", "#include <cstddef>", "namespace lib {"]
    for ns in range(NAMESPACES):
        out.append(f"namespace m{ns} {{ struct Ctx {{ int id; }}; }}")
    for i in range(n):
        ch = v2 and _changed(i, fraction)
        ns = i % NAMESPACES
        extra = "  long added;\n" if ch else ""
        ptype = "long" if ch else "int"
        bump = 7 if ch else 0
        out.append(
            f"""namespace m{ns} {{
struct Data{i} {{
  int a;
  double b;
  char tag[4];
{extra}}};
typedef Data{i} Alias{i};
typedef Alias{i} Alias{i}_t;
enum class Mode{i} {{ off = 0, on = {1 + bump}, fast = 9 }};
class Svc{i} {{
public:
  virtual ~Svc{i}();
  virtual int run(const Alias{i}_t& d, Mode{i} m);
  virtual void reset(Ctx* c);
  int counter;
}};
int free{i}({ptype} x, Data{i}* d);
int over{i}(int x);
int over{i}(double x);
template <typename T> T tmpl{i}(T v, const Data{i}& d);
}}"""
        )
    out.append("}")
    return "\n".join(out) + "\n"


def _source(n: int, v2: bool, fraction: float) -> str:
    out = ['#include "lib.h"', "namespace lib {"]
    for i in range(n):
        ch = v2 and _changed(i, fraction)
        ns = i % NAMESPACES
        ptype = "long" if ch else "int"
        out.append(
            f"""namespace m{ns} {{
Svc{i}::~Svc{i}() {{}}
int Svc{i}::run(const Alias{i}_t& d, Mode{i} m) {{ return d.a + static_cast<int>(m); }}
void Svc{i}::reset(Ctx* c) {{ c->id = 0; }}
int free{i}({ptype} x, Data{i}* d) {{ return static_cast<int>(x) + d->a; }}
int over{i}(int x) {{ return x; }}
int over{i}(double x) {{ return static_cast<int>(x); }}
template <typename T> T tmpl{i}(T v, const Data{i}& d) {{ return v + static_cast<T>(d.a); }}
template int tmpl{i}<int>(int, const Data{i}&);
template double tmpl{i}<double>(double, const Data{i}&);
}}"""
        )
    out.append("}")
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class BuiltLibrary:
    so: Path
    header: Path


def build_library(
    root: Path, n: int, *, v2: bool = False, change_fraction: float = 0.1
) -> BuiltLibrary:
    """Write and compile one side; returns the ``.so`` and its header."""
    root.mkdir(parents=True, exist_ok=True)
    header = root / "lib.h"
    header.write_text(_header(n, v2, change_fraction), encoding="utf-8")
    src = root / "lib.cpp"
    src.write_text(_source(n, v2, change_fraction), encoding="utf-8")
    so = root / "libgen.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-O0",
            "-std=c++17",
            "-I",
            str(root),
            "-o",
            str(so),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    return BuiltLibrary(so=so, header=header)


def dump_library(lib: BuiltLibrary, version: str):
    """``dumper.dump()`` of *lib* -- the production L2 path (castxml)."""
    from abicheck.dumper import dump

    return dump(lib.so, [lib.header], version=version, compiler="c++")


def build_pair(root: Path, n: int, *, change_fraction: float = 0.1):
    """Build and dump v1 and v2; returns ``(old_snapshot, new_snapshot)``."""
    v1 = build_library(root / "v1", n)
    v2 = build_library(root / "v2", n, v2=True, change_fraction=change_fraction)
    return dump_library(v1, "1.0"), dump_library(v2, "2.0")
