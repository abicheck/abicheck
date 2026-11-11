# SPDX-License-Identifier: Apache-2.0
"""CastXML and Clang report each change's declared ChangeKinds alike.

Each cell is an old/new public header pair whose expected ChangeKinds and
verdict are stated here from what the edit means for a consumer. It runs
through the public CLI under each header backend that is installed. The
cells are the two Clang gaps the edit corpus found once it dumped through
the public workflow (docs/contribute/plans/header-backend-parity-and-client-matrix.md):

* a static data member's access was never recorded by the Clang backend,
  and the access detector accepted only CastXML snapshots, so narrowing it
  read NO_CHANGE;
* a return type spelled through a typedef had no type-ABI fact under Clang
  (a function's own type is not sugar, so Clang gives it no desugared
  spelling), so removing the typedef read as a real return-type change
  (``func_return_changed``, BREAKING) instead of an ABI-equivalent one.

The native GCC backend's cells live in ``test_native_gcc_parity_regressions``.
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

from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration

_TOOL = {"castxml": "castxml", "clang": "clang"}


#: The same install name / SONAME on both sides, so neither reads as changed.
_SAME_LIBRARY_NAME = (
    ["-Wl,-install_name,@rpath/libparity.so"]
    if sys.platform == "darwin"
    else ["-Wl,-soname,libparity.so"]
)


@dataclass(frozen=True)
class Cell:
    name: str
    lang: str
    old_header: str
    new_header: str
    source: str
    expected: frozenset[str]
    forbidden: frozenset[str]
    verdict: str
    new_source: str | None = None  # when the edit is in the definitions


CELLS = (
    Cell(
        "static_member_access_narrowed",
        "c++",
        "class Q { public: int x; static int count; };\nint use(Q *q);\n",
        "class Q { public: int x; private: static int count; };\nint use(Q *q);\n",
        "int Q::count = 1;\nint use(Q *q) { return q->x; }\n",
        frozenset({"var_access_changed"}),
        frozenset(),
        "API_BREAK",
    ),
    Cell(
        "static_member_access_widened",
        "c++",
        "class Q { public: int x; protected: static int total; };\nint use(Q *q);\n",
        "class Q { public: int x; static int total; };\nint use(Q *q);\n",
        "int Q::total = 1;\nint use(Q *q) { return q->x; }\n",
        frozenset({"var_access_widened"}),
        frozenset({"var_access_changed"}),
        "COMPATIBLE",
    ),
    Cell(
        "typedef_return_removed_c",
        "c",
        "typedef int count_t;\ncount_t counted(count_t c);\n",
        "int counted(int c);\n",
        "int counted(int c) { return c + 1; }\n",
        frozenset({"typedef_removed"}),
        frozenset({"func_return_changed"}),
        "API_BREAK",
    ),
    Cell(
        # The nearest typedef names a record; a same-named outer typedef of
        # int must not lend it int's ABI fact, or the real return change
        # below would read as ABI-equivalent.
        "shadowed_typedef_return_retyped_cxx",
        "c++",
        "typedef int handle_t;\n"
        "namespace lib { struct H { long a; long b; }; typedef H handle_t;\n"
        "  handle_t make(int n); }\n",
        "typedef int handle_t;\nnamespace lib { int make(int n); }\n",
        "namespace lib { H make(int n) { H h = {n, n}; return h; } }\n",
        frozenset({"func_return_changed"}),
        frozenset({"abi_equivalent_type_changed"}),
        "BREAKING",
        new_source="namespace lib { int make(int n) { return n; } }\n",
    ),
    Cell(
        "namespaced_typedef_return_removed_cxx",
        "c++",
        "namespace lib { typedef long handle_t; handle_t make(int n); }\n",
        "namespace lib { long make(int n); }\n",
        "namespace lib { long make(int n) { return n; } }\n",
        frozenset({"typedef_removed"}),
        frozenset({"func_return_changed"}),
        "API_BREAK",
    ),
)


def _dump(
    work: Path,
    backend: str,
    side: str,
    cell: Cell,
    header_text: str,
    source_text: str,
) -> Path:
    ext, src_ext, cc = ("h", "c", "gcc") if cell.lang == "c" else ("hpp", "cpp", "g++")
    header = work / f"{side}.{ext}"
    header.write_text(header_text)
    source = work / f"{side}.{src_ext}"
    source.write_text(f'#include "{header.name}"\n{source_text}')
    # One library name for both sides: the default Mach-O install name is the
    # output path, so give it (and the ELF SONAME) explicitly.
    (work / side).mkdir(exist_ok=True)
    lib = work / side / "libparity.so"
    subprocess.run(
        [
            cc,
            "-shared",
            "-fPIC",
            "-g0",
            str(source),
            "-o",
            str(lib),
            *_SAME_LIBRARY_NAME,
        ],
        check=True,
        capture_output=True,
    )
    out = work / f"{side}.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "ABICHECK_AST_FRONTEND": backend}
    result = subprocess.run(
        abicheck_command("dump", lib, "-H", header, "-o", out),
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return out


@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.name)
def test_header_backend_reports_the_declared_change(
    cell: Cell, backend: str, tmp_path: Path
) -> None:
    if not shutil.which(_TOOL[backend]):
        pytest.skip(f"{backend} not installed")
    compiler = "gcc" if cell.lang == "c" else "g++"
    if not shutil.which(compiler):
        pytest.skip(f"{compiler} not installed")
    old = _dump(tmp_path, backend, "old", cell, cell.old_header, cell.source)
    new_source = cell.new_source if cell.new_source is not None else cell.source
    new = _dump(tmp_path, backend, "new", cell, cell.new_header, new_source)
    assert load_snapshot(old).ast_producer == backend
    report = tmp_path / "report.json"
    result = subprocess.run(
        abicheck_command("compare", old, new, "-o", f"json={report}"),
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    payload = json.loads(report.read_text())
    kinds = {c["kind"] for c in payload["changes"]}
    assert cell.expected <= kinds, (cell.name, backend, sorted(kinds), result.stderr)
    assert not (cell.forbidden & kinds), (cell.name, backend, sorted(kinds))
    assert payload["verdict"] == cell.verdict, (cell.name, backend, sorted(kinds))
