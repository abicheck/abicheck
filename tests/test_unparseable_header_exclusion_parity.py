# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Automatic exclusion of an unparseable header == pre-filtering it by hand.

Invariant: comparing with ``-H include/`` where one header cannot compile
(abicheck drops it, ``extract/unparseable_header_fallback.py``) yields the
*same* findings and RISK count as passing only the parseable headers, plus
exactly one reduced-evidence disclosure per side that excluded something --
never extra RISK findings for the excluded header's declarations.

The oracle is the manually pre-filtered run, an independent path through the
CLI that never touches the fallback. Covered across C and C++, with and
without exported symbols that only the excluded header declares.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

_GOOD_A = {
    1: "struct A { int x; };\nint fa(struct A*);\nint fa2(int);\n",
    2: "struct A { int x; long y; };\nint fa(struct A*);\n",
}
_CXX_EXTRA = (
    "class Bad { public: Bad(); virtual ~Bad(); virtual int f(); };\n"
    "Bad::Bad(){}\nBad::~Bad(){}\nint Bad::f(){return 1;}\n"
    "namespace ns { struct In { int g(); }; int In::g(){return 2;} }\n"
)


def _build(root: Path, *, cxx: bool, bad_declares_exports: bool) -> None:
    compiler = "g++" if cxx else "gcc"
    if shutil.which(compiler) is None or shutil.which("castxml") is None:
        pytest.skip(f"{compiler} and castxml are required")
    bad = '#error "unsupported compiler"\n'
    if bad_declares_exports:
        bad += "int fbad(int);\nint fbad2(int);\n"
    for v in (1, 2):
        inc = root / f"v{v}" / "inc"
        inc.mkdir(parents=True)
        (inc / "a.h").write_text(_GOOD_A[v])
        (inc / "b.h").write_text("int fb(int);\n")
        (inc / "bad.h").write_text(bad)
        src = '#include "inc/a.h"\nint fa(struct A*a){return a->x;}\nint fb(int x){return x;}\n'
        src += "int fbad(int x){return x;}\n"
        src += (
            "int fa2(int x){return x;}\nint fbad2(int x){return x;}\n" if v == 1 else ""
        )
        if cxx:
            src += _CXX_EXTRA
        unit = root / f"v{v}" / ("l.cpp" if cxx else "l.c")
        unit.write_text(src)
        lib = root / f"v{v}" / "libx.so"
        subprocess.run(
            [compiler, "-g", "-shared", "-fPIC", "-o", str(lib), str(unit)], check=True
        )


def _compare(root: Path, name: str, headers: list[str]) -> dict:
    args = abicheck_command(
        "compare",
        str(root / "v1/libx.so"),
        str(root / "v2/libx.so"),
    )
    for side, v in (("old", 1), ("new", 2)):
        for h in headers:
            args += ["-H", f"{side}={root / f'v{v}' / h}"]
    out = root / f"{name}.json"
    proc = subprocess.run([*args, "-o", f"json={out}"], capture_output=True, text=True)
    assert proc.returncode in (0, 1, 2, 4), proc.stderr
    return json.loads(out.read_text())


def _findings(report: dict) -> Counter[tuple[str, str, str]]:
    return Counter(
        (c["kind"], c.get("symbol") or "", c.get("verdict") or "")
        for c in report["changes"]
    )


def _exclusion_notes(report: dict) -> list[str]:
    return [w for w in report.get("coverage_warnings", []) if "could not compile" in w]


@pytest.mark.parametrize("cxx", [False, True], ids=["c", "cxx"])
@pytest.mark.parametrize(
    "bad_declares_exports", [True, False], ids=["exports", "no-exports"]
)
def test_auto_exclusion_matches_manual_prefilter(
    tmp_path: Path, cxx: bool, bad_declares_exports: bool
) -> None:
    _build(tmp_path, cxx=cxx, bad_declares_exports=bad_declares_exports)
    auto = _compare(tmp_path, "auto", ["inc"])
    manual = _compare(tmp_path, "manual", ["inc/a.h", "inc/b.h"])

    assert _findings(auto) == _findings(manual)
    assert auto["summary"]["risk_changes"] == manual["summary"]["risk_changes"]
    assert auto["summary"]["breaking"] == manual["summary"]["breaking"] > 0
    assert auto["result_counts"] == manual["result_counts"]

    # Disclosure: one note per side that excluded the header, none when the
    # caller pre-filtered -- visible, and never a finding.
    notes = _exclusion_notes(auto)
    assert sorted(n.split(":", 1)[0] for n in notes) == ["new", "old"]
    assert all("bad.h" in n for n in notes)
    assert _exclusion_notes(manual) == []
