"""The indirection guard of fact-based retype equivalence, checked on compiled types.

A pointer or reference slot carries an ABI fact only so the return-convention
and packing checks can read its word size. The equivalence gate must never
prove two such slots equal by fact, because the fact cannot show the pointees
match. The gate reads the fact's ``indirection`` kind.

The oracle is the C++ type of each member, compiled and dumped by both header
backends: a pointer, a pointer to function and a pointer to array are
``POINTER``; a scalar is ``NONE``. The gate's answer is then checked on the
dumped facts, not on hand-built values.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.compare.abi_equivalent_retype import _comparable_equal
from abicheck.compare.fact_comparison import compare_facts
from abicheck.dumper import dump
from abicheck.model.abi_fundamental import AbiFundamental, Indirection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() != "x86_64",
        reason="LP64 x86_64 ABI facts",
    ),
]

# field name -> (C++ type, expected indirection)
_FIELDS = {
    "scalar_long": ("long", Indirection.NONE),
    "scalar_double": ("double", Indirection.NONE),
    "data_pointer": ("int *", Indirection.POINTER),
    "const_data_pointer": ("const char *", Indirection.POINTER),
    "function_pointer": ("void (*)(int)", Indirection.POINTER),
    "array_pointer": ("int (*)[3]", Indirection.POINTER),
}

_HEADER = (
    "struct G {\n"
    + "".join(
        f"    {ctype.replace('(*)', '(*' + name + ')')};\n"
        if "(*)" in ctype
        else f"    {ctype} {name};\n"
        for name, (ctype, _) in _FIELDS.items()
    )
    + "};\n"
)


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
    tmp = tmp_path_factory.mktemp("ig")
    header = tmp / "ig.h"
    header.write_text(_HEADER, encoding="utf-8")
    source = tmp / "ig.cpp"
    source.write_text(
        '#include "ig.h"\nint probe(G *g) { return (int)g->scalar_long; }\n',
        encoding="utf-8",
    )
    lib = tmp / "libig.so"
    subprocess.run(
        [_gxx(), "-std=c++17", "-O0", "-fPIC", "-shared", str(source), "-o", str(lib)],
        check=True,
        capture_output=True,
    )
    return header, lib


def _dumped_facts(
    backend: str, built: tuple[Path, Path]
) -> dict[str, AbiFundamental | None]:
    header, lib = built
    compiler = _clangxx() if backend == "clang" else _gxx()
    snap = dump(lib, headers=[header], header_backend=backend, compiler=compiler)
    record = next(r for r in snap.declarations.types if r.name == "G")
    return {field.name: field.type_abi_fact for field in record.fields}


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_each_member_reports_its_c_plus_plus_indirection(
    backend: str, built: tuple[Path, Path]
) -> None:
    facts = _dumped_facts(backend, built)
    for name, (ctype, expected) in _FIELDS.items():
        fact = facts[name]
        assert fact is not None and fact.is_present, (
            f"{backend}: no fact for {name} ({ctype})"
        )
        assert fact.value.indirection is expected, f"{backend}: {name} ({ctype})"


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_equal_dumped_indirect_facts_are_never_equivalent(
    backend: str, built: tuple[Path, Path]
) -> None:
    facts = _dumped_facts(backend, built)
    for name, (_, expected) in _FIELDS.items():
        fact = facts[name]
        assert fact is not None
        comparison = compare_facts(fact, fact, fact)
        assert _comparable_equal(comparison) is (expected is Indirection.NONE), name


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_a_scalar_with_equal_dumped_facts_stays_equivalent(
    backend: str, built: tuple[Path, Path]
) -> None:
    facts = _dumped_facts(backend, built)
    fact = facts["scalar_long"]
    assert fact is not None
    assert _comparable_equal(compare_facts(fact, fact, fact)) is True
