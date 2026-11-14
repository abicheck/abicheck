"""The native gcc header backend records explicit function template specializations.

gcc keeps ``template <> R f<A>(...)`` in its specialization table, not on the
template's instantiation list, so a walk of the namespaces alone never saw
it. Oracle: castxml (the reference backend) on the same header; each shape
-- a free function, a namespaced one, a member function template -- must be
declared by the gcc backend under the same mangled name, and the declaration
is what makes an added specialization a public ``func_added``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.dumper import dump

pytestmark = pytest.mark.integration

_SHAPES = {
    "free": (
        "template <class T> int f(T v) { return (int)sizeof(v); }\n"
        "template <> int f<int>(int v);\nint use(int v);\n",
        "template <> int f<int>(int v) { return v; }\nint use(int v) { return f(v); }\n",
        "_Z1fIiEiT_",
    ),
    "namespaced": (
        "namespace n { template <class T> int g(T v) { return 1; }\n"
        "template <> int g<long>(long v); }\nint use(int v);\n",
        "namespace n { template <> int g<long>(long v) { return (int)v; } }\n"
        "int use(int v) { return n::g(1L) + v; }\n",
        "_ZN1n1gIlEEiT_",
    ),
    "member": (
        "struct S { template <class T> int m(T v) { return 2; } };\n"
        "template <> int S::m<char>(char v);\nint use(int v);\n",
        "template <> int S::m<char>(char v) { return v; }\n"
        "int use(int v) { S s; return s.m('a') + v; }\n",
        "_ZN1S1mIcEEiT_",
    ),
}


def _declared(tmp: Path, shape: str, backend: str) -> set[str]:
    header, source, _ = _SHAPES[shape]
    (tmp / "api.h").write_text(header, encoding="utf-8")
    (tmp / "api.cpp").write_text('#include "api.h"\n' + source, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g",
            "-std=c++17",
            f"-I{tmp}",
            str(tmp / "api.cpp"),
            "-o",
            str(tmp / "libs.so"),
        ],
        check=True,
        capture_output=True,
    )
    snap = dump(
        tmp / "libs.so",
        [tmp / "api.h"],
        version="1",
        compiler="c++",
        lang="c++",
        header_backend=backend,
        public_headers=[tmp / "api.h"],
    )
    return {f.mangled for f in snap.declarations.functions}


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_gcc_backend_declares_each_explicit_specialization_as_castxml_does(
    tmp_path: Path, shape: str
) -> None:
    from abiforge.checkers.abicheck import header_backend_problem

    problem = header_backend_problem("gcc", "g++", "c++")
    if problem:
        pytest.skip(problem)
    mangled = _SHAPES[shape][2]
    (tmp_path / "c").mkdir()
    (tmp_path / "g").mkdir()
    assert mangled in _declared(tmp_path / "c", shape, "castxml")
    assert mangled in _declared(tmp_path / "g", shape, "gcc")
