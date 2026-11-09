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

"""PE and Mach-O with ``-H`` report what ELF reports for the same sources.

Bug class ``extraction.header_surface_discarded_on_export_mismatch``. When no
header declaration's spelling met an export, the PE/Mach-O header-scoped
dump used to throw the parsed header surface away and compare the export
table alone. That happened for an MSVC DLL against an Itanium header parse,
and for a template-only header on either format. Header-only changes
(template defaults, concepts) then read ``NO_CHANGE``. A header that matched
one ``extern "C"`` export but none of its C++ declarations kept the surface
but read every C++ function as unexported.

Differential oracle: each shape is built three times from identical sources
on this Linux host: ELF with the host g++, PE with clang
``--target=x86_64-pc-windows-msvc`` (the Microsoft C++ ABI) linked by
lld-link, and Mach-O with clang ``--target=x86_64-apple-macos11`` linked by
ld64.lld. The ELF comparison's verdict and its ``(kind, count)`` multiset
are the expected answer for the other two formats, under both header
backends. Nothing here reads abicheck's own binding code.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

_CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "cases"

_CONCEPT_H = """#pragma once
namespace mylib {{
template <typename T>
concept Addable = {body};
template <Addable T> T sum(T a, T b);
}}
"""
_CONCEPT_CPP = """#include "{h}"
namespace mylib {{
template <Addable T> T sum(T a, T b) {{ return a + b; }}
template int sum<int>(int, int);
}}
"""
# One extern "C" export that matches exactly on every format, plus C++
# overloads, members, a static member and a variable that do not on PE.
_MIXED_H = """#pragma once
namespace outer {{ namespace inner {{
int f({f_param});
int over(int);{over_double}
struct S {{ int m(); static int sm(); }};
extern int gv;
}} }}
extern "C" int cfun(int);
"""
_MIXED_CPP = """#include "{h}"
namespace outer {{ namespace inner {{
int f({f_param} x) {{ return (int)x; }}
int over(int) {{ return 0; }}{over_double_def}
int S::m() {{ return 0; }}
int S::sm() {{ return 0; }}
int gv = 3;
}} }}
extern "C" int cfun(int x) {{ return x; }}
"""


def _write_mixed(root: Path) -> None:
    for v, f_param, has_double in (("v1", "int", True), ("v2", "long", False)):
        (root / f"{v}.h").write_text(
            _MIXED_H.format(
                f_param=f_param, over_double=" int over(double);" if has_double else ""
            )
        )
        (root / f"{v}.cpp").write_text(
            _MIXED_CPP.format(
                h=f"{v}.h",
                f_param=f_param,
                over_double_def=" int over(double) { return 1; }" if has_double else "",
            )
        )


def _write_concept(root: Path) -> None:
    bodies = {
        "v1": "requires(T a, T b) { a + b; }",
        "v2": "requires(T a, T b) { a + b; T(); }",
    }
    for v, body in bodies.items():
        (root / f"{v}.h").write_text(_CONCEPT_H.format(body=body))
        (root / f"{v}.cpp").write_text(_CONCEPT_CPP.format(h=f"{v}.h"))


def _copy_case(case: str):
    def write(root: Path) -> None:
        for name in ("v1.h", "v2.h", "v1.cpp", "v2.cpp"):
            shutil.copy(_CATALOG / case / name, root / name)

    return write


SHAPES = {
    "case223": (_copy_case("case223_header_only_template_default_changed"), "c++17"),
    "case225": (_copy_case("case225_header_only_template_default_removed"), "c++17"),
    "case227": (
        _copy_case("case227_nested_namespace_template_default_changed"),
        "c++17",
    ),
    "case228": (_copy_case("case228_nontype_template_param_default_changed"), "c++17"),
    "concept": (_write_concept, "c++20"),
    "mixed_c_and_cxx": (_write_mixed, "c++17"),
}


def _tool(*names: str) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    pytest.skip(f"cross-build tool not found: {names[0]}")


@pytest.fixture(scope="module")
def tools() -> dict[str, str]:
    return {
        "gxx": _tool("g++"),
        "clang": _tool("clang++", "clang"),
        "lld_link": _tool("lld-link", "lld-link-18"),
        "ld64": _tool("ld64.lld", "ld64.lld-18", "ld64.lld-19", "ld64.lld-20"),
        "nm": _tool("llvm-nm", "llvm-nm-18", "llvm-nm-19", "llvm-nm-20"),
    }


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    # A configured compiler that ran and failed is a failure, not a skip.
    assert proc.returncode == 0, f"{cmd}\n{proc.stdout}\n{proc.stderr}"


def _build(root: Path, v: str, std: str, tools: dict[str, str]) -> dict[str, Path]:
    src = root / f"{v}.cpp"
    out = {
        "elf": root / f"{v}.so",
        "pe": root / f"{v}.dll",
        "macho": root / f"{v}.dylib",
    }
    _run(
        [
            tools["gxx"],
            f"-std={std}",
            "-shared",
            "-fPIC",
            str(src),
            "-o",
            str(out["elf"]),
        ],
        root,
    )
    obj = root / f"{v}.obj"
    _run(
        [
            tools["clang"],
            "--target=x86_64-pc-windows-msvc",
            f"-std={std}",
            "-c",
            str(src),
            "-o",
            str(obj),
        ],
        root,
    )
    nm = subprocess.run(
        [tools["nm"], str(obj)], capture_output=True, text=True, check=True
    )
    exports = [
        f"/export:{m.group(1)}"
        for m in (
            re.match(r"^[0-9a-f]+ [TDBR] (\S+)$", ln) for ln in nm.stdout.splitlines()
        )
        if m
    ]
    assert exports, nm.stdout
    _run(
        [
            tools["lld_link"],
            "/dll",
            "/noentry",
            "/nodefaultlib",
            "/force:unresolved",
            f"/out:{out['pe']}",
            *exports,
            str(obj),
        ],
        root,
    )
    mobj = root / f"{v}.o"
    _run(
        [
            tools["clang"],
            "--target=x86_64-apple-macos11",
            f"-std={std}",
            "-c",
            str(src),
            "-o",
            str(mobj),
        ],
        root,
    )
    _run(
        [
            tools["ld64"],
            "-dylib",
            "-arch",
            "x86_64",
            "-platform_version",
            "macos",
            "11.0",
            "11.0",
            "-install_name",
            "@rpath/libapi.dylib",
            "-o",
            str(out["macho"]),
            str(mobj),
        ],
        root,
    )
    return out


def _compare(
    root: Path, old: Path, new: Path, backend: str
) -> tuple[str, Counter, str]:
    cfg = root / f"cfg-{backend}.yml"
    cfg.write_text(f"compile:\n  frontend: {backend}\n")
    report = root / f"{old.suffix[1:]}-{backend}.json"
    proc = subprocess.run(
        abicheck_command(
            "compare",
            old,
            new,
            "-H",
            f"old={root / 'v1.h'}",
            "-H",
            f"new={root / 'v2.h'}",
            "--config",
            cfg,
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
    )
    assert report.exists(), proc.stderr
    doc = json.loads(report.read_text())
    return doc["verdict"], Counter(c["kind"] for c in doc["changes"]), proc.stderr


@pytest.fixture(scope="module")
def built(tmp_path_factory, tools) -> dict[str, tuple[Path, dict, dict]]:
    out = {}
    for name, (write, std) in SHAPES.items():
        root = tmp_path_factory.mktemp(name)
        write(root)
        out[name] = (
            root,
            _build(root, "v1", std, tools),
            _build(root, "v2", std, tools),
        )
    return out


@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_pe_and_macho_match_elf(built, shape, backend):
    root, v1, v2 = built[shape]
    expected = _compare(root, v1["elf"], v2["elf"], backend)[:2]
    for fmt in ("pe", "macho"):
        verdict, kinds, stderr = _compare(root, v1[fmt], v2[fmt], backend)
        assert (verdict, kinds) == expected, (fmt, stderr[-2000:])
        assert "Falling back to export-table mode" not in stderr, (fmt, stderr[-2000:])


def test_the_oracle_sees_the_header_only_changes(built):
    """Guard against a vacuous parity: on clang the ELF oracle itself
    reports the header-only kinds this bug hid."""
    wanted = {
        "case223": "template_param_default_changed",
        "case225": "template_param_default_changed",
        "case227": "template_param_default_changed",
        "case228": "template_param_default_changed",
        "concept": "concept_tightened",
        "mixed_c_and_cxx": "func_removed",
    }
    for shape, kind in wanted.items():
        root, v1, v2 = built[shape]
        verdict, kinds, _ = _compare(root, v1["elf"], v2["elf"], "clang")
        assert kinds[kind] >= 1, (shape, verdict, kinds)
        assert verdict in ("API_BREAK", "BREAKING"), (shape, verdict)
