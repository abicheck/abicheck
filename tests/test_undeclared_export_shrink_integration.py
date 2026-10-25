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

"""End to end: a header-mode export shrink is reported through the CLI JSON.

Real gcc/g++-built libraries, castxml header parsing, ``abicheck compare``
with ``-H`` and no ``--contract``. Oracle: ``readelf --dyn-syms`` (defined,
default-visibility, GLOBAL/WEAK FUNC/OBJECT) on each library, set difference,
independent of abicheck's ELF reader. Every lost export must appear as a
removal finding in ``changes`` with ``severity == "breaking"``, the verdict
must be BREAKING and the exit code 4 (ADR-063 2026-10-01 amendment, catalog
case182). Negative controls: the export set unchanged, and only
hidden-visibility (never exported) functions removed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs gcc/g++"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml"),
    pytest.mark.skipif(shutil.which("readelf") is None, reason="needs readelf"),
]

_REMOVAL_KINDS = {
    "func_removed",
    "func_removed_elf_only",
    "var_removed",
    "var_removed_elf_only",
}


def _exports(so: Path) -> set[str]:
    out = subprocess.run(
        ["readelf", "-W", "--dyn-syms", str(so)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names: set[str] = set()
    for line in out.splitlines():
        cols = line.split()
        # Num: Value Size Type Bind Vis Ndx Name
        if (
            len(cols) < 8
            or cols[3] not in ("FUNC", "OBJECT")
            or cols[4] not in ("GLOBAL", "WEAK")
        ):
            continue
        if cols[5] != "DEFAULT" or cols[6] == "UND":
            continue
        names.add(cols[7].split("@")[0])
    return names


def _build(
    d: Path, lang: str, body: str, header: str, version_script: str | None
) -> Path:
    ext = "c" if lang == "c" else "cpp"
    (d / f"api.{'h' if lang == 'c' else 'hpp'}").write_text(header)
    src = d / f"lib.{ext}"
    src.write_text(body)
    so = d / "libshrink.so"
    cmd = [
        "gcc" if lang == "c" else "g++",
        "-shared",
        "-fPIC",
        "-O0",
        str(src),
        "-o",
        str(so),
    ]
    if version_script is not None:
        (d / "v.map").write_text(version_script)
        cmd.insert(1, f"-Wl,--version-script={d / 'v.map'}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"{cmd}\n{body}\n{proc.stderr}"
    return so


def _lib(lang: str, n: int, declared: int, hidden: int = 0) -> tuple[str, str]:
    """(header, source): one declared anchor, ``declared`` declared helpers
    among the first ``n`` exports, ``hidden`` hidden-visibility functions."""
    inc = '#include "api.h"' if lang == "c" else '#include "api.hpp"'
    if lang == "c":
        decls = ["int api_entry(void);"] + [
            f"int helper_{i}(void);" for i in range(declared)
        ]
        defs = ["int api_entry(void){return 0;}"]
        defs += [f"int helper_{i}(void){{return {i};}}" for i in range(n)]
        defs += [f"int data_{i} = {i};" for i in range(n // 2)]
        defs += [
            f'__attribute__((visibility("hidden"))) int hid_{i}(void){{return {i};}}'
            for i in range(hidden)
        ]
        return "\n".join(decls) + "\n", inc + "\n" + "\n".join(defs) + "\n"
    decls = (
        ["namespace q { int api_entry(int); "]
        + [f"int helper_{i}(int);" for i in range(declared)]
        + ["}"]
    )
    defs = ["namespace q { int api_entry(int x){return x;}"]
    defs += [f"int helper_{i}(int x){{return x+{i};}}" for i in range(n)]
    defs += [f"int data_{i} = {i};" for i in range(n // 2)]
    defs += [
        f'__attribute__((visibility("hidden"))) int hid_{i}(int x){{return x;}}'
        for i in range(hidden)
    ]
    defs += ["}"]
    return " ".join(decls) + "\n", inc + "\n" + "\n".join(defs) + "\n"


def _compare(
    tmp: Path, lang: str, old: tuple[str, str], new: tuple[str, str], versioned: bool
) -> tuple[int, dict, set[str], set[str]]:
    vs = "LIB_1.0 { global: *; };\n" if versioned else None
    sos = []
    for side, (header, body) in (("old", old), ("new", new)):
        d = tmp / side
        d.mkdir()
        sos.append(_build(d, lang, body, header, vs))
    hdr = "api.h" if lang == "c" else "api.hpp"
    out = tmp / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(sos[0]),
            str(sos[1]),
            "--header",
            f"old={tmp / 'old' / hdr}",
            "--header",
            f"new={tmp / 'new' / hdr}",
            "-o",
            f"json={out}",
        ],
    )
    assert result.exit_code in (0, 2, 4), result.output
    return (
        result.exit_code,
        json.loads(out.read_text()),
        _exports(sos[0]),
        _exports(sos[1]),
    )


@pytest.mark.parametrize("lang", ["c", "cxx"])
@pytest.mark.parametrize("versioned", [False, True])
@pytest.mark.parametrize("old_n,new_n,declared", [(2, 1, 0), (12, 4, 3), (60, 20, 0)])
def test_header_mode_export_shrink_is_reported(
    tmp_path: Path, lang: str, versioned: bool, old_n: int, new_n: int, declared: int
) -> None:
    code, report, old_exp, new_exp = _compare(
        tmp_path,
        lang,
        _lib(lang, old_n, declared),
        _lib(lang, new_n, min(declared, new_n)),
        versioned,
    )
    lost = old_exp - new_exp
    assert lost, "fixture must shrink the export table"
    removed = {c["symbol"] for c in report["changes"] if c["kind"] in _REMOVAL_KINDS}
    assert lost <= removed
    assert all(
        c["severity"] == "breaking"
        for c in report["changes"]
        if c["symbol"] in lost and c["kind"] in _REMOVAL_KINDS
    )
    assert report["verdict"] == "BREAKING"
    assert code == 4
    assert report["disposition_audit"]["counts"]["gating"] >= len(lost)


@pytest.mark.parametrize("lang", ["c", "cxx"])
def test_unchanged_export_set_reports_no_removal(tmp_path: Path, lang: str) -> None:
    code, report, old_exp, new_exp = _compare(
        tmp_path, lang, _lib(lang, 10, 2), _lib(lang, 10, 2), False
    )
    assert old_exp == new_exp
    assert [c for c in report["changes"] if c["kind"] in _REMOVAL_KINDS] == []
    assert code == 0


@pytest.mark.parametrize("lang", ["c", "cxx"])
def test_removing_hidden_never_exported_functions_reports_no_removal(
    tmp_path: Path, lang: str
) -> None:
    code, report, old_exp, new_exp = _compare(
        tmp_path, lang, _lib(lang, 5, 1, hidden=20), _lib(lang, 5, 1, hidden=0), False
    )
    assert old_exp == new_exp
    assert [c for c in report["changes"] if c["kind"] in _REMOVAL_KINDS] == []
    assert code == 0
