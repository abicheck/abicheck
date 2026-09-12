#!/usr/bin/env python3
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

"""The small, real C++ fixture the full-L2-CLI perf harness measures against.

Deliberately a *real* shared library with *real* public headers compiled by a
*real* compiler, not a hand-built ``AbiSnapshot``. The harness it serves
measures the whole ``abicheck`` CLI, and a synthetic snapshot would skip the
single most expensive and most regression-prone part of an L2 run: the header
frontend. ``benchmark_scaling.py`` already owns the compiler-free synthetic
level; this is the other one.

Three axes, each generated rather than committed so the sweep can be calibrated
locally without a tree full of binaries:

* **Shape** -- ``simple`` (plain records, functions, one virtual class) and
  ``templates`` (a bounded template-heavy header: instantiated class templates
  with member functions, which is what actually makes a header frontend
  expensive in the field). Bounded on purpose: the point is to cover the axis,
  not to build a pathological stress case, and an unbounded instantiation
  explosion would make the fixture's own *build* dominate the experiment.
* **Header count** -- how many top-level public headers the library publishes,
  all sharing one common dependency closure (a ``detail/`` header every one of
  them includes). Sharing matters: N independent headers measure N unrelated
  parses, while N headers over a shared closure measure what a real library's
  include graph does.
* **Library count** -- how many separate ``.so`` + header sets a multi-library
  run compares, either sharing one header context or each with its own. Each
  library gets genuinely distinct declarations; five copies of one snapshot
  would measure deduplication, not five libraries.

Every variant is produced in a pair: an ``old`` tree and a ``new`` tree. The
``new`` tree carries either *no* change (the unchanged-comparison axis) or a
specific, named public API/ABI break whose findings the harness asserts on, so
a run that reports a fast clean pass because it stopped detecting anything
fails rather than looks good.

Building a fixture is **not** a measured scan -- it is setup, and the harness
calls it outside every timed window.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The two finding *families* a ``break`` variant must produce, each as the set
#: of ``ChangeKind`` spellings that legitimately satisfy it. The harness
#: requires at least one member of each family.
#:
#: Families rather than exact kinds, established by measurement rather than
#: guessed: the same two source edits are reported as ``func_removed`` or
#: ``func_removed_elf_only`` depending on whether the header and export-table
#: evidence agree, and the widened record surfaces as
#: ``type_field_offset_changed``, ``type_size_changed`` or
#: ``struct_size_changed`` depending on the shape and which detector reaches it
#: first. Pinning one spelling would make this assertion a test of detector
#: routing, failing on a change that is not a regression at all.
#:
#: What it still asserts is the thing that matters, and it is much stronger
#: than "the verdict was BREAKING": a verdict can be reached by an unrelated
#: accident (or by a single leaked-internal finding), whereas *a removal was
#: seen* and *a layout change was seen* together can only come from evidence
#: that really reached both the export table and the header AST. That is the
#: assertion that makes "it got faster because it stopped doing the work" a
#: failure instead of an improvement.
EXPECTED_BREAK_KIND_FAMILIES: dict[str, tuple[str, ...]] = {
    "removal": (
        "func_removed",
        "func_removed_elf_only",
        "public_surface_shrank",
    ),
    "layout": (
        "type_size_changed",
        "struct_size_changed",
        "type_field_offset_changed",
        "type_field_added_compatible",
    ),
}

#: Declarations every built variant must expose, used by the harness as proof
#: that a run really parsed the headers rather than falling back to
#: binary-only. Names, not counts: a count can be matched by a degraded parse
#: that happened to find a different set.
EXPECTED_DECLARATIONS = ("distance", "Shape", "Point")


@dataclass(frozen=True)
class FixtureSpec:
    """One generated fixture variant. Its fields are the profile identity."""

    shape: str = "simple"
    headers: int = 1
    libraries: int = 1
    #: ``"break"`` applies :data:`EXPECTED_BREAK_KINDS`-producing edits to the
    #: new side; ``"unchanged"`` leaves the two sides identical.
    change: str = "break"
    #: ``True`` gives every library its own distinct header context (distinct
    #: include dirs and distinct shared-detail content); ``False`` has them
    #: share one. Only meaningful with ``libraries > 1``.
    distinct_contexts: bool = False

    @property
    def profile_id(self) -> str:
        ctx = "distinct" if self.distinct_contexts else "shared"
        return f"{self.shape}-h{self.headers}-l{self.libraries}-{self.change}" + (
            f"-{ctx}" if self.libraries > 1 else ""
        )


@dataclass(frozen=True)
class BuiltLibrary:
    so: Path
    headers: list[Path]
    include_dir: Path


@dataclass(frozen=True)
class BuiltFixture:
    spec: FixtureSpec
    old: list[BuiltLibrary]
    new: list[BuiltLibrary]


def _detail_header(lib: int, distinct: bool) -> str:
    # With shared contexts every library gets byte-identical detail content, so
    # a run can legitimately reuse one parse of it; with distinct contexts the
    # content differs, so it cannot. That difference is the axis.
    salt = f"    static const int ctx_salt_{lib} = {lib};\n" if distinct else ""
    # Per-library namespace (`detail0`, `detail1`, ...) with a `detail` alias, so
    # each library's internals are distinct declarations the way a real set of
    # independent libraries' internals are. Without this, two libraries' own
    # `detail/core.h` files declare the same `l2fx::detail::Tag`, and any parse
    # that sees both fails outright with a redefinition error -- which is exactly
    # what the first multi-library run did.
    return (
        "#pragma once\n"
        "namespace l2fx {\n"
        f"namespace detail{lib} {{\n"
        "struct Tag { int id; long generation; };\n"
        "enum class Mode { Fast, Exact, Adaptive };\n"
        f"{salt}"
        "}\n"
        f"namespace detail = detail{lib};\n"
        "}\n"
    )


def _simple_header(lib: int, index: int, *, widened: bool) -> str:
    extra = "  double z;\n" if widened else ""
    return f"""#pragma once
