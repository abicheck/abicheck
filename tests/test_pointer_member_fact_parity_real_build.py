"""``TypeField.type_abi_fact`` of a pointer member: castxml and clang agree.

The oracle is the compiler's own answer for the target: a data pointer is
``sizeof(void *)`` bytes, aligned to that. Each backend must report that fact
for a ``void *``, an ``int *`` and a function-pointer member.
Linux x86_64 only, the target the fact table is written for.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model.abi_fundamental import (
    AbiFundamental,
    FundamentalEncoding,
    Indirection,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() != "x86_64",
        reason="LP64 x86_64 pointer-width oracle",
    ),
]

_HEADER = """\
struct P { long a; void *v; int *i; void (*fp)(int); };
struct P mkp(long a);
"""
_SOURCE = """\
#include "pp.h"
struct P mkp(long a) { struct P p; p.a = a; p.v = 0; p.i = 0; p.fp = 0; return p; }
"""
_POINTER = AbiFundamental(FundamentalEncoding.UNSIGNED, 8, 8, 0, Indirection.POINTER)


def _gxx() -> str:
    if shutil.which("g++") is None:
        pytest.skip("no g++ on PATH")
    return "g++"


def _clangxx() -> str:
    for name in ("clang++-19", "clang++-18", "clang++-17", "clang++"):
        if shutil.which(name):
            return name
    pytest.skip("no clang++ on PATH")
    raise AssertionError("unreachable")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp = tmp_path_factory.mktemp("pp")
    header = tmp / "pp.h"
    header.write_text(_HEADER, encoding="utf-8")
    source = tmp / "pp.cpp"
    source.write_text(_SOURCE, encoding="utf-8")
    lib = tmp / "libpp.so"
    subprocess.run(
        [_gxx(), "-std=c++17", "-O0", "-fPIC", "-shared", str(source), "-o", str(lib)],
        check=True,
        capture_output=True,
    )
    return header, lib


def _member_facts(backend: str, built: tuple[Path, Path]) -> dict[str, object]:
    header, lib = built
    compiler = _clangxx() if backend == "clang" else _gxx()
    snap = dump(lib, headers=[header], header_backend=backend, compiler=compiler)
    record = next(r for r in snap.declarations.types if r.name == "P")
    out: dict[str, object] = {}
    for field in record.fields:
        fact = field.type_abi_fact
        out[field.name] = fact.value if fact is not None and fact.is_present else None
    return out


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_a_data_pointer_member_carries_the_target_pointer_fact(
    backend: str, built: tuple[Path, Path]
) -> None:
    facts = _member_facts(backend, built)
    assert facts["v"] == _POINTER, facts
    assert facts["i"] == _POINTER, facts


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_a_function_pointer_member_is_a_word_too(
    backend: str, built: tuple[Path, Path]
) -> None:
    # A function pointer is one word in the INTEGER class, like a data pointer.
    assert _member_facts(backend, built)["fp"] == _POINTER


def test_the_two_backends_agree_on_every_member(built: tuple[Path, Path]) -> None:
    assert _member_facts("castxml", built) == _member_facts("clang", built)
