"""``Function.return_by_value_fact`` at the header backends: castxml and clang agree.

The expected answer for each declared return type is written from the C++
type itself (a record is by value; a pointer, a reference, a builtin and an
enumeration are not), so neither extractor is its own oracle. Each backend
must match that table and each other, for the forms a typedef, a cv-qualifier
and a pointer can take.

Linux only: the library is built as an ELF shared object, and the header
backends are dumped from it.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() != "x86_64",
        reason="ELF shared-object build for the header backends",
    ),
]

_HEADER = """\
struct S { long a; long b; };
typedef struct S T;
typedef struct S *TP;
enum E { E_ONE, E_TWO };
S mk(long a);
S *mkp(long a);
S &mkr(long a);
const S cmk(long a);
T tmk(long a);
TP tmkp(long a);
int mki(long a);
E mke(long a);
"""

# The truth, written from the C++ types: record by value only for S, const S,
# and a typedef to S. Pointer, reference, builtin and enum returns are not.
_EXPECTED = {
    "mk": True,
    "mkp": False,
    "mkr": False,
    "cmk": True,
    "tmk": True,
    "tmkp": False,
    "mki": False,
    "mke": False,
}

_SOURCE = """\
#include "api.h"
S mk(long a) { S s; s.a = a; s.b = a; return s; }
S *mkp(long a) { static S s; s.a = a; return &s; }
S &mkr(long a) { static S s; s.a = a; return s; }
const S cmk(long a) { S s; s.a = a; s.b = a; return s; }
T tmk(long a) { S s; s.a = a; s.b = a; return s; }
TP tmkp(long a) { static S s; s.a = a; return &s; }
int mki(long a) { return (int)a; }
E mke(long a) { return a ? E_TWO : E_ONE; }
"""


def _compiler(family: str) -> str:
    candidates = (
        [f"{family}++-19", f"{family}++-18", f"{family}++-17", f"{family}++"]
        if family == "clang"
        else ["g++"]
    )
    for name in candidates:
        if shutil.which(name):
            return name
    pytest.skip(f"no {family}++ on PATH")
    raise AssertionError("unreachable")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp = tmp_path_factory.mktemp("rbv")
    header = tmp / "api.h"
    header.write_text(_HEADER, encoding="utf-8")
    source = tmp / "api.cpp"
    source.write_text(_SOURCE, encoding="utf-8")
    lib = tmp / "libapi.so"
    subprocess.run(
        [
            _compiler("gcc"),
            "-std=c++17",
            "-O0",
            "-fPIC",
            "-shared",
            str(source),
            "-o",
            str(lib),
        ],
        check=True,
        capture_output=True,
    )
    return header, lib


def _facts(backend: str, built: tuple[Path, Path]) -> dict[str, bool | None]:
    header, lib = built
    snap = dump(
        lib, headers=[header], header_backend=backend, compiler=_compiler("gcc")
    )
    out: dict[str, bool | None] = {}
    for fn in snap.declarations.functions:
        if fn.name in _EXPECTED:
            fact = fn.return_by_value_fact
            out[fn.name] = fact.value if fact is not None and fact.is_present else None
    return out


def test_castxml_matches_the_c_plus_plus_types(built: tuple[Path, Path]) -> None:
    got = _facts("castxml", built)
    assert got == _EXPECTED, got


def test_clang_matches_the_c_plus_plus_types(built: tuple[Path, Path]) -> None:
    got = _facts("clang", built)
    assert got == _EXPECTED, got


def test_the_two_backends_agree(built: tuple[Path, Path]) -> None:
    assert _facts("castxml", built) == _facts("clang", built)
