# SPDX-License-Identifier: Apache-2.0
"""Evidence monotonicity: ``--build-info`` never drops a clang record layout.

The clang header backend reads record alignment from its layout probe
(``attach_clang_layout``; DWARF carries no alignment). That probe used to be
handed the raw CLI ``lang`` default (``c++``) while the main header parse
auto-detected C, so a C build's ``-std=gnu11`` from ``--build-info`` made
clang reject the C++ probe and every record lost ``alignment_bits`` -- the
build-source catalog lane then stopped reporting ``type_alignment_changed``
that the poorer debug-headers lane reported (catalog case26, 36, 40, 45, 48,
56, 57, 70).

Invariant: for every C record shape and every C ``-std=`` a build may carry,
the clang dump with ``--build-info`` records the same alignment as the
compiler itself (``_Alignof``, the oracle, compiled by gcc), and the same as
the dump without build info.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.serialization import load_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("clang") and shutil.which("gcc"))
        or not sys.platform.startswith("linux"),
        reason="needs clang (header frontend) and gcc (oracle) on Linux",
    ),
]

#: name -> body; each is a distinct alignment-determining shape.
_RECORDS = {
    "U": "union U { int i; float f; double d; };",
    "S": "struct S { char c; long long q; };",
    "P": "struct __attribute__((packed)) P { char c; int i; };",
    "A": "struct A { char c; _Alignas(16) int x; };",
    "F": "struct F { short n; double tail[]; };",
}
_STDS = ("gnu11", "c99", "c17")


def _oracle_alignments(tmp: Path, header: Path) -> dict[str, int]:
    prog = tmp / "oracle.c"
    prog.write_text(
        f'#include <stdio.h>\n#include "{header}"\nint main(void) {{\n'
        + "".join(
            f'  printf("{n} %zu\\n", _Alignof({body.split("{")[0].strip().replace("__attribute__((packed)) ", "")}));\n'
            for n, body in _RECORDS.items()
        )
        + "  return 0;\n}\n"
    )
    exe = tmp / "oracle"
    subprocess.run(["gcc", "-std=gnu11", str(prog), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    return {n: int(a) * 8 for n, a in (ln.split() for ln in out.stdout.splitlines())}


def _dump(tmp: Path, lib: Path, header: Path, build_info: Path | None) -> dict:
    out = tmp / ("bi.json" if build_info else "plain.json")
    cmd = [sys.executable, "-m", "abicheck", "dump", str(lib), "-o", str(out)]
    cmd += ["-H", str(header)]
    if build_info is not None:
        cmd += ["--build-info", str(build_info)]
    env = {
        **os.environ,
        "ABICHECK_AST_FRONTEND": "clang",
        "XDG_CACHE_HOME": str(tmp / "cache"),
    }
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    snap = load_snapshot(out)
    return {t.name: t.alignment_bits for t in snap.declarations.types}


@pytest.mark.parametrize("std", _STDS)
def test_build_info_keeps_clang_record_alignment(tmp_path: Path, std: str) -> None:
    header = tmp_path / "lib.h"
    header.write_text(
        "#ifndef LIB_H\n#define LIB_H\n"
        + "\n".join(_RECORDS.values())
        + "\n"
        + "".join(
            f"void use_{n}({b.split('{')[0].strip()} *p);\n"
            for n, b in _RECORDS.items()
        )
        + "#endif\n"
    )
    src = tmp_path / "lib.c"
    src.write_text(
        '#include "lib.h"\n'
        + "".join(
            f"void use_{n}({b.split('{')[0].strip()} *p) {{ (void)p; }}\n"
            for n, b in _RECORDS.items()
        )
    )
    lib = tmp_path / "liblib.so"
    subprocess.run(
        ["gcc", f"-std={std}", "-shared", "-fPIC", "-g", str(src), "-o", str(lib)],
        check=True,
    )
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "command": f"/usr/bin/cc -std={std} -fPIC -g -I{tmp_path} -c {src}",
                }
            ]
        )
    )
    expected = _oracle_alignments(tmp_path, header)
    plain = _dump(tmp_path, lib, header, None)
    rich = _dump(tmp_path, lib, header, db)
    got = {n: rich.get(n) for n in _RECORDS}
    assert got == expected
    # Adding evidence never removes a fact the poorer dump had.
    assert got == {n: plain.get(n) for n in _RECORDS}
