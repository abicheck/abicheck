# SPDX-License-Identifier: Apache-2.0
"""End-to-end: a builtin type change through ``abicheck compare`` is breaking.

``float _Complex`` -> ``double _Complex`` on a parameter, a struct field and a
return type (8 -> 16 bytes each). Before the fix the default header lane gave
``COMPATIBLE_WITH_RISK`` with a single ``declaration_renamed`` ("the same
declaration, renamed"), and the other lane gave ``NO_CHANGE`` because castxml
spelled both types ``Unimplemented``. Both lanes now report the change as a
break (exit 4) and never as a rename.

Runs ``python -m abicheck compare`` on gcc-built shared objects with ``-H``
headers, on both header backends: the default castxml lane and the clang lane
(``ABICHECK_AST_FRONTEND=clang``). Needs Linux, castxml, clang and gcc.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="gcc emits ELF on Linux only"),
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("clang") and shutil.which("gcc")),
        reason="needs castxml, clang and gcc",
    ),
]

_BREAKING = 4


@dataclass(frozen=True)
class _Shape:
    old_header: str
    old_body: str
    new_header: str
    new_body: str
    expected_kind: str


_SHAPES: dict[str, _Shape] = {
    "param": _Shape(
        old_header="void f(float _Complex p);\n",
        old_body="void f(float _Complex p) { (void)p; }\n",
        new_header="void f(double _Complex p);\n",
        new_body="void f(double _Complex p) { (void)p; }\n",
        expected_kind="func_params_changed",
    ),
    "field": _Shape(
        old_header="struct S { _Complex float c; int m; };\nvoid g(struct S *s);\n",
        old_body="void g(struct S *s) { (void)s; }\n",
        new_header="struct S { _Complex double c; int m; };\nvoid g(struct S *s);\n",
        new_body="void g(struct S *s) { (void)s; }\n",
        expected_kind="type_field_type_changed",
    ),
    "return": _Shape(
        old_header="_Complex float r(void);\n",
        old_body="_Complex float r(void) { return 0; }\n",
        new_header="_Complex double r(void);\n",
        new_body="_Complex double r(void) { return 0; }\n",
        expected_kind="func_return_changed",
    ),
    # The element type reached through a pointer, an array, a return pointer and
    # a typedef chain: castxml wraps the Unimplemented node in these declarators.
    "pointer_param": _Shape(
        old_header="void f(_Complex float *p);\n",
        old_body="void f(_Complex float *p) { (void)p; }\n",
        new_header="void f(_Complex double *p);\n",
        new_body="void f(_Complex double *p) { (void)p; }\n",
        expected_kind="func_params_changed",
    ),
    "array_field": _Shape(
        old_header="struct S { _Complex float c[4]; int m; };\nvoid g(struct S *s);\n",
        old_body="void g(struct S *s) { (void)s; }\n",
        new_header="struct S { _Complex double c[4]; int m; };\nvoid g(struct S *s);\n",
        new_body="void g(struct S *s) { (void)s; }\n",
        expected_kind="type_field_type_changed",
    ),
    "pointer_return": _Shape(
        old_header="_Complex float *r(void);\n",
        old_body="_Complex float *r(void) { static _Complex float v; return &v; }\n",
        new_header="_Complex double *r(void);\n",
        new_body="_Complex double *r(void) { static _Complex double v; return &v; }\n",
        expected_kind="func_return_changed",
    ),
    "typedef_chain_param": _Shape(
        old_header="typedef _Complex float cf;\ntypedef cf *cfp;\nvoid f(cfp p);\n",
        old_body="void f(cfp p) { (void)p; }\n",
        new_header="typedef _Complex double cf;\ntypedef cf *cfp;\nvoid f(cfp p);\n",
        new_body="void f(cfp p) { (void)p; }\n",
        expected_kind="typedef_base_changed",
    ),
}

_BACKENDS = {
    "castxml": {},
    "clang": {"ABICHECK_AST_FRONTEND": "clang"},
}

#: The checkout this test lives in. The subprocess must import *this* package:
#: from a tmp working directory ``python -m abicheck`` otherwise resolves to
#: whatever ``abicheck`` the interpreter has installed, not the code under test.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _build(root: Path, name: str, header: str, body: str) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    header_path = root / f"{name}.h"
    header_path.write_text(header)
    source_path = root / f"{name}.c"
    source_path.write_text(f'#include "{name}.h"\n{body}')
    so_path = root / f"lib{name}.so"
    built = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-w", "-o", str(so_path), str(source_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stderr
    return so_path, header_path


def _compare(
    tmp_path: Path, old: tuple[Path, Path], new: tuple[Path, Path], backend: str
) -> tuple[int, list[str], str]:
    report = tmp_path / f"report-{backend}.json"
    env = {
        **os.environ,
        **_BACKENDS[backend],
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
        ),
    }
    result = subprocess.run(
        abicheck_command("compare",
            str(old[0]),
            str(new[0]),
            "-H",
            f"old={old[1]}",
            "-H",
            f"new={new[1]}",
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
        env=env,
    )
    assert report.exists(), result.stdout + result.stderr
    payload = json.loads(report.read_text())
    kinds = [change["kind"] for change in payload.get("changes", [])]
    return result.returncode, kinds, payload.get("verdict", "")


@pytest.mark.parametrize("backend", sorted(_BACKENDS))
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_complex_size_change_is_breaking_not_a_rename(
    tmp_path: Path, shape: str, backend: str
) -> None:
    spec = _SHAPES[shape]
    old = _build(tmp_path / "old", "cx", spec.old_header, spec.old_body)
    new = _build(tmp_path / "new", "cx", spec.new_header, spec.new_body)
    code, kinds, verdict = _compare(tmp_path, old, new, backend)
    assert "declaration_renamed" not in kinds, kinds
    assert spec.expected_kind in kinds, kinds
    assert code == _BREAKING, (code, verdict, kinds)
    assert verdict == "BREAKING", verdict