#include "detail/core.h"
namespace l2fx {{
namespace unit{lib}_{index} {{
struct Point {{
  double x;
  double y;
{extra}}};
class Shape {{
public:
  virtual ~Shape();
  virtual double area() const;
  virtual double perimeter() const;
  int tag() const;
  detail::Mode mode() const;
private:
  Point origin_;
  detail::Tag tag_;
}};
double distance(const Point& a, const Point& b);
int shape_count();
}}
}}
"""


def _template_header(lib: int, index: int, *, widened: bool) -> str:
    extra = "  T z;\n" if widened else ""
    # Bounded template weight: one class template with several member
    # functions, explicitly instantiated at three arithmetic types, plus a
    # small recursive alias chain. Enough to exercise the instantiation and
    # type-canonicalization paths a header frontend spends its time in,
    # without an explosion that would make building the fixture the
    # experiment.
    return f"""#pragma once
#include "detail/core.h"
namespace l2fx {{
namespace unit{lib}_{index} {{
template <typename T>
struct Vec {{
  T x;
  T y;
{extra}  T dot(const Vec<T>& other) const;
  Vec<T> scaled(T factor) const;
  bool near(const Vec<T>& other, T epsilon) const;
}};
template <typename T> struct Wrap {{ using type = Vec<T>; }};
template <typename T> using WrapT = typename Wrap<T>::type;
extern template struct Vec<float>;
extern template struct Vec<double>;
extern template struct Vec<int>;
struct Point {{ double x; double y;{" double z;" if widened else ""} }};
class Shape {{
public:
  virtual ~Shape();
  virtual double area() const;
  WrapT<double> axis() const;
private:
  Point origin_;
}};
double distance(const Point& a, const Point& b);
int shape_count();
}}
}}
"""


def _source(spec: FixtureSpec, lib: int, indices: list[int], *, broken: bool) -> str:
    parts = ['#include "all.h"\n', "namespace l2fx {\n"]
    for index in indices:
        ns = f"unit{lib}_{index}"
        parts.append(f"namespace {ns} {{\n")
        parts.append("Shape::~Shape() {}\n")
        parts.append("double Shape::area() const { return 1.0; }\n")
        if spec.shape == "simple":
            parts.append("double Shape::perimeter() const { return 2.0; }\n")
            parts.append("int Shape::tag() const { return tag_.id; }\n")
            parts.append(
                "detail::Mode Shape::mode() const { return detail::Mode::Fast; }\n"
            )
        else:
            parts.append("WrapT<double> Shape::axis() const { return {}; }\n")
            # Generic out-of-line member definitions followed by explicit
            # instantiation *definitions*, not per-type specializations: the
            # header's `extern template` declarations already trigger
            # instantiation, and a `template <>` specialization after that
            # point is ill-formed (g++ rejects it outright).
            parts.append(
                "template <typename T> T Vec<T>::dot(const Vec<T>& o) const "
                "{ return x * o.x + y * o.y; }\n"
                "template <typename T> Vec<T> Vec<T>::scaled(T f) const "
                "{ Vec<T> r = *this; r.x = r.x * f; r.y = r.y * f; return r; }\n"
                "template <typename T> bool Vec<T>::near(const Vec<T>& o, T e) const "
                "{ return (x - o.x) < e && (y - o.y) < e; }\n"
            )
            for t in ("float", "double", "int"):
                parts.append(f"template struct Vec<{t}>;\n")
        parts.append(
            "double distance(const Point& a, const Point& b) "
            "{ return a.x - b.x + a.y - b.y; }\n"
        )
        # The removal half of EXPECTED_BREAK_KIND_FAMILIES: gone from both the
        # header and the definition on the new side, so it really leaves the
        # export table rather than merely losing its declaration.
        if not broken:
            parts.append("int shape_count() { return 0; }\n")
        parts.append("}\n")
    parts.append("}\n")
    return "".join(parts)


def _write_library(
    root: Path, spec: FixtureSpec, lib: int, *, broken: bool, cxx: str
) -> BuiltLibrary:
    include = root / f"lib{lib}" / "include"
    (include / "detail").mkdir(parents=True, exist_ok=True)
    (include / "detail" / "core.h").write_text(
        _detail_header(lib, spec.distinct_contexts)
    )
    indices = list(range(spec.headers))
    headers: list[Path] = []
    render = _simple_header if spec.shape == "simple" else _template_header
    for index in indices:
        path = include / f"part{index}.h"
        path.write_text(render(lib, index, widened=broken))
        headers.append(path)
    # One aggregate header for the .cpp to include, so the translation unit
    # sees every part without the harness having to generate N sources.
    (include / "all.h").write_text(
        "#pragma once\n" + "".join(f'#include "part{i}.h"\n' for i in indices)
    )
    src = root / f"lib{lib}" / "impl.cpp"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(_source(spec, lib, indices, broken=broken))
    so = root / f"lib{lib}" / f"libl2fx{lib}.so"
    subprocess.run(
        [
            cxx,
            "-shared",
            "-fPIC",
            # -g: real DWARF, so the L1 evidence a real artifact carries is
            # present too and an L2 run is not accidentally measuring a
            # debug-info-free artifact.
            "-g",
            "-O0",
            "-std=c++17",
            f"-I{include}",
            "-o",
            str(so),
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    return BuiltLibrary(so=so, headers=headers, include_dir=include)


def compiler_available(cxx: str = "g++") -> bool:
    return shutil.which(cxx) is not None


def build(spec: FixtureSpec, root: Path, *, cxx: str = "g++") -> BuiltFixture:
    """Build *spec*'s old/new trees under *root*. Setup, never a measured scan."""
    old_root = root / "old"
    new_root = root / "new"
    old = [
        _write_library(old_root, spec, lib, broken=False, cxx=cxx)
        for lib in range(spec.libraries)
    ]
    # "broken" is applied only for a `break` variant; an `unchanged` variant
    # builds the identical sources into a separate tree, which is what makes it
    # a real two-artifact comparison rather than a self-comparison.
    new = [
        _write_library(new_root, spec, lib, broken=(spec.change == "break"), cxx=cxx)
        for lib in range(spec.libraries)
    ]
    return BuiltFixture(spec=spec, old=old, new=new)
