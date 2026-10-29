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

"""``compare --depth binary`` on header-built snapshots never reads falsely clean.

Bug class ``evidence.depth_projection_diverges_from_shallow_dump``: a
header-built snapshot does not persist the debug-info subprogram/variable
records or record relations a binary-only dump reads, so its ``binary``
projection cannot report a return/parameter type, typedef, vtable or base
change. Such a run read ``NO_CHANGE`` with ``complete`` assurance where a
binary-only dump of the same libraries reads ``BREAKING``.

Invariant, over sibling changes of each lost evidence family: whenever the
binary-only dumps of a pair read BREAKING (the oracle: an independent
extraction with no headers, run through the CLI), the projected header pair
either reports the break or reads ``partial`` assurance with the missing
evidence named; never a clean ``complete`` result. A binary-only pair
projected to ``binary`` and the unprojected header pair keep ``complete``
assurance: the gap belongs to the projection alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    # The oracle is a binary-only dump that reads the debug info embedded in
    # the library: ELF + DWARF. A Mach-O dylib keeps its DWARF in the objects
    # or a dSYM, so the binary-only side has no signatures to break on.
    pytest.mark.skipif(sys.platform != "linux", reason="needs ELF with embedded DWARF"),
]

#: name -> (v1 header, v2 header, v1 source body, v2 source body).
CASES: dict[str, tuple[str, str, str, str]] = {
    "return_type": (
        "int f(int);",
        "long f(int);",
        "int f(int x) { return x; }",
        "long f(int x) { return x; }",
    ),
    "param_type": (
        "int f(int);",
        "int f(long);",
        "int f(int x) { return x; }",
        "int f(long x) { return (int)x; }",
    ),
    "typedef": (
        "typedef int T; T f(T);",
        "typedef long T; T f(T);",
        "T f(T x) { return x; }",
        "T f(T x) { return x; }",
    ),
    "vtable": (
        "struct S { virtual int a(); virtual ~S(); }; int use(S*);",
        "struct S { virtual int b(); virtual int a(); virtual ~S(); }; int use(S*);",
        "int S::a() { return 1; } S::~S() {} int use(S* s) { return s->a(); }",
        "int S::a() { return 1; } int S::b() { return 2; } S::~S() {}"
        " int use(S* s) { return s->a(); }",
    ),
    "base": (
        "struct B1 { int x; }; struct B2 { long y; long w; };"
        " struct D : B1 { int z; }; int use(D*);",
        "struct B1 { int x; }; struct B2 { long y; long w; };"
        " struct D : B2 { int z; }; int use(D*);",
        "int use(D* d) { return d->z; }",
        "int use(D* d) { return d->z; }",
    ),
}


def _build(tmp: Path, name: str, idx: int) -> tuple[Path, Path]:
    header, body = CASES[name][idx - 1], CASES[name][idx + 1]
    hdr = tmp / f"v{idx}.h"
    hdr.write_text(header + "\n")
    src = tmp / f"v{idx}.cpp"
    src.write_text(f'#include "v{idx}.h"\n{body}\n')
    lib = tmp / f"libv{idx}.so"
    proc = subprocess.run(
        ["g++", "-g", "-shared", "-fPIC", str(src), "-o", str(lib)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return lib, hdr


def _abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(abicheck_command(*args), capture_output=True, text=True)


def _dump(lib: Path, out: Path, header: Path | None) -> Path:
    args = ["dump", str(lib), "-o", str(out)]
    if header is not None:
        args += ["-H", str(header)]
    proc = _abicheck(*args)
    assert proc.returncode == 0, proc.stderr
    return out


def _compare(old: Path, new: Path, report: Path, *extra: str) -> tuple[str, dict]:
    proc = _abicheck("compare", str(old), str(new), "-o", f"json={report}", *extra)
    assert report.exists(), proc.stderr
    doc = json.loads(report.read_text())
    return doc["verdict"], doc["analysis_assurance"]


@pytest.fixture(scope="module")
def _tools() -> None:
    if not (shutil.which("g++") and shutil.which("castxml")):
        pytest.skip("g++ and castxml are required")


@pytest.mark.parametrize("name", sorted(CASES))
def test_projected_header_pair_is_never_falsely_clean(
    name: str, tmp_path: Path, _tools: None
) -> None:
    v1, h1 = _build(tmp_path, name, 1)
    v2, h2 = _build(tmp_path, name, 2)
    b1 = _dump(v1, tmp_path / "b1.json", None)
    b2 = _dump(v2, tmp_path / "b2.json", None)
    r1 = _dump(v1, tmp_path / "h1.json", h1)
    r2 = _dump(v2, tmp_path / "h2.json", h2)

    oracle, oracle_assurance = _compare(b1, b2, tmp_path / "oracle.json")
    assert oracle == "BREAKING", f"{name}: the fixture must break at binary level"

    verdict, assurance = _compare(
        r1, r2, tmp_path / "projected.json", "--depth", "binary"
    )
    named = [n for n in assurance["notes"] if "not assessed at depth binary" in n]
    assert assurance["status"] == "partial", (name, verdict, assurance)
    assert any("signatures and typedefs" in n for n in named), assurance
    assert any("record relations" in n for n in named), assurance

    # A binary-only pair projected to binary loses nothing it stored.
    _, same = _compare(b1, b2, tmp_path / "bin_projected.json", "--depth", "binary")
    assert same["status"] == oracle_assurance["status"]
    assert not any("not assessed at depth" in n for n in same["notes"])
    # The unprojected header pair is untouched.
    _, full = _compare(r1, r2, tmp_path / "full.json")
    assert not any("not assessed at depth" in n for n in full["notes"])
