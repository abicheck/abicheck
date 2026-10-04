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


def _header(n: int, v2: bool, fraction: float, tag: str = "") -> str:
    out = ["#pragma once", "#include <cstddef>", f"namespace lib{tag} {{"]
    for ns in range(NAMESPACES):
        out.append(f"namespace m{ns} {{ struct Ctx {{ int id; }}; }}")
    for i in range(n):
        ch = v2 and _changed(i, fraction)
        ns = i % NAMESPACES
        # Fixed width: no unit's name is a prefix of another's (`Data1` of
        # `Data17`), which would make prefix-candidate work grow with the
        # share of multi-digit indices rather than with the library.
        u = f"{i:04d}"
        extra = "  long added;\n" if ch else ""
        ptype = "long" if ch else "int"
        bump = 7 if ch else 0
        out.append(
            f"""namespace m{ns} {{
struct Data{u} {{
  int a;
  double b;
  char tag[4];
{extra}}};
typedef Data{u} Alias{u};
typedef Alias{u} Alias{u}_t;
enum class Mode{u} {{ off = 0, on = {1 + bump}, fast = 9 }};
class Svc{u} {{
public:
  virtual ~Svc{u}();
  virtual int run(const Alias{u}_t& d, Mode{u} m);
  virtual void reset(Ctx* c);
  int counter;
}};
int free{u}({ptype} x, Data{u}* d);
int over{u}(int x);
int over{u}(double x);
template <typename T> T tmpl{u}(T v, const Data{u}& d);
}}"""
        )
    out.append("}")
    return "\n".join(out) + "\n"


def _source(n: int, v2: bool, fraction: float, tag: str = "") -> str:
    out = ['#include "lib.h"', f"namespace lib{tag} {{"]
    for i in range(n):
        ch = v2 and _changed(i, fraction)
        ns = i % NAMESPACES
        # Fixed width: no unit's name is a prefix of another's (`Data1` of
        # `Data17`), which would make prefix-candidate work grow with the
        # share of multi-digit indices rather than with the library.
        u = f"{i:04d}"
        ptype = "long" if ch else "int"
        out.append(
            f"""namespace m{ns} {{
Svc{u}::~Svc{u}() {{}}
int Svc{u}::run(const Alias{u}_t& d, Mode{u} m) {{ return d.a + static_cast<int>(m); }}
void Svc{u}::reset(Ctx* c) {{ c->id = 0; }}
int free{u}({ptype} x, Data{u}* d) {{ return static_cast<int>(x) + d->a; }}
int over{u}(int x) {{ return x; }}
int over{u}(double x) {{ return static_cast<int>(x); }}
template <typename T> T tmpl{u}(T v, const Data{u}& d) {{ return v + static_cast<T>(d.a); }}
template int tmpl{u}<int>(int, const Data{u}&);
template double tmpl{u}<double>(double, const Data{u}&);
}}"""
        )
    out.append("}")
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class BuiltLibrary:
    so: Path
    header: Path


def build_library(
    root: Path, n: int, *, v2: bool = False, change_fraction: float = 0.1, tag: str = ""
) -> BuiltLibrary:
    """Write and compile one side; returns the ``.so`` and its header.

    *tag* salts the outer namespace, so every mangled name is new: the
    demangling and spelling caches are process-wide, and two runs over the
    same names would measure the second against a warm cache.
    """
    if tag and not tag.isidentifier():
        raise ValueError(f"tag must be an identifier fragment: {tag!r}")
    root.mkdir(parents=True, exist_ok=True)
    header = root / "lib.h"
    header.write_text(_header(n, v2, change_fraction, tag), encoding="utf-8")
    src = root / "lib.cpp"
    src.write_text(_source(n, v2, change_fraction, tag), encoding="utf-8")
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


def build_pair(root: Path, n: int, *, change_fraction: float = 0.1, tag: str = ""):
    """Build and dump v1 and v2; returns ``(old_snapshot, new_snapshot)``."""
    v1 = build_library(root / "v1", n, tag=tag)
    v2 = build_library(
        root / "v2", n, v2=True, change_fraction=change_fraction, tag=tag
    )
    return dump_library(v1, "1.0"), dump_library(v2, "2.0")
