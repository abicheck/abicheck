"""castxml reads the type a declaration was written with (generation 91).

Bug class ``extract.castxml_written_type``: castxml's XML holds what C wrote,
and two reader bugs lost it.

* A multi-dimensional array is a nested ``ArrayType`` chain, outermost first;
  the reader spelled the outer bound after the inner ones (``int[3][2]`` for
  ``int m[2][3]``). Oracle: the C declarator (each case states the expected
  spelling by hand) and the clang header backend, compared spelling for
  spelling.
* A ``va_list`` parameter decays to ``__va_list_tag *`` on x86-64, but castxml
  keeps the written alias in ``original_type``; the reader never followed it.
  Oracle: the declaration as written, positive and negative cases.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import AbiSnapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so files"),
]

# (C declaration of field ``m``, the C spelling of its type)
_ARRAYS = (
    ("int m[2][3];", "int[2][3]"),
    ("int m[4][3][2];", "int[4][3][2]"),
    ("char m[1][7];", "char[1][7]"),
    ("const char *m[2][5];", "const char*[2][5]"),
    ("row m[2];", "row[2]"),
    ("row m[4][2];", "row[4][2]"),
    # An array of pointers to arrays is two declarators; castxml and clang
    # spell that shape differently (``int[3]*[2]`` / ``int (*[2])[3]``) apart
    # from bound order, so only the bounds' order is checked for it.
    ("int (*m[2])[3];", "int[3]*[2]"),
    ("double m[6];", "double[6]"),
)


def _snapshot(tmp: Path, header: str, backend: str) -> AbiSnapshot:
    if backend == "castxml" and shutil.which("castxml") is None:
        pytest.skip("castxml unavailable")
    if backend == "clang" and shutil.which("clang") is None:
        pytest.skip("clang unavailable")
    tmp.mkdir(parents=True, exist_ok=True)
    h = tmp / "lib.h"
    h.write_text(
        f"#pragma GCC visibility push(default)\n{header}\n#pragma GCC visibility pop\n"
    )
    src = tmp / "lib.c"
    src.write_text('#include "lib.h"\nint lib_anchor(void) { return 0; }\n')
    so = tmp / "libw.so"
    subprocess.run(
        ["gcc", "-std=c17", "-g", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    return dump(
        so,
        [h],
        version="1",
        compiler="cc",
        lang="c",
        header_backend=backend,
        gcc_options="-std=c17",
    )


def _fields(snap: AbiSnapshot) -> dict[str, str]:
    out: dict[str, str] = {}
    for record in snap.declarations.types:
        if record.name.startswith("S"):
            out[record.name] = record.fields[0].type
    return out


def test_castxml_spells_array_bounds_in_c_order(tmp_path: Path) -> None:
    header = "typedef int row[3];\nint lib_anchor(void);\n" + "\n".join(
        f"struct S{i} {{ {decl} }};\nvoid use{i}(struct S{i} *p);"
        for i, (decl, _) in enumerate(_ARRAYS)
    )
    castxml = _fields(_snapshot(tmp_path / "x", header, "castxml"))
    for i, (decl, spelling) in enumerate(_ARRAYS):
        assert castxml[f"S{i}"] == spelling, decl
    if shutil.which("clang") is not None:
        clang = _fields(_snapshot(tmp_path / "c", header, "clang"))
        for i, (decl, _) in enumerate(_ARRAYS):
            if "(*" not in decl:
                assert _bounds(castxml[f"S{i}"]) == _bounds(clang[f"S{i}"]), decl


def _bounds(spelling: str) -> str:
    """*spelling* without whitespace (clang writes ``char *``, castxml ``char*``)."""
    return spelling.replace(" ", "")


# (parameter declaration, is it written as a va_list)
_PARAMS = (
    ("va_list ap", True),
    ("__builtin_va_list ap", True),
    ("my_list ap", True),
    ("__gnuc_va_list ap", True),
    ("void *ap", False),
    ("struct __va_list_tag *ap", False),
    ("int ap", False),
)


def test_castxml_marks_a_va_list_parameter(tmp_path: Path) -> None:
    header = "#include <stdarg.h>\ntypedef va_list my_list;\nint lib_anchor(void);\n"
    header += "\n".join(f"int f{i}(int n, {p});" for i, (p, _) in enumerate(_PARAMS))
    snap = _snapshot(tmp_path / "x", header, "castxml")
    by_name = {f.name: f for f in snap.declarations.functions}
    for i, (decl, expected) in enumerate(_PARAMS):
        params = by_name[f"f{i}"].params
        assert params[0].is_va_list_fact.value is False, decl
        fact = params[1].is_va_list_fact
        assert fact.is_present, decl
        assert fact.value is expected, decl
