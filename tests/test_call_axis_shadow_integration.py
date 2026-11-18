"""ADR-094 D9 end to end: the shadow ``call`` axis on real L2 compares.

Each case builds two versions of a small C library with gcc, compares them
through the public CLI with their headers (castxml, L2; no debug info), and
checks the ``call_axis_shadow`` block of the JSON report and the Markdown
table, and that the exit code is the one the existing verdict gives. The
oracle is the x86-64 SysV register assignment stated per case.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("gcc") is None or shutil.which("castxml") is None,
        reason="needs gcc and castxml",
    ),
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"),
        reason="the call axis models x86-64 SysV only",
    ),
]

#: name -> (old decl, new decl, old body, new body, axis status, disagreement)
_CASES = {
    "widen": (
        "long f(int x)",
        "long f(long x)",
        "return x;",
        "return x;",
        "broken",
        None,
    ),
    "drop_trailing": (
        "int f(int a, int b)",
        "int f(int a)",
        "return a + b;",
        "return a;",
        "preserved",
        "breaking_but_call_preserved",
    ),
    "append": (
        "int f(int a)",
        "int f(int a, int b)",
        "return a;",
        "return a + b;",
        "broken",
        None,
    ),
    "drop_leading": (
        "int f(int a, int b)",
        "int f(int b)",
        "return a + b;",
        "return b;",
        "broken",  # b moves from rsi to rdi
        None,
    ),
}


def _build(root: Path, decl: str, body: str) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    header = root / "lib.h"
    header.write_text(f"{decl};\n")
    src = root / "lib.c"
    src.write_text(f'#include "lib.h"\n{decl} {{ {body} }}\n')
    lib = root / "libdemo.so"
    proc = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return lib, header


@pytest.mark.parametrize("case", sorted(_CASES))
def test_cli_l2_compare_reports_the_call_axis(tmp_path: Path, case: str) -> None:
    old_decl, new_decl, old_body, new_body, status, disagreement = _CASES[case]
    old_lib, old_h = _build(tmp_path / "v1", old_decl, old_body)
    new_lib, new_h = _build(tmp_path / "v2", new_decl, new_body)
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(old_lib),
            str(new_lib),
            "--header",
            f"old={old_h}",
            "--header",
            f"new={new_h}",
            "-o",
            f"json={out_json}",
            "-o",
            f"markdown={out_md}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    doc = json.loads(out_json.read_text())
    # Shadow only: the exit code still follows the existing verdict.
    assert doc["verdict"] == "BREAKING"
    assert proc.returncode == 4, proc.stderr
    (entry,) = doc["call_axis_shadow"]
    assert entry["symbol"] == "f"
    assert entry["abi"] == "sysv_amd64"
    assert entry["change_kinds"] == ["func_params_changed"]
    assert entry["status"] == status, entry["reason"]
    assert entry["existing_verdict"] == "BREAKING"
    assert entry["disagreement"] == disagreement
    md = out_md.read_text()
    assert "## Call axis (shadow, ADR-094)" in md
    (row,) = [line for line in md.splitlines() if line.startswith("| `f` |")]
    assert f"| {status} |" in row
